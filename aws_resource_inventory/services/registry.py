"""
Service Registry
----------------

Single source of truth for the AWS services this tool can scan.

Each supported service registers exactly one ``ServiceRegistration`` pairing
its scanner with its output processor. The scan dispatcher
(``aws_resource_inventory.lib.scan``) and the output dispatcher
(``aws_resource_inventory.lib.outputs``) both look services up here, so adding a service
means writing its module and adding one entry to ``SERVICES``.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from aws_resource_inventory.lib.records import CallerIdentity, Resource
from aws_resource_inventory.services.autoscaling_service import (
    process_autoscaling_output,
    scan_autoscaling,
)
from aws_resource_inventory.services.ec2_service import process_ec2_output, scan_ec2
from aws_resource_inventory.services.ecs_service import process_ecs_output, scan_ecs
from aws_resource_inventory.services.efs_service import process_efs_output, scan_efs
from aws_resource_inventory.services.elb_service import process_elb_output, scan_elb
from aws_resource_inventory.services.rds_service import process_rds_output, scan_rds
from aws_resource_inventory.services.s3_service import process_s3_output, scan_s3
from aws_resource_inventory.services.vpc_service import process_vpc_output, scan_vpc

# (session, region[, tag_key, tag_value]) -> {resource_key: [raw boto3 dicts]}
ScanFunc = Callable[..., dict[str, Any]]
# (service_data, region, flattened_resources, identity) -> None (appends
# in place; identity supplies account + partition for constructed ARNs)
ProcessOutputFunc = Callable[
    [dict[str, Any], str, list[Resource], CallerIdentity], None
]


@dataclass(frozen=True)
class ServiceRegistration:
    """One scannable AWS service: how to scan it and how to flatten its output."""

    scan: ScanFunc
    process_output: ProcessOutputFunc
    # Auto Scaling keeps client-side tag filtering because the Resource Groups
    # Tagging API does not cover ASGs; the other scanners take no tag args.
    accepts_tags: bool = False


SERVICES: dict[str, ServiceRegistration] = {
    "ec2": ServiceRegistration(scan_ec2, process_ec2_output),
    "s3": ServiceRegistration(scan_s3, process_s3_output),
    "ecs": ServiceRegistration(scan_ecs, process_ecs_output),
    "efs": ServiceRegistration(scan_efs, process_efs_output),
    "elb": ServiceRegistration(scan_elb, process_elb_output),
    "vpc": ServiceRegistration(scan_vpc, process_vpc_output),
    "rds": ServiceRegistration(scan_rds, process_rds_output),
    "autoscaling": ServiceRegistration(
        scan_autoscaling, process_autoscaling_output, accepts_tags=True
    ),
}

SUPPORTED_SERVICES: list[str] = list(SERVICES)
