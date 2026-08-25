"""
Waste seams: the Finding record, the rules registry, and the ec2 rules.

Rules are pure functions over fixture dicts (no moto, no AWS): each one
is pinned on both sides — it fires on the state it names and stays
silent on the healthy shape. The state-rules provider's contract is
pinned too: a rule is skipped for a region whose scan data is missing a
service the rule reads (an errored service never fabricates findings,
PRODUCT.md decision 16), and a raw item the inventory skipped as
unidentifiable yields no finding.
"""

from datetime import datetime, timezone
from typing import Any

from aws_resource_inventory.lib.records import Resource
from aws_resource_inventory.waste.config import WasteConfig
from aws_resource_inventory.waste.findings import Finding
from aws_resource_inventory.waste.providers import evaluate_state_rules
from aws_resource_inventory.waste.registry import RULES

REGION = "eu-central-1"
NOW = datetime(2026, 8, 26, 12, 0, 0, tzinfo=timezone.utc)
CONFIG = WasteConfig(now=NOW)


def resource(resource_type: str, resource_id: str) -> Resource:
    return Resource(
        region=REGION,
        resource_type=resource_type,
        resource_id=resource_id,
        resource_arn=f"arn:aws:ec2:{REGION}:111122223333:{resource_type.split(':', 1)[1]}/{resource_id}",
        arn_source="constructed",
    )


def evaluate(scan_data: dict[str, Any], resources: list[Resource]) -> list[Finding]:
    return evaluate_state_rules(scan_data, resources, REGION, CONFIG)


def empty_ec2_data(**sections: list[dict[str, Any]]) -> dict[str, Any]:
    """A clean ec2 scan: every section present, overridden per test."""
    base: dict[str, Any] = {
        "instances": [],
        "volumes": [],
        "security_groups": [],
        "amis": [],
        "snapshots": [],
        "addresses": [],
    }
    base.update(sections)
    return {"ec2": base}


class TestFindingRecord:
    def test_serializes_resource_identity_plus_judgment(self) -> None:
        finding = Finding(
            resource=resource("ec2:volume", "vol-1"),
            rule="ebs-unattached",
            confidence="certain",
            evidence={"State": "available"},
            suggested_action="snapshot-then-delete",
        )
        assert finding.to_record() == {
            "region": REGION,
            "type": "ec2:volume",
            "id": "vol-1",
            "name": None,
            "arn": f"arn:aws:ec2:{REGION}:111122223333:volume/vol-1",
            "arn_source": "constructed",
            "rule": "ebs-unattached",
            "confidence": "certain",
            "evidence": {"State": "available"},
            "suggested_action": "snapshot-then-delete",
        }


class TestRegistry:
    def test_the_v1_ec2_rules_are_registered(self) -> None:
        # The registry is the single source of truth; the rule-name
        # vocabulary is public output language, pinned like resource types.
        assert {
            "ebs-unattached",
            "eip-unassociated",
            "ec2-long-stopped",
            "snapshot-orphaned",
            "ami-unused",
        } <= set(RULES)

    def test_every_registration_names_the_services_it_reads(self) -> None:
        for name, registration in RULES.items():
            assert registration.services, name


class TestEbsUnattached:
    def test_available_volume_is_certain_waste(self) -> None:
        data = empty_ec2_data(
            volumes=[
                {
                    "VolumeId": "vol-1",
                    "State": "available",
                    "CreateTime": datetime(2024, 1, 3, tzinfo=timezone.utc),
                    "Size": 100,
                }
            ]
        )
        findings = evaluate(data, [resource("ec2:volume", "vol-1")])
        assert [f.rule for f in findings] == ["ebs-unattached"]
        finding = findings[0]
        assert finding.confidence == "certain"
        assert finding.suggested_action == "snapshot-then-delete"
        assert finding.evidence == {
            "State": "available",
            "CreateTime": "2024-01-03T00:00:00+00:00",
            "Size": 100,
        }
        assert finding.resource.resource_id == "vol-1"

    def test_in_use_volume_is_not_waste(self) -> None:
        data = empty_ec2_data(volumes=[{"VolumeId": "vol-1", "State": "in-use"}])
        assert evaluate(data, [resource("ec2:volume", "vol-1")]) == []

    def test_volume_the_inventory_skipped_yields_no_finding(self) -> None:
        # The processor skips unidentifiable raw dicts; a finding without
        # an identity would violate the record contract, so the rule
        # skips it too (logged, never silent).
        data = empty_ec2_data(volumes=[{"VolumeId": "vol-1", "State": "available"}])
        assert evaluate(data, []) == []


class TestEipUnassociated:
    def test_unassociated_address_is_certain_waste(self) -> None:
        data = empty_ec2_data(
            addresses=[
                {
                    "AllocationId": "eipalloc-1",
                    "PublicIp": "192.0.2.10",
                    "Domain": "vpc",
                }
            ]
        )
        findings = evaluate(data, [resource("ec2:elastic-ip", "eipalloc-1")])
        assert [f.rule for f in findings] == ["eip-unassociated"]
        assert findings[0].confidence == "certain"
        assert findings[0].suggested_action == "delete"
        assert findings[0].evidence == {
            "AssociationId": None,
            "PublicIp": "192.0.2.10",
            "Domain": "vpc",
        }

    def test_associated_address_is_not_waste(self) -> None:
        data = empty_ec2_data(
            addresses=[
                {
                    "AllocationId": "eipalloc-1",
                    "PublicIp": "192.0.2.10",
                    "AssociationId": "eipassoc-1",
                }
            ]
        )
        assert evaluate(data, [resource("ec2:elastic-ip", "eipalloc-1")]) == []


