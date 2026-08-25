"""
Scan module for AWS Scanner

Handles scanning operations for AWS services across regions.
"""

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import boto3
from botocore.exceptions import (
    ClientError,
    ConnectTimeoutError,
    EndpointConnectionError,
)

from aws_resource_inventory.services.registry import SERVICES, SUPPORTED_SERVICES

# Import cache functions
from .cache import cache_result, get_cached_result
from .envelope import ScanError

# Import logging (using simplified logging)
from .logging import get_logger

# Module-level logger
logger = get_logger()


def scan_all_services_with_tags(
    session: boto3.Session,
    region: str,
    tag_key: str | None = None,
    tag_value: str | None = None,
    use_cache: bool = True,
) -> tuple[str, dict[str, Any], float, list[ScanError]]:
    """
    Scan ALL AWS services using Resource Groups Tagging API.

    This function provides service-agnostic resource discovery across ALL AWS services
    that support tagging, not just the traditional 6 services (EC2, VPC, S3, etc.).

    Returns resources in the same format as scan_region for compatibility.
    This path has no per-service granularity: a Resource Groups API
    failure fails the whole region, recorded as one ScanError with
    ``service=None`` (ADR-0010).
    """
    from .resource_groups_utils import scan_all_tagged_resources

    start_time = time.time()

    logger.debug("Starting all-services scan in region %s", region)
    logger.log_aws_operation(
        "resource-groups",
        "scan_all_tagged_resources",
        region,
        tag_key=tag_key,
        tag_value=tag_value,
    )

    # Check cache first
    if use_cache:
        logger.log_cache_operation(
            "check", f"{region}:all_services:{tag_key}:{tag_value}"
        )
        cached_result = get_cached_result(region, "all_services", tag_key, tag_value)
        if cached_result is not None:
            logger.log_cache_operation(
                "hit",
                f"{region}:all_services",
                hit=True,
                resource_count=sum(
                    len(v) if isinstance(v, list) else 1 for v in cached_result.values()
                ),
            )
            scan_duration = time.time() - start_time
            return region, cached_result, scan_duration, []

    try:
        with logger.timer(f"All-services scan in {region}"):
            # Use service-agnostic Resource Groups API scan
            results = scan_all_tagged_resources(session, region, tag_key, tag_value)

        scan_duration = time.time() - start_time
        resource_count = sum(
            len(v) if isinstance(v, list) else 1 for v in results.values()
        )

        # Cache the results
        if use_cache and results:
            logger.log_cache_operation(
                "store", f"{region}:all_services", resource_count=resource_count
            )
            cache_result(region, "all_services", results, tag_key, tag_value)

        logger.log_scan_progress("all-services", region, resource_count, scan_duration)

        if not results:
            logger.info("No tagged resources found in region %s", region)

        return region, results, scan_duration, []

    except (ClientError, EndpointConnectionError, ConnectTimeoutError) as e:
        logger.error("Failed cross-service scan in region %s: %s", region, str(e))
        logger.log_error_context(
            e,
            {
                "region": region,
                "operation": "all_services_scan",
                "tag_key": tag_key,
                "tag_value": tag_value,
            },
        )
        error = ScanError(region=region, service=None, message=str(e))
        return region, {}, time.time() - start_time, [error]


def scan_service(
    session: boto3.Session,
    region: str,
    service: str,
    tag_key: str | None = None,
    tag_value: str | None = None,
    use_cache: bool = True,
) -> dict[str, Any]:
    """Route service scan to the appropriate modular scanner, with caching.

    Failures propagate: the caller (scan_region) owns the catch and
    records them as ScanError data (ADR-0010) — swallowing here is how a
    denied service used to read as "zero resources".
    """

    # Check cache first
    if use_cache:
        cached_result = get_cached_result(region, service, tag_key, tag_value)
        if cached_result is not None:
            # Cache message now displayed upfront, just return silently
            return cached_result

    def _do_scan() -> dict[str, Any]:
        registration = SERVICES.get(service)
        if registration is None:
            logger.warning("Service scan for '%s' not implemented yet", service)
            return {}
        if registration.accepts_tags:
            return registration.scan(session, region, tag_key, tag_value)
        return registration.scan(session, region)

    # Transient-error retries are owned by botocore's adaptive retry
    # mode on the clients themselves (aws_resource_inventory.lib.clients).
    result = _do_scan()

    # Cache the result
    if use_cache and result:
        cache_result(region, service, result, tag_key, tag_value)

    return result


