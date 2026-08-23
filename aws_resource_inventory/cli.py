#!/usr/bin/env python3
"""
AWS Resource Inventory CLI
----------------------------

Command-line interface for scanning AWS resources across multiple services and regions.
This module contains all CLI logic separated from core business functionality.
"""

import os
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import typer
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table

import aws_resource_inventory.lib.outputs as outputs_module
import aws_resource_inventory.orchestrator as orchestrator_module
from aws_resource_inventory.lib.envelope import ScanFilters

# Import logging configuration
from aws_resource_inventory.lib.logging import (
    DEFAULT_DEBUG_LOG_DIR,
    configure_logging,
    create_debug_log_file,
    get_output_console,
)
from aws_resource_inventory.lib.outputs import (
    TABLE_MINIMUM_WIDTH,
    output_results,
)

# Import core scanning functionality
from aws_resource_inventory.lib.resource_groups_utils import (
    should_use_resource_groups_api,
)

# Import core business logic functions
from aws_resource_inventory.orchestrator import (
    check_and_display_cache_status,
    display_banner,
    display_region_summaries,
    get_session,
    perform_scan,
    validate_aws_credentials,
)

# Single source of truth for scannable services
from aws_resource_inventory.services.registry import SUPPORTED_SERVICES

# Global AWS profile (module-level constant)
aws_profile = os.environ.get("AWS_PROFILE", "default")


# Global shutdown flag for graceful exit
shutdown_requested = threading.Event()
# Track if shutdown message was already printed
shutdown_printed = threading.Event()

# Global context for log file (shared across commands)
app_log_file: Path | None = DEFAULT_DEBUG_LOG_DIR

# Global context for verbose logging (shared across commands)
app_verbose: bool = False

# Add the script's directory to the Python path to find modules
script_dir = Path(__file__).parent.absolute()
if str(script_dir) not in sys.path:
    sys.path.insert(0, str(script_dir))

# Create the CLI application
app = typer.Typer(
    name="aws-inventory",
    help="AWS Resource Inventory\n\nScan multiple AWS services across regions with optional tag filtering\n\nUse the 'scan' command to start scanning AWS resources.",
    add_completion=True,
)
console = Console()


@app.callback()
def main(
    log_file: Path | None = typer.Option(
        None,
        "--log-file",
        "-l",
        help="Global log file path for debug output (applies to all commands with --debug argument)",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Enable verbose AWS API call tracing (use with --debug for detailed boto3/botocore logging)",
    ),
) -> None:
    """
    AWS Resource Inventory
    A comprehensive tool for scanning AWS resources across multiple services and regions
    with advanced filtering, caching, and output capabilities.
    Features:
    • Multi-service scanning (EC2, S3, ECS, EFS, ELB, VPC, RDS, Auto Scaling)
    • Cross-region resource discovery
    • Tag-based filtering with Resource Groups API
    • Intelligent caching system (10-minute TTL)
    • Terminal table plus a JSON results document (--output - for stdout)
    • Real-time progress tracking
    • Graceful interrupt handling
    • AWS credential validation

    Use the 'scan' command to start scanning AWS resources.
    """
    global app_log_file, app_verbose
    app_log_file = log_file
    app_verbose = verbose


def complete_service_name(incomplete: str) -> list[str]:
    """Tab-completion values for --service: the registered service names."""
    return [name for name in SUPPORTED_SERVICES if name.startswith(incomplete)]


