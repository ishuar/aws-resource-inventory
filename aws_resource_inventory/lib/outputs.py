"""
Outputs module for AWS Scanner

Handles formatting and output of scan results in various formats (JSON, table, markdown).
"""

import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from rich.console import Console
from rich.table import Table

from aws_resource_inventory.services.registry import SERVICES

from .arn import extract_resource_id_from_arn
from .envelope import ScanFilters, build_envelope, tool_version
from .logging import get_logger
from .records import CallerIdentity, Resource, name_from_tags
from .resource_groups_utils import SERVICE_SHAPED_SECTIONS

logger = get_logger()
console = Console()
# Minimum width for tables to ensure readability
TABLE_MINIMUM_WIDTH = 86


def create_aws_resources_table(
    flattened_resources: list[Resource], debug: bool, identity: CallerIdentity
) -> Table:
    """
    Create a standardized AWS resources table with consistent formatting.

    Display only — the serialized envelope is the record of truth. No ARN
    column: an ARN is reconstructible from account + region + type + id,
    and the JSON file still carries it. The scan's account id heads the
    table so the id columns have their missing ARN segment in view.

    Args:
        flattened_resources: Resources to display
        debug: Debug mode switches the border colour
        identity: The scanning caller's account, shown in the table title

    Returns:
        Table: Rich Table object ready for display
    """
    table = Table(
        title=f"AWS Resources — Account {identity.account}",
        border_style="bright_blue" if not debug else "green",
        min_width=TABLE_MINIMUM_WIDTH,
    )
    table.add_column("Region", style="blue")
    table.add_column("Type", style="yellow")
    table.add_column("Name", style="cyan")
    table.add_column("ID", style="green")

    for resource in flattened_resources:
        table.add_row(
            resource.region,
            resource.resource_type,
            # Display-only fallback: the record's resource_name stays null.
            resource.resource_name or resource.resource_id,
            resource.resource_id,
        )

    return table


def ensure_output_directory(output_file: Path) -> None:
    """Ensure the output directory exists, create if it doesn't."""
    output_dir = output_file.parent
    if not output_dir.exists():
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
            console.print(f"[dim]Created output directory: {output_dir}[/dim]")
        except Exception as e:
            console.print(
                f"[red]Failed to create output directory {output_dir}: {e}[/red]"
            )
            raise


def generate_markdown_summary(
    flattened_resources: list[Resource], results: dict[str, Any]
) -> str:
    """Generate a markdown summary report from scan results."""
    md_content = []

    # Header
    md_content.append("# AWS Resources Scan Report")
    md_content.append(
        f"\n**Generated:** {datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S')}"
    )
    md_content.append(f"**Total Resources:** {len(flattened_resources)}")

    # Summary by region
    md_content.append("\n## Summary by Region")
    region_counts = Counter(r.region for r in flattened_resources)
    for region, count in sorted(region_counts.items()):
        md_content.append(f"- **{region}**: {count} resources")

    # Summary by service (extracted from resource_type)
    md_content.append("\n## Summary by Service")
    service_counts = Counter(r.service for r in flattened_resources)
    for service, count in sorted(service_counts.items()):
        md_content.append(f"- **{service.upper()}**: {count} resources")

    # Summary by resource type
    md_content.append("\n## Summary by Resource Type")
    type_counts = Counter(r.resource_type for r in flattened_resources)
    for resource_type, count in sorted(type_counts.items()):
        md_content.append(f"- **{resource_type}**: {count}")

    # Detailed breakdown by region and service
    md_content.append("\n## Detailed Resources")

    for region in sorted(region_counts.keys()):
        region_resources = [r for r in flattened_resources if r.region == region]
        if not region_resources:
            continue

        md_content.append(f"\n### {region}")

        # Group by service within region (extracted from resource_type)
        region_services: dict[str, list[Resource]] = {}
        for resource in region_resources:
            service = resource.service
            if service not in region_services:
                region_services[service] = []
            region_services[service].append(resource)

        for service in sorted(region_services.keys()):
            service_resources = region_services[service]
            md_content.append(
                f"\n#### {service.upper()} ({len(service_resources)} resources)"
            )

            md_content.append("| Resource Name | Type | ID | ARN |")
            md_content.append("|---------------|------|----|----|")

            for resource in sorted(
                service_resources,
                key=lambda x: x.resource_name or x.resource_id,
            ):
                name = (resource.resource_name or resource.resource_id).replace(
                    "|", "\\|"
                )  # Escape pipes
                resource_type = resource.resource_type.replace("|", "\\|")
                resource_id = resource.resource_id.replace("|", "\\|")
                arn = resource.resource_arn.replace("|", "\\|")

                # Format ID and ARN with code blocks for better readability
                # (every record carries a real id and ARN — "N/A" is gone).
                md_content.append(
                    f"| {name} | {resource_type} | `{resource_id}` | `{arn}` |"
                )

    # Add scan metadata
    md_content.append("\n## Scan Metadata")
    md_content.append("- **Tool**: AWS Resource Inventory")
    md_content.append("- **Version**: Modular Version with Advanced Optimizations")

    return "\n".join(md_content)


