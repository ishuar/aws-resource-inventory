"""
Output seam: aws_resource_inventory.lib.outputs.output_results and the markdown report.

output_results is the single funnel from nested scan results to files on
disk. These tests pin: format routing, which files get written, the return
value, and how results from the two scan paths (traditional vs Resource
Groups API) are routed to processors.
"""

import json
from pathlib import Path
from typing import Any

from aws_resource_inventory.lib.envelope import ScanFilters
from aws_resource_inventory.lib.outputs import generate_markdown_summary, output_results
from aws_resource_inventory.lib.records import CallerIdentity, Resource

REGION = "eu-central-1"
IDENTITY = CallerIdentity(account="111122223333", partition="aws")
FILTERS = ScanFilters(
    services=["ec2", "s3"], tag_key=None, tag_value=None, all_services=False
)
# The scan block the CLI would supply — fixed values, so tests stay
# deterministic; the envelope's own schema is pinned in test_envelope.py.
ENVELOPE_KWARGS: dict[str, Any] = {
    "regions": [REGION],
    "filters": FILTERS,
    "started_at": "2026-08-23T09:14:22Z",
    "duration_seconds": 1.5,
}


def traditional_results() -> dict[str, Any]:
    """Nested results as scan_region produces them (traditional path)."""
    return {
        REGION: {
            "s3": {"buckets": [{"Name": "bucket-a"}, {"Name": "bucket-b"}]},
            "ec2": {"instances": [{"InstanceId": "i-1", "Tags": []}]},
        }
    }


def resource_groups_results() -> dict[str, Any]:
    """Nested results as the Resource Groups API path produces them."""
    return {
        REGION: {
            "lambda": {
                "functions": [
                    {
                        "ResourceARN": "arn:aws:lambda:eu-central-1:1:function:fn",
                        "ResourceId": "fn",
                        "ResourceType": "lambda:function",
                        "Region": REGION,
                        "Tags": [{"Key": "env", "Value": "prod"}],
                    }
                ]
            }
        }
    }