@app.command(name="scan")
def scan_command(
    regions: str | None = typer.Option(
        None, "--regions", "-r", help="Comma-separated AWS regions to scan"
    ),
    services: list[str] = typer.Option(
        SUPPORTED_SERVICES,
        "--service",
        "-s",
        help="AWS services to scan",
        autocompletion=complete_service_name,
    ),
    profile: str | None = typer.Option(
        aws_profile, "--profile", "-p", help="AWS profile to use"
    ),
    tag_key: str | None = typer.Option(None, "--tag-key", help="Filter by tag key"),
    tag_value: str | None = typer.Option(
        None, "--tag-value", help="Filter by tag value"
    ),
    output_file: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help=(
            "Path for the JSON results document. '-' writes it to stdout and "
            "suppresses all other output (pipes cleanly into jq). "
            "If not provided, a dynamic name will be generated."
        ),
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show what would be scanned without executing"
    ),
    max_workers: int = typer.Option(
        8,
        "--max-workers",
        "-w",
        help="Maximum number of parallel workers for region scanning (1-20)",
    ),
    service_workers: int = typer.Option(
        4,
        "--service-workers",
        help="Maximum number of parallel workers for service scanning within each region (1-10)",
    ),
    use_cache: bool = typer.Option(
        True,
        "--cache/--no-cache",
        help="Enable/disable caching of scan results (TTL: 10 minutes)",
    ),
    refresh: bool = typer.Option(
        False,
        "--refresh/--no-refresh",
        help="Enable continuous refresh mode to update results periodically",
    ),
    refresh_interval: int = typer.Option(
        10,
        "--refresh-interval",
        "-i",
        help="Refresh interval in seconds (default: 10, min: 5, max: 300)",
    ),
    all_services: bool = typer.Option(
        False,
        "--all-services",
        help="Use Resource Groups API to scan ALL AWS services (requires tags). Discovers 100+ services beyond EC2/VPC/S3/etc.",
    ),
    debug: bool = typer.Option(
        False,
        "--debug",
        "-d",
        help="Enable debug mode with verbose logging and detailed execution traces",
    ),
) -> None:
    """
    AWS Resource Inventory

    Scan multiple AWS services across regions with optional tag filtering. Use the 'scan' command to start scanning AWS resources.
    """

    # --output -: stdout carries only the JSON document. This must be
    # decided before logging is configured — configure_logging emits the
    # debug banner through the console it builds, so a console chosen
    # afterwards is already too late to keep the pipe clean.
    # A dry run produces no document — its whole point is the printed
    # plan — so it stays loud on stdout.
    to_stdout = output_file is not None and str(output_file) == "-"
    stdout_is_the_document = to_stdout and not dry_run

    # Configure AWS scanner logging system
    debug_log_file = create_debug_log_file(app_log_file) if debug else None
    logger = configure_logging(
        debug=debug,
        log_file=debug_log_file,
        verbose=app_verbose,
        log_to_stderr=stdout_is_the_document,
    )

    if stdout_is_the_document:
        _silence_decorative_output()

    if debug:
        logger.info("Debug mode enabled - verbose logging activated")
        if debug_log_file:
            logger.info("Debug logs will be saved to: %s", debug_log_file)

    # Log command parameters in debug mode
    if debug:
        with logger.timer("Parameter validation and setup"):
            logger.debug("CLI Parameters:")
            logger.debug("  • regions: %s", regions)
            logger.debug("  • services: %s", services)
            logger.debug("  • profile: %s", profile)
            logger.debug("  • tag_key: %s", tag_key)
            logger.debug("  • tag_value: %s", tag_value)
            logger.debug("  • output_file: %s", output_file)
            logger.debug("  • max_workers: %s", max_workers)
            logger.debug("  • service_workers: %s", service_workers)
            logger.debug("  • use_cache: %s", use_cache)
            logger.debug("  • refresh: %s", refresh)
            logger.debug("  • all_services: %s", all_services)

    # Validate worker counts
    max_workers = max(1, min(max_workers, 20))  # Limit between 1-20
    service_workers = max(1, min(service_workers, 10))  # Limit between 1-10

    if debug:
        logger.debug(
            "Worker counts validated - max_workers: %d, service_workers: %d",
            max_workers,
            service_workers,
        )

    # Validate all_services mode requirements
    if all_services:
        logger.debug("All-services mode requested, validating requirements")
        if not tag_key and not tag_value:
            logger.error("All-services mode validation failed: missing tag filters")
            console.print(
                "[red]❌ Error: --all-services mode requires either --tag-key or --tag-value[/red]"
            )
            console.print(
                "[dim]The Resource Groups API requires tag filtering to discover resources[/dim]"
            )
            raise typer.Exit(1)
        logger.info("All-services mode validated successfully")
        console.print(
            "[bold cyan]🌐 Cross-Service Mode: Scanning ALL AWS services (100+ services)[/bold cyan]"
        )

    # Validate refresh parameters
    original_refresh_interval = refresh_interval
    refresh_interval = max(5, min(refresh_interval, 300))  # Limit between 5-300 seconds
    if debug and original_refresh_interval != refresh_interval:
        logger.debug(
            "Refresh interval adjusted from %d to %d seconds",
            original_refresh_interval,
            refresh_interval,
        )

    # Refresh mode validation
    if refresh and dry_run:
        logger.error("Invalid combination: refresh mode with dry run")
        console.print(
            "[red]❌ Cannot use refresh mode with dry run. Please choose one.[/red]"
        )
        raise typer.Exit(1)

    # Disable cache when refresh mode is enabled to ensure fresh data
    if refresh:
        logger.debug("Refresh mode enabled - disabling cache for fresh data")
        use_cache = False

    # Display banner
    display_banner(debug)

    # Handle regions first: the configuration panel reports the parallelism
    # the scan will actually use, which depends on the resolved region list.
    region_list = _handle_regions(regions)

    # Create configuration panel
    _display_configuration_panel(
        all_services,
        tag_key,
        tag_value,
        services,
        region_list,
        max_workers,
        service_workers,
        use_cache,
        refresh,
        refresh_interval,
        aws_profile,
        debug,
    )

    # Display regions panel
    _display_regions_panel(region_list, debug)

    # Check and display cache status prominently if caching is enabled
    if use_cache:
        has_cache_hits = check_and_display_cache_status(
            region_list, services, tag_key, tag_value, use_cache, all_services
        )
        if has_cache_hits:
            console.print(
                "[dim]Results will be a combination of cached and real-time data.[/dim]"
            )

    # Get AWS session
    try:
        if debug:
            with logger.timer("AWS session creation"):
                logger.debug("Creating AWS session with profile: %s", profile)
                session = get_session(profile)
        else:
            session = get_session(profile)
    except RuntimeError as e:
        logger.log_error_context(
            e, {"profile": profile, "operation": "AWS session creation"}
        )
        console.print(f"\n[red]AWS Session Error: {e}[/red]")
        console.print(
            "\n[yellow]💡 Please check your AWS configuration and try again.[/yellow]"
        )
        raise typer.Exit(1) from None

    # Validate credentials outside the try above: typer.Exit subclasses
    # RuntimeError and must not be swallowed by the session error handler.
    if debug:
        with logger.timer("AWS credential validation"):
            logger.debug("Validating AWS credentials")
            credentials_valid, credential_message, caller_identity = (
                validate_aws_credentials(session, profile)
            )
    else:
        credentials_valid, credential_message, caller_identity = (
            validate_aws_credentials(session, profile)
        )

    if not credentials_valid or caller_identity is None:
        logger.error("AWS credential validation failed: %s", credential_message)
        console.print(f"\n[red]{credential_message}[/red]")
        console.print("\n[yellow]💡 Possible solutions:[/yellow]")
        console.print("   • [dim]Configure AWS credentials: aws configure[/dim]")
        console.print(
            "   • [dim]Set AWS profile: export AWS_PROFILE=your-profile[/dim]"
        )
        console.print(
            "   • [dim]Use AWS SSO: aws sso login --profile your-profile[/dim]"
        )
        console.print(
            "   • [dim]Check existing profiles: aws configure list-profiles[/dim]"
        )
        # Every resource identity is anchored to the caller's account and
        # partition (constructed ARNs need both), so output cannot be
        # produced without valid credentials. The old "show cached results
        # anyway" fallback is gone for the same reason.
        raise typer.Exit(1)

    logger.info("AWS credentials validated successfully")
    console.print(f"\n[green]{credential_message}[/green]")

    # Handle dry run
    if dry_run:
        _handle_dry_run(
            region_list,
            services,
            tag_key,
            tag_value,
            max_workers,
            service_workers,
            use_cache,
            output_file,
        )
        return

    # Set up signal handler for graceful exit
    _setup_signal_handlers()

    # Initialize refresh mode variables
    scan_count = 0
    total_scan_time = 0.0

    # Main scanning loop (single scan or refresh mode)
    while True:
        scan_count += 1
        start_time = time.time()
        # Captured per scan iteration: in refresh mode every scan writes
        # its own envelope and must stamp its own start.
        started_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        if debug:
            logger.debug("Starting scan iteration #%d", scan_count)

        # Handle refresh mode display
        if refresh and scan_count > 1:
            if debug:
                logger.debug("Refresh mode - clearing screen and updating display")
            console.clear()
            display_banner(debug)
            console.print(f"[bold cyan]Refresh Mode - Scan #{scan_count}[/bold cyan]")
            console.print(
                f"[dim]Last updated: {time.strftime('%Y-%m-%d %H:%M:%S')}[/dim]\n"
            )

        # Display scan start message
        _display_scan_start_message(
            refresh, scan_count, all_services, tag_key, tag_value, services, region_list
        )

        # Check for shutdown before starting scan
        if shutdown_requested.is_set():
            logger.info("Shutdown requested - cancelling scan before start")
            console.print("[yellow]Scan cancelled before starting[/yellow]")
            break
        if debug:
            logger.debug("Starting scan execution")
            logger.debug(
                "Scan parameters: regions=%d, services=%d, workers=%d/%d",
                len(region_list),
                len(services),
                max_workers,
                service_workers,
            )

        # Create a clean progress display that doesn't interfere with debug logging
        progress_console = get_output_console()

        # Create Progress with Panel for clean bordered display
        progress_display = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            console=progress_console,
            refresh_per_second=10,
        )

        # Use Live to properly contain the progress in a panel
        with Live(
            Panel(
                progress_display,
                title=f"[bold white]Scanning Progress (Scan #{scan_count})[/bold white]",
                border_style="bright_blue" if not debug else "green",
                padding=(0, 1),
            ),
            console=progress_console,
            refresh_per_second=10,
        ):
            progress = progress_display

            # Temporarily disable console logging during Live display to prevent interference
            logger.disable_console_output(debug_log_file)

            try:
                if debug:
                    with logger.timer(f"Core scan execution (iteration #{scan_count})"):
                        all_results = perform_scan(
                            session,
                            region_list,
                            services,
                            tag_key,
                            tag_value,
                            max_workers,
                            service_workers,
                            use_cache,
                            progress,
                            all_services,
                            shutdown_requested,
                        )
                else:
                    all_results = perform_scan(
                        session,
                        region_list,
                        services,
                        tag_key,
                        tag_value,
                        max_workers,
                        service_workers,
                        use_cache,
                        progress,
                        all_services,
                        shutdown_requested,
                    )
            finally:
                # Re-enable console logging after Live display completes
                logger.enable_console_output(debug_log_file)

        scan_duration = time.time() - start_time
        total_scan_time += scan_duration

        if debug:
            total_resources = sum(
                len(results) for results in all_results.values() if results
            )
            logger.info(
                "Scan #%d completed: %d resources in %.2fs",
                scan_count,
                total_resources,
                scan_duration,
            )
            avg_time = total_scan_time / scan_count
            logger.debug(
                "Performance metrics: avg=%.2fs, total=%.2fs", avg_time, total_scan_time
            )

        # Check for shutdown after scan completes
        if shutdown_requested.is_set():
            logger.info("Shutdown requested - scan interrupted")
            console.print("[yellow]Scan interrupted during execution[/yellow]")
            if scan_count == 1:
                console.print(
                    f"[dim]Partial scan completed in {scan_duration:.1f}s[/dim]"
                )
            break

        # Handle scan results. A scan that found nothing still writes the
        # envelope: it records who scanned what, when and with which
        # filters (ADR-0005), and that evidence is worth as much as a
        # non-empty result. Only the region summaries need resources.
        if not all_results:
            console.print(
                "[yellow]⚪ No resources found matching the criteria.[/yellow]"
            )
        else:
            display_region_summaries(all_results, debug)

        # Stdout mode never touches the disk, so no filename exists
        # to generate.
        current_output_file = (
            None
            if to_stdout
            else _generate_output_filename(
                output_file, tag_key, tag_value, region_list, services
            )
        )

        # Check for shutdown before processing results
        if shutdown_requested.is_set():
            console.print(
                "[yellow]Skipping output generation due to shutdown request[/yellow]"
            )
            break

        # Output results
        if not refresh or scan_count == 1:
            console.print("\n[bold green]Generating output...[/bold green]")

        # The caller knows which scan path produced the results:
        # tags or --all-services switch to the Resource Groups path.
        scan_source: Literal["services", "tagging"] = (
            "tagging"
            if should_use_resource_groups_api(tag_key, tag_value, all_services)
            else "services"
        )
        scan_filters = ScanFilters(
            services=None if scan_source == "tagging" else list(services),
            tag_key=tag_key,
            tag_value=tag_value,
            all_services=all_services,
        )
        resource_count = output_results(
            all_results,
            current_output_file,
            debug,
            identity=caller_identity,
            source=scan_source,
            regions=region_list,
            filters=scan_filters,
            started_at=started_at,
            duration_seconds=round(scan_duration, 1),
        )

        # Show scan completion status
        _display_scan_completion(
            refresh, scan_count, resource_count, len(region_list), scan_duration
        )

        # Exit if not in refresh mode
        if not refresh:
            break

        # Handle refresh mode continuation
        if not _handle_refresh_continuation(
            refresh, scan_count, total_scan_time, refresh_interval
        ):
            break


