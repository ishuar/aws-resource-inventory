"""
Cache module for aws-resource-inventory

Handles caching of scan results to improve performance and reduce API calls.
"""

import hashlib
import os
import pickle
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, cast

from .logging import get_logger


def default_cache_dir() -> Path:
    """The per-user directory holding cached scan results.

    Not a shared temp directory: the entries are pickles of an account's
    resource inventory, so a world-writable predictable path would let
    any local user read them or hand `pickle.load` a payload of their
    own. `XDG_CACHE_HOME` wins where it is set; `~/.cache` is the
    fallback on every platform, so there is one path to document.
    """
    xdg_cache_home = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg_cache_home) if xdg_cache_home else Path.home() / ".cache"
    return base / "aws-resource-inventory"


# Cache configuration
CACHE_DIR = default_cache_dir()
CACHE_TTL_MINUTES = 10  # Cache TTL in minutes

# Module logger
logger = get_logger("cache")


def get_cache_key(
    region: str,
    service: str,
    tag_key: str | None = None,
    tag_value: str | None = None,
) -> str:
    """Generate a cache key for the given parameters."""
    cache_data = f"{region}:{service}:{tag_key or ''}:{tag_value or ''}"
    return hashlib.md5(cache_data.encode()).hexdigest()


def get_cached_result(
    region: str,
    service: str,
    tag_key: str | None = None,
    tag_value: str | None = None,
) -> dict[str, Any] | None:
    """Get cached result if available and not expired."""
    if not CACHE_DIR.exists():
        logger.debug("Cache directory does not exist")
        return None

    cache_key = get_cache_key(region, service, tag_key, tag_value)
    cache_file = CACHE_DIR / f"{cache_key}.pkl"

    if cache_file.exists():
        try:
            with open(cache_file, "rb") as f:
                cached_data = pickle.load(f)

            # Check if cache is still valid
            cache_time = datetime.fromtimestamp(
                cache_file.stat().st_mtime, tz=timezone.utc
            )
            if datetime.now(timezone.utc) - cache_time < timedelta(
                minutes=CACHE_TTL_MINUTES
            ):
                resource_count = (
                    len(cached_data)
                    if isinstance(cached_data, list)
                    else (
                        sum(
                            len(v) if isinstance(v, list) else 1
                            for v in cached_data.values()
                        )
                        if isinstance(cached_data, dict)
                        else 0
                    )
                )
                logger.log_cache_operation(
                    "check",
                    f"{region}:{service}:{tag_key}:{tag_value}",
                    hit=True,
                    resource_count=resource_count,
                )
                return cast(dict[str, Any], cached_data)
            else:
                logger.debug("Cache expired for %s:%s", region, service)
        # Cache is best-effort: any read failure is just a miss.
        except Exception as e:  # noqa: BLE001
            logger.debug("Cache read error for %s:%s: %s", region, service, str(e))

    logger.log_cache_operation(
        "check", f"{region}:{service}:{tag_key}:{tag_value}", hit=False
    )
    return None


def cache_result(
    region: str,
    service: str,
    result: Any,
    tag_key: str | None = None,
    tag_value: str | None = None,
) -> None:
    """Cache the scan result."""
    try:
        # parents: unlike /tmp, ~/.cache is not guaranteed to exist.
        # mode: the entries are this account's inventory — owner only.
        CACHE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
        # mkdir applies its mode only when it creates the directory, so a
        # directory left behind at a wider mode keeps it — exactly the
        # exposure ADR-0008 exists to close. Assert the mode every time.
        CACHE_DIR.chmod(0o700)
        cache_key = get_cache_key(region, service, tag_key, tag_value)
        cache_file = CACHE_DIR / f"{cache_key}.pkl"

        # Opened 0o600 rather than written and chmod'd: umask can only
        # clear permission bits, never set them, so the entry is never
        # briefly world-readable between creation and tightening.
        descriptor = os.open(cache_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "wb") as f:
            pickle.dump(result, f)

        resource_count = (
            len(result)
            if isinstance(result, list)
            else (
                sum(len(v) if isinstance(v, list) else 1 for v in result.values())
                if isinstance(result, dict)
                else 0
            )
        )
        logger.log_cache_operation(
            "store",
            f"{region}:{service}:{tag_key}:{tag_value}",
            resource_count=resource_count,
        )

    # Cache is best-effort: a write failure must not break the scan.
    except Exception as e:  # noqa: BLE001
        logger.debug("Cache write error for %s:%s: %s", region, service, str(e))
