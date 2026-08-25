"""Helpers every state-rules module shares."""

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from aws_resource_inventory.lib.logging import get_logger
from aws_resource_inventory.lib.records import Resource

logger = get_logger()

Index = Mapping[tuple[str, str], Resource]


def identified(
    index: Index, resource_type: str, resource_id: str | None
) -> Resource | None:
    """The indexed Resource, or None (logged) when the inventory has none.

    A raw item the inventory skipped as unidentifiable yields no finding
    — a finding without a real ARN would violate the record contract.
    """
    resource = index.get((resource_type, resource_id)) if resource_id else None
    if resource is None:
        logger.warning(
            "No finding for %s %s: the inventory carries no identity for it",
            resource_type,
            resource_id,
        )
    return resource


def iso(value: Any) -> Any:
    """Datetimes become ISO strings; everything else passes through.

    Evidence must be JSON-serializable — boto3 hands rules datetimes.
    """
    return value.isoformat() if isinstance(value, datetime) else value
