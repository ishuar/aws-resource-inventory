"""
Client configuration seam: aws_resource_inventory.lib.clients

Every scanning client must be built with the same botocore settings:
a connection pool sized for the region x service x worker fan-out, and
botocore's adaptive retry mode as the single owner of transient-error
retries (replacing the hand-rolled retry_with_backoff wrapper).
"""

from typing import Any

import boto3

from aws_resource_inventory.lib.clients import get_scan_client, scan_client_config

REGION = "eu-central-1"


def test_scan_client_config_values() -> None:
    config = scan_client_config()

    assert config.max_pool_connections == 50
    assert config.retries == {"max_attempts": 5, "mode": "adaptive"}
    assert config.read_timeout == 60
    assert config.connect_timeout == 10
    # CloudTrail-identifiable user agent; already carries the upcoming
    # aws-resource-inventory name.
    assert config.user_agent_extra == "aws-resource-inventory"


def test_building_a_client_does_not_rewrite_the_next_config() -> None:
    """botocore normalizes a config's retries dict IN PLACE while it
    builds a client (max_attempts becomes total_max_attempts), so one
    shared instance would be rewritten by whichever client is built
    first."""
    get_scan_client(boto3.Session(region_name=REGION), "ec2", REGION)

    assert scan_client_config().retries == {"max_attempts": 5, "mode": "adaptive"}


def test_get_scan_client_applies_the_scan_config() -> None:
    session = boto3.Session(region_name=REGION)

    client = get_scan_client(session, "ec2", REGION)

    assert client.meta.region_name == REGION
    assert client.meta.config.max_pool_connections == 50
    # botocore normalizes max_attempts=5 to total_max_attempts=6
    # (1 initial call + 5 retries).
    assert client.meta.config.retries == {
        "mode": "adaptive",
        "total_max_attempts": 6,
    }


def test_get_scan_client_passes_service_and_region_through() -> None:
    captured: dict = {}

    class FakeSession:
        def client(self, service_name: str, **kwargs: Any) -> str:
            captured["service_name"] = service_name
            captured.update(kwargs)
            return "the-client"

    assert get_scan_client(FakeSession(), "elbv2", REGION) == "the-client"
    assert captured["service_name"] == "elbv2"
    assert captured["region_name"] == REGION
    assert captured["config"].retries == scan_client_config().retries
    assert captured["config"].user_agent_extra == "aws-resource-inventory"
