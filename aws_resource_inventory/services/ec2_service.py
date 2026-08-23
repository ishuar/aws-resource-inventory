"""
EC2 Service Scanner
------------------

Scans EC2 resources: instances, volumes, security groups, AMIs, and
snapshots. Tag-based filtering is handled by the Resource Groups API at
the main scanner level.

Fully declarative: every resource type is one paginated describe call,
so the whole scan is a Describe spec executed by the shared engine.
Documentation: https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/ec2.html
"""

from typing import Any

from aws_resource_inventory.lib.clients import get_scan_client
from aws_resource_inventory.lib.engine import Describe, ScanResult, scan_keyed
from aws_resource_inventory.lib.logging import get_logger
from aws_resource_inventory.lib.records import CallerIdentity, Resource

logger = get_logger()


def _instances_from(page: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        instance
        for reservation in page["Reservations"]
        for instance in reservation["Instances"]
    ]


EC2_SPECS: dict[str, Describe] = {
    "instances": Describe(
        "describe_instances", "Reservations", flatten=_instances_from
    ),
    "volumes": Describe("describe_volumes", "Volumes"),
    "security_groups": Describe("describe_security_groups", "SecurityGroups"),
    "amis": Describe("describe_images", "Images", kwargs={"Owners": ["self"]}),
    "snapshots": Describe(
        "describe_snapshots", "Snapshots", kwargs={"OwnerIds": ["self"]}
    ),
}


def scan_ec2(session: Any, region: str) -> ScanResult:
    """Scan all EC2 resources in the region (no tag filtering)."""
    client = get_scan_client(session, "ec2", region)
    return scan_keyed(client, EC2_SPECS, service="ec2", region=region, max_workers=4)


def _skip_missing_id(resource_type: str, region: str) -> None:
    logger.warning(
        "Skipping %s in %s: the API response carries no id field", resource_type, region
    )


def process_ec2_output(
    service_data: dict[str, Any],
    region: str,
    flattened_resources: list[Resource],
    identity: CallerIdentity,
) -> None:
    """Process EC2 scan results for output formatting.

    The EC2 API returns no ARNs, so every ARN here is constructed from
    the caller identity using the documented formats (AWS Service
    Authorization Reference). Note: image and snapshot ARNs have an
    EMPTY account field by definition.
    """
    # EC2 Instances
    for instance in service_data.get("instances", []):
        instance_id = instance.get("InstanceId")
        if not instance_id:
            _skip_missing_id("ec2:instance", region)
            continue

        flattened_resources.append(
            Resource(
                region=region,
                resource_type="ec2:instance",  # Unified format: service:type
                resource_id=instance_id,
                resource_arn=f"arn:{identity.partition}:ec2:{region}:{identity.account}:instance/{instance_id}",
                arn_source="constructed",
            )
        )

    # EBS Volumes
    for volume in service_data.get("volumes", []):
        volume_id = volume.get("VolumeId")
        if not volume_id:
            _skip_missing_id("ec2:volume", region)
            continue
        volume_name = "N/A"
        # Try to get Name tag
        for tag in volume.get("Tags", []):
            if tag["Key"] == "Name":
                volume_name = tag["Value"]
                break
        if volume_name == "N/A":
            volume_name = volume_id

        flattened_resources.append(
            Resource(
                region=region,
                resource_name=volume_name,
                resource_type="ec2:volume",
                resource_id=volume_id,
                resource_arn=f"arn:{identity.partition}:ec2:{region}:{identity.account}:volume/{volume_id}",
                arn_source="constructed",
            )
        )

    # Security Groups
    for sg in service_data.get("security_groups", []):
        sg_id = sg.get("GroupId")
        if not sg_id:
            _skip_missing_id("ec2:security_group", region)
            continue
        sg_name = sg.get("GroupName", sg_id)

        flattened_resources.append(
            Resource(
                region=region,
                resource_name=sg_name,
                resource_type="ec2:security_group",
                resource_id=sg_id,
                resource_arn=f"arn:{identity.partition}:ec2:{region}:{identity.account}:security-group/{sg_id}",
                arn_source="constructed",
            )
        )

    # AMIs
    for ami in service_data.get("amis", []):
        ami_id = ami.get("ImageId")
        if not ami_id:
            _skip_missing_id("ec2:ami", region)
            continue
        ami_name = ami.get("Name", ami_id)

        flattened_resources.append(
            Resource(
                region=region,
                resource_name=ami_name,
                resource_type="ec2:ami",
                resource_id=ami_id,
                # The owner's account, because the scan asks only for
                # self-owned images (Owners: ["self"]). The empty-account
                # form in the IAM reference covers images shared from
                # another account, which this scanner never returns.
                resource_arn=f"arn:{identity.partition}:ec2:{region}:{identity.account}:image/{ami_id}",
                arn_source="constructed",
            )
        )

    # Snapshots
    for snapshot in service_data.get("snapshots", []):
        snapshot_id = snapshot.get("SnapshotId")
        if not snapshot_id:
            _skip_missing_id("ec2:snapshot", region)
            continue
        snapshot_name = snapshot.get("Description", snapshot_id)

        flattened_resources.append(
            Resource(
                region=region,
                resource_name=snapshot_name,
                resource_type="ec2:snapshot",
                resource_id=snapshot_id,
                # The owner's account, as for images above: the scan asks
                # only for self-owned snapshots (OwnerIds: ["self"]).
                resource_arn=f"arn:{identity.partition}:ec2:{region}:{identity.account}:snapshot/{snapshot_id}",
                arn_source="constructed",
            )
        )