def _silence_decorative_output() -> None:
    """--output -: stdout belongs to the JSON document alone.

    Every decorative rich console goes quiet — banner, panels, table, and
    the stderr progress display, which would otherwise redraw over the
    log lines now sharing stderr. Logging itself is not touched: it was
    pointed at stderr when it was configured, so diagnostics survive.
    Exit codes are unaffected.
    """
    for decorative_console in (
        console,
        orchestrator_module.console,
        outputs_module.console,
        get_output_console(),
    ):
        decorative_console.quiet = True


def _display_configuration_panel(
    all_services: bool,
    tag_key: str | None,
    tag_value: str | None,
    services: list[str],
    region_list: list[str],
    max_workers: int,
    service_workers: int,
    use_cache: bool,
    refresh: bool,
    refresh_interval: int,
    aws_profile: str,
    debug: bool,
) -> None:
    """Display the configuration panel."""
    # Create an elegant configuration panel with centered title
    config_table = Table(show_header=False, box=None, min_width=TABLE_MINIMUM_WIDTH)
    config_table.add_column("Parameter", style="cyan", width=18, highlight=True)
    config_table.add_column("Value", style="yellow", width=60, highlight=True)

    # Auto-detect scanning mode for display
    use_resource_groups_api = should_use_resource_groups_api(
        tag_key, tag_value, all_services
    )

    if use_resource_groups_api:
        if all_services:
            config_table.add_row("Mode", "All AWS Services (100+ services)")
            config_table.add_row("Discovery", "Resource Groups Tagging API")
        else:
            config_table.add_row("Mode", "Cross-Service (Tag-based)")
            config_table.add_row("Discovery", "Resource Groups Tagging API")
    else:
        config_table.add_row("Mode", "Service-Specific")
        config_table.add_row("Services", f"{', '.join(services)}")

    # Show the parallelism the scan will actually use: pools are bounded by
    # the real work (orchestrator.py region pool, scan.py service pool), and
    # the Resource Groups API path fans out per region only.
    region_workers = min(len(region_list), max_workers)
    regions_label = f"{region_workers} region{'s' if region_workers != 1 else ''}"
    if use_resource_groups_api:
        config_table.add_row("Workers", f"{regions_label} in parallel")
    else:
        service_count = min(len(services), service_workers)
        services_label = f"{service_count} service{'s' if service_count != 1 else ''}"
        config_table.add_row(
            "Workers", f"{regions_label} × {services_label} in parallel"
        )
    config_table.add_row("Caching", "Enabled" if use_cache else "Disabled")

    if refresh:
        config_table.add_row("Refresh", f"Every {refresh_interval}s")

    if tag_key and tag_value:
        config_table.add_row("Tag Filter", f"{tag_key}={tag_value}")
    elif tag_key:
        config_table.add_row("Tag Filter", f"{tag_key}=*")

    config_table.add_row("AWS Profile 👤", aws_profile)

    # Center the title and create a more compact panel
    console.print(
        Panel(
            config_table,
            title="[bold white]Configuration[/bold white]",
            title_align="center",
            border_style="bright_blue" if not debug else "green",
            padding=(0, 1),
        )
    )


