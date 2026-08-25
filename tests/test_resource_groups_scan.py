"""
Tag-scan seam: the Resource Groups Tagging API path.

scan_all_tagged_resources is the hybrid entry the CLI uses whenever a tag
filter is given (RGTA sweep + Auto Scaling merge, since RGTA doesn't cover
ASGs). scan_all_services_with_tags wraps it with caching and timing. The
planned unified-RegionScanner refactor (candidate 6) must preserve these
observable results.
"""

from typing import Any

from aws_resource_inventory.lib.records import CallerIdentity
from aws_resource_inventory.lib.resource_groups_utils import (
    get_all_tagged_resources_across_services,
    scan_all_tagged_resources,
)
from aws_resource_inventory.lib.scan import scan_all_services_with_tags

REGION = "eu-central-1"
# moto's fake account, matching the ARNs it stamps on fixtures.
IDENTITY = CallerIdentity(account="123456789012", partition="aws")


def create_tagged_fixtures(aws_session: Any) -> str:
    """One tagged bucket, one untagged bucket, one tagged ASG. Returns instance-free setup."""
    s3 = aws_session.client("s3", region_name=REGION)
    s3.create_bucket(
        Bucket="tagged-bucket",
        CreateBucketConfiguration={"LocationConstraint": REGION},
    )
    s3.put_bucket_tagging(
        Bucket="tagged-bucket",
        Tagging={"TagSet": [{"Key": "env", "Value": "prod"}]},
    )
    s3.create_bucket(
        Bucket="untagged-bucket",
        CreateBucketConfiguration={"LocationConstraint": REGION},
    )

    ec2 = aws_session.client("ec2", region_name=REGION)
    ec2.create_launch_template(
        LaunchTemplateName="rgta-lt",
        LaunchTemplateData={"ImageId": "ami-12345678", "InstanceType": "t3.micro"},
    )
    aws_session.client("autoscaling", region_name=REGION).create_auto_scaling_group(
        AutoScalingGroupName="prod-asg",
        MinSize=0,
        MaxSize=1,
        AvailabilityZones=[f"{REGION}a"],
        LaunchTemplate={"LaunchTemplateName": "rgta-lt"},
        Tags=[
            {
                "Key": "env",
                "Value": "prod",
                "ResourceId": "prod-asg",
                "ResourceType": "auto-scaling-group",
            }
        ],
    )
    return "tagged-bucket"


class TestGetAllTaggedResources:
    def test_no_tags_returns_empty_without_calling_aws(self) -> None:
        # session=None: any API call would crash, so {} proves the guard.
        assert get_all_tagged_resources_across_services(None, REGION) == {}

    def test_tagged_resources_are_grouped_by_service(self, aws_session: Any) -> None:
        create_tagged_fixtures(aws_session)

        grouped = get_all_tagged_resources_across_services(
            aws_session, REGION, "env", "prod"
        )

        assert "s3" in grouped
        (resources,) = [r for rs in grouped["s3"].values() for r in rs]
        assert resources["ResourceARN"] == "arn:aws:s3:::tagged-bucket"
        assert resources["ResourceId"] == "tagged-bucket"
        assert resources["Region"] == REGION
        assert {"Key": "env", "Value": "prod"} in resources["Tags"]

    def test_untagged_resources_are_not_returned(self, aws_session: Any) -> None:
        create_tagged_fixtures(aws_session)

        grouped = get_all_tagged_resources_across_services(
            aws_session, REGION, "env", "prod"
        )

        all_arns = [
            r["ResourceARN"]
            for types in grouped.values()
            for rs in types.values()
            for r in rs
        ]
        assert "arn:aws:s3:::untagged-bucket" not in all_arns


