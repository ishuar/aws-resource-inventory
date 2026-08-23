"""
Resource shape seam: the flattened record every process_*_output emits.

This is the contract the planned typed-Resource refactor must preserve:
consumers (table, markdown, JSON, diff) read exactly these keys. The tests
pin the shape per producer, including today's quirks (ecs records omit
resource_name because their resource_id is already the friendly name; some
producers hardcode "N/A"), so any change to the shape is a deliberate
decision, not an accident.
"""

from collections.abc import Callable
from typing import Any

import pytest

from aws_resource_inventory.lib.outputs import process_generic_service_output
from aws_resource_inventory.lib.records import Resource
from aws_resource_inventory.services.autoscaling_service import (
    process_autoscaling_output,
)
from aws_resource_inventory.services.ec2_service import process_ec2_output
from aws_resource_inventory.services.ecs_service import process_ecs_output
from aws_resource_inventory.services.elb_service import process_elb_output
from aws_resource_inventory.services.rds_service import process_rds_output
from aws_resource_inventory.services.s3_service import process_s3_output
from aws_resource_inventory.services.vpc_service import process_vpc_output

REGION = "eu-central-1"

REQUIRED_KEYS = {"region", "resource_type", "resource_id", "resource_arn"}

# Representative boto3-shaped fixtures, one resource per key the scanner emits.
SERVICE_FIXTURES: dict[str, dict[str, Any]] = {
    "ec2": {
        "instances": [{"InstanceId": "i-1", "Tags": [{"Key": "Name", "Value": "web"}]}],
        "volumes": [{"VolumeId": "vol-1", "Tags": []}],
        "security_groups": [{"GroupId": "sg-1", "GroupName": "default"}],
        "amis": [{"ImageId": "ami-1", "Name": "golden"}],
        "snapshots": [{"SnapshotId": "snap-1", "Description": "backup"}],
    },
    "s3": {"buckets": [{"Name": "my-bucket"}]},
    "vpc": {
        "vpcs": [{"VpcId": "vpc-1", "CidrBlock": "10.0.0.0/16"}],
        "subnets": [
            {
                "SubnetId": "subnet-1",
                "CidrBlock": "10.0.1.0/24",
                "SubnetArn": "arn:aws:ec2:eu-central-1:1:subnet/subnet-1",
            }
        ],
        "nat_gateways": [{"NatGatewayId": "nat-1"}],
        "internet_gateways": [{"InternetGatewayId": "igw-1"}],
        "route_tables": [{"RouteTableId": "rtb-1"}],
        "dhcp_options": [{"DhcpOptionsId": "dopt-1"}],
        "vpc_peering_connections": [{"VpcPeeringConnectionId": "pcx-1"}],
        "vpc_endpoints": [
            {"VpcEndpointId": "vpce-1", "ServiceName": "com.amazonaws.eu-central-1.s3"}
        ],
    },
    "elb": {
        "load_balancers": [
            {
                "LoadBalancerName": "my-alb",
                "LoadBalancerArn": "arn:aws:elasticloadbalancing:eu-central-1:1:loadbalancer/app/my-alb/abc",
                "Type": "application",
            }
        ],
        "target_groups": [
            {
                "TargetGroupName": "my-tg",
                "TargetGroupArn": "arn:aws:elasticloadbalancing:eu-central-1:1:targetgroup/my-tg/def",
            }
        ],
        "listeners": [
            {
                "ListenerArn": "arn:aws:elasticloadbalancing:eu-central-1:1:listener/app/my-alb/abc/ghi",
                "Protocol": "HTTPS",
                "Port": 443,
            }
        ],
        "listener_rules": [
            {
                "RuleArn": "arn:aws:elasticloadbalancing:eu-central-1:1:listener-rule/app/my-alb/abc/ghi/jkl",
                "Priority": "1",
            }
        ],
    },
    "ecs": {
        "clusters": [
            {
                "clusterName": "prod",
                "clusterArn": "arn:aws:ecs:eu-central-1:1:cluster/prod",
            }
        ],
        "services": [
            {
                "serviceName": "api",
                "serviceArn": "arn:aws:ecs:eu-central-1:1:service/prod/api",
            }
        ],
        "task_definitions": [
            {"taskDefinitionArn": "arn:aws:ecs:eu-central-1:1:task-definition/api:3"}
        ],
        "capacity_providers": [
            {
                "name": "FARGATE",
                "capacityProviderArn": "arn:aws:ecs:eu-central-1:1:capacity-provider/FARGATE",
            }
        ],
    },
    "rds": {
        "db_instances": [
            {
                "DBInstanceIdentifier": "app-db",
                "DBInstanceArn": "arn:aws:rds:eu-central-1:1:db:app-db",
            }
        ],
        "db_clusters": [
            {
                "DBClusterIdentifier": "app-cluster",
                "DBClusterArn": "arn:aws:rds:eu-central-1:1:cluster:app-cluster",
            }
        ],
        "db_snapshots": [
            {
                "DBSnapshotIdentifier": "app-db-snap",
                "DBSnapshotArn": "arn:aws:rds:eu-central-1:1:snapshot:app-db-snap",
            }
        ],
        "db_cluster_snapshots": [
            {
                "DBClusterSnapshotIdentifier": "app-cluster-snap",
                "DBClusterSnapshotArn": "arn:aws:rds:eu-central-1:1:cluster-snapshot:app-cluster-snap",
            }
        ],
    },
    "autoscaling": {
        "auto_scaling_groups": [
            {
                "AutoScalingGroupName": "web-asg",
                "AutoScalingGroupARN": "arn:aws:autoscaling:eu-central-1:1:autoScalingGroup:x:autoScalingGroupName/web-asg",
            }
        ],
        "launch_configurations": [
            {
                "LaunchConfigurationName": "web-lc",
                "LaunchConfigurationARN": "arn:aws:autoscaling:eu-central-1:1:launchConfiguration:x:launchConfigurationName/web-lc",
            }
        ],
        "launch_templates": [
            {"LaunchTemplateName": "web-lt", "LaunchTemplateId": "lt-1"}
        ],
    },
}

