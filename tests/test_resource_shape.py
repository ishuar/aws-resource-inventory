"""
Resource shape seam: the flattened record every process_*_output emits.

This is the contract every consumer (table, markdown, JSON, diff) reads:
exactly these keys, a real id and a real ARN on every record — never
"N/A". ARNs the AWS API does not return are constructed from the caller
identity (account + partition) using the documented per-type formats, and
every record states whether its ARN was observed or constructed.
resource_name is a name AWS itself supplies (a Name/name attribute or
the Name tag, read by lib.records.name_from_tags on both scan paths) or
None — never synthesized, never an id copy — and the key is always
serialized. Any change to the shape is a deliberate decision, not an
accident.
"""

import re
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

REQUIRED_KEYS = {
    "region",
    "type",
    "id",
    "name",
    "arn",
    "arn_source",
}

# The synthesized-name patterns this tool used to invent. Deleted
# deliberately: a resource_name is a name AWS itself supplies (a
# Name/name attribute or the Name tag) or None — never fabricated.
BANNED_INVENTED_NAME_PATTERNS = (
    r"^VPC-",  # VPC-{cidr}
    r"^Subnet-",  # Subnet-{cidr}
    r"^vpce-.*-",  # {endpoint id}-{service suffix}
    r"^[A-Z]+:\d+$",  # ELB listener {protocol}:{port}
    r"^Rule-",  # ELB rule Rule-{priority}
)

