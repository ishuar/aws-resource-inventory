"""
Scanning engine — the shared library every service scanner calls.

One home for the mechanics all six scanners used to re-implement
privately: pagination, parallel collection, per-item fan-out, tag
matching, and summary logging.

This is a library the services call, not a framework that calls the
services: each service keeps its own readable ``scan_x`` function and
reaches for these helpers where they help.

Invariants (relied on by tests/test_engine.py and every caller):

- ``collect_pages`` always paginates via ``get_paginator``; items keep
  page order; boto errors raise — guarding is the caller's decision.
- ``run_parallel`` returns EXACTLY the task keys, in insertion order,
  every value a list. Every exception propagates: scan_region records
  it as ScanError data (ADR-0010), so a denied describe never reads as
  "zero resources". Expected per-item conditions (e.g. s3's
  NoSuchTagSet) are each scanner's own narrow catch, not the engine's.
- ``map_parallel`` preserves input order despite parallel completion;
  a ``None`` result drops that item; every exception propagates.
- ``matches_tags`` implements the pinned tag semantics: key+value =
  exact pair; key-only = key exists (any value); value-only = value
  exists (any key); no filter = always True.

Guard rail (agreed design rule): ``Describe`` never grows beyond
``op / result_key / kwargs / flatten``. Anything needing more is a
plain function calling ``collect_pages``.
"""

from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from functools import partial
from typing import Any, TypeVar

from .logging import get_logger

logger = get_logger()

ResourceList = list[dict[str, Any]]
ScanResult = dict[str, ResourceList]

_T = TypeVar("_T")
_R = TypeVar("_R")


@dataclass(frozen=True)
class Describe:
    """One paginated call filling one result key — the common case."""

    op: str
    result_key: str
    kwargs: Mapping[str, Any] = field(default_factory=dict)
    flatten: Callable[[dict[str, Any]], ResourceList] | None = None


def collect_pages(
    client: Any,
    op: str,
    result_key: str,
    *,
    flatten: Callable[[dict[str, Any]], ResourceList] | None = None,
    **kwargs: Any,
) -> ResourceList:
    """Collect every page of a paginated operation, in page order."""
    resources: ResourceList = []
    paginator = client.get_paginator(op)
    for page in paginator.paginate(**kwargs):
        resources.extend(flatten(page) if flatten else page[result_key])
    return resources


def run_parallel(
    tasks: Mapping[str, Callable[[], ResourceList]],
    *,
    service: str,
    region: str,
    max_workers: int,
) -> ScanResult:
    """Run named collection tasks concurrently; failures propagate."""
    result: ScanResult = {}
    if not tasks:
        return result

    with ThreadPoolExecutor(max_workers=min(len(tasks), max(1, max_workers))) as pool:
        futures = {key: pool.submit(task) for key, task in tasks.items()}
        for key, future in futures.items():
            result[key] = future.result()
    return result


def map_parallel(
    fn: Callable[[_T], _R | None],
    items: Iterable[_T],
    *,
    max_workers: int,
) -> list[_R]:
    """Apply fn to every item concurrently, preserving input order.

    A None result drops that item only; failures propagate.
    """
    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as pool:
        mapped = list(pool.map(fn, items))
    return [r for r in mapped if r is not None]


def matches_tags(
    tags: Iterable[Mapping[str, Any]],
    tag_key: str | None,
    tag_value: str | None,
) -> bool:
    """Client-side tag filter with the pinned semantics (see module docs)."""
    if tag_key and tag_value:
        return any(
            t.get("Key") == tag_key and t.get("Value") == tag_value for t in tags
        )
    if tag_key:
        return any(t.get("Key") == tag_key for t in tags)
    if tag_value:
        return any(t.get("Value") == tag_value for t in tags)
    return True


def finish(service: str, region: str, result: ScanResult) -> ScanResult:
    """Emit the per-scan summary logging and hand the result back."""
    total_resources = sum(len(resources) for resources in result.values())
    logger.info(
        "%s scan completed in region %s: %d total resources",
        service.upper(),
        region,
        total_resources,
    )
    for resource_type, resources in result.items():
        if resources:
            logger.debug(
                "%s %s in %s: %d resources",
                service.upper(),
                resource_type,
                region,
                len(resources),
            )
    return result


def scan_keyed(
    client: Any,
    specs: Mapping[str, Describe],
    *,
    service: str,
    region: str,
    max_workers: int,
) -> ScanResult:
    """Run a whole declarative scan: one Describe per result key."""
    tasks = {
        key: partial(
            collect_pages,
            client,
            spec.op,
            spec.result_key,
            flatten=spec.flatten,
            **spec.kwargs,
        )
        for key, spec in specs.items()
    }
    return finish(
        service,
        region,
        run_parallel(tasks, service=service, region=region, max_workers=max_workers),
    )
