"""
Shared boto3 client construction for all scanners.

One set of botocore settings owns connection pooling, timeouts, and
retries for every scanning client:

- max_pool_connections=50 — the scanner fans out regions x services x
  worker threads over shared clients; botocore's default pool of 10
  serializes that fan-out.
- retries mode "adaptive" — botocore is the single owner of
  transient-error retries (client-side rate limiting, Retry-After
  support, full transient error code coverage). No hand-rolled retry
  wrappers on top: stacking them multiplies retry attempts.
"""

import threading
from typing import Any

import botocore.config


def scan_client_config() -> botocore.config.Config:
    """A fresh scanning config — one instance per client, deliberately.

    botocore rewrites a config's ``retries`` dict in place while it
    builds a client (``max_attempts`` becomes ``total_max_attempts``),
    so a single shared instance stops describing the settings declared
    here as soon as the first client is built.
    """
    return botocore.config.Config(
        max_pool_connections=50,
        retries={"max_attempts": 5, "mode": "adaptive"},
        read_timeout=60,
        connect_timeout=10,
        # Stamp every API call so scanner traffic is identifiable in CloudTrail.
        user_agent_extra="aws-resource-inventory",
    )


# boto3: clients are thread-safe to USE, but sessions are not thread-safe
# to CREATE clients from — scan_region spawns service scans in parallel
# threads that all build clients off one shared session, so creation is
# serialized here. Scans themselves stay fully parallel.
_client_creation_lock = threading.Lock()


def get_scan_client(session: Any, service_name: str, region: str) -> Any:
    """Build a boto3 client for scanning with the scan config."""
    with _client_creation_lock:
        return session.client(
            service_name, region_name=region, config=scan_client_config()
        )
