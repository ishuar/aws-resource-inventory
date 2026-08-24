"""
Envelope seam: aws_resource_inventory.lib.envelope.build_envelope — the one
function that shapes serialized scan output.

The envelope is the tool's public JSON schema (schema_version 1, ADR-0005):
scan metadata + summary + resources[]. build_envelope is pure — fixtures
in, dict out — so the whole schema pins here without moto or a clock.
"""

import json
from typing import Any

from aws_resource_inventory.lib.envelope import (
    SCHEMA_VERSION,
    ScanFilters,
    build_envelope,
    tool_version,
)
from aws_resource_inventory.lib.records import CallerIdentity, Resource

REGION = "eu-central-1"
IDENTITY = CallerIdentity(account="111122223333", partition="aws")
NO_FILTERS = ScanFilters(
    services=["ec2", "s3"], tag_key=None, tag_value=None, all_services=False
)


def make_resource(**overrides: Any) -> Resource:
    fields: dict[str, Any] = {
        "region": REGION,
        "resource_type": "s3:bucket",
        "resource_id": "my-bucket",
        "resource_arn": "arn:aws:s3:::my-bucket",
        "arn_source": "constructed",
    }
    fields.update(overrides)
    return Resource(**fields)


def build(resources: list[Resource], **overrides: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "version": "0.2.0",
        "identity": IDENTITY,
        "regions": [REGION],
        "source": "services",
        "filters": NO_FILTERS,
        "started_at": "2026-08-23T09:14:22Z",
        "duration_seconds": 12.4,
    }
    kwargs.update(overrides)
    return build_envelope(resources, **kwargs)


def test_envelope_schema_snapshot() -> None:
    # The full schema, pinned key for key. Three resources exercise a
    # constructed ARN with a real name, an observed ARN with no name,
    # and the vpc:vpc case.
    resources = [
        Resource(
            region=REGION,
            resource_type="ec2:instance",
            resource_id="i-0abc123def456789a",
            resource_name="web-server-prod-01",
            resource_arn="arn:aws:ec2:eu-central-1:111122223333:instance/i-0abc123def456789a",
            arn_source="constructed",
        ),
        Resource(
            region=REGION,
            resource_type="elb:listener",
            resource_id="app/my-alb/1a2b3c4d/9i8j7k6l",
            resource_arn="arn:aws:elasticloadbalancing:eu-central-1:111122223333:listener/app/my-alb/1a2b3c4d/9i8j7k6l",
            arn_source="observed",
        ),
        Resource(
            region=REGION,
            resource_type="vpc:vpc",
            resource_id="vpc-0f1e2d3c",
            resource_arn="arn:aws:ec2:eu-central-1:111122223333:vpc/vpc-0f1e2d3c",
            arn_source="constructed",
        ),
    ]
    envelope = build(
        resources,
        filters=ScanFilters(
            services=["ec2", "vpc", "elb"],
            tag_key=None,
            tag_value=None,
            all_services=False,
        ),
    )

    assert envelope == {
        "schema_version": 1,
        "scan": {
            "tool": {"name": "aws-resource-inventory", "version": "0.2.0"},
            "account": "111122223333",
            "partition": "aws",
            "regions": ["eu-central-1"],
            "source": "services",
            "filters": {
                "services": ["ec2", "vpc", "elb"],
                "tag_key": None,
                "tag_value": None,
                "all_services": False,
            },
            "started_at": "2026-08-23T09:14:22Z",
            "duration_seconds": 12.4,
        },
        "summary": {
            "total": 3,
            "by_region": {"eu-central-1": 3},
            "by_type": {"ec2:instance": 1, "elb:listener": 1, "vpc:vpc": 1},
        },
        "resources": [
            {
                "region": "eu-central-1",
                "type": "ec2:instance",
                "id": "i-0abc123def456789a",
                "name": "web-server-prod-01",
                "arn": "arn:aws:ec2:eu-central-1:111122223333:instance/i-0abc123def456789a",
                "arn_source": "constructed",
            },
            {
                "region": "eu-central-1",
                "type": "elb:listener",
                "id": "app/my-alb/1a2b3c4d/9i8j7k6l",
                "name": None,
                "arn": "arn:aws:elasticloadbalancing:eu-central-1:111122223333:listener/app/my-alb/1a2b3c4d/9i8j7k6l",
                "arn_source": "observed",
            },
            {
                "region": "eu-central-1",
                "type": "vpc:vpc",
                "id": "vpc-0f1e2d3c",
                "name": None,
                "arn": "arn:aws:ec2:eu-central-1:111122223333:vpc/vpc-0f1e2d3c",
                "arn_source": "constructed",
            },
        ],
    }
    # Key order is part of the schema, not an accident of construction.
    assert list(envelope) == ["schema_version", "scan", "summary", "resources"]
    assert list(envelope["scan"]) == [
        "tool",
        "account",
        "partition",
        "regions",
        "source",
        "filters",
        "started_at",
        "duration_seconds",
    ]
    assert list(envelope["summary"]) == ["total", "by_region", "by_type"]
    for record in envelope["resources"]:
        assert list(record) == ["region", "type", "id", "name", "arn", "arn_source"]


