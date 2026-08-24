"""
Auto Scaling Service Scanner
---------------------------

Scans Auto Scaling groups, launch configurations, and launch templates,
with optional client-side tag filtering — the Resource Groups Tagging
API does not cover ASGs, so this scanner filters tags itself.
? Documentation: https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/autoscaling.html
"""

from typing import Any

from aws_resource_inventory.lib.clients import get_scan_client
from aws_resource_inventory.lib.engine import (
    ScanResult,
    collect_pages,
    finish,
    matches_tags,
    run_parallel,
)
from aws_resource_inventory.lib.logging import get_logger
from aws_resource_inventory.lib.records import (
    CallerIdentity,
    Resource,
    name_from_tags,
)

logger = get_logger()


def scan_autoscaling(
    session: Any,
    region: str,
    tag_key: str | None = None,
    tag_value: str | None = None,
) -> ScanResult:
    """Scan Auto Scaling resources, optionally filtered by tags."""
    autoscaling_client = get_scan_client(session, "autoscaling", region)
    ec2_client = get_scan_client(session, "ec2", region)

    def matching_asgs() -> list[dict[str, Any]]:
        return [
            asg
            for asg in collect_pages(
                autoscaling_client, "describe_auto_scaling_groups", "AutoScalingGroups"
            )
            if matches_tags(asg.get("Tags", []), tag_key, tag_value)
        ]

    def matching_launch_templates() -> list[dict[str, Any]]:
        return [
            template
            for template in collect_pages(
                ec2_client, "describe_launch_templates", "LaunchTemplates"
            )
            if matches_tags(template.get("Tags", []), tag_key, tag_value)
        ]

    result = run_parallel(
        {
            "auto_scaling_groups": matching_asgs,
            "launch_templates": matching_launch_templates,
        },
        service="autoscaling",
        region=region,
        max_workers=2,
    )

    # Dependent step: only launch configurations referenced by matching ASGs.
    lc_names = {
        asg["LaunchConfigurationName"]
        for asg in result["auto_scaling_groups"]
        if asg.get("LaunchConfigurationName")
    }

    def referenced_launch_configurations() -> list[dict[str, Any]]:
        return [
            lc
            for lc in collect_pages(
                autoscaling_client,
                "describe_launch_configurations",
                "LaunchConfigurations",
            )
            if lc["LaunchConfigurationName"] in lc_names
        ]

    if lc_names:
        result["launch_configurations"] = run_parallel(
            {"launch_configurations": referenced_launch_configurations},
            service="autoscaling",
            region=region,
            max_workers=1,
        )["launch_configurations"]
    else:
        result["launch_configurations"] = []

    return finish("autoscaling", region, result)


def process_autoscaling_output(
    service_data: dict[str, Any],
    region: str,
    flattened_resources: list[Resource],
    identity: CallerIdentity,
) -> None:
    """Process Auto Scaling scan results for output formatting."""
    # Auto Scaling Groups
    for asg in service_data.get("auto_scaling_groups", []):
        asg_name = asg.get("AutoScalingGroupName")
        asg_arn = asg.get("AutoScalingGroupARN")
        if not asg_name or not asg_arn:
            logger.warning(
                "Skipping autoscaling:autoScalingGroup in %s: missing name or ARN",
                region,
            )
            continue

        flattened_resources.append(
            Resource(
                # An ASG's AWS "name" IS its id, so only a Name tag can
                # add anything — and the usual Name tag mirrors the group
                # name, which name_from_tags drops back to None.
                region=region,
                resource_name=name_from_tags(asg.get("Tags"), asg_name),
                resource_type="autoscaling:autoScalingGroup",
                resource_id=asg_name,
                resource_arn=asg_arn,
                arn_source="observed",
            )
        )

    # Launch Configurations
    for lc in service_data.get("launch_configurations", []):
        lc_name = lc.get("LaunchConfigurationName")
        lc_arn = lc.get("LaunchConfigurationARN")
        if not lc_name or not lc_arn:
            logger.warning(
                "Skipping autoscaling:launchConfiguration in %s: missing name or ARN",
                region,
            )
            continue

        flattened_resources.append(
            Resource(
                # A launch configuration's AWS "name" IS its id and the
                # API returns no tags at all, so there is no name to have.
                region=region,
                resource_type="autoscaling:launchConfiguration",
                resource_id=lc_name,
                resource_arn=lc_arn,
                arn_source="observed",
            )
        )

    # Launch Templates
    for lt in service_data.get("launch_templates", []):
        lt_id = lt.get("LaunchTemplateId")
        if not lt_id:
            logger.warning(
                "Skipping autoscaling:launch-template in %s: missing id", region
            )
            continue
        flattened_resources.append(
            Resource(
                region=region,
                # Unlike groups and launch configurations, a launch
                # template's name is genuinely distinct from its lt- id.
                resource_name=lt.get("LaunchTemplateName"),
                resource_type="autoscaling:launch-template",
                resource_id=lt_id,
                # The API returns no launch template ARN; it is an EC2
                # resource, constructed per the documented format.
                resource_arn=f"arn:{identity.partition}:ec2:{region}:{identity.account}:launch-template/{lt_id}",
                arn_source="constructed",
            )
        )
