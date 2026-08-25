"""
Signal providers: sources of judgment behind one seam.

``evaluate(scan_data, resources, region, config) -> list[Finding]`` —
pure functions over one region's raw scan sections and its flattened
Resources; no session, no AWS calls (PRODUCT.md decision 13). The
tag-drift provider joins here when it lands; two real implementations
make the seam real (engineering rule 15).
"""

from collections.abc import Mapping
from typing import Any

from aws_resource_inventory.lib.logging import get_logger
from aws_resource_inventory.lib.records import Resource
from aws_resource_inventory.waste.config import WasteConfig
from aws_resource_inventory.waste.findings import Finding
from aws_resource_inventory.waste.registry import RULES

logger = get_logger()


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
