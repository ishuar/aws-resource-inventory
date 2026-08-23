"""
S3 Service Scanner
-----------------

Scans S3 buckets in the target region, with per-bucket tag enrichment.
Tag-based filtering is handled by the Resource Groups API at the main
scanner level.
? Documentation: https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/s3.html
"""

from functools import partial
from typing import Any

from botocore.exceptions import ClientError

from aws_resource_inventory.lib.clients import get_scan_client
from aws_resource_inventory.lib.engine import (
    ScanResult,
    collect_pages,
    finish,
    map_parallel,
    run_parallel,
)
from aws_resource_inventory.lib.logging import get_logger
from aws_resource_inventory.lib.records import CallerIdentity, Resource

logger = get_logger()

# Per-bucket calls (location + tags) fan out on threads.
S3_BUCKET_WORKERS = 6


def _bucket_in_region(
    s3_client: Any, region: str, bucket: dict[str, Any]
) -> dict[str, Any] | None:
    """Keep a bucket only if it lives in the target region; attach its tags."""
    location = s3_client.get_bucket_location(Bucket=bucket["Name"]).get(
        "LocationConstraint"
    )
    # AWS reports us-east-1 as a null LocationConstraint.
    if (location or "us-east-1") != region:
        return None

    try:
        bucket["tags"] = s3_client.get_bucket_tagging(Bucket=bucket["Name"]).get(
            "TagSet", []
        )
    except ClientError as e:
        if e.response["Error"]["Code"] != "NoSuchTagSet":
            logger.warning("Could not get tags for bucket %s: %s", bucket["Name"], e)
        bucket["tags"] = []
    return bucket


def scan_s3(session: Any, region: str) -> ScanResult:
    """Scan all S3 buckets in the region (no tag filtering)."""
    s3_client = get_scan_client(session, "s3", region)

    def buckets_in_region() -> list[dict[str, Any]]:
        buckets = collect_pages(s3_client, "list_buckets", "Buckets")
        return map_parallel(
            partial(_bucket_in_region, s3_client, region),
            buckets,
            max_workers=S3_BUCKET_WORKERS,
        )

    result = run_parallel(
        {"buckets": buckets_in_region}, service="s3", region=region, max_workers=1
    )
    return finish("s3", region, result)


def process_s3_output(
    service_data: dict[str, Any],
    region: str,
    flattened_resources: list[Resource],
    identity: CallerIdentity,
) -> None:
    """Process S3 scan results for output formatting.

    ListBuckets returns no ARN; the bucket ARN is constructed from the
    documented partition-only format (arn:<partition>:s3:::<name>).
    """
    for bucket in service_data.get("buckets", []):
        bucket_name = bucket.get("Name")
        if not bucket_name:
            logger.warning(
                "Skipping s3:bucket in %s: the API response carries no name", region
            )
            continue

        flattened_resources.append(
            Resource(
                # The bucket name IS the id — no separate name exists,
                # so resource_name stays None.
                region=region,
                resource_type="s3:bucket",
                resource_id=bucket_name,
                resource_arn=f"arn:{identity.partition}:s3:::{bucket_name}",
                arn_source="constructed",
            )
        )
