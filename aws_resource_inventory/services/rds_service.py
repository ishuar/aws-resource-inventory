"""
RDS Service Scanner
-------------------

Scans RDS resources: DB instances, DB clusters, and DB snapshots.
Tag-based filtering is handled by the Resource Groups API at the main
scanner level.

Fully declarative: every resource type is one paginated describe call,
so the whole scan is a Describe spec executed by the shared engine.
Raw boto3 dicts are kept as values — the future waste rules (e.g.
rds-stopped) read DBInstanceStatus and the storage fields off them.
Documentation: https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/rds.html
"""

from typing import Any

from aws_resource_inventory.lib.clients import get_scan_client
from aws_resource_inventory.lib.engine import Describe, ScanResult, scan_keyed
from aws_resource_inventory.lib.logging import get_logger
from aws_resource_inventory.lib.records import CallerIdentity, Resource

logger = get_logger()

RDS_SPECS: dict[str, Describe] = {
    "db_instances": Describe("describe_db_instances", "DBInstances"),
    "db_clusters": Describe("describe_db_clusters", "DBClusters"),
    "db_snapshots": Describe("describe_db_snapshots", "DBSnapshots"),
    "db_cluster_snapshots": Describe(
        "describe_db_cluster_snapshots", "DBClusterSnapshots"
    ),
}


def scan_rds(session: Any, region: str) -> ScanResult:
    """Scan all RDS resources in the region (no tag filtering)."""
    client = get_scan_client(session, "rds", region)
    return scan_keyed(client, RDS_SPECS, service="rds", region=region, max_workers=4)


# Every RDS record follows the same pattern: the identifier field is the
# id (RDS has no separate name, so resource_name stays None) and the API
# returns the ARN directly.
_RDS_RECORD_FIELDS: list[tuple[str, str, str, str]] = [
    ("db_instances", "rds:db", "DBInstanceIdentifier", "DBInstanceArn"),
    ("db_clusters", "rds:cluster", "DBClusterIdentifier", "DBClusterArn"),
    (
        "db_cluster_snapshots",
        "rds:cluster-snapshot",
        "DBClusterSnapshotIdentifier",
        "DBClusterSnapshotArn",
    ),
    ("db_snapshots", "rds:snapshot", "DBSnapshotIdentifier", "DBSnapshotArn"),
]


def process_rds_output(
    service_data: dict[str, Any],
    region: str,
    flattened_resources: list[Resource],
    identity: CallerIdentity,
) -> None:
    """Process RDS scan results for output formatting."""
    for result_key, resource_type, id_field, arn_field in _RDS_RECORD_FIELDS:
        for raw in service_data.get(result_key, []):
            resource_id = raw.get(id_field)
            resource_arn = raw.get(arn_field)
            if not resource_id or not resource_arn:
                logger.warning(
                    "Skipping %s in %s: missing id or ARN", resource_type, region
                )
                continue

            flattened_resources.append(
                Resource(
                    region=region,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    resource_arn=resource_arn,
                    arn_source="observed",
                )
            )