class TestOutputResults:
    def test_json_writes_the_envelope_and_returns_the_count(
        self, tmp_path: Path
    ) -> None:
        out = tmp_path / "scan.json"
        count = output_results(
            traditional_results(),
            out,
            "json",
            debug=False,
            identity=IDENTITY,
            source="services",
            **ENVELOPE_KWARGS,
        )

        assert count == 3
        written = json.loads(out.read_text())
        # The file is the envelope document, never a bare array; the
        # scan block carries exactly what the caller supplied.
        assert written["schema_version"] == 1
        assert written["scan"]["account"] == IDENTITY.account
        assert written["scan"]["source"] == "services"
        assert written["scan"]["regions"] == [REGION]
        assert written["scan"]["started_at"] == "2026-08-23T09:14:22Z"
        assert written["scan"]["duration_seconds"] == 1.5
        assert written["scan"]["filters"]["services"] == ["ec2", "s3"]
        assert written["summary"]["total"] == 3
        resources = written["resources"]
        assert len(resources) == 3
        assert {r["type"] for r in resources} == {"s3:bucket", "ec2:instance"}

    def test_table_format_still_writes_the_json_file(self, tmp_path: Path) -> None:
        out = tmp_path / "scan.json"
        count = output_results(
            traditional_results(),
            out,
            "table",
            debug=False,
            identity=IDENTITY,
            source="services",
            **ENVELOPE_KWARGS,
        )

        assert count == 3
        assert len(json.loads(out.read_text())["resources"]) == 3

    def test_markdown_format_writes_a_md_file_with_the_md_suffix(
        self, tmp_path: Path
    ) -> None:
        out = tmp_path / "scan.json"
        count = output_results(
            traditional_results(),
            out,
            "md",
            debug=False,
            identity=IDENTITY,
            source="services",
            **ENVELOPE_KWARGS,
        )

        assert count == 3
        md_file = tmp_path / "scan.md"
        assert md_file.exists()
        assert "# AWS Resources Scan Report" in md_file.read_text()

    def test_markdown_alias_behaves_like_md(self, tmp_path: Path) -> None:
        out = tmp_path / "scan.json"
        output_results(
            traditional_results(),
            out,
            "markdown",
            debug=False,
            identity=IDENTITY,
            source="services",
            **ENVELOPE_KWARGS,
        )
        assert (tmp_path / "scan.md").exists()

    def test_unknown_format_writes_nothing_but_still_returns_the_count(
        self, tmp_path: Path
    ) -> None:
        # Characterization: an unknown format is reported on the console
        # only — no file, no exception, count still returned. (A future
        # change may make this a hard error; that would be an improvement.)
        out = tmp_path / "scan.json"
        count = output_results(
            traditional_results(),
            out,
            "yaml",
            debug=False,
            identity=IDENTITY,
            source="services",
            **ENVELOPE_KWARGS,
        )

        assert count == 3
        assert not out.exists()

    def test_missing_output_directory_is_created(self, tmp_path: Path) -> None:
        out = tmp_path / "deeply" / "nested" / "scan.json"
        output_results(
            traditional_results(),
            out,
            "json",
            debug=False,
            identity=IDENTITY,
            source="services",
            **ENVELOPE_KWARGS,
        )
        assert out.exists()

    def test_empty_results_produce_an_empty_envelope_and_zero(
        self, tmp_path: Path
    ) -> None:
        out = tmp_path / "scan.json"
        count = output_results(
            {},
            out,
            "json",
            debug=False,
            identity=IDENTITY,
            source="services",
            **ENVELOPE_KWARGS,
        )

        assert count == 0
        written = json.loads(out.read_text())
        assert written["resources"] == []
        assert written["summary"] == {"total": 0, "by_region": {}, "by_type": {}}

    def test_empty_service_data_is_skipped(self, tmp_path: Path) -> None:
        out = tmp_path / "scan.json"
        count = output_results(
            {REGION: {"ec2": {}}},
            out,
            "json",
            debug=False,
            identity=IDENTITY,
            source="services",
            **ENVELOPE_KWARGS,
        )
        assert count == 0

    def test_tagging_source_routes_to_the_generic_processor(
        self, tmp_path: Path
    ) -> None:
        # Tag-path results (any service, incl. ones with no dedicated
        # processor) go through the generic processor, keyed by ResourceType.
        out = tmp_path / "scan.json"
        count = output_results(
            resource_groups_results(),
            out,
            "json",
            debug=False,
            identity=IDENTITY,
            source="tagging",
            **ENVELOPE_KWARGS,
        )

        assert count == 1
        (record,) = json.loads(out.read_text())["resources"]
        assert record["type"] == "lambda:function"
        assert record["id"] == "fn"

    def test_tagging_source_bypasses_service_processors_even_for_known_names(
        self, tmp_path: Path
    ) -> None:
        # The explicit source replaces the old structural sniffing: under
        # source="tagging", even an "ec2" key goes through the generic
        # processor (which keeps the real ARN; process_ec2_output would
        # have hardcoded resource_arn to "N/A").
        results = {
            REGION: {
                "ec2": {
                    "instances": [
                        {
                            "ResourceARN": "arn:aws:ec2:eu-central-1:1:instance/i-7",
                            "ResourceId": "i-7",
                            "ResourceType": "ec2:instance",
                        }
                    ]
                }
            }
        }
        out = tmp_path / "scan.json"
        count = output_results(
            results,
            out,
            "json",
            debug=False,
            identity=IDENTITY,
            source="tagging",
            **ENVELOPE_KWARGS,
        )

        assert count == 1
        (record,) = json.loads(out.read_text())["resources"]
        assert record["arn"] == "arn:aws:ec2:eu-central-1:1:instance/i-7"

    def test_unknown_traditional_service_falls_back_to_generic(
        self, tmp_path: Path
    ) -> None:
        results = {REGION: {"unknownsvc": {"things": [{"SomeKey": "some-value"}]}}}
        out = tmp_path / "scan.json"
        count = output_results(
            results,
            out,
            "json",
            debug=False,
            identity=IDENTITY,
            source="services",
            **ENVELOPE_KWARGS,
        )

        # Nothing identifiable (no ARN, no id): the record is skipped with
        # a log line — "N/A" identities are banned output.
        assert count == 0
        assert json.loads(out.read_text())["resources"] == []


