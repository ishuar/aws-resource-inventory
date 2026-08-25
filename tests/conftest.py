"""
Shared fixtures for the aws-resource-inventory test suite.

Every test here runs without real AWS credentials:
- fake credential env vars are forced for the whole session
- the pickle cache is redirected to a per-test tmp directory
- moto provides the fake AWS backend for functional scanner tests
"""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import boto3
import pytest
from moto import mock_aws

REGION = "eu-central-1"


@pytest.fixture(autouse=True)
def _fake_aws_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Guarantee no test can ever touch a real AWS account."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    monkeypatch.delenv("AWS_PROFILE", raising=False)


@pytest.fixture(autouse=True)
def isolated_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point the scan-result cache at a per-test directory (never /tmp)."""
    import aws_resource_inventory.lib.cache as cache_module

    cache_dir = tmp_path / "aws-resource-inventory"
    monkeypatch.setattr(cache_module, "CACHE_DIR", cache_dir)
    return cache_dir


@pytest.fixture()
def aws_session() -> Iterator[Any]:
    """A boto3 session backed entirely by moto's fake AWS."""
    with mock_aws():
        yield boto3.Session(region_name=REGION)
