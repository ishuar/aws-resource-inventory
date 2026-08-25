"""
Signal providers: sources of judgment behind one seam.

``evaluate(scan_data, resources, region, config) -> list[Finding]`` —
pure functions over one region's raw scan sections and its flattened
Resources; no session, no AWS calls (PRODUCT.md decision 13). The
tag-drift provider joins here when it lands; two real implementations
make the seam real (engineering rule 15).
"""

from collections.abc import Callable, Mapping
from typing import Any

from aws_resource_inventory.lib.logging import get_logger
from aws_resource_inventory.lib.records import Resource
from aws_resource_inventory.waste.config import WasteConfig
from aws_resource_inventory.waste.findings import Finding
from aws_resource_inventory.waste.registry import RULES

logger = get_logger()


# Types the diff must never judge: the Resource Groups Tagging API does
# not cover Auto Scaling groups, and launch configurations cannot be
# tagged at all — absence from the tagged set proves nothing for them.
TAGGING_BLIND_TYPES = frozenset(
    {"autoscaling:autoScalingGroup", "autoscaling:launchConfiguration"}
)


def evaluate_state_rules(
    scan_data: Mapping[str, Any],
    resources: list[Resource],
    region: str,
    config: WasteConfig,
) -> list[Finding]:
    """Run every registered state rule over one region's scan data.

    A rule is skipped when any service it reads is absent from the
    region's data: an errored service never reaches the results
    (ADR-0010), and a cross-referencing rule fed partial data would
    invent findings (decision 16). The gap is already visible to the
    consumer as ``scan.errors``.
    """
    index = {(r.resource_type, r.resource_id): r for r in resources}
    findings: list[Finding] = []
    for name, registration in RULES.items():
        missing = [s for s in registration.services if s not in scan_data]
        if missing:
            logger.info(
                "Skipping rule %s in %s: no scan data for %s",
                name,
                region,
                ", ".join(missing),
            )
            continue
        findings.extend(registration.evaluate(scan_data, index, config))
    return findings


def evaluate_tag_drift(
    scan_data: Mapping[str, Any],
    resources: list[Resource],
    region: str,
    config: WasteConfig,
) -> list[Finding]:
    """inventory minus tagged-set: resources not carrying the managed tag.

    The left side is the per-service inventory (describe calls see
    everything, including never-tagged resources); the right side is
    the ARNs the Tagging API reported for the managed tag, fetched by
    the orchestration into ``config.tagged_arns``. Drift is review-grade
    by default — untagged is not proof of waste; ``--trust-tags``
    upgrades it to likely as the user's own declaration.
    """
    if not config.managed_tag_key:
        raise ValueError(
            "tag-drift needs config.managed_tag_key — the provider runs "
            "only when --managed-tag is given"
        )
    managed_tag = (
        f"{config.managed_tag_key}={config.managed_tag_value}"
        if config.managed_tag_value
        else config.managed_tag_key
    )
    findings = []
    for resource in resources:
        if resource.resource_type in TAGGING_BLIND_TYPES:
            continue
        if resource.resource_arn in config.tagged_arns:
            continue
        findings.append(
            Finding(
                resource=resource,
                rule="tag-drift",
                confidence="likely" if config.trust_tags else "review",
                evidence={"managed_tag": managed_tag},
                suggested_action="review",
            )
        )
    return findings


# The provider seam's registry: both v1 providers behind one signature
# (PRODUCT.md §3). tag-drift is opt-in — the CLI invokes it only when
# --managed-tag is given.
ProviderFunc = Callable[
    [Mapping[str, Any], list[Resource], str, WasteConfig], list[Finding]
]

PROVIDERS: dict[str, ProviderFunc] = {
    "state-rules": evaluate_state_rules,
    "tag-drift": evaluate_tag_drift,
}
