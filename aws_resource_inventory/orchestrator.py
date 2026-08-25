"""
AWS Multi-Service Scanner (Core Module)
--------------------------------------

Core functionality for scanning AWS resources across multiple services and regions.
This module provides the business logic for AWS resource discovery, caching,
and result processing, separated from CLI concerns.
"""

import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import boto3
import pyfiglet
from botocore.exceptions import (
    ClientError,
    NoCredentialsError,
    ProfileNotFound,
    TokenRetrievalError,
)
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress
from rich.table import Table

# Import logging (using simplified logging)
from aws_resource_inventory.lib.envelope import ScanError
from aws_resource_inventory.lib.logging import get_logger
from aws_resource_inventory.lib.outputs import TABLE_MINIMUM_WIDTH
from aws_resource_inventory.lib.records import CallerIdentity
from aws_resource_inventory.lib.resource_groups_utils import (
    should_use_resource_groups_api,
)

# Import modular components
from aws_resource_inventory.lib.scan import scan_region

# Global components
console = Console()
logger = get_logger()

# Thread-safe session pool for connection reuse
_session_pool: dict[str, boto3.Session] = {}
_session_pool_lock = threading.Lock()


def get_session(profile_name: str | None = None) -> boto3.Session:
    """Get AWS session with thread-safe connection pooling."""
    cache_key = profile_name or "default"

    logger.debug("Requesting AWS session for profile: %s", cache_key)

    # First check if session exists (avoid lock if not needed)
    if cache_key in _session_pool:
        logger.debug("Reusing cached session for profile: %s", cache_key)
        return _session_pool[cache_key]

    # Use lock for thread-safe session creation
    with _session_pool_lock:
        # Double-check pattern: verify session wasn't created while waiting for lock
        if cache_key not in _session_pool:
            try:
                logger.debug("Creating new AWS session for profile: %s", cache_key)
                session = boto3.Session(profile_name=profile_name)
                _session_pool[cache_key] = session
                logger.debug(
                    "AWS session created and cached for profile: %s", cache_key
                )
            except ProfileNotFound as e:
                logger.log_error_context(
                    e, {"profile": profile_name, "operation": "session_creation"}
                )
                raise RuntimeError(f"AWS profile '{profile_name}' not found") from e
            except Exception as e:  # More generic exception handling
                logger.log_error_context(
                    e, {"profile": profile_name, "operation": "session_creation"}
                )
                raise RuntimeError(f"Failed to create AWS session: {e}") from e

    return _session_pool[cache_key]


def validate_aws_credentials(
    session: boto3.Session, profile_name: str | None = None
) -> tuple[bool, str, CallerIdentity | None]:
    """
    Validate AWS credentials by attempting to call STS get-caller-identity.

    Returns:
        tuple: (is_valid, message, caller_identity). The identity carries
        the account and partition every constructed ARN is built from;
        it is None exactly when validation failed.
    """
    try:
        logger.debug("Validating AWS credentials using STS GetCallerIdentity")
        # Try to get caller identity to validate credentials
        sts_client = session.client("sts")

        logger.log_aws_operation(
            "sts", "get_caller_identity", "global", profile=profile_name
        )
        response = sts_client.get_caller_identity()

        # STS always returns Account and Arn on success; a surprise here
        # is converted to a failure by the boundary except below.
        account_id = response["Account"]
        user_arn = response["Arn"]
        user_name = user_arn.split("/")[-1]
        identity = CallerIdentity.from_caller_arn(account_id, user_arn)

        logger.debug(
            "Credentials validated - Account: %s, Partition: %s, User: %s",
            account_id,
            identity.partition,
            user_name,
        )

        return (
            True,
            f"✅ AWS credentials valid (Account: {account_id}, User: {user_name})",
            identity,
        )

    except NoCredentialsError:
        profile_msg = f" for profile '{profile_name}'" if profile_name else ""
        return (
            False,
            f"❌ No AWS credentials found{profile_msg}. Please configure AWS credentials or set AWS_PROFILE environment variable.",
            None,
        )

    except TokenRetrievalError:
        return (
            False,
            "❌ Failed to retrieve AWS credentials. Check your AWS CLI configuration or SSO session.",
            None,
        )

    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "Unknown")
        if error_code in ["InvalidUserID.NotFound", "AccessDenied"]:
            return False, f"❌ AWS credentials invalid or expired: {e}", None
        return False, f"❌ AWS API error: {e}", None

    # User-facing boundary: any failure becomes a message, never a crash.
    except Exception as e:  # noqa: BLE001
        return False, f"❌ Unexpected error validating credentials: {e}", None