def _handle_regions(regions: str | None) -> list[str]:
    """Handle region processing and return region list."""
    # List of all AWS Europe and US regions
    default_europe_us_regions = [
        "eu-north-1",
        "eu-west-1",
        "eu-west-2",
        "eu-west-3",
        "eu-central-1",
        "us-east-1",
        "us-east-2",
        "us-west-1",
        "us-west-2",
    ]
    if not regions:
        region_list = default_europe_us_regions
        console.print(
            "[dim yellow]INFO: No regions specified. Scanning all Europe and US regions.\n[/dim yellow]"
        )
    else:
        region_list = [r.strip() for r in regions.split(",") if r.strip()]

    return region_list


def _display_regions_panel(region_list: list[str], debug: bool) -> None:
    """Display the regions panel."""
    # Create regions panel with more compact layout
    regions_display: str | Table
    if len(region_list) <= 4:
        # Show regions in a single row for small lists
        regions_display = "  ".join([f"{region}" for region in region_list])
    else:
        # Show regions in multiple columns for larger lists
        regions_table = Table(
            show_header=False, box=None, min_width=TABLE_MINIMUM_WIDTH
        )
        regions_table.add_column("", style="bold green", width=25)
        regions_table.add_column("", style="bold green", width=25)
        regions_table.add_column("", style="bold green", width=25)

        # Add regions in groups of 3
        for i in range(0, len(region_list), 3):
            row_regions = region_list[i : i + 3]
            row = [
                f"{row_regions[j]}" if j < len(row_regions) else "" for j in range(3)
            ]
            if len(row_regions) > 0:
                regions_table.add_row(
                    row[0],
                    row[1] if len(row) > 1 else "",
                    row[2] if len(row) > 2 else "",
                )

        regions_display = regions_table

    console.print(
        Panel(
            regions_display,
            title=f"[bold white]Target Regions:({len(region_list)})[/bold white]",
            title_align="center",
            border_style="bright_blue" if not debug else "green",
            padding=(0, 1),
        )
    )


