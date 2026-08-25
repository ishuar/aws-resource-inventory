"""
The Finding record: one judgment about one resource.

``Finding`` pairs a scanned ``Resource`` (the identity) with the rule
that fired, an honest confidence level, the evidence, and a suggested
action. ``to_record()`` serializes to the findings document's record:
the envelope's exact identity vocabulary plus the judgment keys, so a
consumer joins findings to inventory on ``arn`` with no translation
(PRODUCT.md decision 15).

Evidence keys are AWS's own field names verbatim (``State``, never
``state``); derived values a rule computed (``days_stopped``) use
snake_case so the two are distinguishable at a glance. Values must be
JSON-serializable — rules convert datetimes before constructing the
Finding.
"""

from dataclasses import dataclass
from typing import Any, Literal

from aws_resource_inventory.lib.records import Resource

Confidence = Literal["certain", "likely", "review"]


@dataclass(frozen=True, slots=True)
class Finding:
    """One judgment: this resource looks abandoned, and here is why.

    Frozen like every domain type here, but the ``evidence`` dict makes
    instances unhashable — collect findings in lists, never sets.
    """

    resource: Resource
    rule: str
    confidence: Confidence
    evidence: dict[str, Any]
    suggested_action: Literal["delete", "snapshot-then-delete", "review"]

    def to_record(self) -> dict[str, Any]:
        """Serialize to the findings document's record (stable key order)."""
        return {
            **self.resource.to_record(),
            "rule": self.rule,
            "confidence": self.confidence,
            "evidence": self.evidence,
            "suggested_action": self.suggested_action,
        }
