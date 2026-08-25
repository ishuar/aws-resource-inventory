"""
Scan orchestration seam: aws_resource_inventory.lib.scan

scan_service, scan_region and perform_scan are the interface the CLI
drives. These tests exercise dispatch, caching, error reporting, service
filtering, shutdown, and progress reporting through that interface only —
no reaching into the dispatch mechanism — so they must keep passing
unchanged through the planned service-registry and spec-driven-scanner
refactors.

Failures are data, not log lines (ADR-0010): a failed scan unit surfaces
as a ScanError instead of silently reading as "zero resources".
"""

import threading
from typing import Any

import pytest
from botocore.exceptions import ClientError

from aws_resource_inventory.lib.cache import cache_result
from aws_resource_inventory.lib.scan import scan_region, scan_service
from aws_resource_inventory.orchestrator import perform_scan

REGION = "eu-central-1"


def client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": code}}, "DescribeThings")


class TestScanService:
    def test_unknown_service_returns_empty_dict(self, aws_session: Any) -> None:
        assert scan_service(aws_session, REGION, "not-a-service", use_cache=False) == {}

    def test_cache_hit_short_circuits_the_scan(self) -> None:
        # session=None: any attempt to actually scan would crash. Returning
        # the cached payload proves AWS is never touched on a hit.
        cached = {"buckets": [{"Name": "from-cache"}]}
        cache_result(REGION, "s3", cached)

        assert scan_service(None, REGION, "s3", use_cache=True) == cached

    def test_scan_result_is_stored_in_the_cache(self, aws_session: Any) -> None:
        aws_session.client("s3", region_name=REGION).create_bucket(
            Bucket="cached-bucket",
            CreateBucketConfiguration={"LocationConstraint": REGION},
        )

        first = scan_service(aws_session, REGION, "s3", use_cache=True)
        assert [b["Name"] for b in first["buckets"]] == ["cached-bucket"]

        # Second call with a session that cannot scan → must come from cache.
        assert scan_service(None, REGION, "s3", use_cache=True) == first

    def test_no_cache_flag_bypasses_a_poisoned_cache(self, aws_session: Any) -> None:
        cache_result(REGION, "s3", {"buckets": [{"Name": "stale"}]})
        result = scan_service(aws_session, REGION, "s3", use_cache=False)
        assert result == {"buckets": []}

    def test_tag_filters_are_forwarded_to_the_autoscaling_scanner(
        self, aws_session: Any
    ) -> None:
        # autoscaling is the one service whose scanner accepts tag filters
        # (RGTA doesn't cover ASGs); scan_service must pass them through.
        ec2 = aws_session.client("ec2", region_name=REGION)
        autoscaling = aws_session.client("autoscaling", region_name=REGION)
        ec2.create_launch_template(
            LaunchTemplateName="disp-lt",
            LaunchTemplateData={"ImageId": "ami-12345678", "InstanceType": "t3.micro"},
        )
        for name, tags in [
            ("prod-asg", [{"Key": "env", "Value": "prod"}]),
            ("dev-asg", []),
        ]:
            autoscaling.create_auto_scaling_group(
                AutoScalingGroupName=name,
                MinSize=0,
                MaxSize=1,
                AvailabilityZones=[f"{REGION}a"],
                LaunchTemplate={"LaunchTemplateName": "disp-lt"},
                Tags=[
                    {**t, "ResourceId": name, "ResourceType": "auto-scaling-group"}
                    for t in tags
                ],
            )

        result = scan_service(
            aws_session, REGION, "autoscaling", "env", "prod", use_cache=False
        )

        assert [a["AutoScalingGroupName"] for a in result["auto_scaling_groups"]] == [
            "prod-asg"
        ]

    def test_client_error_from_a_scanner_propagates(self) -> None:
        # scan_service no longer swallows: the caller (scan_region) owns
        # the catch and records the failure as ScanError data. Swallowing
        # here is how AccessDenied used to read as "zero resources".
        class BrokenSession:
            def client(self, *_args: Any, **_kwargs: Any) -> Any:
                raise client_error("AccessDenied")

        with pytest.raises(ClientError):
            scan_service(BrokenSession(), REGION, "s3", use_cache=False)


