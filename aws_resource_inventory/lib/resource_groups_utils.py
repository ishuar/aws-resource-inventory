"""
Resource Groups Tagging API Utility
----------------------------------

Centralized utility for using AWS Resource Groups Tagging API to efficiently
find resources by tags across all AWS services. This provides server-side
filtering that dramatically improves performance compared to client-side filtering.
? Supported Services: https://docs.aws.amazon.com/ARG/latest/userguide/supported-resources.html
? Documentation: https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/resourcegroupstaggingapi.html

"""

from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from .clients import get_scan_client
from .logging import get_logger, get_output_console

# Service logger
logger = get_logger("resource_groups")

# Console for user output
output_console = get_output_console()


def get_all_tagged_resources_across_services(
    session: Any,
    region: str,
    tag_key: str | None = None,
    tag_value: str | None = None,
) -> dict[str, Any]:
    """
    Get ALL resources across ALL AWS services using Resource Groups Tagging API.

    This is the main function for service-agnostic resource discovery.
    It finds resources from ANY AWS service that supports tagging.

    Args:
        session: AWS session object
        region: AWS region name
        tag_key: Tag key to filter by
        tag_value: Tag value to filter by

    Returns:
        Dictionary organized by service with all discovered resources
    """
    if not tag_key and not tag_value:
        logger.debug("No tags specified - Resource Groups API requires tags")
        return {}

    try:
        logger.debug("Discovering resources in %s via Resource Groups API", region)

        # Use Resource Groups Tagging API WITHOUT ResourceTypeFilters to get ALL resources
        tagging_client = get_scan_client(session, "resourcegroupstaggingapi", region)

        tag_filters = []
        if tag_key and tag_value:
            tag_filters.append({"Key": tag_key, "Values": [tag_value]})
        elif tag_key:
            # If only tag_key provided, find all resources with that key (any value)
            tag_filters.append({"Key": tag_key})

        paginator = tagging_client.get_paginator("get_resources")
        page_iterator = paginator.paginate(
            TagFilters=tag_filters
            # NO ResourceTypeFilters - this gets ALL resource types across ALL services
        )

        # Organize resources by service
        service_resources: dict[str, Any] = {}
        total_resources = 0

        for page in page_iterator:
            for resource in page.get("ResourceTagMappingList", []):
                resource_arn = resource["ResourceARN"]
                tags = resource.get("Tags", [])

                # Extract service and resource type from ARN
                service_name, resource_type = _extract_service_and_type_from_arn(
                    resource_arn
                )

                if service_name:
                    if service_name not in service_resources:
                        service_resources[service_name] = {}

                    if resource_type not in service_resources[service_name]:
                        service_resources[service_name][resource_type] = []

                    # Create comprehensive resource object
                    resource_obj = {
                        "ResourceARN": resource_arn,
                        "ResourceId": _extract_resource_id_from_arn(
                            resource_arn, f"{service_name}:{resource_type}"
                        ),
                        "ResourceType": f"{service_name}:{resource_type}",
                        "Region": region,
                        "Tags": tags,
                        "Service": service_name,
                    }

                    service_resources[service_name][resource_type].append(resource_obj)
                    total_resources += 1

        # Per-region verbose logging removed - overall results shown by main scanner
        return service_resources

    except (ClientError, BotoCoreError) as e:
        logger.error("Resource Groups API error in %s: %s", region, str(e))
        logger.log_error_context(
            e, {"region": region, "operation": "resource_groups_api"}
        )
        return {}


def _extract_service_and_type_from_arn(arn: str) -> tuple[str, str]:
    """Extract service name and resource type from an AWS ARN."""
    try:
        # ARN format: arn:aws:service:region:account:resource-type/resource-id
        # or arn:aws:service:region:account:resource-type:resource-id
        parts = arn.split(":")
        if len(parts) >= 6:
            service = parts[2]
            resource_part = parts[5]

            # S3 bucket ARNs (arn:aws:s3:::name) carry no type segment —
            # the resource part IS the bucket name. Use the same
            # "s3:bucket" vocabulary as the per-service scanner.
            if service == "s3":
                return "s3", "bucket"

            # Handle different resource formats
            if "/" in resource_part:
                resource_type = resource_part.split("/")[0]
            else:
                resource_type = resource_part

            return service, resource_type
    except (IndexError, ValueError):
        pass

    return "", ""


def _extract_resource_id_from_arn(arn: str, resource_type: str) -> str | None:
    """Extract the resource ID from an AWS ARN based on resource type."""
    try:
        if resource_type in ["s3:bucket"]:
            # S3 buckets: arn:aws:s3:::bucket-name
            return arn.split(":::")[-1] if ":::" in arn else None
        elif resource_type.startswith("elasticloadbalancing:"):
            # ELB resources: arn:aws:elasticloadbalancing:region:account:loadbalancer/type/name/id
            # or arn:aws:elasticloadbalancing:region:account:targetgroup/name/id
            parts = arn.split("/")
            if len(parts) >= 2:
                if (
                    resource_type == "elasticloadbalancing:loadbalancer"
                    and len(parts) >= 4
                ):
                    return f"{parts[1]}/{parts[2]}/{parts[3]}"  # type/name/id
                elif (
                    resource_type == "elasticloadbalancing:targetgroup"
                    and len(parts) >= 3
                ):
                    return f"{parts[1]}/{parts[2]}"  # name/id
        elif "/" in arn:
            # Most resources: arn:aws:service:region:account:resource-type/resource-id
            return arn.split("/")[-1]
        else:
            # Some resources use colon separator
            return arn.split(":")[-1]
    except (IndexError, ValueError):
        pass

    return None