def process_generic_service_output(
    service_data: dict[str, Any],
    region: str,
    flattened_resources: list[Resource],
    identity: CallerIdentity,
) -> None:
    """
    Generic processor for cross-service resources discovered via Resource Groups API.

    This handles any AWS service that doesn't have a specific processor, ensuring
    all discovered resources are included in the unified output format. Every
    ARN here was returned by the Tagging API, so arn_source is "observed";
    a record without a usable ARN cannot be identified and is skipped with
    a log line — never emitted as "N/A".

    The Tagging API returns every resource's tags, so the Name tag is read
    with the same helper the per-service scanners use: a Name-tagged
    resource reports the same name whichever path found it.
    """
    for resource_type_key, resources in service_data.items():
        if not isinstance(resources, list):
            continue
        for resource in resources:
            if not isinstance(resource, dict):
                continue
            # Extract resource details from Resource Groups API format
            resource_arn = resource.get("ResourceARN")
            resource_type = resource.get("ResourceType", resource_type_key)
            resource_id = resource.get("ResourceId") or (
                extract_resource_id_from_arn(resource_arn, resource_type)
                if resource_arn
                else None
            )
            if not resource_arn or not resource_id:
                logger.warning(
                    "Skipping %s in %s: no usable ARN/id in %r",
                    resource_type,
                    region,
                    resource,
                )
                continue

            flattened_resources.append(
                Resource(
                    region=region,
                    resource_name=name_from_tags(resource.get("Tags"), resource_id),
                    # Already in service:type format from Resource Groups API
                    resource_type=resource_type,
                    resource_id=resource_id,
                    resource_arn=resource_arn,
                    arn_source="observed",
                )
            )


def output_results(
    results: dict[str, Any],
    output_file: Path,
    output_format: str,
    debug: bool,
    *,
    identity: CallerIdentity,
    source: Literal["services", "tagging"],
    regions: list[str],
    filters: ScanFilters,
    started_at: str,
    duration_seconds: float,
) -> int:
    """Process results using modular output processors and format for output.

    ``identity`` is the scanning caller's account + partition (from STS);
    processors need it to construct the ARNs the AWS APIs do not return.

    ``source`` states which scan path produced ``results`` — the caller
    always knows (it chose the path). "tagging" results are Resource
    Groups API shaped and go through the generic processor; "services"
    results route to each service's registered processor.

    ``regions``, ``filters``, ``started_at`` (UTC ISO-8601 with Z) and
    ``duration_seconds`` fill the envelope's scan block — the caller
    owns the clock and the resolved scan parameters.

    Returns:
        int: The total number of flattened resources found.
    """

    # Flatten results into a list of resources with the required columns
    flattened_resources: list[Resource] = []

    for region, services in results.items():
        for service_name, service_data in services.items():
            if not service_data:  # Skip empty services
                continue

            if source == "tagging" and service_name not in SERVICE_SHAPED_SECTIONS:
                # Resource Groups API data all shares one shape.
                process_generic_service_output(
                    service_data, region, flattened_resources, identity
                )
            else:
                registration = SERVICES.get(service_name)
                if registration is not None:
                    registration.process_output(
                        service_data, region, flattened_resources, identity
                    )
                else:
                    # Unknown service: fall back to the generic processor.
                    process_generic_service_output(
                        service_data, region, flattened_resources, identity
                    )

    # Ensure output directory exists before writing files
    ensure_output_directory(output_file)

    # Every serialized scan is the self-describing envelope document
    # (schema_version 1, ADR-0005) — never a bare resource array.
    envelope = build_envelope(
        flattened_resources,
        version=tool_version(),
        identity=identity,
        regions=regions,
        source=source,
        filters=filters,
        started_at=started_at,
        duration_seconds=duration_seconds,
    )
    serialized = json.dumps(envelope, indent=2)

    # Output in the requested format
    if output_format == "json":
        output_file.write_text(serialized)
        console.print(f"[green]Results saved to {output_file}[/green]")
        # Also print to console for immediate viewing
        console.print(serialized)
    elif output_format == "table":
        # Create and display the standardized table
        table = create_aws_resources_table(flattened_resources, debug, identity)
        console.print(table)

        output_file.write_text(serialized)
        console.print(f"[green]Data also saved to {output_file}[/green]")
    elif output_format in ("md", "markdown"):
        # Generate markdown summary report
        markdown_content = generate_markdown_summary(flattened_resources, results)

        # Change extension to .md for markdown files
        md_output_file = output_file.with_suffix(".md")
        # Ensure directory exists for markdown file (might have different path)
        ensure_output_directory(md_output_file)
        md_output_file.write_text(markdown_content)
        console.print(f"[green]Markdown report saved to {md_output_file}[/green]")

        # Display the table view in terminal as well
        console.print("\n[bold blue]Resource Table View:[/bold blue]")
        table = create_aws_resources_table(flattened_resources, debug, identity)
        console.print(table)

        # Also display a summary in console
        console.print("\n[bold blue]Markdown Summary Generated:[/bold blue]")
        console.print(f"Total resources: {len(flattened_resources)}")

        # Count by service (extracted from resource_type)
        service_counts = Counter(r.service for r in flattened_resources)
        for service, count in service_counts.items():
            console.print(f"  {service}: {count} resources")

    else:
        console.print(
            f"[red]Unknown output format '{output_format}'. Supported: json, table, md or markdown[/red]"
        )

    return len(flattened_resources)