class TestScanRegion:
    def test_scans_requested_services_and_reports_progress(
        self, aws_session: Any
    ) -> None:
        aws_session.client("s3", region_name=REGION).create_bucket(
            Bucket="region-bucket",
            CreateBucketConfiguration={"LocationConstraint": REGION},
        )

        progress_calls: list[tuple[int, int, str, str]] = []

        def on_progress(completed: int, total: int, service: str, region: str) -> None:
            progress_calls.append((completed, total, service, region))

        scan = scan_region(
            aws_session,
            REGION,
            services=["s3"],
            use_cache=False,
            progress_callback=on_progress,
        )

        assert scan.region == REGION
        assert [b["Name"] for b in scan.results["s3"]["buckets"]] == ["region-bucket"]
        assert scan.duration_seconds >= 0
        assert scan.errors == []
        assert progress_calls == [(1, 1, "s3", REGION)]

    def test_unsupported_services_are_silently_filtered(self, aws_session: Any) -> None:
        progress_calls: list[tuple[int, int, str, str]] = []

        results = scan_region(
            aws_session,
            REGION,
            services=["s3", "dynamodb", "bogus"],
            use_cache=False,
            progress_callback=lambda *args: progress_calls.append(args),
        ).results

        assert "dynamodb" not in results
        assert "bogus" not in results
        # Only the supported service was submitted and counted.
        assert all(total == 1 for _, total, _, _ in progress_calls)

    def test_service_with_no_resources_is_omitted_from_results(
        self, aws_session: Any
    ) -> None:
        # Characterization: an empty scan result ({} or all-empty values is
        # still truthy for dicts with keys, but a falsy {} is dropped).
        # s3 with no buckets returns {"buckets": []} (truthy) and is kept.
        results = scan_region(
            aws_session, REGION, services=["s3"], use_cache=False
        ).results
        assert results["s3"] == {"buckets": []}

    def test_pre_set_shutdown_event_stops_before_collecting_results(
        self, aws_session: Any
    ) -> None:
        shutdown = threading.Event()
        shutdown.set()

        results = scan_region(
            aws_session,
            REGION,
            services=["s3"],
            use_cache=False,
            shutdown_event=shutdown,
        ).results

        assert results == {}

    def test_failed_service_becomes_error_data_and_the_rest_still_scan(
        self, aws_session: Any
    ) -> None:
        # One denied service must not kill the region — and must not read
        # as "zero resources" either: it surfaces as a ScanError.
        aws_session.client("s3", region_name=REGION).create_bucket(
            Bucket="survivor-bucket",
            CreateBucketConfiguration={"LocationConstraint": REGION},
        )

        broken = BrokenForOneService(aws_session, broken_service="ec2")
        scan = scan_region(broken, REGION, services=["s3", "ec2"], use_cache=False)

        assert [b["Name"] for b in scan.results["s3"]["buckets"]] == ["survivor-bucket"]
        assert "ec2" not in scan.results
        assert [(e.region, e.service) for e in scan.errors] == [(REGION, "ec2")]
        assert "AccessDenied" in scan.errors[0].message


class BrokenForOneService:
    """A session that raises on one service's client and delegates the rest."""

    def __init__(self, real_session: Any, broken_service: str) -> None:
        self._real_session = real_session
        self._broken_service = broken_service

    def client(self, service_name: str, *args: Any, **kwargs: Any) -> Any:
        if service_name == self._broken_service:
            raise client_error("AccessDenied")
        return self._real_session.client(service_name, *args, **kwargs)


class TestPerformScan:
    def test_returns_results_and_errors_across_regions(self, aws_session: Any) -> None:
        aws_session.client("s3", region_name=REGION).create_bucket(
            Bucket="fanout-bucket",
            CreateBucketConfiguration={"LocationConstraint": REGION},
        )

        broken = BrokenForOneService(aws_session, broken_service="ec2")
        results, errors = perform_scan(
            broken,
            [REGION],
            ["s3", "ec2"],
            tag_key=None,
            tag_value=None,
            max_workers=2,
            service_workers=2,
            use_cache=False,
        )

        assert [b["Name"] for b in results[REGION]["s3"]["buckets"]] == [
            "fanout-bucket"
        ]
        assert [(e.region, e.service) for e in errors] == [(REGION, "ec2")]

    def test_clean_scan_reports_no_errors(self, aws_session: Any) -> None:
        _results, errors = perform_scan(
            aws_session,
            [REGION],
            ["s3"],
            tag_key=None,
            tag_value=None,
            max_workers=2,
            service_workers=2,
            use_cache=False,
        )
        assert errors == []

    def test_tagging_path_region_failure_is_a_region_level_error(
        self, aws_session: Any
    ) -> None:
        # The tagging path has no per-service granularity: a Resource
        # Groups API failure fails the whole region → service is None.
        broken = BrokenForOneService(
            aws_session, broken_service="resourcegroupstaggingapi"
        )
        results, errors = perform_scan(
            broken,
            [REGION],
            [],
            tag_key="env",
            tag_value="prod",
            max_workers=2,
            service_workers=2,
            use_cache=False,
        )

        assert results == {}
        assert [(e.region, e.service) for e in errors] == [(REGION, None)]
        assert "AccessDenied" in errors[0].message

    def test_region_crash_is_a_region_level_error(
        self, aws_session: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The orchestrator's own safety net: anything that escapes
        # scan_region entirely still lands in the errors list.
        import aws_resource_inventory.orchestrator as orchestrator_module

        def crash(*_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeError("thread pool exploded")

        monkeypatch.setattr(orchestrator_module, "scan_region", crash)
        results, errors = perform_scan(
            aws_session,
            [REGION],
            ["s3"],
            tag_key=None,
            tag_value=None,
            max_workers=2,
            service_workers=2,
            use_cache=False,
        )

        assert results == {}
        assert [(e.region, e.service) for e in errors] == [(REGION, None)]
        assert "thread pool exploded" in errors[0].message