def should_use_resource_groups_api(
    tag_key: str | None,
    tag_value: str | None,
    all_services: bool = False,
) -> bool:
    """Whether this scan takes the Resource Groups Tagging API path.

    The single definition of that choice. It selects which scanner runs and,
    via ``output_results(source=...)``, how the results are flattened — those
    two must never be able to disagree, which is why the expression lives
    here rather than being restated at each call site. A new way of entering
    the tag path belongs in this function and nowhere else.
    """
    return bool(all_services or tag_key or tag_value)


# Sections of the hybrid tag-scan results that carry raw service-shaped
# dicts instead of Resource Groups API records: the Tagging API does not
# cover Auto Scaling, so scan_all_tagged_resources merges the dedicated
# scanner's output for it. Flattening must route these sections through
# their registered service processors.
SERVICE_SHAPED_SECTIONS = frozenset({"autoscaling"})


def scan_all_tagged_resources(
    session: Any,
    region: str,
    tag_key: str | None = None,
    tag_value: str | None = None,
) -> dict[str, Any]:
    """
    MAIN FUNCTION: Service-agnostic scanning using Resource Groups Tagging API.

    This function finds ALL resources across ALL AWS services that match the tag criteria,
    with special handling for Auto Scaling Groups which are not supported by Resource Groups API.
    It returns results in a format compatible with the existing output system.

    Args:
        session: AWS session object
        region: AWS region name
        tag_key: Tag key to filter by
        tag_value: Tag value to filter by

    Returns:
        Dictionary with service names as keys, organized for output compatibility
    """
    if not should_use_resource_groups_api(tag_key, tag_value):
        logger.debug("No tags specified for Resource Groups API scan")
        return {}

    # Import here to avoid circular imports
    from concurrent.futures import ThreadPoolExecutor

    from services.autoscaling_service import scan_autoscaling

    logger.debug("Starting hybrid scan: Resource Groups API + Auto Scaling")

    output_results: dict[str, Any] = {}

    try:
        # Use ThreadPoolExecutor to run Resource Groups API and Auto Scaling scans in parallel
        with ThreadPoolExecutor(max_workers=2) as executor:
            # Submit Resource Groups API scan
            rg_future = executor.submit(
                get_all_tagged_resources_across_services,
                session,
                region,
                tag_key,
                tag_value,
            )

            # Submit Auto Scaling scan with tag filtering
            asg_future = executor.submit(
                scan_autoscaling, session, region, tag_key, tag_value
            )

            # Collect Resource Groups API results
            try:
                all_resources = rg_future.result()
                logger.debug("Resource Groups API scan completed")

                # Convert Resource Groups results to output-compatible format
                for service_name, resource_types in all_resources.items():
                    if service_name not in output_results:
                        output_results[service_name] = {}

                    for resource_type, resources in resource_types.items():
                        # Use a consistent naming scheme for resource type keys
                        resource_key = (
                            f"{resource_type}s"
                            if not resource_type.endswith("s")
                            else resource_type
                        )
                        output_results[service_name][resource_key] = resources

            except (ClientError, BotoCoreError, OSError, RuntimeError) as e:
                logger.warning("Resource Groups API scan failed: %s", str(e))

            # Collect Auto Scaling results
            try:
                asg_results = asg_future.result()
                logger.debug("Auto Scaling scan completed")

                # Add Auto Scaling results to output if any resources found
                if asg_results and any(asg_results.values()):
                    if "autoscaling" not in output_results:
                        output_results["autoscaling"] = {}

                    # Add each resource type from Auto Scaling scan
                    for resource_type, resources in asg_results.items():
                        if resources:  # Only add non-empty resource lists
                            output_results["autoscaling"][resource_type] = resources

            except (ClientError, BotoCoreError, OSError, RuntimeError) as e:
                logger.warning("Auto Scaling tag-filtered scan failed: %s", str(e))

    except (ClientError, BotoCoreError, OSError, RuntimeError) as e:
        logger.error("Hybrid scan failed: %s", str(e))
        logger.log_error_context(
            e, {"region": region, "operation": "hybrid_tagged_scan"}
        )

    # Log summary of hybrid scan results
    total_services = len(output_results)
    total_resources = 0
    for service_data in output_results.values():
        for resource_list in service_data.values():
            if isinstance(resource_list, list):
                total_resources += len(resource_list)

    logger.debug(
        "Hybrid scan completed: %d services, %d total resources",
        total_services,
        total_resources,
    )

    return output_results
