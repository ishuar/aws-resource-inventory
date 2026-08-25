"""
Engine seam: aws_resource_inventory.lib.engine — the shared scanning library.

The engine is the one home for pagination, parallel collection, and
tag matching. Its invariants (tested here, stated in its docstrings)
are what every scanner relies on:

- collect_pages always paginates; items keep page order; boto errors raise.
- run_parallel returns EXACTLY the task keys, in insertion order; every
  exception propagates (fail fast) — the caller records it as ScanError
  data (ADR-0010), so a denied describe never reads as "zero resources".
- map_parallel preserves input order despite parallel fan-out; None
  results drop the item; every exception propagates.
- matches_tags implements the pinned tag semantics (exact pair /
  key-only / value-only / no filter).
"""

import time
from typing import Any, ClassVar

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

from aws_resource_inventory.lib.engine import (
    Describe,
    collect_pages,
    finish,
    map_parallel,
    matches_tags,
    run_parallel,
    scan_keyed,
)

REGION = "eu-central-1"


def client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": code}}, "DescribeThings")


class FakePaginator:
    def __init__(self, pages: list[dict[str, Any]], record: dict[str, Any]) -> None:
        self._pages = pages
        self._record = record

    def paginate(self, **kwargs: Any) -> Any:
        self._record["paginate_kwargs"] = kwargs
        return iter(self._pages)


class FakeClient:
    """Paginator-only fake: raw operation calls don't exist on purpose."""

    def __init__(self, pages_by_op: dict[str, list[dict[str, Any]]]) -> None:
        self._pages_by_op = pages_by_op
        self.calls: dict[str, Any] = {}

    def get_paginator(self, op: str) -> FakePaginator:
        record: dict[str, Any] = {}
        self.calls[op] = record
        return FakePaginator(self._pages_by_op[op], record)


class TestCollectPages:
    def test_collects_all_pages_in_order(self) -> None:
        client = FakeClient(
            {
                "describe_vpcs": [
                    {"Vpcs": [{"VpcId": "a"}, {"VpcId": "b"}]},
                    {"Vpcs": [{"VpcId": "c"}]},
                ]
            }
        )
        items = collect_pages(client, "describe_vpcs", "Vpcs")
        assert [i["VpcId"] for i in items] == ["a", "b", "c"]

    def test_kwargs_reach_the_paginator(self) -> None:
        client = FakeClient({"describe_images": [{"Images": []}]})
        collect_pages(client, "describe_images", "Images", Owners=["self"])
        assert client.calls["describe_images"]["paginate_kwargs"] == {
            "Owners": ["self"]
        }

    def test_flatten_overrides_the_page_field(self) -> None:
        pages = [
            {"Reservations": [{"Instances": [{"Id": 1}]}, {"Instances": [{"Id": 2}]}]}
        ]
        client = FakeClient({"describe_instances": pages})
        items = collect_pages(
            client,
            "describe_instances",
            "Reservations",
            flatten=lambda p: [i for r in p["Reservations"] for i in r["Instances"]],
        )
        assert [i["Id"] for i in items] == [1, 2]

    def test_boto_errors_propagate(self) -> None:
        class Exploding:
            def get_paginator(self, op: str) -> Any:
                raise client_error("AccessDenied")

        with pytest.raises(ClientError):
            collect_pages(Exploding(), "describe_vpcs", "Vpcs")


class TestRunParallel:
    def test_result_has_exactly_the_task_keys_in_insertion_order(self) -> None:
        result = run_parallel(
            {"b": lambda: [{"n": 1}], "a": lambda: [{"n": 2}, {"n": 3}]},
            service="svc",
            region=REGION,
            max_workers=4,
        )
        assert list(result) == ["b", "a"]
        assert result == {"b": [{"n": 1}], "a": [{"n": 2}, {"n": 3}]}

    def test_boto_error_propagates(self) -> None:
        # No engine-level swallow (ADR-0010): a denied describe must reach
        # scan_region and become ScanError data, never read as [].
        def boom() -> list[Any]:
            raise client_error("Throttling")

        with pytest.raises(ClientError):
            run_parallel(
                {"ok": lambda: [{"n": 1}], "broken": boom},
                service="svc",
                region=REGION,
                max_workers=2,
            )

    def test_connection_error_also_propagates(self) -> None:
        def boom() -> list[Any]:
            raise EndpointConnectionError(endpoint_url="https://example.test")

        with pytest.raises(EndpointConnectionError):
            run_parallel({"broken": boom}, service="svc", region=REGION, max_workers=1)

    def test_non_boto_exceptions_propagate(self) -> None:
        def bug() -> list[Any]:
            raise ValueError("engine bugs must surface")

        with pytest.raises(ValueError):
            run_parallel({"x": bug}, service="svc", region=REGION, max_workers=1)

    def test_no_tasks_yields_empty_result(self) -> None:
        assert run_parallel({}, service="svc", region=REGION, max_workers=4) == {}