def _handle_dry_run(
    region_list: list[str],
    services: list[str],
    tag_key: str | None,
    tag_value: str | None,
    max_workers: int,
    service_workers: int,
    use_cache: bool,
    output_file: Path | None,
) -> None:
    """Handle dry run display."""
    console.print(
        "\n[bold yellow]🔍 DRY RUN MODE - No actual scanning will be performed[/bold yellow]"
    )
    console.print("\n[bold blue]Scan Plan:[/bold blue]")
    console.print(f"  • [bold]Regions to scan:[/bold] {len(region_list)}")
    for i, region in enumerate(region_list, 1):
        console.print(f"    {i}. {region}")

    console.print(f"\n  • [bold]Services to scan per region:[/bold] {len(services)}")
    for i, service in enumerate(services, 1):
        console.print(f"    {i}. {service}")

    console.print(
        f"\n  • [bold]Total operations:[/bold] {len(region_list)} × {len(services)} = {len(region_list) * len(services)} service scans"
    )

    if tag_key and tag_value:
        console.print(f"  • [bold]Tag filtering:[/bold] {tag_key}={tag_value}")
    elif tag_key:
        console.print(f"  • [bold]Tag filtering:[/bold] {tag_key}=* (any value)")
    elif tag_value:
        console.print(f"  • [bold]Tag filtering:[/bold] *={tag_value} (any key)")
    else:
        console.print("  • [bold]Tag filtering:[/bold] None (all resources)")

    console.print(
        f"  • [bold]Parallel workers:[/bold] {max_workers} regions, {service_workers} services per region"
    )
    console.print(f"  • [bold]Caching:[/bold] {'Enabled' if use_cache else 'Disabled'}")

    if output_file and str(output_file) == "-":
        console.print("  • [bold]Output:[/bold] JSON to stdout")
    elif output_file:
        console.print(f"  • [bold]Output file:[/bold] {output_file}")
    else:
        console.print("  • [bold]Output file:[/bold] Auto-generated")

    console.print(
        "\n[bold green]✅ Dry run completed. Use without --dry-run to execute the actual scan.[/bold green]"
    )


