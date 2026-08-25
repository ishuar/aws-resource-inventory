"""
The findings document — waste's serialized output (ADR-0011).

One self-describing JSON document, sibling of the inventory envelope:
the same ``scan`` block (including ``errors`` — the document states its
own completeness, decision 16), a ``waste`` block recording the
judgment inputs, a summary by confidence and rule, and the sorted
``findings[]``. ``build_findings_document`` is pure — findings in,
dict out — the CLI owns the clock and the fetches.

``FINDINGS_SCHEMA_VERSION`` is this document's own counter (consumers
tell the two documents apart by ``findings`` vs ``resources``); it
bumps only on breaking changes, additive fields don't bump it.
"""

from collections import Counter
from typing import Any

from aws_resource_inventory.lib.envelope import (
    CallerIdentity,
    ScanError,
    ScanFilters,
    scan_block,
)
from aws_resource_inventory.waste.findings import Finding
from aws_resource_inventory.waste.registry import RULES

FINDINGS_SCHEMA_VERSION = 1


def build_findings_document(
    findings: list[Finding],
    *,
    version: str,
    identity: CallerIdentity,
    regions: list[str],
    services: list[str],
    managed_tag: str | None,
    trust_tags: bool,
    stopped_days: int,
    unused_image_days: int,
    started_at: str,
    duration_seconds: float,
    errors: list[ScanError],
) -> dict[str, Any]:
    """Shape one waste run's findings into the schema_version-1 document.

    Findings are sorted region → type → id (→ rule, since one resource
    can carry several findings) — the machine sort; the terminal table
    orders by confidence for humans. Summary keys are stable: all three
    confidence levels always appear, and every registered rule reports
    (0 when silent) so consumers diff runs on fixed keys — ``tag-drift``
    appears only when the opt-in provider actually ran.
    """
    records = sorted(
        (finding.to_record() for finding in findings),
        key=lambda record: (
            record["region"],
            record["type"],
            record["id"],
            record["rule"],
        ),
    )
    by_confidence = Counter({"certain": 0, "likely": 0, "review": 0})
    by_confidence.update(record["confidence"] for record in records)
    rule_names = list(RULES) + (["tag-drift"] if managed_tag else [])
    by_rule = Counter(dict.fromkeys(rule_names, 0))
    by_rule.update(record["rule"] for record in records)
    return {
        "schema_version": FINDINGS_SCHEMA_VERSION,
        "scan": scan_block(
            version=version,
            identity=identity,
            regions=regions,
            source="services",
            filters=ScanFilters(
                services=services, tag_key=None, tag_value=None, all_services=False
            ),
            started_at=started_at,
            duration_seconds=duration_seconds,
            errors=errors,
        ),
        "waste": {
            "managed_tag": managed_tag,
            "trust_tags": trust_tags,
            "thresholds": {
                "stopped_days": stopped_days,
                "unused_image_days": unused_image_days,
            },
        },
        "summary": {
            "total": len(records),
            "by_confidence": dict(by_confidence),
            "by_rule": dict(sorted(by_rule.items())),
        },
        "findings": records,
    }
