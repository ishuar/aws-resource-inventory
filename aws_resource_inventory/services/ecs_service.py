"""
ECS Service Scanner
------------------

Scans ECS resources: clusters, services, task definitions, and capacity
providers — a dependent traversal (services hang off clusters; task
definitions are capped at the latest two revisions per family).
Tag-based filtering is handled centrally by the Resource Groups Tagging API.
? Documentation: https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/ecs.html
"""

from functools import partial
from typing import Any, cast

from botocore.exceptions import BotoCoreError, ClientError

from aws_resource_inventory.lib.arn import extract_resource_id_from_arn
from aws_resource_inventory.lib.clients import get_scan_client
from aws_resource_inventory.lib.engine import (
    ResourceList,
    ScanResult,
    collect_pages,
    finish,
    map_parallel,
)
from aws_resource_inventory.lib.logging import get_logger
from aws_resource_inventory.lib.records import (
    CallerIdentity,
    Resource,
    name_from_tags,
)

logger = get_logger()

ECS_BATCH_SIZE = 10  # describe_services accepts up to 10 services per call
ECS_TASK_DEF_WORKERS = 4  # parallel task-definition lookups (throttle-friendly)
# Only the latest 2 revisions per task-definition family are kept
# (current + previous, for rollback visibility).
ECS_REVISIONS_PER_FAMILY = 2


def _tags_of(ecs_client: Any, arn: str) -> ResourceList:
    try:
        return list(ecs_client.list_tags_for_resource(resourceArn=arn).get("tags", []))
    except ClientError:
        return []


def _clusters(ecs_client: Any) -> ResourceList:
    clusters: ResourceList = []
    arns = collect_pages(ecs_client, "list_clusters", "clusterArns")
    for start in range(0, len(arns), 100):  # describe_clusters caps at 100
        clusters.extend(
            ecs_client.describe_clusters(clusters=arns[start : start + 100]).get(
                "clusters", []
            )
        )
    for cluster in clusters:
        cluster["tags"] = _tags_of(ecs_client, cluster["clusterArn"])
    return clusters


def _services_of(ecs_client: Any, cluster: dict[str, Any]) -> ResourceList:
    cluster_name = cluster["clusterName"]
    service_arns = collect_pages(
        ecs_client, "list_services", "serviceArns", cluster=cluster_name
    )
    services: ResourceList = []
    for start in range(0, len(service_arns), ECS_BATCH_SIZE):
        batch = service_arns[start : start + ECS_BATCH_SIZE]
        for service in ecs_client.describe_services(
            cluster=cluster_name, services=batch
        ).get("services", []):
            service["clusterName"] = cluster_name
            service["tags"] = _tags_of(ecs_client, service.get("serviceArn", ""))
            services.append(service)
    return services


def _latest_task_definition_arns(ecs_client: Any, family: str) -> list[str]:
    # list_task_definitions pages hold plain ARN strings, not dicts.
    arns = cast(
        list[str],
        collect_pages(
            ecs_client,
            "list_task_definitions",
            "taskDefinitionArns",
            familyPrefix=family,
            status="ACTIVE",
            sort="DESC",  # newest first
        ),
    )
    return arns[:ECS_REVISIONS_PER_FAMILY]


def _described_task_definition(
    ecs_client: Any, task_def_arn: str
) -> dict[str, Any] | None:
    task_def: dict[str, Any] | None = ecs_client.describe_task_definition(
        taskDefinition=task_def_arn
    ).get("taskDefinition")
    if not task_def:
        return None
    task_def["tags"] = _tags_of(ecs_client, task_def["taskDefinitionArn"])
    return task_def