def _setup_signal_handlers() -> None:
    """Set up signal handlers for graceful shutdown."""

    def signal_handler(signum: int, frame: Any) -> None:
        if not shutdown_requested.is_set():
            shutdown_requested.set()
            shutdown_printed.set()
            console.print("\n\n[yellow]Graceful shutdown requested...[/yellow]")
            console.print("[dim]Stopping ongoing operations and cleaning up...[/dim]")
        else:
            # If shutdown already requested, force exit on second Ctrl+C
            console.print(
                "\n[red]Force exit requested. Terminating immediately...[/red]"
            )
            sys.exit(1)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)


def _display_scan_start_message(
    refresh: bool,
    scan_count: int,
    all_services: bool,
    tag_key: str | None,
    tag_value: str | None,
    services: list[str],
    region_list: list[str],
) -> None:
    """Display the scan start message."""
    if refresh:
        console.print(
            f"[bold blue]🔄 Starting scan #{scan_count}{'...' if scan_count == 1 else ' (refresh mode)'}[/bold blue]"
        )
    else:
        # More descriptive message based on scan type
        use_resource_groups_api = should_use_resource_groups_api(
            tag_key, tag_value, all_services
        )
        if use_resource_groups_api:
            if all_services:
                console.print(
                    "\n[bold blue]🌐 Scanning ALL AWS services across regions...[/bold blue]"
                )
            else:
                console.print(
                    f"\n[bold blue]🏷️  Scanning tagged resources across {len(region_list)} region{'s' if len(region_list) > 1 else ''}...[/bold blue]"
                )
        else:
            console.print(
                f"\n[bold blue]🔧 Scanning {len(services)} service{'s' if len(services) > 1 else ''} across {len(region_list)} region{'s' if len(region_list) > 1 else ''}...[/bold blue]"
            )


