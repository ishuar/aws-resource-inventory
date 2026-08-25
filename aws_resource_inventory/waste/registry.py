"""
Rule Registry
-------------

Single source of truth for the waste rules, mirroring
``services/registry.py``: adding a rule means writing its function in
the service's ``state_rules`` module and adding one ``RULES`` entry.
Rule names are public output language (they appear on every finding),
kebab-case, named after what they detect — pinned by tests the way
resource types are.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from aws_resource_inventory.lib.records import Resource
from aws_resource_inventory.waste.config import WasteConfig
from aws_resource_inventory.waste.findings import Finding
from aws_resource_inventory.waste.state_rules import (
    ec2_rules,
    ecs_rules,
    efs_rules,
    elb_rules,
    rds_rules,
)

# (one region's scan data, (type, id) -> Resource index, config) -> findings
RuleFunc = Callable[
    [Mapping[str, Any], Mapping[tuple[str, str], Resource], WasteConfig],
    list[Finding],
]


@dataclass(frozen=True)
class RuleRegistration:
    """One waste rule: what it reads and how it judges.

    ``services`` names the scanner registry keys whose data the rule
    reads. All of them must be present in a region's scan data for the
    rule to run there — an errored service is absent (ADR-0010), and a
    rule fed partial data would invent findings (PRODUCT.md decision
    16), so the provider skips it instead.
    """

    services: tuple[str, ...]
    evaluate: RuleFunc


RULES: dict[str, RuleRegistration] = {
    "ebs-unattached": RuleRegistration(("ec2",), ec2_rules.ebs_unattached),
    "eip-unassociated": RuleRegistration(("ec2",), ec2_rules.eip_unassociated),
    "ec2-long-stopped": RuleRegistration(("ec2",), ec2_rules.ec2_long_stopped),
    "snapshot-orphaned": RuleRegistration(("ec2",), ec2_rules.snapshot_orphaned),
    "ami-unused": RuleRegistration(("ec2",), ec2_rules.ami_unused),
    "elb-no-targets": RuleRegistration(("elb",), elb_rules.elb_no_targets),
    "rds-stopped": RuleRegistration(("rds",), rds_rules.rds_stopped),
    "efs-empty": RuleRegistration(("efs",), efs_rules.efs_empty),
    # The EC2-backed check cross-references capacity providers with
    # their Auto Scaling groups, so both services must have scanned.
    "ecs-cluster-idle": RuleRegistration(
        ("ecs", "autoscaling"), ecs_rules.ecs_cluster_idle
    ),
    "ecs-service-zero-tasks": RuleRegistration(
        ("ecs",), ecs_rules.ecs_service_zero_tasks
    ),
}
