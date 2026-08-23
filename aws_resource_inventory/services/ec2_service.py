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
from aws_resource_inventory.lib.records import Resource


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


def process_ec2_output(
    service_data: dict[str, Any], region: str, flattened_resources: list[Resource]
) -> None:
    """Process EC2 scan results for output formatting."""
    # EC2 Instances
    for instance in service_data.get("instances", []):
        instance_id = instance.get("InstanceId", "N/A")
        instance_name = "N/A"
        # Try to get Name tag
        for tag in instance.get("Tags", []):
            if tag["Key"] == "Name":
                instance_name = tag["Value"]
                break
        if instance_name == "N/A":
            instance_name = instance_id

        flattened_resources.append(
            Resource(
                region=region,
                resource_name=instance_name,
                resource_type="ec2:instance",  # Unified format: service:type
                resource_id=instance_id,
                resource_arn="N/A",  # Instances don't have ARNs in AWS API
            )
        )

    # EBS Volumes
    for volume in service_data.get("volumes", []):
        volume_id = volume.get("VolumeId", "N/A")
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
                resource_arn="N/A",  # Volumes don't have ARNs in AWS API
            )
        )

    # Security Groups
    for sg in service_data.get("security_groups", []):
        sg_id = sg.get("GroupId", "N/A")
        sg_name = sg.get("GroupName", sg_id)

        flattened_resources.append(
            Resource(
                region=region,
                resource_name=sg_name,
                resource_type="ec2:security_group",
                resource_id=sg_id,
                resource_arn="N/A",  # Security groups don't have ARNs in AWS API
            )
        )

    # AMIs
    for ami in service_data.get("amis", []):
        ami_id = ami.get("ImageId", "N/A")
        ami_name = ami.get("Name", ami_id)

        flattened_resources.append(
            Resource(
                region=region,
                resource_name=ami_name,
                resource_type="ec2:ami",
                resource_id=ami_id,
                resource_arn="N/A",  # AMIs don't have ARNs in AWS API
            )
        )

    # Snapshots
    for snapshot in service_data.get("snapshots", []):
        snapshot_id = snapshot.get("SnapshotId", "N/A")
        snapshot_name = snapshot.get("Description", snapshot_id)

        flattened_resources.append(
            Resource(
                region=region,
                resource_name=snapshot_name,
                resource_type="ec2:snapshot",
                resource_id=snapshot_id,
                resource_arn="N/A",  # Snapshots don't have ARNs in AWS API
            )
        )
