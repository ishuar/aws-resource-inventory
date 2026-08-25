"""ELB state rules: target groups."""

from collections.abc import Mapping
from typing import Any

from aws_resource_inventory.lib.arn import extract_resource_id_from_arn
from aws_resource_inventory.waste.config import WasteConfig
from aws_resource_inventory.waste.findings import Finding
from aws_resource_inventory.waste.state_rules.common import Index, identified


def elb_no_targets(
    scan_data: Mapping[str, Any], index: Index, config: WasteConfig
) -> list[Finding]:
    """A target group with zero registered targets serves nothing.

    Likely, not certain: a freshly provisioned group legitimately has
    none (PRODUCT.md decision 7). A group *without* the
    TargetHealthDescriptions key (a cached scan predating health
    attachment) is not claimed — missing data must never read as "zero
    targets" (decision 16).
    """
    findings = []
    for target_group in scan_data["elb"]["target_groups"]:
        if "TargetHealthDescriptions" not in target_group:
            continue
        if target_group["TargetHealthDescriptions"]:
            continue
        tg_arn = target_group.get("TargetGroupArn")
        tg_id = (
            extract_resource_id_from_arn(tg_arn, "elasticloadbalancing:targetgroup")
            if tg_arn
            else None
        )
        resource = identified(index, "elb:targetgroup", tg_id)
        if resource is None:
            continue
        findings.append(
            Finding(
                resource=resource,
                rule="elb-no-targets",
                confidence="likely",
                evidence={
                    "TargetHealthDescriptions": [],
                    "TargetType": target_group.get("TargetType"),
                },
                suggested_action="review",
            )
        )
    return findings