class TestMapParallel:
    def test_order_is_preserved_despite_parallel_completion(self) -> None:
        def slow_for_early_items(n: int) -> int:
            time.sleep((9 - n) * 0.003)  # later items finish first
            return n * 10

        out = map_parallel(slow_for_early_items, list(range(10)), max_workers=5)
        assert out == [n * 10 for n in range(10)]

    def test_none_results_are_dropped(self) -> None:
        out = map_parallel(
            lambda n: n if n % 2 == 0 else None, [0, 1, 2, 3], max_workers=2
        )
        assert out == [0, 2]

    def test_boto_error_propagates(self) -> None:
        # Same rule as run_parallel: dropping the item silently would hide
        # a real failure — expected per-item conditions (e.g. s3's
        # NoSuchTagSet) are the scanner's own narrow catch, not the engine's.
        def fn(n: int) -> int:
            if n == 1:
                raise client_error("AccessDenied")
            return n

        with pytest.raises(ClientError):
            map_parallel(fn, [0, 1, 2], max_workers=3)

    def test_non_boto_exceptions_propagate(self) -> None:
        def bug(n: int) -> int:
            raise RuntimeError("surface me")

        with pytest.raises(RuntimeError):
            map_parallel(bug, [1], max_workers=1)


class TestMatchesTags:
    TAGS: ClassVar[list[dict[str, str]]] = [
        {"Key": "env", "Value": "prod"},
        {"Key": "team", "Value": "infra"},
    ]

    def test_key_and_value_requires_the_exact_pair(self) -> None:
        assert matches_tags(self.TAGS, "env", "prod") is True
        assert matches_tags(self.TAGS, "env", "dev") is False
        assert matches_tags(self.TAGS, "team", "prod") is False

    def test_key_only_matches_any_value(self) -> None:
        assert matches_tags(self.TAGS, "env", None) is True
        assert matches_tags(self.TAGS, "missing", None) is False

    def test_value_only_matches_any_key(self) -> None:
        assert matches_tags(self.TAGS, None, "infra") is True
        assert matches_tags(self.TAGS, None, "missing") is False

    def test_no_filter_matches_everything(self) -> None:
        assert matches_tags(self.TAGS, None, None) is True
        assert matches_tags([], None, None) is True


class TestScanKeyed:
    def test_whole_scan_from_describe_specs(self) -> None:
        client = FakeClient(
            {
                "describe_vpcs": [{"Vpcs": [{"VpcId": "v1"}]}],
                "describe_subnets": [{"Subnets": []}],
            }
        )
        specs = {
            "vpcs": Describe("describe_vpcs", "Vpcs"),
            "subnets": Describe("describe_subnets", "Subnets"),
        }
        result = scan_keyed(client, specs, service="vpc", region=REGION, max_workers=2)
        assert result == {"vpcs": [{"VpcId": "v1"}], "subnets": []}

    def test_describe_kwargs_and_flatten_are_honoured(self) -> None:
        client = FakeClient(
            {"describe_instances": [{"Reservations": [{"Instances": [{"Id": 1}]}]}]}
        )
        specs = {
            "instances": Describe(
                "describe_instances",
                "Reservations",
                kwargs={"MaxResults": 100},
                flatten=lambda p: [
                    i for r in p["Reservations"] for i in r["Instances"]
                ],
            )
        }
        result = scan_keyed(client, specs, service="ec2", region=REGION, max_workers=1)
        assert result == {"instances": [{"Id": 1}]}
        assert client.calls["describe_instances"]["paginate_kwargs"] == {
            "MaxResults": 100
        }


def test_finish_returns_the_result_unchanged() -> None:
    result: dict[str, list[dict[str, Any]]] = {"a": [{"n": 1}], "b": []}
    assert finish("svc", REGION, result) is result
