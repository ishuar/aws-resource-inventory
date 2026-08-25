"""EFS state rules: file systems."""

from collections.abc import Mapping
from typing import Any

from aws_resource_inventory.waste.config import WasteConfig
from aws_resource_inventory.waste.findings import Finding
from aws_resource_inventory.waste.state_rules.common import Index, identified

# An empty file system still reports ~6 KiB of metadata, never zero.
EMPTY_FILE_SYSTEM_BYTES = 6144


def efs_empty(
    scan_data: Mapping[str, Any], index: Index, config: WasteConfig
) -> list[Finding]:
    """Metadata-only contents, or no mount target to reach the data by.

    Only file systems in LifeCycleState "available" are judged — one
    still creating or already deleting is in transition, not abandoned.
    """
    findings = []
    for file_system in scan_data["efs"]["file_systems"]:
        if file_system.get("LifeCycleState") != "available":
            continue
        size = file_system.get("SizeInBytes", {}).get("Value", 0)
        mount_targets = file_system.get("NumberOfMountTargets", 0)
        if size > EMPTY_FILE_SYSTEM_BYTES and mount_targets > 0:
            continue
        resource = identified(index, "efs:file-system", file_system.get("FileSystemId"))
        if resource is None:
            continue
        findings.append(
            Finding(
                resource=resource,
                rule="efs-empty",
                confidence="likely",
                evidence={
                    "SizeInBytes": {"Value": size},
                    "NumberOfMountTargets": mount_targets,
                },
                suggested_action="review",
            )
        )
    return findings