class TestScanAllTaggedResources:
    def test_no_tags_returns_empty(self) -> None:
        assert scan_all_tagged_resources(None, REGION) == {}

    def test_hybrid_scan_merges_rgta_and_autoscaling(self, aws_session: Any) -> None:
        create_tagged_fixtures(aws_session)

        results = scan_all_tagged_resources(aws_session, REGION, "env", "prod")

        # RGTA side: the tagged bucket, under the s3 service.
        assert "s3" in results
        s3_arns = [r["ResourceARN"] for rs in results["s3"].values() for r in rs]
        assert s3_arns == ["arn:aws:s3:::tagged-bucket"]

        # ASG side: merged in from the dedicated Auto Scaling scan because
        # RGTA does not cover ASGs.
        assert "autoscaling" in results
        assert [
            a["AutoScalingGroupName"]
            for a in results["autoscaling"]["auto_scaling_groups"]
        ] == ["prod-asg"]

    def test_resource_type_keys_are_pluralized(self, aws_session: Any) -> None:
        create_tagged_fixtures(aws_session)

        results = scan_all_tagged_resources(aws_session, REGION, "env", "prod")

        # Every RGTA-derived key ends in "s" (type + "s" unless already so).
        for service, types in results.items():
            if service == "autoscaling":
                continue
            for key in types:
                assert key.endswith("s"), (service, key)


class TestTagScanFlattensEndToEnd:
    """The gap the 2026-08 regression slipped through: the tag scan's
    REAL output (produced by moto, not a hand-typed fixture) fed
    straight into output_results. All identifiers below are synthetic
    moto data — nothing from any real account."""

    def test_real_hybrid_scan_output_flattens_correctly(
        self, aws_session: Any, tmp_path: Any
    ) -> None:
        import json

        from aws_resource_inventory.lib.envelope import ScanFilters
        from aws_resource_inventory.lib.outputs import output_results

        create_tagged_fixtures(aws_session)

        # Producer: the actual hybrid scan (RGTA sections + merged ASGs).
        results = scan_all_tagged_resources(aws_session, REGION, "env", "prod")

        # Consumer: the real report pipeline, no hand-built middle.
        out = tmp_path / "scan.json"
        count = output_results(
            {REGION: results},
            out,
            debug=False,
            identity=IDENTITY,
            source="tagging",
            output_is_ours=False,
            regions=[REGION],
            filters=ScanFilters(
                services=None, tag_key="env", tag_value="prod", all_services=False
            ),
            started_at="2026-08-23T09:14:22Z",
            duration_seconds=1.5,
            errors=[],
        )

        records = {r["type"]: r for r in json.loads(out.read_text())["resources"]}
        # RGTA side flattens generically with real ARN/id; S3 records use
        # the same s3:bucket vocabulary as the per-service scanner.
        assert records["s3:bucket"]["id"] == "tagged-bucket"
        assert records["s3:bucket"]["arn"] == "arn:aws:s3:::tagged-bucket"
        # Merged autoscaling side flattens through its service processor —
        # the exact hand-off the regression mangled.
        assert records["autoscaling:autoScalingGroup"]["id"] == "prod-asg"
        assert "launch_templates" not in records  # the mangled form must not exist
        assert count == len(records)


class TestScanAllServicesWithTags:
    def test_returns_region_results_and_duration(self, aws_session: Any) -> None:
        create_tagged_fixtures(aws_session)

        region, results, duration, errors = scan_all_services_with_tags(
            aws_session, REGION, "env", "prod", use_cache=False
        )

        assert region == REGION
        assert "s3" in results
        assert duration >= 0
        assert errors == []

    def test_results_are_cached_for_the_tag_combination(self, aws_session: Any) -> None:
        create_tagged_fixtures(aws_session)

        _, first, _, _ = scan_all_services_with_tags(
            aws_session, REGION, "env", "prod", use_cache=True
        )
        assert first

        # A session that cannot scan: only a cache hit can produce results.
        _, cached, _, _ = scan_all_services_with_tags(
            None, REGION, "env", "prod", use_cache=True
        )
        assert cached == first
