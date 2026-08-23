"""
Resource shape seam: the flattened record every process_*_output emits.

This is the contract every consumer (table, markdown, JSON, diff) reads:
exactly these keys, a real id and a real ARN on every record — never
"N/A". ARNs the AWS API does not return are constructed from the caller
identity (account + partition) using the documented per-type formats, and
every record states whether its ARN was observed or constructed. Any
change to the shape is a deliberate decision, not an accident.
"""

from typing import Any

import pytest

from aws_resource_inventory.lib.outputs import process_generic_service_output
from aws_resource_inventory.lib.records import CallerIdentity, Resource
from aws_resource_inventory.services.ec2_service import process_ec2_output
from aws_resource_inventory.services.elb_service import process_elb_output
from aws_resource_inventory.services.registry import SERVICES, ProcessOutputFunc
from aws_resource_inventory.services.vpc_service import process_vpc_output

REGION = "eu-central-1"
IDENTITY = CallerIdentity(account="111122223333", partition="aws")
GOV_IDENTITY = CallerIdentity(account="111122223333", partition="aws-us-gov")

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
                "SubnetArn": "arn:aws:ec2:eu-central-1:111122223333:subnet/subnet-1",
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
    "efs": {
        "file_systems": [
            {
                "FileSystemId": "fs-1",
                "Name": "shared-data",
                "FileSystemArn": "arn:aws:elasticfilesystem:eu-central-1:1:file-system/fs-1",
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

# Derived from the registry, never hand-listed: a service registered in
# SERVICES without a fixture here fails loudly instead of quietly
# escaping the contract every test below parametrizes over.
PROCESSORS: dict[str, ProcessOutputFunc] = {
    name: registration.process_output for name, registration in SERVICES.items()
}


def flatten_resources(
    service: str, identity: CallerIdentity = IDENTITY
) -> list[Resource]:
    resources: list[Resource] = []
    PROCESSORS[service](SERVICE_FIXTURES[service], REGION, resources, identity)
    return resources


def flatten(service: str) -> list[dict[str, Any]]:
    return [resource.to_record() for resource in flatten_resources(service)]


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


@pytest.mark.parametrize("service", sorted(PROCESSORS))
def test_no_identity_field_is_ever_na(service: str) -> None:
    # The core guarantee of the identity work: every record has a real
    # id and a real ARN. "N/A" is banned output.
    for record in flatten(service):
        assert record["resource_id"] not in ("", "N/A"), record
        assert record["resource_arn"] not in ("", "N/A"), record
        assert record["resource_arn"].startswith("arn:"), record


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
        "vpc": [
            "vpc:dhcp_options",
            "vpc:endpoint",
            "vpc:internet_gateway",
            "vpc:nat_gateway",
            "vpc:peering_connection",
            "vpc:route_table",
            "vpc:subnet",
            "vpc:vpc",
        ],
        "elb": [
            "elb:listener",
            "elb:listener_rule",
            "elb:load_balancer_application",
            "elb:target_group",
        ],
        "ecs": [
            "ecs:capacity_provider",
            "ecs:cluster",
            "ecs:service",
            "ecs:task_definition",
        ],
        "efs": ["efs:file_system"],
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


@pytest.mark.parametrize("service", sorted(PROCESSORS))
def test_resource_type_starts_with_the_cli_service_key(service: str) -> None:
    # The left half of every resource_type is the producing service's
    # registry key — so any type in the output round-trips into
    # `scan --service <left half>`. PROCESSORS is derived from SERVICES,
    # so every service reaching here is a --service value by construction.
    for record in flatten(service):
        assert record["resource_type"].split(":")[0] == service, record


def test_load_balancer_flavour_comes_from_aws_type_attribute() -> None:
    # ALB/NLB/GWLB stay distinct via AWS's own Type attribute — no
    # hardcoded enum, so new flavours (e.g. "gateway") work unchanged.
    resources: list[Resource] = []
    process_elb_output(
        {
            "load_balancers": [
                {
                    "LoadBalancerName": "my-gwlb",
                    "LoadBalancerArn": "arn:aws:elasticloadbalancing:eu-central-1:1:loadbalancer/gwy/my-gwlb/xyz",
                    "Type": "gateway",
                },
                {
                    # No Type in the response: the flavourless base type,
                    # never an invented suffix.
                    "LoadBalancerName": "no-type",
                    "LoadBalancerArn": "arn:aws:elasticloadbalancing:eu-central-1:1:loadbalancer/app/no-type/abc",
                },
            ]
        },
        REGION,
        resources,
        IDENTITY,
    )
    gwlb, untyped = resources
    assert gwlb.resource_type == "elb:load_balancer_gateway"
    assert untyped.resource_type == "elb:load_balancer"


def test_arn_source_is_pinned_per_producer() -> None:
    # Which ARNs come from the AWS API (observed) and which this tool
    # builds from the caller identity (constructed) is part of the
    # contract — the JSON envelope chunk will serialize it.
    by_type = {
        r.resource_type: r.arn_source for s in PROCESSORS for r in flatten_resources(s)
    }
    assert by_type == {
        # ec2: the API returns no ARNs for these five types.
        "ec2:instance": "constructed",
        "ec2:volume": "constructed",
        "ec2:security_group": "constructed",
        "ec2:ami": "constructed",
        "ec2:snapshot": "constructed",
        # s3: ListBuckets returns no ARN; built from the documented format.
        "s3:bucket": "constructed",
        # vpc: only describe_subnets returns an ARN.
        "vpc:vpc": "constructed",
        "vpc:subnet": "observed",
        "vpc:nat_gateway": "constructed",
        "vpc:internet_gateway": "constructed",
        "vpc:route_table": "constructed",
        "vpc:dhcp_options": "constructed",
        "vpc:peering_connection": "constructed",
        "vpc:endpoint": "constructed",
        # elb/ecs/efs/rds: every ARN comes straight from the API.
        "elb:load_balancer_application": "observed",
        "elb:listener": "observed",
        "elb:listener_rule": "observed",
        "elb:target_group": "observed",
        "ecs:cluster": "observed",
        "ecs:service": "observed",
        "ecs:task_definition": "observed",
        "ecs:capacity_provider": "observed",
        "efs:file_system": "observed",
        "rds:db_instance": "observed",
        "rds:db_cluster": "observed",
        "rds:db_snapshot": "observed",
        "rds:db_cluster_snapshot": "observed",
        # autoscaling: launch templates are the one type without an ARN.
        "autoscaling:auto_scaling_group": "observed",
        "autoscaling:launch_configuration": "observed",
        "autoscaling:launch_template": "constructed",
    }


def test_constructed_arns_follow_the_documented_formats() -> None:
    # Formats verified against AWS's Service Authorization Reference
    # (servicereference.us-east-1.amazonaws.com) and then against the
    # ARNs the Resource Groups Tagging API reports for real resources in
    # a live account — which is what caught image and snapshot: the
    # reference shows them with an empty account field (that form covers
    # AMIs shared from other accounts), but AWS reports an owned image or
    # snapshot with the owner's account, and this scanner only ever asks
    # for self-owned ones. Launch templates are an ec2 resource.
    ec2 = {r["resource_type"]: r["resource_arn"] for r in flatten("ec2")}
    assert ec2 == {
        "ec2:instance": "arn:aws:ec2:eu-central-1:111122223333:instance/i-1",
        "ec2:volume": "arn:aws:ec2:eu-central-1:111122223333:volume/vol-1",
        "ec2:security_group": "arn:aws:ec2:eu-central-1:111122223333:security-group/sg-1",
        "ec2:ami": "arn:aws:ec2:eu-central-1:111122223333:image/ami-1",
        "ec2:snapshot": "arn:aws:ec2:eu-central-1:111122223333:snapshot/snap-1",
    }

    vpc = {r["resource_type"]: r["resource_arn"] for r in flatten("vpc")}
    assert vpc == {
        "vpc:vpc": "arn:aws:ec2:eu-central-1:111122223333:vpc/vpc-1",
        "vpc:subnet": "arn:aws:ec2:eu-central-1:111122223333:subnet/subnet-1",
        "vpc:nat_gateway": "arn:aws:ec2:eu-central-1:111122223333:natgateway/nat-1",
        "vpc:internet_gateway": "arn:aws:ec2:eu-central-1:111122223333:internet-gateway/igw-1",
        "vpc:route_table": "arn:aws:ec2:eu-central-1:111122223333:route-table/rtb-1",
        "vpc:dhcp_options": "arn:aws:ec2:eu-central-1:111122223333:dhcp-options/dopt-1",
        "vpc:peering_connection": "arn:aws:ec2:eu-central-1:111122223333:vpc-peering-connection/pcx-1",
        "vpc:endpoint": "arn:aws:ec2:eu-central-1:111122223333:vpc-endpoint/vpce-1",
    }

    asg = {r["resource_type"]: r["resource_arn"] for r in flatten("autoscaling")}
    assert (
        asg["autoscaling:launch_template"]
        == "arn:aws:ec2:eu-central-1:111122223333:launch-template/lt-1"
    )

    assert flatten("s3")[0]["resource_arn"] == "arn:aws:s3:::my-bucket"


def test_partition_flows_into_every_constructed_arn() -> None:
    # A GovCloud caller must never produce an arn:aws: constructed ARN.
    for service in PROCESSORS:
        for resource in flatten_resources(service, identity=GOV_IDENTITY):
            if resource.arn_source == "constructed":
                assert resource.resource_arn.startswith("arn:aws-us-gov:"), resource


def test_resource_name_is_optional_and_that_is_load_bearing() -> None:
    # Characterization: ec2 instances and every ecs record omit resource_name;
    # consumers must keep falling back to resource_id. The typed-Resource
    # refactor must model name as optional (or fill it in for these producers
    # as a deliberate change).
    ec2_records = {r["resource_type"]: r for r in flatten("ec2")}
    assert "resource_name" not in ec2_records["ec2:instance"]
    assert ec2_records["ec2:volume"]["resource_name"] == "vol-1"

    assert all("resource_name" not in r for r in flatten("ecs"))
    assert all("resource_name" in r for r in flatten("efs"))
    assert all("resource_name" in r for r in flatten("s3"))
    assert all("resource_name" in r for r in flatten("vpc"))
    assert all("resource_name" in r for r in flatten("elb"))
    assert all("resource_name" in r for r in flatten("autoscaling"))
    assert all("resource_name" in r for r in flatten("rds"))


def test_identity_fields_per_producer() -> None:
    s3_record = flatten("s3")[0]
    assert s3_record["resource_id"] == "my-bucket"
    assert s3_record["resource_arn"] == "arn:aws:s3:::my-bucket"

    # ELBv2 ids are extracted from the observed ARN and keep the full
    # path after the resource-type segment — AWS's own id shape.
    elb_records = {r["resource_type"]: r for r in flatten("elb")}
    assert (
        elb_records["elb:load_balancer_application"]["resource_id"] == "app/my-alb/abc"
    )
    assert elb_records["elb:listener"]["resource_id"] == "app/my-alb/abc/ghi"
    assert elb_records["elb:listener_rule"]["resource_id"] == "app/my-alb/abc/ghi/jkl"
    assert elb_records["elb:target_group"]["resource_id"] == "my-tg/def"

    ecs_records = {r["resource_type"]: r for r in flatten("ecs")}
    assert ecs_records["ecs:task_definition"]["resource_id"] == "api:3"

    asg_records = {r["resource_type"]: r for r in flatten("autoscaling")}
    assert asg_records["autoscaling:launch_template"]["resource_id"] == "lt-1"


def test_resource_missing_its_id_is_skipped_not_emitted_as_na() -> None:
    # A raw dict without its id key cannot be identified: skip it (with a
    # log line) rather than emit a record with a fake identity.
    resources: list[Resource] = []
    process_ec2_output(
        {"instances": [{"Tags": []}, {"InstanceId": "i-2", "Tags": []}]},
        REGION,
        resources,
        IDENTITY,
    )
    assert [r.resource_id for r in resources] == ["i-2"]


@pytest.mark.parametrize("service", sorted(PROCESSORS))
def test_unidentifiable_resources_are_skipped_by_every_producer(service: str) -> None:
    # One id-less/ARN-less dict under every result key: nothing a
    # producer cannot identify may reach the output.
    data: dict[str, Any] = {key: [{}] for key in SERVICE_FIXTURES[service]}
    resources: list[Resource] = []
    PROCESSORS[service](data, REGION, resources, IDENTITY)
    assert resources == []


def test_subnet_without_an_observed_arn_gets_a_constructed_one() -> None:
    # describe_subnets normally returns SubnetArn; if it is absent the
    # subnet still gets the documented constructed ARN, never "N/A".
    resources: list[Resource] = []
    process_vpc_output(
        {"subnets": [{"SubnetId": "subnet-9", "CidrBlock": "10.0.9.0/24"}]},
        REGION,
        resources,
        IDENTITY,
    )
    (subnet,) = resources
    assert (
        subnet.resource_arn == "arn:aws:ec2:eu-central-1:111122223333:subnet/subnet-9"
    )
    assert subnet.arn_source == "constructed"


def test_same_resource_yields_the_same_identity_via_both_scan_paths() -> None:
    # Path independence: whether a resource is found by its service
    # scanner or by the Resource Groups tag scan, its id and ARN are
    # identical. Nothing else pins this guarantee.
    service_side = {
        (r.resource_id, r.resource_arn)
        for r in flatten_resources("s3") + flatten_resources("elb")
    }

    tagging_data = {
        "buckets": [
            {"ResourceARN": "arn:aws:s3:::my-bucket", "ResourceType": "s3:bucket"}
        ],
        "listeners": [
            {
                "ResourceARN": SERVICE_FIXTURES["elb"]["listeners"][0]["ListenerArn"],
                "ResourceType": "elasticloadbalancing:listener",
            }
        ],
        "loadbalancers": [
            {
                "ResourceARN": SERVICE_FIXTURES["elb"]["load_balancers"][0][
                    "LoadBalancerArn"
                ],
                "ResourceType": "elasticloadbalancing:loadbalancer",
            }
        ],
    }
    tagging_side: list[Resource] = []
    process_generic_service_output(tagging_data, REGION, tagging_side, IDENTITY)

    assert tagging_side, "tagging path produced no records"
    for resource in tagging_side:
        assert (resource.resource_id, resource.resource_arn) in service_side, resource


def test_tagging_path_types_keep_the_aws_native_prefix() -> None:
    # Counterpart to test_resource_type_starts_with_the_cli_service_key:
    # the tagging path passes AWS's own ResourceType through untouched,
    # so an ELB stays under AWS's "elasticloadbalancing" namespace and is
    # NOT remapped to the "elb" service key the scanners emit. Pinned
    # because Resource.service is shared by both paths (ADR-0005).
    resources: list[Resource] = []
    process_generic_service_output(
        {
            "listeners": [
                {
                    "ResourceARN": (
                        "arn:aws:elasticloadbalancing:eu-central-1:1:"
                        "listener/app/lb/abc/def"
                    ),
                    "ResourceType": "elasticloadbalancing:listener",
                }
            ]
        },
        REGION,
        resources,
        IDENTITY,
    )

    (listener,) = resources
    assert listener.resource_type == "elasticloadbalancing:listener"
    assert listener.service == "elasticloadbalancing"


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
                "ResourceId": None,  # upstream extraction failed: re-derive it
                "ResourceType": "lambda:function",
            },
            {
                # No ARN at all: unidentifiable, skipped with a log line.
                "ResourceType": "mystery:thing",
            },
        ]
    }
    resources: list[Resource] = []
    process_generic_service_output(service_data, REGION, resources, IDENTITY)

    assert all(r.arn_source == "observed" for r in resources)
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
            "resource_id": "fn",
            "resource_arn": "arn:aws:lambda:eu-central-1:1:function:fn",
        },
    ]
