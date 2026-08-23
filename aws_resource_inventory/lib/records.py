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
copies of the id. Serialization always emits the key (JSON ``null`` when
AWS supplies no name), so every record has the same keys and the data
loads into tabular tools without ragged rows.
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
        """Serialize to the legacy record dict (stable key order).

        ``arn_source`` is deliberately NOT serialized yet: the JSON
        envelope chunk emits it. Until then, serialized output changes
        only in resource_id/resource_arn values.
        """
        return {
            "region": self.region,
            "resource_name": self.resource_name,
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "resource_arn": self.resource_arn,
        }
