"""
The scan-record contract.

``Resource`` is the one definition of the flattened record every output
processor produces and every output format (table, markdown, JSON)
consumes. Producers construct it — so a malformed record fails at
construction, in the producing module, instead of at report time in a
user's terminal.

``CallerIdentity`` is the scanning caller's account and partition, read
once from STS GetCallerIdentity; every constructed ARN is built from it.

``resource_name`` is optional: some producers genuinely have no friendly
name to offer. Serialization omits it entirely when absent, and keeps
the historical key order, so JSON output is byte-identical with the
hand-built dicts this type replaces.
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
        """The AWS service prefix of resource_type ("ec2:instance" -> "ec2").

        Bare legacy types without a colon (e.g. "vpc") are their own service.
        """
        return self.resource_type.split(":", 1)[0]

    def to_record(self) -> dict[str, Any]:
        """Serialize to the legacy record dict (stable key order).

        ``arn_source`` is deliberately NOT serialized yet: the JSON
        envelope chunk emits it. Until then, serialized output changes
        only in resource_id/resource_arn values.
        """
        record: dict[str, Any] = {"region": self.region}
        if self.resource_name is not None:
            record["resource_name"] = self.resource_name
        record["resource_type"] = self.resource_type
        record["resource_id"] = self.resource_id
        record["resource_arn"] = self.resource_arn
        return record