def scan_ecs(session: Any, region: str) -> ScanResult:
    """Scan all ECS resources in the region (no tag filtering)."""
    ecs_client = get_scan_client(session, "ecs", region)
    result: ScanResult = {
        "clusters": [],
        "services": [],
        "task_definitions": [],
        "capacity_providers": [],
    }

    result["clusters"] = _clusters(ecs_client)

    service_groups = map_parallel(
        partial(_services_of, ecs_client), result["clusters"], max_workers=2
    )
    result["services"] = [svc for group in service_groups for svc in group]

    families = collect_pages(ecs_client, "list_task_definition_families", "families")
    arn_groups = map_parallel(
        partial(_latest_task_definition_arns, ecs_client),
        families,
        max_workers=ECS_TASK_DEF_WORKERS,
    )
    result["task_definitions"] = map_parallel(
        partial(_described_task_definition, ecs_client),
        [arn for group in arn_groups for arn in group],
        max_workers=ECS_TASK_DEF_WORKERS,
    )

    if result["clusters"]:
        try:
            # describe_capacity_providers has no paginator, and it
            # returns tags only when include asks for them — without
            # this a Name-tagged provider is nameless under `scan`
            # and named under `scan --tag-key`.
            result["capacity_providers"] = ecs_client.describe_capacity_providers(
                include=["TAGS"]
            ).get("capacityProviders", [])
        except (ClientError, BotoCoreError) as e:
            logger.warning("Could not list capacity providers: %s", e)

    return finish("ecs", region, result)


def _skip_missing_identity(resource_type: str, region: str) -> None:
    logger.warning("Skipping %s in %s: missing id or ARN", resource_type, region)


def process_ecs_output(
    service_data: dict[str, Any],
    region: str,
    flattened_resources: list[Resource],
    identity: CallerIdentity,
) -> None:
    """Process ECS scan results for output formatting.

    Every ECS resource's AWS name IS its resource_id, so a Name tag is
    the only thing that can add a name. Tags arrive under "tags" (ECS's
    lowercase key/value shape): the scanner attaches them from
    list_tags_for_resource for clusters, services and task definitions,
    and capacity providers return theirs inline.
    """
    # ECS Clusters
    for cluster in service_data.get("clusters", []):
        cluster_name = cluster.get("clusterName")
        cluster_arn = cluster.get("clusterArn")
        if not cluster_name or not cluster_arn:
            _skip_missing_identity("ecs:cluster", region)
            continue

        flattened_resources.append(
            Resource(
                region=region,
                resource_name=name_from_tags(cluster.get("tags"), cluster_name),
                resource_type="ecs:cluster",
                resource_id=cluster_name,
                resource_arn=cluster_arn,
                arn_source="observed",
            )
        )

    # ECS Services
    for service in service_data.get("services", []):
        service_arn = service.get("serviceArn")
        # The id is the ARN's cluster/service path, read by the one
        # shared extractor: serviceName alone repeats across clusters.
        service_id = (
            extract_resource_id_from_arn(service_arn, "ecs:service")
            if service_arn
            else None
        )
        if not service_arn or not service_id:
            _skip_missing_identity("ecs:service", region)
            continue

        flattened_resources.append(
            Resource(
                region=region,
                resource_name=name_from_tags(service.get("tags"), service_id),
                resource_type="ecs:service",
                resource_id=service_id,
                resource_arn=service_arn,
                arn_source="observed",
            )
        )

    # ECS Task Definitions
    for task_def in service_data.get("task_definitions", []):
        task_def_arn = task_def.get("taskDefinitionArn")
        if not task_def_arn:
            _skip_missing_identity("ecs:task-definition", region)
            continue
        task_def_name = task_def_arn.split("/")[-1]

        flattened_resources.append(
            Resource(
                region=region,
                resource_name=name_from_tags(task_def.get("tags"), task_def_name),
                resource_type="ecs:task-definition",  # Unified format: service:type
                resource_id=task_def_name,
                resource_arn=task_def_arn,
                arn_source="observed",
            )
        )

    # ECS Capacity Providers
    for cp in service_data.get("capacity_providers", []):
        cp_name = cp.get("name")
        cp_arn = cp.get("capacityProviderArn")
        if not cp_name or not cp_arn:
            _skip_missing_identity("ecs:capacity-provider", region)
            continue

        flattened_resources.append(
            Resource(
                region=region,
                resource_name=name_from_tags(cp.get("tags"), cp_name),
                resource_type="ecs:capacity-provider",  # Unified format: service:type
                resource_id=cp_name,
                resource_arn=cp_arn,
                arn_source="observed",
            )
        )