def display_banner(debug: bool) -> str:
    """Display fancy ASCII banner with AWS profile information."""
    # Create fancy ASCII banner
    try:
        banner = pyfiglet.figlet_format("AWS Inventory", font="slant")
        bold_cyan_banner = f"[bold cyan]{banner}[/bold cyan]"
        bold_green_banner = f"[bold green]{banner}[/bold green]"
        output = bold_cyan_banner if not debug else bold_green_banner
    except (pyfiglet.FontNotFound, pyfiglet.FigletError, OSError):
        # Fallback if pyfiglet fails
        output = "[bold cyan]╔═══════════════════════════════════════════════════════════╗[/bold cyan]\n"
        output += "[bold cyan]║                  AWS RESOURCE INVENTORY                   ║[/bold cyan]\n"
        output += "[bold cyan]╚═══════════════════════════════════════════════════════════╝[/bold cyan]"

    output += "\n[dim]Modular Version with Advanced Optimizations[/dim]\n"

    console.print(output)
    return output


def check_and_display_cache_status(
    region_list: list[str],
    services: list[str],
    tag_key: str | None,
    tag_value: str | None,
    use_cache: bool,
    all_services: bool,
) -> bool:
    """
    Check cache status for all regions/services and display summary.

    Returns:
        bool: True if any cached results are found, False otherwise
    """
    if not use_cache:
        return False

    # Import cache function
    from aws_resource_inventory.lib.cache import get_cached_result

    cached_items = []

    for region in region_list:
        if should_use_resource_groups_api(tag_key, tag_value, all_services):
            # Check cross-service cache
            cached_result = get_cached_result(
                region, "all_services", tag_key, tag_value
            )
            if cached_result is not None:
                cached_items.append(f"All services in {region}")
        else:
            # Check individual service caches
            for service in services:
                cached_result = get_cached_result(region, service, tag_key, tag_value)
                if cached_result is not None:
                    cached_items.append(f"{service} in {region}")

    if cached_items:
        # Create cache status table
        cache_table = Table(show_header=False, box=None, min_width=TABLE_MINIMUM_WIDTH)
        cache_table.add_column("", style="dim cyan", width=80)

        for item in cached_items:
            cache_table.add_row(f"✓ Using cached result for {item}")

        console.print(
            Panel(
                cache_table,
                title="[bold white]Cache Status[/bold white]",
                title_align="center",
                border_style="bright_blue",
                padding=(0, 1),
            )
        )
        console.print(
            "[dim yellow]⚠️  Note: Results may include cached data. For real-time results, disable caching with --no-cache[/dim yellow]\n"
        )
        return True

    return False


def display_region_summaries(
    all_results: dict[str, dict[str, Any]], debug: bool
) -> None:
    """Display region-wise resource summaries after scanning is complete."""

    if not all_results:
        return

    console.print()  # Add spacing

    for region_name, region_results in all_results.items():
        if not region_results:
            continue

        # Count resources by service
        service_counts = {}
        for service, service_data in region_results.items():
            if isinstance(service_data, dict):
                total_resources = sum(
                    len(v) if isinstance(v, list) else 1 for v in service_data.values()
                )
                service_counts[service] = total_resources
            else:
                service_counts[service] = 1 if service_data else 0

        if not service_counts or sum(service_counts.values()) == 0:
            continue

        # Create rich Table for clean display
        total_resources = sum(service_counts.values())
        results_table = Table(
            show_header=True,
            header_style="white",
            border_style="bright_blue" if not debug else "green",
            expand=False,
            min_width=TABLE_MINIMUM_WIDTH,
            box=box.SIMPLE_HEAVY,
        )
        results_table.add_column("Service", style="cyan")
        results_table.add_column("Count", style="yellow", justify="right")

        # Add service rows
        for service, count in service_counts.items():
            results_table.add_row(service.upper(), str(count))

        # Add separator row
        results_table.add_section()
        results_table.add_row("TOTAL", str(total_resources), style="bold green")

        # Display with Panel
        console.print(
            Panel(
                results_table,
                title=f"[bold white]{region_name.upper()}[/bold white]",
                title_align="center",
                border_style="bright_blue" if not debug else "green",
                padding=(0, 1),
            )
        )


