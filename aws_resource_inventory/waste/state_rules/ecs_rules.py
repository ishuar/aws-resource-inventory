"""
ECS state rules: clusters and services.

ECS objects are free — the money is in what backs them (EC2 capacity,
or Fargate per running task). So these are cleanup rules first
(PRODUCT.md decision 9): an idle EC2-backed cluster is likely waste
(instances billing for no tasks), an idle Fargate-only or empty cluster
is review-grade clutter, and a scaled-to-zero service is review-grade
by design (scale-to-zero is often intentional).
"""

from collections.abc import Mapping
from typing import Any

from aws_resource_inventory.lib.arn import extract_resource_id_from_arn
from aws_resource_inventory.waste.config import WasteConfig
from aws_resource_inventory.waste.findings import Finding
from aws_resource_inventory.waste.state_rules.common import Index, identified


def _ec2_backed(cluster: dict[str, Any], scan_data: Mapping[str, Any]) -> bool:
    """Does EC2 capacity sit behind this cluster?

    Registered container instances prove it directly. Otherwise, an
    attached EC2 capacity provider whose Auto Scaling group still runs
    instances counts too: those instances bill even though they never
    registered — capacity no other rule sees (ec2-long-stopped only
    sees stopped instances).
    """
    if cluster.get("registeredContainerInstancesCount", 0) > 0:
        return True
    provider_names = set(cluster.get("capacityProviders", []))
    if not provider_names:
        return False
    instances_by_asg_arn = {
        asg.get("AutoScalingGroupARN"): asg.get("Instances", [])
        for asg in scan_data["autoscaling"]["auto_scaling_groups"]
    }
    for provider in scan_data["ecs"]["capacity_providers"]:
        if provider.get("name") not in provider_names:
            continue
        asg_arn = provider.get("autoScalingGroupProvider", {}).get(
            "autoScalingGroupArn"
        )
        if asg_arn and instances_by_asg_arn.get(asg_arn):
            return True
    return False


def ecs_cluster_idle(
    scan_data: Mapping[str, Any], index: Index, config: WasteConfig
) -> list[Finding]:
    """No task runs (or is starting) in the cluster.

    Likely when EC2-backed — that capacity bills for nothing. Review
    when Fargate-only or empty: idle Fargate costs nothing, so the
    finding is a clutter signal, and the evidence says which case this
    is (ec2_backed).
    """
    findings = []
    for cluster in scan_data["ecs"]["clusters"]:
        if cluster.get("runningTasksCount", 0) > 0:
            continue
        if cluster.get("pendingTasksCount", 0) > 0:
            continue
        resource = identified(index, "ecs:cluster", cluster.get("clusterName"))
        if resource is None:
            continue
        ec2_backed = _ec2_backed(cluster, scan_data)
        findings.append(
            Finding(
                resource=resource,
                rule="ecs-cluster-idle",
                confidence="likely" if ec2_backed else "review",
                evidence={
                    "runningTasksCount": cluster.get("runningTasksCount", 0),
                    "pendingTasksCount": cluster.get("pendingTasksCount", 0),
                    "activeServicesCount": cluster.get("activeServicesCount", 0),
                    "registeredContainerInstancesCount": cluster.get(
                        "registeredContainerInstancesCount", 0
                    ),
                    "capacityProviders": cluster.get("capacityProviders", []),
                    "ec2_backed": ec2_backed,
                },
                suggested_action="review",
            )
        )
    return findings


def ecs_service_zero_tasks(
    scan_data: Mapping[str, Any], index: Index, config: WasteConfig
) -> list[Finding]:
    """A service scaled to zero runs nothing — clutter signal only."""
    findings = []
    for service in scan_data["ecs"]["services"]:
        if service.get("desiredCount", 0) != 0:
            continue
        service_arn = service.get("serviceArn")
        service_id = (
            extract_resource_id_from_arn(service_arn, "ecs:service")
            if service_arn
            else None
        )
        resource = identified(index, "ecs:service", service_id)
        if resource is None:
            continue
        findings.append(
            Finding(
                resource=resource,
                rule="ecs-service-zero-tasks",
                confidence="review",
                evidence={
                    "desiredCount": service.get("desiredCount", 0),
                    "runningCount": service.get("runningCount", 0),
                },
                suggested_action="review",
            )
        )
    return findings
