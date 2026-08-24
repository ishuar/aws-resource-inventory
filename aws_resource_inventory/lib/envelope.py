"""
The JSON envelope — the tool's public output schema (ADR-0005).

Serialized scan output is one self-describing document: scan metadata
(who scanned what, when, with which filters), a summary, and the sorted
flat ``resources[]`` array. ``build_envelope`` is pure — fixtures in,
dict out — so the whole schema is testable without moto or a clock; the
caller supplies everything time- or environment-dependent.

``SCHEMA_VERSION`` bumps only on breaking changes (renaming or removing
a key, changing a type, meaning, or the sort order). Additive fields do
not bump it.
"""

import importlib.metadata
from collections import Counter
from dataclasses import dataclass
from typing import Any, Literal

from .records import CallerIdentity, Resource

SCHEMA_VERSION = 1
TOOL_NAME = "aws-resource-inventory"


def tool_version() -> str:
    """The installed distribution's version, or "unknown" from source.

    A source tree run without any install has no distribution metadata;
    "unknown" is honest there, and any real install (including poetry's
    editable install) resolves normally.
    """
    try:
        return importlib.metadata.version(TOOL_NAME)
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


@dataclass(frozen=True, slots=True)
class ScanFilters:
    """The filters a scan ran with, as recorded in the envelope.

    ``services`` is None on the tagging path: the Resource Groups scan
    discovers by tag across all services and never reads the service
    list, so recording one would be a lie.
    """

    services: list[str] | None
    tag_key: str | None
    tag_value: str | None
    all_services: bool


def build_envelope(
    resources: list[Resource],
    *,
    version: str,
    identity: CallerIdentity,
    regions: list[str],
    source: Literal["services", "tagging"],
    filters: ScanFilters,
    started_at: str,
    duration_seconds: float,
) -> dict[str, Any]:
    """Shape one scan's results into the schema_version-1 document.

    Resources are emitted sorted by region → type → id, so identical
    inputs always serialize identically; ``started_at`` and
    ``duration_seconds`` are the only non-deterministic fields, by
    design (e2e-diff compares ``.resources`` only for that reason).
    """
    records = sorted(
        (resource.to_record() for resource in resources),
        key=lambda record: (record["region"], record["type"], record["id"]),
    )
    # by_region is seeded from `regions` so a scanned-but-empty region
    # reports 0 rather than vanishing (ADR-0005: a partially-failed scan
    # must stay visible). by_type cannot be seeded: resource types are
    # discovered, not requested — the tagging path emits whatever AWS
    # returns — so there is no input list to seed from.
    by_region = Counter(dict.fromkeys(regions, 0))
    by_region.update(record["region"] for record in records)
    by_type = Counter(record["type"] for record in records)
    return {
        "schema_version": SCHEMA_VERSION,
        "scan": {
            "tool": {"name": TOOL_NAME, "version": version},
            "account": identity.account,
            "partition": identity.partition,
            "regions": regions,
            "source": source,
            "filters": {
                "services": filters.services,
                "tag_key": filters.tag_key,
                "tag_value": filters.tag_value,
                "all_services": filters.all_services,
            },
            "started_at": started_at,
            "duration_seconds": duration_seconds,
        },
        "summary": {
            "total": len(records),
            # by_service is deliberately absent: derivable from by_type
            # by splitting on ":".
            "by_region": dict(sorted(by_region.items())),
            "by_type": dict(sorted(by_type.items())),
        },
        "resources": records,
    }