def scan_region(
    session: boto3.Session,
    region: str,
    services: list[str],
    tag_key: str | None = None,
    tag_value: str | None = None,
    service_workers: int = 4,
    use_cache: bool = True,
    progress_callback: Any | None = None,
    shutdown_event: Any | None = None,
) -> tuple[str, dict[str, Any], float, list[ScanError]]:
    """Scan all services in a single region with parallel service scanning.

    Returns the region, its results, the scan duration, and the failed
    scan units as ScanError data (ADR-0010) — one entry per service whose
    scan raised, so a denied service never reads as "zero resources".
    """
    start_time = time.time()
    region_results = {}
    errors: list[ScanError] = []

    logger.debug("Starting region scan for %s with %d services", region, len(services))
    logger.debug(
        "Service configuration: workers=%d, cache=%s", service_workers, use_cache
    )

    # Limit workers to reasonable bounds
    max_service_workers = min(len(services), max(1, min(service_workers, 10)))

    if max_service_workers != service_workers:
        logger.debug(
            "Adjusted service workers from %d to %d for region %s",
            service_workers,
            max_service_workers,
            region,
        )

    with ThreadPoolExecutor(max_workers=max_service_workers) as executor:
        # Submit service scanning tasks
        future_to_service = {
            executor.submit(
                scan_service, session, region, service, tag_key, tag_value, use_cache
            ): service
            for service in services
            if service in SUPPORTED_SERVICES
        }

        # Collect results as they complete
        service_results_summary = {}
        total_services = len(future_to_service)

        for completed_services, future in enumerate(
            as_completed(future_to_service), start=1
        ):
            # Check for shutdown request before processing each service
            if shutdown_event and shutdown_event.is_set():
                logger.warning("Cancelling remaining services in region %s", region)
                # Cancel remaining futures
                for pending_future in future_to_service:
                    if not pending_future.done():
                        pending_future.cancel()
                break

            service = future_to_service[future]
            try:
                service_data = future.result(
                    timeout=60
                )  # 60 second timeout per service
                if service_data:
                    region_results[service] = service_data
                    # Count resources for feedback
                    total_resources = sum(
                        len(v) if isinstance(v, list) else 1
                        for v in service_data.values()
                    )
                    service_results_summary[service] = total_resources
                else:
                    service_results_summary[service] = 0
            # Thread-pool boundary: one service failing must not kill the
            # region, and must not read as "zero resources" — it becomes
            # ScanError data for the envelope (ADR-0010).
            except Exception as e:  # noqa: BLE001
                logger.error("Error scanning %s in %s: %s", service, region, str(e))
                logger.log_error_context(
                    e,
                    {"service": service, "region": region, "operation": "service_scan"},
                )
                errors.append(ScanError(region=region, service=service, message=str(e)))
                service_results_summary[service] = 0

            # Update progress if callback provided
            if progress_callback:
                progress_callback(completed_services, total_services, service, region)

    # Calculate and display region scan time
    end_time = time.time()
    scan_duration = end_time - start_time

    # Log region completion
    total_resources = sum(service_results_summary.values())
    logger.log_scan_progress("region", region, total_resources, scan_duration)

    if logger.is_debug_enabled():
        logger.debug("Region %s service breakdown:", region)
        for service, count in service_results_summary.items():
            logger.debug("  • %s: %d resources", service, count)

    # Only show region completion in debug mode to avoid interfering with progress
    if logger.is_debug_enabled():
        logger.debug("Region %s scan completed in %.1fs", region, scan_duration)

    return region, region_results, scan_duration, errors