class TestEc2LongStopped:
    def stopped_instance(self, since: str) -> dict[str, Any]:
        return {
            "InstanceId": "i-1",
            "State": {"Name": "stopped"},
            "StateTransitionReason": f"User initiated ({since})",
        }

    def test_stopped_beyond_threshold_is_likely_waste(self) -> None:
        data = empty_ec2_data(
            instances=[self.stopped_instance("2026-01-01 10:00:00 GMT")]
        )
        findings = evaluate(data, [resource("ec2:instance", "i-1")])
        assert [f.rule for f in findings] == ["ec2-long-stopped"]
        finding = findings[0]
        assert finding.confidence == "likely"
        assert finding.suggested_action == "review"
        assert finding.evidence["State"] == "stopped"
        assert finding.evidence["days_stopped"] == 237
        assert "StateTransitionReason" in finding.evidence

    def test_recently_stopped_is_not_waste(self) -> None:
        data = empty_ec2_data(
            instances=[self.stopped_instance("2026-08-01 10:00:00 GMT")]
        )
        assert evaluate(data, [resource("ec2:instance", "i-1")]) == []

    def test_running_instance_is_not_waste(self) -> None:
        data = empty_ec2_data(
            instances=[{"InstanceId": "i-1", "State": {"Name": "running"}}]
        )
        assert evaluate(data, [resource("ec2:instance", "i-1")]) == []

    def test_stopped_without_a_parseable_date_is_not_claimed(self) -> None:
        # AWS clears StateTransitionReason on some paths. Without a date
        # there is no evidence for "long" — the rule claims nothing
        # rather than guessing (evidence, not vibes).
        data = empty_ec2_data(
            instances=[
                {
                    "InstanceId": "i-1",
                    "State": {"Name": "stopped"},
                    "StateTransitionReason": "",
                }
            ]
        )
        assert evaluate(data, [resource("ec2:instance", "i-1")]) == []


class TestSnapshotOrphaned:
    def test_snapshot_of_deleted_volume_is_likely_waste(self) -> None:
        data = empty_ec2_data(
            snapshots=[
                {
                    "SnapshotId": "snap-1",
                    "VolumeId": "vol-gone",
                    "StartTime": datetime(2024, 1, 3, tzinfo=timezone.utc),
                }
            ]
        )
        findings = evaluate(data, [resource("ec2:snapshot", "snap-1")])
        assert [f.rule for f in findings] == ["snapshot-orphaned"]
        assert findings[0].confidence == "likely"
        assert findings[0].suggested_action == "delete"
        assert findings[0].evidence == {
            "VolumeId": "vol-gone",
            "StartTime": "2024-01-03T00:00:00+00:00",
        }

    def test_snapshot_of_existing_volume_is_not_waste(self) -> None:
        data = empty_ec2_data(
            volumes=[{"VolumeId": "vol-1", "State": "in-use"}],
            snapshots=[{"SnapshotId": "snap-1", "VolumeId": "vol-1"}],
        )
        resources = [
            resource("ec2:volume", "vol-1"),
            resource("ec2:snapshot", "snap-1"),
        ]
        assert evaluate(data, resources) == []

    def test_ami_backing_snapshot_is_not_waste(self) -> None:
        # A snapshot referenced by a registered image is in use even if
        # its source volume is long gone.
        data = empty_ec2_data(
            snapshots=[{"SnapshotId": "snap-1", "VolumeId": "vol-gone"}],
            amis=[
                {
                    "ImageId": "ami-1",
                    "BlockDeviceMappings": [{"Ebs": {"SnapshotId": "snap-1"}}],
                }
            ],
        )
        resources = [
            resource("ec2:snapshot", "snap-1"),
            resource("ec2:image", "ami-1"),
        ]
        assert evaluate(data, resources) == []


class TestAmiUnused:
    def old_image(self) -> dict[str, Any]:
        return {"ImageId": "ami-1", "CreationDate": "2024-01-03T00:00:00.000Z"}

    def test_unreferenced_old_image_is_likely_waste(self) -> None:
        data = empty_ec2_data(amis=[self.old_image()])
        findings = evaluate(data, [resource("ec2:image", "ami-1")])
        assert [f.rule for f in findings] == ["ami-unused"]
        assert findings[0].confidence == "likely"
        assert findings[0].suggested_action == "delete"
        assert findings[0].evidence == {"CreationDate": "2024-01-03T00:00:00.000Z"}

    def test_image_referenced_by_an_instance_is_not_waste(self) -> None:
        data = empty_ec2_data(
            amis=[self.old_image()],
            instances=[
                {"InstanceId": "i-1", "ImageId": "ami-1", "State": {"Name": "running"}}
            ],
        )
        resources = [resource("ec2:image", "ami-1"), resource("ec2:instance", "i-1")]
        assert evaluate(data, resources) == []

    def test_young_image_is_not_waste(self) -> None:
        data = empty_ec2_data(
            amis=[{"ImageId": "ami-1", "CreationDate": "2026-08-01T00:00:00.000Z"}]
        )
        assert evaluate(data, [resource("ec2:image", "ami-1")]) == []


class TestPartialDataGuard:
    def test_rules_are_skipped_when_their_service_is_absent(self) -> None:
        # An errored service never reaches the region's scan data
        # (ADR-0010) — and absent data must yield no findings, or
        # cross-referencing rules would invent them (decision 16).
        assert evaluate({}, [resource("ec2:volume", "vol-1")]) == []

    def test_present_but_empty_sections_still_evaluate(self) -> None:
        # Present-and-empty is a real answer ("nothing exists"), distinct
        # from absent ("could not look").
        data = empty_ec2_data(
            addresses=[{"AllocationId": "eipalloc-1", "PublicIp": "192.0.2.10"}]
        )
        findings = evaluate(data, [resource("ec2:elastic-ip", "eipalloc-1")])
        assert len(findings) == 1