# Representative boto3-shaped fixtures, one resource per key the scanner emits.
SERVICE_FIXTURES: dict[str, dict[str, Any]] = {
    "ec2": {
        "instances": [{"InstanceId": "i-1", "Tags": [{"Key": "Name", "Value": "web"}]}],
        "volumes": [{"VolumeId": "vol-1", "Tags": []}],
        "security_groups": [{"GroupId": "sg-1", "GroupName": "default"}],
        "amis": [{"ImageId": "ami-1", "Name": "golden"}],
        "snapshots": [
            {
                "SnapshotId": "snap-1",
                "Description": "backup",
                "Tags": [{"Key": "Name", "Value": "nightly"}],
            }
        ],
    },
    "s3": {"buckets": [{"Name": "my-bucket"}]},
    "vpc": {
        "vpcs": [
            {
                "VpcId": "vpc-1",
                "CidrBlock": "10.0.0.0/16",
                "Tags": [{"Key": "Name", "Value": "prod"}],
            }
        ],
        "subnets": [
            {
                "SubnetId": "subnet-1",
                "CidrBlock": "10.0.1.0/24",
                "SubnetArn": "arn:aws:ec2:eu-central-1:111122223333:subnet/subnet-1",
                "Tags": [{"Key": "Name", "Value": "prod-public-a"}],
            }
        ],
        "nat_gateways": [
            {"NatGatewayId": "nat-1", "Tags": [{"Key": "Name", "Value": "egress"}]}
        ],
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
                "tags": [{"key": "Name", "value": "Production"}],
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
                "Tags": [{"Key": "Name", "Value": "shared-data"}],
                "FileSystemArn": "arn:aws:elasticfilesystem:eu-central-1:1:file-system/fs-1",
            }
        ],
    },
    "rds": {
        "db_instances": [
            {
                "DBInstanceIdentifier": "app-db",
                "DBInstanceArn": "arn:aws:rds:eu-central-1:1:db:app-db",
                "TagList": [{"Key": "Name", "Value": "orders primary"}],
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
                # The ASG Name tag mirrors the group name: a name that
                # repeats the id is not a name, so this stays None.
                "Tags": [{"Key": "Name", "Value": "web-asg"}],
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
        assert record["id"] not in ("", "N/A"), record
        assert record["arn"] not in ("", "N/A"), record
        assert record["arn"].startswith("arn:"), record


def test_resource_types_are_pinned_per_producer() -> None:
    # The resource_type vocabulary is the tool's public output language —
    # dashboards and diffs downstream key on these exact strings.
    by_service = {s: sorted({r["type"] for r in flatten(s)}) for s in PROCESSORS}
    assert by_service == {
        "ec2": [
            "ec2:image",
            "ec2:instance",
            "ec2:security-group",
            "ec2:snapshot",
            "ec2:volume",
        ],
        "s3": ["s3:bucket"],
        "vpc": [
            "vpc:dhcp-options",
            "vpc:internet-gateway",
            "vpc:natgateway",
            "vpc:route-table",
            "vpc:subnet",
            "vpc:vpc",
            "vpc:vpc-endpoint",
            "vpc:vpc-peering-connection",
        ],
        "elb": [
            "elb:listener",
            "elb:listener-rule",
            "elb:loadbalancer-application",
            "elb:targetgroup",
        ],
        "ecs": [
            "ecs:capacity-provider",
            "ecs:cluster",
            "ecs:service",
            "ecs:task-definition",
        ],
        "efs": ["efs:file-system"],
        "autoscaling": [
            "autoscaling:autoScalingGroup",
            "autoscaling:launch-template",
            "autoscaling:launchConfiguration",
        ],
        "rds": [
            "rds:cluster",
            "rds:cluster-snapshot",
            "rds:db",
            "rds:snapshot",
        ],
    }


@pytest.mark.parametrize("service", sorted(PROCESSORS))
def test_resource_type_starts_with_the_cli_service_key(service: str) -> None:
    # The left half of every resource_type is the producing service's
    # registry key — so any type in the output round-trips into
    # `scan --service <left half>`. PROCESSORS is derived from SERVICES,
    # so every service reaching here is a --service value by construction.
    for record in flatten(service):
        assert record["type"].split(":")[0] == service, record


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
    assert gwlb.resource_type == "elb:loadbalancer-gateway"
    assert untyped.resource_type == "elb:loadbalancer"


def test_arn_source_is_pinned_per_producer() -> None:
    # Which ARNs come from the AWS API (observed) and which this tool
    # builds from the caller identity (constructed) is part of the
    # contract — the JSON envelope serializes it on every record.
    by_type = {
        r.resource_type: r.arn_source for s in PROCESSORS for r in flatten_resources(s)
    }
    assert by_type == {
        # ec2: the API returns no ARNs for these five types.
        "ec2:instance": "constructed",
        "ec2:volume": "constructed",
        "ec2:security-group": "constructed",
        "ec2:image": "constructed",
        "ec2:snapshot": "constructed",
        # s3: ListBuckets returns no ARN; built from the documented format.
        "s3:bucket": "constructed",
        # vpc: only describe_subnets returns an ARN.
        "vpc:vpc": "constructed",
        "vpc:subnet": "observed",
        "vpc:natgateway": "constructed",
        "vpc:internet-gateway": "constructed",
        "vpc:route-table": "constructed",
        "vpc:dhcp-options": "constructed",
        "vpc:vpc-peering-connection": "constructed",
        "vpc:vpc-endpoint": "constructed",
        # elb/ecs/efs/rds: every ARN comes straight from the API.
        "elb:loadbalancer-application": "observed",
        "elb:listener": "observed",
        "elb:listener-rule": "observed",
        "elb:targetgroup": "observed",
        "ecs:cluster": "observed",
        "ecs:service": "observed",
        "ecs:task-definition": "observed",
        "ecs:capacity-provider": "observed",
        "efs:file-system": "observed",
        "rds:db": "observed",
        "rds:cluster": "observed",
        "rds:snapshot": "observed",
        "rds:cluster-snapshot": "observed",
        # autoscaling: launch templates are the one type without an ARN.
        "autoscaling:autoScalingGroup": "observed",
        "autoscaling:launchConfiguration": "observed",
        "autoscaling:launch-template": "constructed",
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
    ec2 = {r["type"]: r["arn"] for r in flatten("ec2")}
    assert ec2 == {
        "ec2:instance": "arn:aws:ec2:eu-central-1:111122223333:instance/i-1",
        "ec2:volume": "arn:aws:ec2:eu-central-1:111122223333:volume/vol-1",
        "ec2:security-group": "arn:aws:ec2:eu-central-1:111122223333:security-group/sg-1",
        "ec2:image": "arn:aws:ec2:eu-central-1:111122223333:image/ami-1",
        "ec2:snapshot": "arn:aws:ec2:eu-central-1:111122223333:snapshot/snap-1",
    }

    vpc = {r["type"]: r["arn"] for r in flatten("vpc")}
    assert vpc == {
        "vpc:vpc": "arn:aws:ec2:eu-central-1:111122223333:vpc/vpc-1",
        "vpc:subnet": "arn:aws:ec2:eu-central-1:111122223333:subnet/subnet-1",
        "vpc:natgateway": "arn:aws:ec2:eu-central-1:111122223333:natgateway/nat-1",
        "vpc:internet-gateway": "arn:aws:ec2:eu-central-1:111122223333:internet-gateway/igw-1",
        "vpc:route-table": "arn:aws:ec2:eu-central-1:111122223333:route-table/rtb-1",
        "vpc:dhcp-options": "arn:aws:ec2:eu-central-1:111122223333:dhcp-options/dopt-1",
        "vpc:vpc-peering-connection": "arn:aws:ec2:eu-central-1:111122223333:vpc-peering-connection/pcx-1",
        "vpc:vpc-endpoint": "arn:aws:ec2:eu-central-1:111122223333:vpc-endpoint/vpce-1",
    }

    asg = {r["type"]: r["arn"] for r in flatten("autoscaling")}
    assert (
        asg["autoscaling:launch-template"]
        == "arn:aws:ec2:eu-central-1:111122223333:launch-template/lt-1"
    )

    assert flatten("s3")[0]["arn"] == "arn:aws:s3:::my-bucket"


def test_partition_flows_into_every_constructed_arn() -> None:
    # A GovCloud caller must never produce an arn:aws: constructed ARN.
    for service in PROCESSORS:
        for resource in flatten_resources(service, identity=GOV_IDENTITY):
            if resource.arn_source == "constructed":
                assert resource.resource_arn.startswith("arn:aws-us-gov:"), resource


@pytest.mark.parametrize("service", sorted(PROCESSORS))
def test_resource_name_key_is_always_present(service: str) -> None:
    # The name key is always serialized, None (JSON null) when AWS
    # supplies no name — so the data loads into pandas/Parquet/SQL
    # without ragged rows and consumers never need hasattr-style checks.
    for record in flatten(service):
        assert "name" in record, record


@pytest.mark.parametrize("service", sorted(PROCESSORS))
def test_resource_name_is_real_or_null_never_invented(service: str) -> None:
    # A name is something AWS itself supplies. It is never a copy of the
    # id and never one of the synthesized patterns this tool used to
    # fabricate (VPC-{cidr}, {protocol}:{port}, Rule-{priority}, ...).
    for record in flatten(service):
        name = record["name"]
        if name is None:
            continue
        assert name != record["id"], record
        for pattern in BANNED_INVENTED_NAME_PATTERNS:
            assert not re.match(pattern, name), (pattern, record)


def test_resource_names_are_pinned_per_producer() -> None:
    # The full name decision table, one row per resource type: a real
    # AWS-supplied name (Name/name attribute or Name tag) or None.
    by_type = {r["type"]: r["name"] for s in PROCESSORS for r in flatten(s)}
    assert by_type == {
        # ec2: security_group and ami have genuine name attributes;
        # instance, volume and snapshot use the Name tag (a snapshot
        # Description is not a name), and the untagged volume stays None.
        "ec2:instance": "web",
        "ec2:volume": None,
        "ec2:security-group": "default",
        "ec2:image": "golden",
        "ec2:snapshot": "nightly",
        # s3: ListBuckets returns no tags and the bucket name IS the id.
        "s3:bucket": None,
        # vpc: no API here supplies a name attribute, so the Name tag is
        # the only source — present on three fixtures, absent on the rest.
        "vpc:vpc": "prod",
        "vpc:subnet": "prod-public-a",
        "vpc:natgateway": "egress",
        "vpc:internet-gateway": None,
        "vpc:route-table": None,
        "vpc:dhcp-options": None,
        "vpc:vpc-peering-connection": None,
        "vpc:vpc-endpoint": None,
        # elb: load balancers and target groups carry real AWS names;
        # listeners and rules have none, and no elbv2 describe response
        # returns tags.
        "elb:loadbalancer-application": "my-alb",
        "elb:targetgroup": "my-tg",
        "elb:listener": None,
        "elb:listener-rule": None,
        # ecs: the AWS "name" IS the resource_id, so only a Name tag can
        # add anything — read from ECS's lowercase tag shape.
        "ecs:cluster": "Production",
        "ecs:service": None,
        "ecs:task-definition": None,
        "ecs:capacity-provider": None,
        # efs: AWS surfaces the Name tag as a Name field, but the tag
        # itself is what we read, so the id-repeat guard applies.
        "efs:file-system": "shared-data",
        # rds: the identifier IS the id, so only a Name tag (RDS calls
        # the field TagList) can add anything.
        "rds:db": "orders primary",
        "rds:cluster": None,
        "rds:snapshot": None,
        "rds:cluster-snapshot": None,
        # autoscaling: the ASG fixture's Name tag mirrors its group name,
        # so it stays None; launch configurations carry no tags at all;
        # a launch template's name is genuinely distinct from its lt- id.
        "autoscaling:autoScalingGroup": None,
        "autoscaling:launchConfiguration": None,
        "autoscaling:launch-template": "web-lt",
    }


def test_ec2_name_tag_or_null_for_instances_and_volumes() -> None:
    # Carried from PR #51 (which fell back to the instance id) and
    # deliberately superseded: the Name tag when AWS has one, otherwise
    # null — the id is never duplicated into the name.
    resources: list[Resource] = []
    process_ec2_output(
        {
            "instances": [
                {"InstanceId": "i-tagged", "Tags": [{"Key": "Name", "Value": "web"}]},
                {"InstanceId": "i-untagged"},
                {"InstanceId": "i-other-tags", "Tags": [{"Key": "env", "Value": "p"}]},
            ],
            "volumes": [
                {"VolumeId": "vol-tagged", "Tags": [{"Key": "Name", "Value": "data"}]},
                {"VolumeId": "vol-untagged"},
            ],
        },
        REGION,
        resources,
        IDENTITY,
    )
    assert [(r.resource_id, r.resource_name) for r in resources] == [
        ("i-tagged", "web"),
        ("i-untagged", None),
        ("i-other-tags", None),
        ("vol-tagged", "data"),
        ("vol-untagged", None),
    ]


def test_identity_fields_per_producer() -> None:
    s3_record = flatten("s3")[0]
    assert s3_record["id"] == "my-bucket"
    assert s3_record["arn"] == "arn:aws:s3:::my-bucket"

    # ELBv2 ids are extracted from the observed ARN and keep the full
    # path after the resource-type segment — AWS's own id shape.
    elb_records = {r["type"]: r for r in flatten("elb")}
    assert elb_records["elb:loadbalancer-application"]["id"] == "app/my-alb/abc"
    assert elb_records["elb:listener"]["id"] == "app/my-alb/abc/ghi"
    assert elb_records["elb:listener-rule"]["id"] == "app/my-alb/abc/ghi/jkl"
    assert elb_records["elb:targetgroup"]["id"] == "my-tg/def"

    ecs_records = {r["type"]: r for r in flatten("ecs")}
    assert ecs_records["ecs:task-definition"]["id"] == "api:3"
    # ECS service ids are the cluster/service path AWS puts in the ARN,
    # for the same reason as ELBv2: the trailing segment alone is not an
    # identity.
    assert ecs_records["ecs:service"]["id"] == "prod/api"

    asg_records = {r["type"]: r for r in flatten("autoscaling")}
    assert asg_records["autoscaling:launch-template"]["id"] == "lt-1"


def test_same_service_name_in_two_clusters_yields_two_distinct_records() -> None:
    # The collision the cluster segment exists to prevent. Without it
    # both services flatten to the same (region, type, id) triple, which
    # is also the envelope's sort key — so the records are
    # indistinguishable and their order stops being deterministic.
    data = {
        "services": [
            {
                "serviceName": "api",
                "serviceArn": "arn:aws:ecs:eu-central-1:1:service/prod/api",
            },
            {
                "serviceName": "api",
                "serviceArn": "arn:aws:ecs:eu-central-1:1:service/staging/api",
            },
        ]
    }
    resources: list[Resource] = []
    PROCESSORS["ecs"](data, REGION, resources, IDENTITY)

    ids = sorted(r.resource_id for r in resources)
    assert ids == ["prod/api", "staging/api"]
    assert len({(r.region, r.resource_type, r.resource_id) for r in resources}) == 2


def test_ecs_service_name_tag_is_a_name_now_that_the_id_is_the_path() -> None:
    # name_from_tags drops a Name tag that merely repeats the id. The id
    # is the cluster/service path, so a service tagged Name=api reports
    # that name instead of null — the tag is no longer a copy of the id.
    data = {
        "services": [
            {
                "serviceName": "api",
                "serviceArn": "arn:aws:ecs:eu-central-1:1:service/prod/api",
                "tags": [{"key": "Name", "value": "api"}],
            }
        ]
    }
    resources: list[Resource] = []
    PROCESSORS["ecs"](data, REGION, resources, IDENTITY)

    assert resources[0].resource_id == "prod/api"
    assert resources[0].resource_name == "api"


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


# Every producer whose only possible name is the ``Name`` tag: the
# per-service scan and the tag scan must agree, or the same resource is
# named under `scan` and nameless under `scan --tag-key`. The types whose
# name comes from a name *attribute* cannot appear here — the Tagging API
# returns only an ARN and tags, so it never sees the attribute
# (ADR-0005 Consequences).
NAME_TAG = [{"Key": "Name", "Value": "shared"}]
ECS_NAME_TAG = [{"key": "Name", "value": "shared"}]

BOTH_PATH_NAME_CASES: list[tuple[str, dict[str, Any], dict[str, Any]]] = [
    (
        "ec2",
        {"instances": [{"InstanceId": "i-7", "Tags": NAME_TAG}]},
        {
            "ResourceARN": "arn:aws:ec2:eu-central-1:111122223333:instance/i-7",
            "ResourceId": "i-7",
            "ResourceType": "ec2:instance",
            "Tags": NAME_TAG,
        },
    ),
    (
        "s3",
        {"buckets": [{"Name": "my-bucket", "tags": NAME_TAG}]},
        {
            "ResourceARN": "arn:aws:s3:::my-bucket",
            "ResourceType": "s3:bucket",
            "Tags": NAME_TAG,
        },
    ),
    (
        "elb",
        {
            "listeners": [
                {
                    "ListenerArn": (
                        "arn:aws:elasticloadbalancing:eu-central-1:1:"
                        "listener/app/my-alb/abc/ghi"
                    ),
                    "Tags": NAME_TAG,
                }
            ]
        },
        {
            "ResourceARN": (
                "arn:aws:elasticloadbalancing:eu-central-1:1:"
                "listener/app/my-alb/abc/ghi"
            ),
            "ResourceType": "elasticloadbalancing:listener",
            "Tags": NAME_TAG,
        },
    ),
    (
        "elb",
        {
            "listener_rules": [
                {
                    "RuleArn": (
                        "arn:aws:elasticloadbalancing:eu-central-1:1:"
                        "listener-rule/app/my-alb/abc/ghi/jkl"
                    ),
                    "Tags": NAME_TAG,
                }
            ]
        },
        {
            "ResourceARN": (
                "arn:aws:elasticloadbalancing:eu-central-1:1:"
                "listener-rule/app/my-alb/abc/ghi/jkl"
            ),
            "ResourceType": "elasticloadbalancing:listener-rule",
            "Tags": NAME_TAG,
        },
    ),
    (
        "efs",
        {
            "file_systems": [
                {
                    "FileSystemId": "fs-1",
                    "FileSystemArn": (
                        "arn:aws:elasticfilesystem:eu-central-1:1:file-system/fs-1"
                    ),
                    "Name": "shared",
                    "Tags": NAME_TAG,
                }
            ]
        },
        {
            "ResourceARN": (
                "arn:aws:elasticfilesystem:eu-central-1:1:file-system/fs-1"
            ),
            "ResourceId": "fs-1",
            "ResourceType": "elasticfilesystem:file-system",
            "Tags": NAME_TAG,
        },
    ),
    (
        "ecs",
        {
            "capacity_providers": [
                {
                    "name": "cp-1",
                    "capacityProviderArn": (
                        "arn:aws:ecs:eu-central-1:1:capacity-provider/cp-1"
                    ),
                    "tags": ECS_NAME_TAG,
                }
            ]
        },
        {
            "ResourceARN": "arn:aws:ecs:eu-central-1:1:capacity-provider/cp-1",
            "ResourceId": "cp-1",
            "ResourceType": "ecs:capacity-provider",
            "Tags": NAME_TAG,
        },
    ),
]


@pytest.mark.parametrize(
    ("service", "service_data", "tagging_record"),
    BOTH_PATH_NAME_CASES,
    ids=[case[2]["ResourceType"] for case in BOTH_PATH_NAME_CASES],
)
def test_the_name_tag_yields_the_same_name_via_both_scan_paths(
    service: str,
    service_data: dict[str, Any],
    tagging_record: dict[str, Any],
) -> None:
    # Path independence for the name, the counterpart of the identity
    # test above. The tag scan already carries every resource's tags, so
    # it reads them with the same helper instead of dropping a name AWS
    # supplied — otherwise one instance is "shared" under `scan` and null
    # under `scan --tag-key`.
    service_side: list[Resource] = []
    PROCESSORS[service](service_data, REGION, service_side, IDENTITY)

    tagging_side: list[Resource] = []
    process_generic_service_output(
        {"resources": [tagging_record]}, REGION, tagging_side, IDENTITY
    )

    (from_service,) = service_side
    (from_tagging,) = tagging_side
    assert from_service.resource_name == from_tagging.resource_name == "shared"


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
                # Tags without a Name key leave resource_name None.
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
            "type": "ec2:instance",
            "id": "i-9",
            "name": None,
            "arn": "arn:aws:ec2:eu-central-1:1:instance/i-9",
            "arn_source": "observed",
        },
        {
            "region": REGION,
            "type": "lambda:function",
            "id": "fn",
            "name": None,
            "arn": "arn:aws:lambda:eu-central-1:1:function:fn",
            "arn_source": "observed",
        },
    ]