def test_schema_version_is_one() -> None:
    assert SCHEMA_VERSION == 1
    assert build([])["schema_version"] == 1


def test_resources_are_sorted_by_region_then_type_then_id() -> None:
    unsorted = [
        make_resource(region="us-east-1", resource_type="s3:bucket", resource_id="b"),
        make_resource(
            region=REGION,
            resource_type="vpc:vpc",
            resource_id="vpc-1",
            resource_arn="arn:aws:ec2:eu-central-1:111122223333:vpc/vpc-1",
        ),
        make_resource(region=REGION, resource_type="s3:bucket", resource_id="b"),
        make_resource(region=REGION, resource_type="s3:bucket", resource_id="a"),
    ]
    envelope = build(unsorted)
    assert [(r["region"], r["type"], r["id"]) for r in envelope["resources"]] == [
        (REGION, "s3:bucket", "a"),
        (REGION, "s3:bucket", "b"),
        (REGION, "vpc:vpc", "vpc-1"),
        ("us-east-1", "s3:bucket", "b"),
    ]


def test_same_input_twice_yields_identical_resources() -> None:
    resources = [
        make_resource(resource_id="b"),
        make_resource(resource_id="a"),
    ]
    first = build(resources)
    second = build(resources)
    assert json.dumps(first["resources"]) == json.dumps(second["resources"])
    assert json.dumps(first) == json.dumps(second)


def test_summary_counts_the_emitted_resources() -> None:
    resources = [
        make_resource(region=REGION, resource_id="a"),
        make_resource(region=REGION, resource_id="b"),
        make_resource(
            region="us-east-1",
            resource_type="ec2:instance",
            resource_id="i-1",
            resource_arn="arn:aws:ec2:us-east-1:111122223333:instance/i-1",
        ),
    ]
    summary = build(resources)["summary"]
    assert summary == {
        "total": 3,
        "by_region": {"eu-central-1": 2, "us-east-1": 1},
        "by_type": {"ec2:instance": 1, "s3:bucket": 2},
    }
    # by_service is deliberately absent — derivable from by_type.
    assert "by_service" not in summary


def test_empty_scan_still_produces_the_full_envelope() -> None:
    envelope = build([])
    # by_region is seeded from the scanned regions: a region that returned
    # nothing reports 0, never absence (ADR-0005 — a partially-failed scan
    # must stay visible).
    assert envelope["summary"] == {
        "total": 0,
        "by_region": {REGION: 0},
        "by_type": {},
    }
    assert envelope["resources"] == []


def test_by_region_reports_zero_for_a_scanned_but_empty_region() -> None:
    # Two regions scanned, one produced nothing. The empty one must still
    # appear — absence would be indistinguishable from "never scanned".
    envelope = build(
        [make_resource(region="eu-central-1")],
        regions=["eu-central-1", "eu-west-1"],
    )
    assert envelope["summary"]["by_region"] == {"eu-central-1": 1, "eu-west-1": 0}


def test_by_region_keeps_a_region_absent_from_the_scanned_list() -> None:
    # Seeding adds regions, it never drops observed ones.
    envelope = build(
        [
            make_resource(
                region="us-east-1",
                resource_arn="arn:aws:s3:::my-bucket-us",
            )
        ],
        regions=["eu-central-1"],
    )
    assert envelope["summary"]["by_region"] == {"eu-central-1": 0, "us-east-1": 1}


def test_by_type_is_never_seeded_with_zeros() -> None:
    # Deliberate asymmetry with by_region: resource types are discovered,
    # not requested. There is no input list to seed from — the tagging path
    # emits whatever AWS returns — so absence is the only honest answer.
    envelope = build([], regions=["eu-central-1", "eu-west-1"])
    assert envelope["summary"]["by_type"] == {}
    envelope = build([make_resource()], filters=NO_FILTERS)
    assert 0 not in envelope["summary"]["by_type"].values()


def test_absent_name_and_filters_serialize_as_json_null() -> None:
    envelope = build(
        [make_resource()],
        source="tagging",
        filters=ScanFilters(
            services=None, tag_key="managed_by", tag_value=None, all_services=False
        ),
    )
    serialized = json.dumps(envelope, indent=2)
    assert '"name": null' in serialized
    assert '"services": null' in serialized
    assert '"tag_value": null' in serialized
    assert envelope["scan"]["filters"]["tag_key"] == "managed_by"
    assert envelope["scan"]["source"] == "tagging"


def test_tool_version_reads_the_installed_distribution() -> None:
    # The test venv carries an editable install, so metadata resolves.
    version = tool_version()
    assert version
    assert version != "unknown"


def test_tool_version_falls_back_when_not_installed(monkeypatch: Any) -> None:
    import importlib.metadata

    def missing(_name: str) -> str:
        raise importlib.metadata.PackageNotFoundError

    monkeypatch.setattr(importlib.metadata, "version", missing)
    assert tool_version() == "unknown"