def perform_scan(
    session: boto3.Session,
    region_list: list[str],
    services: list[str],
    tag_key: str | None,
    tag_value: str | None,
    max_workers: int,
    service_workers: int,
    use_cache: bool,
    progress: Progress | None = None,
    all_services: bool = False,
    shutdown_event: threading.Event | None = None,
) -> tuple[dict[str, dict[str, Any]], list[ScanError]]:
    """Perform the AWS scanning operation with optional progress reporting.

    Returns the per-region results and every failed scan unit as ScanError
    data (ADR-0010) — service-level from the per-service path, region-level
    (``service=None``) from the tagging path and from anything that kills a
    whole region. Failures never read as "zero resources".
    """
    logger.debug(
        "Starting perform_scan with %d regions, %d services",
        len(region_list),
        len(services),
    )
    logger.debug(
        "Configuration: max_workers=%d, service_workers=%d, use_cache=%s, all_services=%s",
        max_workers,
        service_workers,
        use_cache,
        all_services,
    )

    all_results = {}
    scan_errors: list[ScanError] = []
    main_task = None
    region_tasks = {}

    if progress:
        # Main progress bar for overall region completion
        main_task = progress.add_task(
            f"🔍 Scanning {len(region_list)} regions", total=len(region_list)
        )

        def create_progress_callback(
            region_name: str,
        ) -> Callable[[int, int, str, str], None]:
            """Create a progress callback function for a specific region"""

            def update_progress(
                completed: int, total: int, service: str, region: str
            ) -> None:
                if region_name not in region_tasks:
                    region_tasks[region_name] = progress.add_task(
                        f"  📍 {region_name}", total=total
                    )
                progress.update(
                    region_tasks[region_name],
                    completed=completed,
                    description=f"  📍 {region_name} ({service})",
                )

            return update_progress

    else:

        def create_progress_callback(
            region_name: str,
        ) -> Callable[[int, int, str, str], None]:
            return lambda *args: None

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit region scanning tasks with progress callbacks
        # Auto-detect scanning mode: Use Resource Groups API when tags are provided OR --all-services is used
        use_resource_groups_api = should_use_resource_groups_api(
            tag_key, tag_value, all_services
        )

        if use_resource_groups_api:
            # Use Resource Groups API for cross-service scanning (when tags provided or --all-services)
            from aws_resource_inventory.lib.scan import scan_all_services_with_tags

            future_to_region = {
                executor.submit(
                    scan_all_services_with_tags,
                    session,
                    region,
                    tag_key,
                    tag_value,
                    use_cache,
                ): region
                for region in region_list
            }
        else:
            # Use traditional service-specific scanning (when no tags provided)
            future_to_region = {
                executor.submit(
                    scan_region,
                    session,
                    region,
                    services,
                    tag_key,
                    tag_value,
                    service_workers,
                    use_cache,
                    create_progress_callback(region),
                    shutdown_event,  # Pass shutdown event to scan_region
                ): region
                for region in region_list
            }

        # Collect results as they complete
        total_scan_time = 0.0
        completed_futures = 0

        try:
            for future in as_completed(future_to_region):
                # Check for shutdown request before processing each future
                if shutdown_event and shutdown_event.is_set():
                    logger.warning("Cancelling remaining region scans")
                    # Cancel all pending futures
                    for pending_future in future_to_region:
                        if not pending_future.done():
                            pending_future.cancel()
                    break

                region = future_to_region[future]
                completed_futures += 1

                try:
                    region_name, region_results, scan_duration, region_errors = (
                        future.result(timeout=300)
                    )
                    scan_errors.extend(region_errors)
                    total_scan_time += scan_duration
                    if region_results:
                        all_results[region_name] = region_results
                        if progress and main_task is not None:
                            # Update the region task to show completion
                            if region_name in region_tasks:
                                progress.update(
                                    region_tasks[region_name],
                                    description=f"  ✅ {region_name}",
                                    completed=progress.tasks[
                                        region_tasks[region_name]
                                    ].total,
                                )
                            logger.debug("Completed scanning region %s", region_name)
                    else:
                        if progress and main_task is not None:
                            # Update the region task to show no resources
                            if region_name in region_tasks:
                                progress.update(
                                    region_tasks[region_name],
                                    description=f"  ⚪ {region_name} (no resources)",
                                    completed=progress.tasks[
                                        region_tasks[region_name]
                                    ].total,
                                )
                            logger.info("No resources found in region %s", region_name)
                # One region failing must not kill the other regions —
                # and must not vanish: it becomes ScanError data for the
                # envelope (ADR-0010).
                except Exception as e:  # noqa: BLE001
                    scan_errors.append(
                        ScanError(region=region, service=None, message=str(e))
                    )
                    if progress and main_task is not None and region in region_tasks:
                        # Update the region task to show error
                        progress.update(
                            region_tasks[region],
                            description=f"  ❌ {region}",
                            completed=progress.tasks[region_tasks[region]].total,
                        )
                    logger.error("Failed to scan region %s: %s", region, str(e))
                finally:
                    if progress and main_task is not None:
                        progress.advance(main_task)

        except KeyboardInterrupt:
            # Handle direct KeyboardInterrupt during scanning
            logger.warning("Keyboard interrupt received - cancelling scans")
            if shutdown_event:
                shutdown_event.set()
            # Cancel all remaining futures
            for pending_future in future_to_region:
                if not pending_future.done():
                    pending_future.cancel()
            raise

    return all_results, scan_errors
