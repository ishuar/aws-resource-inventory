"""RDS state rules: database instances."""

from collections.abc import Mapping
from typing import Any

from aws_resource_inventory.waste.config import WasteConfig
from aws_resource_inventory.waste.findings import Finding
from aws_resource_inventory.waste.state_rules.common import Index, identified


def rds_stopped(
    scan_data: Mapping[str, Any], index: Index, config: WasteConfig
) -> list[Finding]:
    """A stopped DB still bills its storage — and AWS restarts it after 7 days."""
    findings = []
    for db in scan_data["rds"]["db_instances"]:
        if db.get("DBInstanceStatus") != "stopped":
            continue
        resource = identified(index, "rds:db", db.get("DBInstanceIdentifier"))
        if resource is None:
            continue
        findings.append(
            Finding(
                resource=resource,
                rule="rds-stopped",
                confidence="likely",
                evidence={
                    "DBInstanceStatus": db["DBInstanceStatus"],
                    "Engine": db.get("Engine"),
                    "AllocatedStorage": db.get("AllocatedStorage"),
                },
                suggested_action="review",
            )
        )
    return findings
