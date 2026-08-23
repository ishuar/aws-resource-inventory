"""
EFS Service Scanner
-------------------

Scans EFS file systems. Tag-based filtering is handled by the Resource
Groups API at the main scanner level.

Fully declarative: one paginated describe call executed by the shared
engine. The raw boto3 dicts are kept as values because the future
efs-empty waste rule needs ``SizeInBytes`` and ``NumberOfMountTargets``,
both returned by ``describe_file_systems``.
? Documentation: https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/efs.html
"""

from typing import Any

from aws_resource_inventory.lib.clients import get_scan_client
from aws_resource_inventory.lib.engine import Describe, ScanResult, scan_keyed
from aws_resource_inventory.lib.logging import get_logger
from aws_resource_inventory.lib.records import CallerIdentity, Resource

logger = get_logger()

EFS_SPECS: dict[str, Describe] = {
    "file_systems": Describe("describe_file_systems", "FileSystems"),
}


def scan_efs(session: Any, region: str) -> ScanResult:
    """Scan all EFS file systems in the region (no tag filtering)."""
    client = get_scan_client(session, "efs", region)
    return scan_keyed(client, EFS_SPECS, service="efs", region=region, max_workers=1)


def process_efs_output(
    service_data: dict[str, Any],
    region: str,
    flattened_resources: list[Resource],
    identity: CallerIdentity,
) -> None:
    """Process EFS scan results for output formatting."""
    for fs in service_data.get("file_systems", []):
        fs_id = fs.get("FileSystemId")
        fs_arn = fs.get("FileSystemArn")
        if not fs_id or not fs_arn:
            logger.warning("Skipping efs:file-system in %s: missing id or ARN", region)
            continue

        flattened_resources.append(
            Resource(
                region=region,
                resource_name=fs.get("Name"),
                resource_type="efs:file-system",
                resource_id=fs_id,
                resource_arn=fs_arn,
                arn_source="observed",
            )
        )
