"""
Outputs module for AWS Scanner

Flattens scan results into Resource records, renders the terminal table,
and writes the JSON envelope (to a file, or to stdout with --output -).
"""

import json
import os
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


def ensure_output_directory(output_file: Path, *, ours: bool) -> None:
    """Ensure the output directory exists, create if it doesn't.

    ``ours`` states whether we chose the path — the caller always knows
    (it applied the default). It is declared rather than recovered by
    comparing against ``default_output_dir()``, because two spellings of
    one directory compare unequal, and the comparison decides whether
    the hardening runs at all.

    Our own directory is made owner-only; a directory the user named
    with ``--output`` gets the mode any other tool would give it, on
    creation as much as after. mkdir applies its mode only when it
    actually creates the directory, so the chmod is what holds the
    guarantee for ours (ADR-0009).
    """
    output_dir = output_file.parent
    if not output_dir.exists():
        try:
            # 0o777 is masked by umask into the usual 0o755: their
            # directory, their mode. Creating it does not make it ours.
            output_dir.mkdir(parents=True, exist_ok=True, mode=0o700 if ours else 0o777)
            console.print(f"[dim]Created output directory: {output_dir}[/dim]")
        except Exception as e:
            console.print(
                f"[red]Failed to create output directory {output_dir}: {e}[/red]"
            )
            raise
    if ours:
        try:
            output_dir.chmod(0o700)
        except OSError as e:
            # The scan has already run and the document is written 0600
            # regardless, so this is said out loud rather than raised —
            # losing finished results over it would be the worse bug.
            console.print(
                f"[yellow]Could not restrict {output_dir} to owner-only: {e}[/yellow]"
            )


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


def write_document(output_file: Path, serialized: str, *, ours: bool) -> None:
    """Write the JSON document, owner-only when we chose the path.

    ``ours`` is declared by the caller for the same reason it is in
    ``ensure_output_directory``: the directory and the document must
    agree about who owns the path, and a path comparison can disagree
    with itself across two spellings of one directory.

    A document at our default path gets 0o600 for the same reason cache
    entries do (ADR-0008): the parent directory is the outer guard, and
    a file left world-readable is exposed the moment it is copied or
    that guard is weakened. A path the user named with ``--output`` is
    written normally — forcing a mode on a file they asked for
    somewhere specific would be surprising, not secure.

    ``os.open`` with an explicit mode rather than write-then-chmod:
    umask can only clear permission bits, never set them, so the
    document is never briefly world-readable.
    """
    if not ours:
        output_file.write_text(serialized)
        return

    descriptor = os.open(output_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as document:
        document.write(serialized)


def output_results(
    results: dict[str, Any],
    output_file: Path | None,
    debug: bool,
    *,
    identity: CallerIdentity,
    source: Literal["services", "tagging"],
    output_is_ours: bool,
    regions: list[str],
    filters: ScanFilters,
    started_at: str,
    duration_seconds: float,
) -> int:
    """Flatten results, render the table, and write the JSON envelope.

    ``output_file`` is where the envelope document lands. ``None`` means
    stdout mode (``--output -``): the document is printed to stdout and
    nothing touches the disk — the table and every decorative console
    line are suppressed by the CLI so the output pipes cleanly into jq.

    ``identity`` is the scanning caller's account + partition (from STS);
    processors need it to construct the ARNs the AWS APIs do not return.

    ``output_is_ours`` states whether ``output_file`` is the path we
    generated rather than one the user named with ``--output`` — the
    caller always knows (it applied the default). Only a path we chose
    is hardened to owner-only.

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

    if output_file is None:
        # --output -: the document owns stdout. Plain print, not a rich
        # console (which could wrap or highlight), keeps it jq-clean.
        print(serialized)
    else:
        table = create_aws_resources_table(flattened_resources, debug, identity)
        console.print(table)

        ensure_output_directory(output_file, ours=output_is_ours)
        write_document(output_file, serialized, ours=output_is_ours)
        console.print(f"[green]Data also saved to {output_file}[/green]")

    return len(flattened_resources)
