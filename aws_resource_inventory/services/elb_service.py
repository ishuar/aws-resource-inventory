"""
ELB Service Scanner
------------------

Scans ELBv2 resources: load balancers, target groups, listeners, and
listener rules — a dependent traversal (listeners hang off load
balancers, rules off listeners), annotated so the output processors can
name each resource's parent.
?Documentation: https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/elbv2.html
"""

from functools import partial
from typing import Any

from botocore.exceptions import ClientError

from aws_resource_inventory.lib.arn import extract_resource_id_from_arn
from aws_resource_inventory.lib.clients import get_scan_client
from aws_resource_inventory.lib.engine import (
    ResourceList,
    ScanResult,
    collect_pages,
    finish,
    map_parallel,
    run_parallel,
)
from aws_resource_inventory.lib.logging import get_logger
from aws_resource_inventory.lib.records import CallerIdentity, Resource

logger = get_logger()

# Per-parent listener/rule lookups fan out on threads.
ELB_CHILD_WORKERS = 4


def _attach_tags(elbv2_client: Any, arn_field: str, resource: dict[str, Any]) -> None:
    arn = resource[arn_field]
    try:
        descriptions = elbv2_client.describe_tags(ResourceArns=[arn]).get(
            "TagDescriptions", []
        )
        resource["Tags"] = descriptions[0].get("Tags", []) if descriptions else []
    except ClientError as e:
        logger.warning("Could not get tags for %s: %s", arn, e)
        resource["Tags"] = []


def _listeners_of(elbv2_client: Any, lb: dict[str, Any]) -> ResourceList:
    listeners = collect_pages(
        elbv2_client,
        "describe_listeners",
        "Listeners",
        LoadBalancerArn=lb["LoadBalancerArn"],
    )
    for listener in listeners:
        listener["LoadBalancerArn"] = lb["LoadBalancerArn"]
        listener["LoadBalancerName"] = lb["LoadBalancerName"]
    return listeners


def _rules_of(elbv2_client: Any, listener: dict[str, Any]) -> ResourceList:
    rules = collect_pages(
        elbv2_client, "describe_rules", "Rules", ListenerArn=listener["ListenerArn"]
    )
    for rule in rules:
        rule["ListenerArn"] = listener["ListenerArn"]
        rule["LoadBalancerArn"] = listener["LoadBalancerArn"]
        rule["LoadBalancerName"] = listener["LoadBalancerName"]
    return rules


def scan_elb(session: Any, region: str) -> ScanResult:
    """Scan all ELBv2 resources in the region (no tag filtering)."""
    elbv2_client = get_scan_client(session, "elbv2", region)

    def load_balancers_with_tags() -> ResourceList:
        load_balancers = collect_pages(
            elbv2_client, "describe_load_balancers", "LoadBalancers"
        )
        for lb in load_balancers:
            _attach_tags(elbv2_client, "LoadBalancerArn", lb)
        return load_balancers

    def target_groups_with_tags() -> ResourceList:
        target_groups = collect_pages(
            elbv2_client, "describe_target_groups", "TargetGroups"
        )
        for tg in target_groups:
            _attach_tags(elbv2_client, "TargetGroupArn", tg)
        return target_groups

    result = run_parallel(
        {
            "load_balancers": load_balancers_with_tags,
            "target_groups": target_groups_with_tags,
        },
        service="elb",
        region=region,
        max_workers=2,
    )

    # Dependent traversal: listeners per load balancer, rules per listener.
    # A failing parent is skipped with a warning; the rest keep going.
    listener_groups = map_parallel(
        partial(_listeners_of, elbv2_client),
        result["load_balancers"],
        max_workers=ELB_CHILD_WORKERS,
    )
    result["listeners"] = [listener for group in listener_groups for listener in group]

    rule_groups = map_parallel(
        partial(_rules_of, elbv2_client),
        result["listeners"],
        max_workers=ELB_CHILD_WORKERS,
    )
    result["listener_rules"] = [rule for group in rule_groups for rule in group]

    return finish("elb", region, result)


def _extracted_id(arn: str | None, arn_resource_type: str, region: str) -> str | None:
    """The id AWS embeds in an ELBv2 ARN, or None (logged) if unusable.

    ELBv2 resources have no separate id field in the API — the ARN is
    the identity, and the id is its full path after the resource-type
    segment (e.g. "app/my-alb/<lb-id>/<listener-id>").
    """
    resource_id = extract_resource_id_from_arn(arn, arn_resource_type) if arn else None
    if not resource_id:
        logger.warning(
            "Skipping %s in %s: no usable ARN to extract an id from (%r)",
            arn_resource_type,
            region,
            arn,
        )
        return None
    return resource_id


def process_elb_output(
    service_data: dict[str, Any],
    region: str,
    flattened_resources: list[Resource],
    identity: CallerIdentity,
) -> None:
    """Process ELB scan results for output formatting."""
    # Load Balancers
    for lb in service_data.get("load_balancers", []):
        lb_arn = lb.get("LoadBalancerArn")
        lb_id = _extracted_id(lb_arn, "elasticloadbalancing:loadbalancer", region)
        if not lb_arn or not lb_id:
            continue
        lb_name = lb.get("LoadBalancerName", "N/A")
        # The flavour suffix is AWS's own Type attribute ("application" |
        # "network" | "gateway") — never an enum here, so a new AWS
        # flavour flows through unchanged.
        lb_type = lb.get("Type")

        flattened_resources.append(
            Resource(
                region=region,
                resource_name=lb_name,
                resource_type=(
                    f"elb:load_balancer_{lb_type}" if lb_type else "elb:load_balancer"
                ),
                resource_id=lb_id,
                resource_arn=lb_arn,
                arn_source="observed",
            )
        )

    # Listeners
    for listener in service_data.get("listeners", []):
        listener_arn = listener.get("ListenerArn")
        listener_id = _extracted_id(
            listener_arn, "elasticloadbalancing:listener", region
        )
        if not listener_arn or not listener_id:
            continue
        protocol = listener.get("Protocol", "N/A")
        port = listener.get("Port", "N/A")

        flattened_resources.append(
            Resource(
                region=region,
                resource_name=f"{protocol}:{port}",
                resource_type="elb:listener",
                resource_id=listener_id,
                resource_arn=listener_arn,
                arn_source="observed",
            )
        )

    # Listener Rules
    for rule in service_data.get("listener_rules", []):
        rule_arn = rule.get("RuleArn")
        rule_id = _extracted_id(rule_arn, "elasticloadbalancing:listener-rule", region)
        if not rule_arn or not rule_id:
            continue
        priority = rule.get("Priority", "N/A")

        flattened_resources.append(
            Resource(
                region=region,
                resource_name=f"Rule-{priority}",
                resource_type="elb:listener_rule",
                resource_id=rule_id,
                resource_arn=rule_arn,
                arn_source="observed",
            )
        )

    # Target Groups
    for tg in service_data.get("target_groups", []):
        tg_arn = tg.get("TargetGroupArn")
        tg_id = _extracted_id(tg_arn, "elasticloadbalancing:targetgroup", region)
        if not tg_arn or not tg_id:
            continue
        tg_name = tg.get("TargetGroupName", "N/A")

        flattened_resources.append(
            Resource(
                region=region,
                resource_name=tg_name,
                resource_type="elb:target_group",
                resource_id=tg_id,
                resource_arn=tg_arn,
                arn_source="observed",
            )
        )
