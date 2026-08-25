"""
The findings document seam: waste's serialized output (ADR-0011).

``build_findings_document`` is pure — findings in, dict out — so the
whole schema is pinned without moto or a clock, exactly like
tests/test_envelope.py pins the inventory envelope. Changing any key
here is a deliberate act.
"""

from typing import Any

from aws_resource_inventory.lib.envelope import ScanError
from aws_resource_inventory.lib.records import CallerIdentity, Resource
from aws_resource_inventory.waste.document import (
    FINDINGS_SCHEMA_VERSION,
    build_findings_document,
)
from aws_resource_inventory.waste.findings import Finding

IDENTITY = CallerIdentity(account="111122223333", partition="aws")


def finding(region: str, resource_type: str, resource_id: str, rule: str) -> Finding:
    return Finding(
        resource=Resource(
            region=region,
            resource_type=resource_type,
            resource_id=resource_id,
            resource_arn=f"arn:aws:ec2:{region}:111122223333:x/{resource_id}",
            arn_source="constructed",
        ),
        rule=rule,
        confidence="certain",
        evidence={"State": "available"},
        suggested_action="delete",
    )


def build(findings: list[Finding], **overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "version": "1.2.3",
        "identity": IDENTITY,
        "regions": ["eu-central-1"],
        "services": ["ec2", "elb"],
        "managed_tag": None,
        "trust_tags": False,
        "stopped_days": 90,
        "unused_image_days": 90,
        "started_at": "2026-08-26T12:00:00Z",
        "duration_seconds": 4.2,
        "errors": [],
    }
    values.update(overrides)
    return build_findings_document(findings, **values)


class TestDocumentSchema:
    def test_top_level_shape_is_pinned(self) -> None:
        document = build([])
        assert list(document) == [
            "schema_version",
            "scan",
            "waste",
            "summary",
            "findings",
        ]
        assert document["schema_version"] == FINDINGS_SCHEMA_VERSION

    def test_scan_block_matches_the_envelope_vocabulary(self) -> None:
        document = build([], errors=[ScanError("eu-central-1", "ec2", "denied")])
        assert document["scan"] == {
            "tool": {"name": "aws-resource-inventory", "version": "1.2.3"},
            "account": "111122223333",
            "partition": "aws",
            "regions": ["eu-central-1"],
            "source": "services",
            "filters": {
                "services": ["ec2", "elb"],
                "tag_key": None,
                "tag_value": None,
                "all_services": False,
            },
            "started_at": "2026-08-26T12:00:00Z",
            "duration_seconds": 4.2,
            "errors": [
                {"region": "eu-central-1", "service": "ec2", "message": "denied"}
            ],
        }

    def test_waste_block_records_the_judgment_inputs(self) -> None:
        document = build(
            [], managed_tag="managed_by=terraform", trust_tags=True, stopped_days=30
        )
        assert document["waste"] == {
            "managed_tag": "managed_by=terraform",
            "trust_tags": True,
            "thresholds": {"stopped_days": 30, "unused_image_days": 90},
        }

    def test_findings_are_sorted_and_carry_the_record_vocabulary(self) -> None:
        unsorted = [
            finding("eu-west-1", "ec2:volume", "vol-2", "ebs-unattached"),
            finding("eu-central-1", "ec2:volume", "vol-1", "ebs-unattached"),
            finding("eu-central-1", "ec2:elastic-ip", "eip-1", "eip-unassociated"),
        ]
        records = build(unsorted, regions=["eu-central-1", "eu-west-1"])["findings"]
        assert [(r["region"], r["type"], r["id"]) for r in records] == [
            ("eu-central-1", "ec2:elastic-ip", "eip-1"),
            ("eu-central-1", "ec2:volume", "vol-1"),
            ("eu-west-1", "ec2:volume", "vol-2"),
        ]
        assert set(records[0]) == {
            "region",
            "type",
            "id",
            "name",
            "arn",
            "arn_source",
            "rule",
            "confidence",
            "evidence",
            "suggested_action",
        }

    def test_summary_counts_by_confidence_and_rule(self) -> None:
        document = build(
            [finding("eu-central-1", "ec2:volume", "vol-1", "ebs-unattached")]
        )
        summary = document["summary"]
        assert summary["total"] == 1
        # All three confidence levels always present — consumers diff on
        # stable keys.
        assert summary["by_confidence"] == {"certain": 1, "likely": 0, "review": 0}
        # Every registered rule reports, 0 when silent; tag-drift only
        # when the provider ran (it is opt-in).
        assert summary["by_rule"]["ebs-unattached"] == 1
        assert summary["by_rule"]["rds-stopped"] == 0
        assert "tag-drift" not in summary["by_rule"]

    def test_tag_drift_counts_only_when_the_provider_ran(self) -> None:
        document = build([], managed_tag="managed_by")
        assert document["summary"]["by_rule"]["tag-drift"] == 0

    def test_document_serializes_to_json(self) -> None:
        import json

        json.dumps(
            build([finding("eu-central-1", "ec2:volume", "vol-1", "ebs-unattached")])
        )
