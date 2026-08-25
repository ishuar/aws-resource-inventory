"""
EC2 state rules: volumes, Elastic IPs, instances, snapshots, images.

Each rule reads the raw describe fields the EC2 scanner already fetched
and yields findings with those fields as evidence, verbatim. Identity
comes from the index; a raw item the inventory skipped as
unidentifiable yields no finding (logged by ``identified``) — a finding
without a real ARN would violate the record contract.
"""

import re
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from aws_resource_inventory.waste.config import WasteConfig
from aws_resource_inventory.waste.findings import Finding
from aws_resource_inventory.waste.state_rules.common import Index, identified, iso

# The timestamp AWS embeds in StateTransitionReason, e.g.
# "User initiated (2024-01-03 12:34:56 GMT)". Cleared by AWS on some
# paths, so a stopped instance without it stays unclaimed.
_STOPPED_AT = re.compile(r"\((\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) GMT\)")


def ebs_unattached(
    scan_data: Mapping[str, Any], index: Index, config: WasteConfig
) -> list[Finding]:
    """A volume in state "available" is attached to nothing — proof of non-use."""
    findings = []
    for volume in scan_data["ec2"]["volumes"]:
        if volume.get("State") != "available":
            continue
        resource = identified(index, "ec2:volume", volume.get("VolumeId"))
        if resource is None:
            continue
        findings.append(
            Finding(
                resource=resource,
                rule="ebs-unattached",
                confidence="certain",
                evidence={
                    "State": volume["State"],
                    "CreateTime": iso(volume.get("CreateTime")),
                    "Size": volume.get("Size"),
                },
                suggested_action="snapshot-then-delete",
            )
        )
    return findings


def eip_unassociated(
    scan_data: Mapping[str, Any], index: Index, config: WasteConfig
) -> list[Finding]:
    """An address without an AssociationId points at nothing — and still bills."""
    findings = []
    for address in scan_data["ec2"]["addresses"]:
        if address.get("AssociationId"):
            continue
        resource = identified(index, "ec2:elastic-ip", address.get("AllocationId"))
        if resource is None:
            continue
        findings.append(
            Finding(
                resource=resource,
                rule="eip-unassociated",
                confidence="certain",
                # The explicit null states the trigger: no association.
                evidence={
                    "AssociationId": address.get("AssociationId"),
                    "PublicIp": address.get("PublicIp"),
                    "Domain": address.get("Domain"),
                },
                suggested_action="delete",
            )
        )
    return findings


def ec2_long_stopped(
    scan_data: Mapping[str, Any], index: Index, config: WasteConfig
) -> list[Finding]:
    """Stopped past the threshold. Without a parseable stop time, no claim."""
    findings = []
    for instance in scan_data["ec2"]["instances"]:
        if instance.get("State", {}).get("Name") != "stopped":
            continue
        reason = instance.get("StateTransitionReason") or ""
        match = _STOPPED_AT.search(reason)
        if not match:
            continue
        stopped_at = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=timezone.utc
        )
        days_stopped = (config.now - stopped_at).days
        if days_stopped <= config.stopped_days:
            continue
        resource = identified(index, "ec2:instance", instance.get("InstanceId"))
        if resource is None:
            continue
        findings.append(
            Finding(
                resource=resource,
                rule="ec2-long-stopped",
                confidence="likely",
                evidence={
                    "State": "stopped",
                    "StateTransitionReason": reason,
                    "days_stopped": days_stopped,
                },
                suggested_action="review",
            )
        )
    return findings


def snapshot_orphaned(
    scan_data: Mapping[str, Any], index: Index, config: WasteConfig
) -> list[Finding]:
    """The source volume is gone and no registered image uses the snapshot.

    Copied snapshots carry AWS's placeholder volume id (vol-ffffffff),
    which never matches a real volume — they are orphans unless an image
    references them, same as any other snapshot.
    """
    ec2 = scan_data["ec2"]
    volume_ids = {volume.get("VolumeId") for volume in ec2["volumes"]}
    image_snapshot_ids = {
        mapping.get("Ebs", {}).get("SnapshotId")
        for image in ec2["amis"]
        for mapping in image.get("BlockDeviceMappings", [])
    }
    findings = []
    for snapshot in ec2["snapshots"]:
        snapshot_id = snapshot.get("SnapshotId")
        if snapshot.get("VolumeId") in volume_ids:
            continue
        if snapshot_id in image_snapshot_ids:
            continue
        resource = identified(index, "ec2:snapshot", snapshot_id)
        if resource is None:
            continue
        findings.append(
            Finding(
                resource=resource,
                rule="snapshot-orphaned",
                confidence="likely",
                evidence={
                    "VolumeId": snapshot.get("VolumeId"),
                    "StartTime": iso(snapshot.get("StartTime")),
                },
                suggested_action="delete",
            )
        )
    return findings


def ami_unused(
    scan_data: Mapping[str, Any], index: Index, config: WasteConfig
) -> list[Finding]:
    """No instance runs the image and it is older than the threshold."""
    ec2 = scan_data["ec2"]
    referenced = {instance.get("ImageId") for instance in ec2["instances"]}
    findings = []
    for image in ec2["amis"]:
        image_id = image.get("ImageId")
        if image_id in referenced:
            continue
        created = _parse_creation_date(image.get("CreationDate"))
        if created is None:
            continue
        if (config.now - created).days < config.unused_image_days:
            continue
        resource = identified(index, "ec2:image", image_id)
        if resource is None:
            continue
        findings.append(
            Finding(
                resource=resource,
                rule="ami-unused",
                confidence="likely",
                evidence={"CreationDate": image.get("CreationDate")},
                suggested_action="delete",
            )
        )
    return findings


def _parse_creation_date(value: str | None) -> datetime | None:
    """AMI CreationDate ("2024-01-03T00:00:00.000Z") as an aware datetime."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