def _generate_output_filename(
    output_file: Path | None,
    tag_key: str | None,
    tag_value: str | None,
    region_list: list[str],
    services: list[str],
) -> Path:
    """Generate output filename dynamically if not provided."""
    if output_file is None:
        parts = ["aws-resources"]
        if tag_key:
            parts.append(str(tag_key))
        if tag_value:
            parts.append(str(tag_value))
        # If only one region and one service, include them
        if len(region_list) == 1:
            parts.append(region_list[0])
        if len(services) == 1:
            parts.append(services[0])
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        filename = "-".join(parts) + f"-{timestamp}.json"
        current_output_file = Path(f"/tmp/aws_resource_inventory/{filename}")
    else:
        current_output_file = output_file

    return current_output_file


def _display_scan_completion(
    refresh: bool,
    scan_count: int,
    resource_count: int,
    region_count: int,
    scan_duration: float,
) -> None:
    """Display scan completion status.

    ``region_count`` is the regions asked for, not the regions that
    returned something: a scan of two regions that found nothing still
    scanned two regions.
    """
    plural = "s" if region_count != 1 else ""
    summary = (
        f"[green]📊 Found {resource_count} resources across "
        f"{region_count} region{plural} in {scan_duration:.1f}s[/green]"
    )
    if refresh:
        console.print(f"\n[bold green]🎉 Scan #{scan_count} completed![/bold green]")
        console.print(summary)
        console.print("[dim cyan]💡 Press Ctrl+C to stop refresh mode[/dim cyan]")
    else:
        console.print("\n[bold green]🎉 Scan completed successfully![/bold green]")
        console.print(summary)


def _handle_refresh_continuation(
    refresh: bool, scan_count: int, total_scan_time: float, refresh_interval: int
) -> bool:
    """Handle refresh mode continuation. Returns True to continue, False to break."""
    # Exit immediately if shutdown was requested
    if shutdown_requested.is_set():
        console.print(
            f"[yellow]Scan stopped after {scan_count} {'scan' if scan_count == 1 else 'scans'}[/yellow]"
        )
        console.print(f"[dim]Total runtime: {total_scan_time:.1f}s[/dim]")
        return False

    # Wait for next refresh or stop if requested
    if refresh and not shutdown_requested.is_set():
        console.print(f"[dim]Waiting {refresh_interval}s until next scan...[/dim]")

        # Interruptible sleep
        for _ in range(refresh_interval):
            time.sleep(1)
            if shutdown_requested.is_set():
                break

    # Exit if stop was requested during wait
    if shutdown_requested.is_set():
        console.print(f"[yellow]Refresh mode stopped after {scan_count} scans[/yellow]")
        console.print(f"[dim]Total runtime: {total_scan_time:.1f}s[/dim]")
        return False

    return True


if __name__ == "__main__":
    app()
