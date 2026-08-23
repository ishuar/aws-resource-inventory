"""
Credential seam: orchestrator.validate_aws_credentials.

STS GetCallerIdentity is the one call that yields the caller identity
(account + partition) every constructed ARN is built from, so the
validator must return it — not just a pass/fail message.
"""

from typing import Any

from botocore.exceptions import NoCredentialsError

from aws_resource_inventory.lib.records import CallerIdentity
from aws_resource_inventory.orchestrator import validate_aws_credentials


class _NoCredentialsSession:
    """A session whose client creation fails like missing credentials do."""

    def client(self, service_name: str) -> Any:
        raise NoCredentialsError()


def test_valid_credentials_return_the_caller_identity(aws_session: Any) -> None:
    is_valid, message, identity = validate_aws_credentials(aws_session)

    assert is_valid is True
    assert "credentials valid" in message
    # moto's STS stamps the fake account and the aws partition.
    assert identity == CallerIdentity(account="123456789012", partition="aws")


def test_missing_credentials_return_no_identity(aws_session: Any) -> None:
    session: Any = _NoCredentialsSession()
    is_valid, message, identity = validate_aws_credentials(session)

    assert is_valid is False
    assert "No AWS credentials found" in message
    assert identity is None
