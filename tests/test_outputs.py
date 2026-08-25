"""
Output seam: aws_resource_inventory.lib.outputs.output_results.

output_results is the single funnel from nested scan results to the JSON
envelope (a file, or stdout when output_file is None — the CLI's
--output -). These tests pin: what gets written where, the return value,
and how results from the two scan paths (traditional vs Resource Groups
API) are routed to processors.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from aws_resource_inventory.lib.envelope import ScanFilters
from aws_resource_inventory.lib.outputs import output_results
from aws_resource_inventory.lib.records import CallerIdentity

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
    "errors": [],
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
    def test_scan_writes_the_envelope_and_returns_the_count(
        self, tmp_path: Path
    ) -> None:
        out = tmp_path / "scan.json"
        count = output_results(
            traditional_results(),
            out,
            debug=False,
            identity=IDENTITY,
            source="services",
            output_is_ours=False,
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

    def test_stdout_mode_prints_only_the_envelope_and_touches_no_disk(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: Any
    ) -> None:
        # output_file=None is --output -: the document goes to stdout,
        # nothing is written anywhere on disk.
        monkeypatch.chdir(tmp_path)
        count = output_results(
            traditional_results(),
            None,
            debug=False,
            identity=IDENTITY,
            source="services",
            output_is_ours=False,
            **ENVELOPE_KWARGS,
        )

        assert count == 3
        captured = capsys.readouterr()
        # The whole stream must parse — any decoration would break jq.
        envelope = json.loads(captured.out)
        assert envelope["schema_version"] == 1
        assert len(envelope["resources"]) == 3
        assert list(tmp_path.iterdir()) == []

    def test_missing_output_directory_is_created(self, tmp_path: Path) -> None:
        out = tmp_path / "deeply" / "nested" / "scan.json"
        output_results(
            traditional_results(),
            out,
            debug=False,
            identity=IDENTITY,
            source="services",
            output_is_ours=False,
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
            debug=False,
            identity=IDENTITY,
            source="services",
            output_is_ours=False,
            **ENVELOPE_KWARGS,
        )

        assert count == 0
        written = json.loads(out.read_text())
        assert written["resources"] == []
        # by_region names every scanned region even at zero — absence
        # would be indistinguishable from "never scanned" (ADR-0005).
        assert written["summary"] == {
            "total": 0,
            "by_region": {REGION: 0},
            "by_type": {},
        }

    def test_empty_service_data_is_skipped(self, tmp_path: Path) -> None:
        out = tmp_path / "scan.json"
        count = output_results(
            {REGION: {"ec2": {}}},
            out,
            debug=False,
            identity=IDENTITY,
            source="services",
            output_is_ours=False,
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
            debug=False,
            identity=IDENTITY,
            source="tagging",
            output_is_ours=False,
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
            debug=False,
            identity=IDENTITY,
            source="tagging",
            output_is_ours=False,
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
            debug=False,
            identity=IDENTITY,
            source="services",
            output_is_ours=False,
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
            debug=False,
            identity=IDENTITY,
            source="tagging",
            output_is_ours=False,
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