PROCESSORS: dict[str, Callable[..., None]] = {
    "ec2": process_ec2_output,
    "s3": process_s3_output,
    "rds": process_rds_output,
    "vpc": process_vpc_output,
    "elb": process_elb_output,
    "ecs": process_ecs_output,
    "autoscaling": process_autoscaling_output,
}


def flatten(service: str) -> list[dict[str, Any]]:
    resources: list[Resource] = []
    PROCESSORS[service](SERVICE_FIXTURES[service], REGION, resources)
    return [resource.to_record() for resource in resources]


@pytest.mark.parametrize("service", sorted(PROCESSORS))
def test_every_record_carries_the_required_keys(service: str) -> None:
    records = flatten(service)
    assert records, f"{service} fixture produced no records"
    for record in records:
        assert REQUIRED_KEYS.issubset(record.keys()), record
        assert record["region"] == REGION


@pytest.mark.parametrize("service", sorted(PROCESSORS))
def test_one_record_per_fixture_resource(service: str) -> None:
    expected = sum(len(v) for v in SERVICE_FIXTURES[service].values())
    assert len(flatten(service)) == expected


def test_resource_types_are_pinned_per_producer() -> None:
    # The resource_type vocabulary is the tool's public output language —
    # dashboards and diffs downstream key on these exact strings.
    by_service = {
        s: sorted({r["resource_type"] for r in flatten(s)}) for s in PROCESSORS
    }
    assert by_service == {
        "ec2": [
            "ec2:ami",
            "ec2:instance",
            "ec2:security_group",
            "ec2:snapshot",
            "ec2:volume",
        ],
        "s3": ["s3:bucket"],
        # Characterization: bare "vpc" (no colon) for VPCs is current behaviour.
        "vpc": [
            "vpc",
            "vpc:dhcp_options",
            "vpc:endpoint",
            "vpc:internet_gateway",
            "vpc:nat_gateway",
            "vpc:peering_connection",
            "vpc:route_table",
            "vpc:subnet",
        ],
        "elb": [
            "elbv2:listener",
            "elbv2:listener_rule",
            "elbv2:load_balancer_application",
            "elbv2:target_group",
        ],
        "ecs": [
            "ecs:capacity_provider",
            "ecs:cluster",
            "ecs:service",
            "ecs:task_definition",
        ],
        "autoscaling": [
            "autoscaling:auto_scaling_group",
            "autoscaling:launch_configuration",
            "autoscaling:launch_template",
        ],
        "rds": [
            "rds:db_cluster",
            "rds:db_cluster_snapshot",
            "rds:db_instance",
            "rds:db_snapshot",
        ],
    }


