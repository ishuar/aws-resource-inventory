"""
The scan-record contract.

``Resource`` is the one definition of the flattened record every output
processor produces and every output format (table, markdown, JSON)
consumes. Producers construct it — so a malformed record fails at
construction, in the producing module, instead of at report time in a
user's terminal.

``CallerIdentity`` is the scanning caller's account and partition, read
once from STS GetCallerIdentity; every constructed ARN is built from it.

``resource_name`` is a name AWS itself supplies — a Name/name attribute
or the ``Name`` tag — or ``None``: names are never synthesized and never
copies of the id. ``name_from_tags`` is the one reader of the ``Name``
tag, shared by the per-service scanners and the tag scan, so the same
``Name`` tag yields the same name whichever path found the resource. A
name taken from a name *attribute* is service-path only: the Tagging API
returns an ARN and tags, never the attribute (ADR-0005 Consequences).
Serialization always emits the key (JSON ``null`` when AWS supplies no name), so every record has
the same keys and the data loads into tabular tools without ragged rows.
"""

from dataclasses import dataclass
from typing import Any, Literal


@dataclass(frozen=True, slots=True)
class CallerIdentity:
    """The scanning caller's AWS account id and partition."""

    account: str
    partition: str

    @classmethod
    def from_caller_arn(cls, account: str, caller_arn: str) -> "CallerIdentity":
        """Build from STS GetCallerIdentity's Account and Arn fields.

        The partition is the 2nd segment of the caller's own ARN — never
        hardcoded, so GovCloud (aws-us-gov) and China (aws-cn) callers
        get constructed ARNs in their own partition.
        """
        parts = caller_arn.split(":")
        if len(parts) < 2 or not parts[1]:
            raise ValueError(f"No partition in caller ARN: {caller_arn!r}")
        return cls(account=account, partition=parts[1])


@dataclass(frozen=True, slots=True)
class Resource:
    """One discovered AWS resource in the unified output shape."""

    region: str
    resource_type: str  # unified "service:type" format, e.g. "ec2:instance"
    resource_id: str
    resource_arn: str
    # "observed": the ARN came from an AWS API response. "constructed":
    # built from the caller identity + the documented per-type format.
    # No default: every construction site must decide which one it is.
    arn_source: Literal["observed", "constructed"]
    resource_name: str | None = None

    @property
    def service(self) -> str:
        """The left half of resource_type ("ec2:instance" -> "ec2").

        A ``SERVICES`` key on ``source: "services"``, so it round-trips
        into ``scan --service``. On ``source: "tagging"`` it is the ARN's
        own service namespace, which coincides with a ``--service`` key
        for some services (``ec2``) and not others
        (``elasticloadbalancing``, ``lambda``) — do not rely on it there.
        ADR-0005 Consequences.
        """
        return self.resource_type.split(":", 1)[0]

    def to_record(self) -> dict[str, Any]:
        """Serialize to the envelope's bare-key record (stable key order).

        The serialized keys are the public schema's vocabulary
        (region/type/id/name/arn/arn_source, ADR-0005); the dataclass
        attributes keep their resource_-prefixed names — they are the
        internal API and only serialization renames.
        """
        return {
            "region": self.region,
            "type": self.resource_type,
            "id": self.resource_id,
            "name": self.resource_name,
            "arn": self.resource_arn,
            "arn_source": self.arn_source,
        }


def name_from_tags(tags: Any, resource_id: str) -> str | None:
    """The AWS-supplied ``Name`` tag, or ``None`` — names are never invented.

    Every producer that has tags to read reads them here, so the same
    ``Name`` tag yields the same name from the per-service scan and from
    the tag scan. A name that comes from a name *attribute* is
    service-path only — the Tagging API never returns the attribute.

    Two AWS quirks are absorbed: tag lists are ``Key``/``Value``
    everywhere except ECS, which uses lowercase ``key``/``value``; and
    RDS calls the field ``TagList`` rather than ``Tags``, so callers pass
    the list, not the field name. A ``Name`` tag whose value merely
    repeats ``resource_id`` is not a name — common on auto scaling groups
    and RDS instances, whose tag mirrors the identifier — and yields
    ``None``, keeping "never a copy of the id" true on the tag path too.
    """
    for tag in tags or []:
        if tag.get("Key", tag.get("key")) != "Name":
            continue
        name = str(tag.get("Value", tag.get("value")))
        return None if name == resource_id else name
    return None