class TestTaggingPathHybridResults:
    """The tag path is a hybrid: Resource Groups API sections are
    generic-shaped, but the merged autoscaling section carries raw
    service-shaped dicts (RGTA does not cover ASGs). Flattening must
    route that section through the autoscaling processor."""

    def test_autoscaling_section_flattens_with_service_vocabulary(
        self, tmp_path: Path
    ) -> None:
        results = {
            REGION: {
                "s3": {
                    "buckets": [
                        {
                            "ResourceARN": "arn:aws:s3:::tagged-bucket",
                            "ResourceId": "tagged-bucket",
                            "ResourceType": "s3:bucket",
                        }
                    ]
                },
                "autoscaling": {
                    "auto_scaling_groups": [
                        {
                            "AutoScalingGroupName": "web-asg",
                            "AutoScalingGroupARN": "arn:aws:autoscaling:eu-central-1:1:asg/web-asg",
                        }
                    ],
                    "launch_templates": [
                        {"LaunchTemplateName": "web-lt", "LaunchTemplateId": "lt-1"}
                    ],
                },
            }
        }
        out = tmp_path / "scan.json"
        count = output_results(
            results,
            out,
            "json",
            debug=False,
            identity=IDENTITY,
            source="tagging",
            **ENVELOPE_KWARGS,
        )

        assert count == 3
        records = {r["type"]: r for r in json.loads(out.read_text())["resources"]}
        assert records["s3:bucket"]["id"] == "tagged-bucket"
        asg = records["autoscaling:autoScalingGroup"]
        assert asg["id"] == "web-asg"
        assert asg["arn"] == "arn:aws:autoscaling:eu-central-1:1:asg/web-asg"
        lt = records["autoscaling:launch-template"]
        assert lt["id"] == "lt-1"
        assert lt["name"] == "web-lt"


class TestMarkdownSummary:
    def test_report_counts_by_region_service_and_type(self) -> None:
        flattened = [
            Resource(
                region=REGION,
                resource_type="s3:bucket",
                resource_id="bucket-a",
                resource_arn="arn:aws:s3:::bucket-a",
                arn_source="constructed",
            ),
            Resource(
                region="us-east-1",
                resource_type="ec2:instance",
                resource_id="i-1",
                resource_arn="arn:aws:ec2:us-east-1:111122223333:instance/i-1",
                arn_source="constructed",
            ),
        ]
        report = generate_markdown_summary(flattened, {})

        assert "**Total Resources:** 2" in report
        assert f"- **{REGION}**: 1 resources" in report
        assert "- **us-east-1**: 1 resources" in report
        assert "- **S3**: 1 resources" in report
        assert "- **EC2**: 1 resources" in report
        assert "- **s3:bucket**: 1" in report

    def test_missing_resource_name_falls_back_to_the_id(self) -> None:
        flattened = [
            Resource(
                region=REGION,
                resource_type="ecs:cluster",
                resource_id="prod-cluster",
                resource_arn="arn:aws:ecs:eu-central-1:1:cluster/prod-cluster",
                arn_source="observed",
            )
        ]
        report = generate_markdown_summary(flattened, {})
        assert "| prod-cluster |" in report

    def test_pipes_in_values_are_escaped_so_tables_stay_valid(self) -> None:
        flattened = [
            Resource(
                region=REGION,
                resource_name="name|with|pipes",
                resource_type="s3:bucket",
                resource_id="name|with|pipes",
                resource_arn="arn:aws:s3:::name|with|pipes",
                arn_source="constructed",
            )
        ]
        report = generate_markdown_summary(flattened, {})
        assert "name\\|with\\|pipes" in report