def test_resource_name_is_optional_and_that_is_load_bearing() -> None:
    # ec2 instances now carry their Name tag (deliberate change: the value was
    # already computed and discarded). ecs records still omit resource_name —
    # their resource_id is already the friendly name, so consumers must keep
    # falling back to resource_id and the field stays optional on Resource.
    ec2_records = {r["resource_type"]: r for r in flatten("ec2")}
    assert ec2_records["ec2:instance"]["resource_name"] == "web"
    assert ec2_records["ec2:volume"]["resource_name"] == "vol-1"

    assert all("resource_name" not in r for r in flatten("ecs"))
    assert all("resource_name" in r for r in flatten("s3"))
    assert all("resource_name" in r for r in flatten("vpc"))
    assert all("resource_name" in r for r in flatten("elb"))
    assert all("resource_name" in r for r in flatten("autoscaling"))
    assert all("resource_name" in r for r in flatten("rds"))


def test_identity_fields_per_producer() -> None:
    s3_record = flatten("s3")[0]
    assert s3_record["resource_id"] == "my-bucket"
    assert s3_record["resource_arn"] == "arn:aws:s3:::my-bucket"

    # elbv2 records never carry an id — the ARN is the identity.
    assert all(r["resource_id"] == "N/A" for r in flatten("elb"))
    assert all(r["resource_arn"] != "N/A" for r in flatten("elb"))

    ecs_records = {r["resource_type"]: r for r in flatten("ecs")}
    assert ecs_records["ecs:task_definition"]["resource_id"] == "api:3"

    asg_records = {r["resource_type"]: r for r in flatten("autoscaling")}
    assert asg_records["autoscaling:launch_template"]["resource_id"] == "lt-1"
    assert asg_records["autoscaling:launch_template"]["resource_arn"] == "N/A"


def test_generic_processor_flattens_resource_groups_records() -> None:
    service_data = {
        "instances": [
            {
                "ResourceARN": "arn:aws:ec2:eu-central-1:1:instance/i-9",
                "ResourceId": "i-9",
                "ResourceType": "ec2:instance",
                "Region": REGION,
                "Tags": [{"Key": "env", "Value": "prod"}],
            },
            {
                "ResourceARN": "arn:aws:lambda:eu-central-1:1:function:fn",
                "ResourceId": None,  # extraction can fail — must not crash
                "ResourceType": "lambda:function",
            },
        ]
    }
    resources: list[Resource] = []
    process_generic_service_output(service_data, REGION, resources)

    assert [resource.to_record() for resource in resources] == [
        {
            "region": REGION,
            "resource_type": "ec2:instance",
            "resource_id": "i-9",
            "resource_arn": "arn:aws:ec2:eu-central-1:1:instance/i-9",
        },
        {
            "region": REGION,
            "resource_type": "lambda:function",
            "resource_id": "N/A",
            "resource_arn": "arn:aws:lambda:eu-central-1:1:function:fn",
        },
    ]


def test_ec2_instance_name_falls_back_to_the_instance_id() -> None:
    """An instance with no Name tag still reports a usable name.

    The reports render resource_name, so an untagged instance must show its
    id rather than an empty cell.
    """
    records: list[Resource] = []
    process_ec2_output({"instances": [{"InstanceId": "i-untagged"}]}, REGION, records)

    assert [r.to_record()["resource_name"] for r in records] == ["i-untagged"]
