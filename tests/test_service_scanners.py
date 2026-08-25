"""
Scanner seams: services.*.scan_* against a fake AWS (moto).

Functional tests for the original six scanners (ec2, s3, vpc, elb, ecs,
autoscaling): create real-shaped resources in moto, run each scanner
through its public interface (session, region), and pin the result-key
vocabulary and the resources found.

Scanners added later get their own per-service test file
(tests/test_<service>_scanner.py) — that is the norm going forward.
"""

from typing import Any

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

from aws_resource_inventory.services.autoscaling_service import scan_autoscaling
from aws_resource_inventory.services.ec2_service import scan_ec2
from aws_resource_inventory.services.ecs_service import scan_ecs
from aws_resource_inventory.services.elb_service import scan_elb
from aws_resource_inventory.services.s3_service import scan_s3
from aws_resource_inventory.services.vpc_service import scan_vpc

REGION = "eu-central-1"


class TestScanEc2:
    def test_result_keys_and_created_resources_are_found(
        self, aws_session: Any
    ) -> None:
        ec2 = aws_session.client("ec2", region_name=REGION)
        ami_id = ec2.describe_images(Owners=["amazon"])["Images"][0]["ImageId"]
        instance_id = ec2.run_instances(ImageId=ami_id, MinCount=1, MaxCount=1)[
            "Instances"
        ][0]["InstanceId"]
        volume_id = ec2.create_volume(AvailabilityZone=f"{REGION}a", Size=8)["VolumeId"]
        snapshot_id = ec2.create_snapshot(VolumeId=volume_id)["SnapshotId"]
        image_id = ec2.create_image(InstanceId=instance_id, Name="golden")["ImageId"]
        allocation_id = ec2.allocate_address(Domain="vpc")["AllocationId"]

        result = scan_ec2(aws_session, REGION)

        assert set(result) == {
            "instances",
            "volumes",
            "security_groups",
            "amis",
            "snapshots",
            "addresses",
        }
        assert instance_id in [i["InstanceId"] for i in result["instances"]]
        assert volume_id in [v["VolumeId"] for v in result["volumes"]]
        assert snapshot_id in [s["SnapshotId"] for s in result["snapshots"]]
        assert allocation_id in [a["AllocationId"] for a in result["addresses"]]
        # Owners=["self"] — only images this account created.
        assert image_id in [a["ImageId"] for a in result["amis"]]
        assert ami_id not in [a["ImageId"] for a in result["amis"]]
        # The default security group always exists.
        assert "default" in [sg["GroupName"] for sg in result["security_groups"]]

    def test_empty_region_returns_all_keys_with_empty_lists(
        self, aws_session: Any
    ) -> None:
        result = scan_ec2(aws_session, REGION)
        assert result["instances"] == []
        assert result["volumes"] == []
        assert result["amis"] == []
        assert result["addresses"] == []
        # No snapshots assertion: moto seeds public snapshots and does not
        # honour the OwnerIds=["self"] filter, so the list is never empty
        # under moto. Ownership filtering is covered by the created-resource
        # test above.


class TestScanS3:
    def test_only_buckets_in_the_target_region_are_returned(
        self, aws_session: Any
    ) -> None:
        s3 = aws_session.client("s3", region_name=REGION)
        s3.create_bucket(
            Bucket="in-region",
            CreateBucketConfiguration={"LocationConstraint": REGION},
        )
        aws_session.client("s3", region_name="us-east-1").create_bucket(
            Bucket="other-region"
        )

        result = scan_s3(aws_session, REGION)

        assert [b["Name"] for b in result["buckets"]] == ["in-region"]

    def test_bucket_tags_are_injected_under_lowercase_tags_key(
        self, aws_session: Any
    ) -> None:
        s3 = aws_session.client("s3", region_name=REGION)
        s3.create_bucket(
            Bucket="tagged",
            CreateBucketConfiguration={"LocationConstraint": REGION},
        )
        s3.put_bucket_tagging(
            Bucket="tagged",
            Tagging={"TagSet": [{"Key": "env", "Value": "prod"}]},
        )
        s3.create_bucket(
            Bucket="untagged",
            CreateBucketConfiguration={"LocationConstraint": REGION},
        )

        buckets = {b["Name"]: b for b in scan_s3(aws_session, REGION)["buckets"]}

        assert buckets["tagged"]["tags"] == [{"Key": "env", "Value": "prod"}]
        # NoSuchTagSet degrades to an empty list, not an error.
        assert buckets["untagged"]["tags"] == []


class TestScanVpc:
    def test_result_keys_and_created_resources_are_found(
        self, aws_session: Any
    ) -> None:
        ec2 = aws_session.client("ec2", region_name=REGION)
        vpc_id = ec2.create_vpc(CidrBlock="10.1.0.0/16")["Vpc"]["VpcId"]
        subnet_id = ec2.create_subnet(VpcId=vpc_id, CidrBlock="10.1.1.0/24")["Subnet"][
            "SubnetId"
        ]
        igw_id = ec2.create_internet_gateway()["InternetGateway"]["InternetGatewayId"]

        result = scan_vpc(aws_session, REGION)

        assert set(result) == {
            "vpcs",
            "subnets",
            "internet_gateways",
            "route_tables",
            "nat_gateways",
            "dhcp_options",
            "vpc_peering_connections",
            "vpc_endpoints",
        }
        assert vpc_id in [v["VpcId"] for v in result["vpcs"]]
        assert subnet_id in [s["SubnetId"] for s in result["subnets"]]
        assert igw_id in [g["InternetGatewayId"] for g in result["internet_gateways"]]
        # Creating a VPC always creates a main route table.
        assert any(rt["VpcId"] == vpc_id for rt in result["route_tables"])

    def test_results_beyond_the_first_page_are_included(self) -> None:
        # AWS truncates describe_* responses; the scanner must paginate.
        # This fake session answers raw describe calls with page 1 only
        # (plus a NextToken) while its paginator yields both pages — so the
        # page-2 resources below only show up if the scanner paginates.
        pages = {
            "describe_vpcs": ("Vpcs", "VpcId", "vpc"),
            "describe_subnets": ("Subnets", "SubnetId", "subnet"),
            "describe_nat_gateways": ("NatGateways", "NatGatewayId", "nat"),
            "describe_internet_gateways": (
                "InternetGateways",
                "InternetGatewayId",
                "igw",
            ),
            "describe_route_tables": ("RouteTables", "RouteTableId", "rtb"),
            "describe_dhcp_options": ("DhcpOptions", "DhcpOptionsId", "dopt"),
            "describe_vpc_peering_connections": (
                "VpcPeeringConnections",
                "VpcPeeringConnectionId",
                "pcx",
            ),
            "describe_vpc_endpoints": ("VpcEndpoints", "VpcEndpointId", "vpce"),
        }

        def page(op: str, n: int) -> Any:
            key, id_field, prefix = pages[op]
            return {key: [{id_field: f"{prefix}-page{n}"}]}

        class FakePaginator:
            def __init__(self, op: str) -> None:
                self.op = op

            def paginate(self, **_kwargs: Any) -> Any:
                return iter([page(self.op, 1), page(self.op, 2)])

        class FakeEC2Client:
            def get_paginator(self, op: str) -> FakePaginator:
                return FakePaginator(op)

            def __getattr__(self, op: str) -> Any:
                if op not in pages:
                    raise AttributeError(op)
                return lambda **_kwargs: {**page(op, 1), "NextToken": "more"}

        class FakeSession:
            def client(self, *_args: Any, **_kwargs: Any) -> FakeEC2Client:
                return FakeEC2Client()

        result = scan_vpc(FakeSession(), REGION)

        for resource_key, (_, id_field, prefix) in [
            ("vpcs", pages["describe_vpcs"]),
            ("subnets", pages["describe_subnets"]),
            ("nat_gateways", pages["describe_nat_gateways"]),
            ("internet_gateways", pages["describe_internet_gateways"]),
            ("route_tables", pages["describe_route_tables"]),
            ("dhcp_options", pages["describe_dhcp_options"]),
        ]:
            ids = [r[id_field] for r in result[resource_key]]
            assert ids == [f"{prefix}-page1", f"{prefix}-page2"], (
                resource_key,
                ids,
            )


class TestScanElb:
    def _create_alb(self, aws_session: Any) -> Any:
        ec2 = aws_session.client("ec2", region_name=REGION)
        elbv2 = aws_session.client("elbv2", region_name=REGION)
        vpc_id = ec2.create_vpc(CidrBlock="10.2.0.0/16")["Vpc"]["VpcId"]
        subnet_a = ec2.create_subnet(
            VpcId=vpc_id, CidrBlock="10.2.1.0/24", AvailabilityZone=f"{REGION}a"
        )["Subnet"]["SubnetId"]
        subnet_b = ec2.create_subnet(
            VpcId=vpc_id, CidrBlock="10.2.2.0/24", AvailabilityZone=f"{REGION}b"
        )["Subnet"]["SubnetId"]
        lb_arn = elbv2.create_load_balancer(
            Name="my-alb",
            Subnets=[subnet_a, subnet_b],
            Tags=[{"Key": "env", "Value": "prod"}],
        )["LoadBalancers"][0]["LoadBalancerArn"]
        tg_arn = elbv2.create_target_group(
            Name="my-tg", Protocol="HTTP", Port=80, VpcId=vpc_id
        )["TargetGroups"][0]["TargetGroupArn"]
        elbv2.create_listener(
            LoadBalancerArn=lb_arn,
            Protocol="HTTP",
            Port=80,
            DefaultActions=[{"Type": "forward", "TargetGroupArn": tg_arn}],
        )
        return lb_arn

    def test_result_keys_and_created_resources_are_found(
        self, aws_session: Any
    ) -> None:
        lb_arn = self._create_alb(aws_session)

        result = scan_elb(aws_session, REGION)

        assert set(result) == {
            "load_balancers",
            "target_groups",
            "listeners",
            "listener_rules",
        }
        assert [lb["LoadBalancerArn"] for lb in result["load_balancers"]] == [lb_arn]
        assert result["load_balancers"][0]["Tags"] == [{"Key": "env", "Value": "prod"}]
        assert [tg["TargetGroupName"] for tg in result["target_groups"]] == ["my-tg"]
        assert len(result["listeners"]) == 1
        # The second (winning) listener pass annotates the LB name.
        assert result["listeners"][0]["LoadBalancerName"] == "my-alb"
        # Every listener has at least its default rule.
        assert len(result["listener_rules"]) >= 1
        assert result["listener_rules"][0]["LoadBalancerArn"] == lb_arn

    def test_listeners_carry_their_tags(self, aws_session: Any) -> None:
        # A listener's only possible name is its Name tag, so a scan that
        # never fetches tags reports it nameless while the tag scan names
        # it (ADR-0005 §4). Rules take the same code path; moto's add_tags
        # rejects rule ARNs, so the rule half is pinned at the processor
        # seam in tests/test_resource_shape.py instead.
        lb_arn = self._create_alb(aws_session)
        elbv2 = aws_session.client("elbv2", region_name=REGION)
        listener_arn = elbv2.describe_listeners(LoadBalancerArn=lb_arn)["Listeners"][0][
            "ListenerArn"
        ]
        elbv2.add_tags(
            ResourceArns=[listener_arn], Tags=[{"Key": "Name", "Value": "public-http"}]
        )

        result = scan_elb(aws_session, REGION)

        assert result["listeners"][0]["Tags"] == [
            {"Key": "Name", "Value": "public-http"}
        ]
        # Rules are fetched too, so the key is always present.
        assert result["listener_rules"][0]["Tags"] == []

    def test_empty_region_yields_empty_lists(self, aws_session: Any) -> None:
        result = scan_elb(aws_session, REGION)
        assert result["load_balancers"] == []
        assert result["listeners"] == []


class TestScanEcs:
    @pytest.fixture(autouse=True)
    def _moto_capacity_provider_shim(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # moto 5.2 crashes on describe_capacity_providers() without an
        # explicit name list (names is None → TypeError). Shim the fake so
        # the scanner's real no-args call works; the scanner code itself is
        # untouched.
        from moto.ecs.models import EC2ContainerServiceBackend

        original = EC2ContainerServiceBackend.describe_capacity_providers

        def patched(self: Any, names: Any, *args: Any, **kwargs: Any) -> Any:
            return original(self, names or [], *args, **kwargs)

        monkeypatch.setattr(
            EC2ContainerServiceBackend, "describe_capacity_providers", patched
        )

    def test_result_keys_and_created_resources_are_found(
        self, aws_session: Any
    ) -> None:
        ecs = aws_session.client("ecs", region_name=REGION)
        ecs.create_cluster(clusterName="prod")
        container_defs = [{"name": "app", "image": "app:latest", "memory": 128}]
        ecs.register_task_definition(family="api", containerDefinitions=container_defs)
        ecs.create_service(
            cluster="prod", serviceName="api-svc", taskDefinition="api", desiredCount=1
        )

        result = scan_ecs(aws_session, REGION)

        assert set(result) == {
            "clusters",
            "services",
            "task_definitions",
            "capacity_providers",
        }
        assert [c["clusterName"] for c in result["clusters"]] == ["prod"]
        services = {s["serviceName"]: s for s in result["services"]}
        assert services["api-svc"]["clusterName"] == "prod"
        assert [
            td["taskDefinitionArn"].split("/")[-1] for td in result["task_definitions"]
        ] == ["api:1"]

    def test_capacity_providers_are_requested_with_their_tags(
        self, aws_session: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # moto returns a capacity provider's tags whether or not they were
        # asked for; real AWS returns them only when include=["TAGS"] is
        # sent. Pin the request itself, or the Name tag silently vanishes
        # in production while every test stays green.
        from moto.ecs.responses import EC2ContainerServiceResponse

        requested: list[Any] = []
        original = EC2ContainerServiceResponse.describe_capacity_providers

        def recording(self: Any) -> Any:
            requested.append(self._get_param("include"))
            return original(self)

        monkeypatch.setattr(
            EC2ContainerServiceResponse, "describe_capacity_providers", recording
        )
        aws_session.client("ecs", region_name=REGION).create_cluster(clusterName="prod")

        scan_ecs(aws_session, REGION)

        assert requested == [["TAGS"]]

    def test_a_capacity_provider_failure_propagates(
        self, aws_session: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # An AccessDenied on describe_capacity_providers fails the whole
        # capacity-provider section — not an expected per-item condition —
        # so swallowing it would read as "zero capacity providers"
        # (ADR-0010). scan_region owns the catch and records ScanError data.
        from moto.ecs.responses import EC2ContainerServiceResponse

        def denied(self: Any) -> Any:
            raise ClientError(
                {"Error": {"Code": "AccessDeniedException", "Message": "denied"}},
                "DescribeCapacityProviders",
            )

        monkeypatch.setattr(
            EC2ContainerServiceResponse, "describe_capacity_providers", denied
        )
        aws_session.client("ecs", region_name=REGION).create_cluster(clusterName="prod")

        with pytest.raises(ClientError):
            scan_ecs(aws_session, REGION)

    def test_a_boto_failure_mid_scan_propagates(self) -> None:
        # scan_ecs must not swallow a BotoCoreError into partial results:
        # the caller (scan_region) owns the catch and records it as
        # ScanError data (ADR-0010) — a connection failure must never
        # read as "zero ECS resources".
        class BrokenClient:
            def get_paginator(self, *_args: Any, **_kwargs: Any) -> Any:
                raise EndpointConnectionError(endpoint_url="https://ecs.example.com")

        class BrokenSession:
            def client(self, *_args: Any, **_kwargs: Any) -> Any:
                return BrokenClient()

        with pytest.raises(EndpointConnectionError):
            scan_ecs(BrokenSession(), REGION)

    def test_only_the_latest_two_task_definition_revisions_are_kept(
        self, aws_session: Any
    ) -> None:
        ecs = aws_session.client("ecs", region_name=REGION)
        ecs.create_cluster(clusterName="prod")
        container_defs = [{"name": "app", "image": "app:latest", "memory": 128}]
        for _ in range(4):
            ecs.register_task_definition(
                family="api", containerDefinitions=container_defs
            )

        result = scan_ecs(aws_session, REGION)

        revisions = sorted(
            td["taskDefinitionArn"].split("/")[-1] for td in result["task_definitions"]
        )
        # The scanner requests sort="DESC" to keep the latest two revisions.
        # moto ignores the sort parameter, so which two come back is not
        # asserted here — the cap of 2 per family is the pinned behaviour.
        assert len(revisions) == 2


class TestScanAutoscaling:
    def _create_asgs(self, aws_session: Any) -> None:
        ec2 = aws_session.client("ec2", region_name=REGION)
        autoscaling = aws_session.client("autoscaling", region_name=REGION)
        ec2.create_launch_template(
            LaunchTemplateName="web-lt",
            LaunchTemplateData={"ImageId": "ami-12345678", "InstanceType": "t3.micro"},
            TagSpecifications=[
                {
                    "ResourceType": "launch-template",
                    "Tags": [{"Key": "env", "Value": "prod"}],
                }
            ],
        )
        for name, tags in [
            ("prod-asg", [{"Key": "env", "Value": "prod"}]),
            ("dev-asg", [{"Key": "env", "Value": "dev"}]),
            ("untagged-asg", []),
        ]:
            autoscaling.create_auto_scaling_group(
                AutoScalingGroupName=name,
                MinSize=0,
                MaxSize=1,
                AvailabilityZones=[f"{REGION}a"],
                LaunchTemplate={"LaunchTemplateName": "web-lt"},
                Tags=[
                    {**t, "ResourceId": name, "ResourceType": "auto-scaling-group"}
                    for t in tags
                ],
            )

    def asg_names(self, result: Any) -> list:
        return sorted(a["AutoScalingGroupName"] for a in result["auto_scaling_groups"])

    def test_no_tags_returns_everything(self, aws_session: Any) -> None:
        self._create_asgs(aws_session)
        result = scan_autoscaling(aws_session, REGION)

        assert set(result) == {
            "auto_scaling_groups",
            "launch_configurations",
            "launch_templates",
        }
        assert self.asg_names(result) == ["dev-asg", "prod-asg", "untagged-asg"]
        assert [t["LaunchTemplateName"] for t in result["launch_templates"]] == [
            "web-lt"
        ]

    def test_key_and_value_filter_requires_an_exact_match(
        self, aws_session: Any
    ) -> None:
        self._create_asgs(aws_session)
        result = scan_autoscaling(aws_session, REGION, "env", "prod")
        assert self.asg_names(result) == ["prod-asg"]

    def test_key_only_filter_matches_any_value(self, aws_session: Any) -> None:
        self._create_asgs(aws_session)
        result = scan_autoscaling(aws_session, REGION, tag_key="env")
        assert self.asg_names(result) == ["dev-asg", "prod-asg"]

    def test_value_only_filter_matches_any_key(self, aws_session: Any) -> None:
        self._create_asgs(aws_session)
        result = scan_autoscaling(aws_session, REGION, tag_value="prod")
        assert self.asg_names(result) == ["prod-asg"]

    def test_no_match_returns_empty_lists(self, aws_session: Any) -> None:
        self._create_asgs(aws_session)
        result = scan_autoscaling(aws_session, REGION, "env", "does-not-exist")
        assert result["auto_scaling_groups"] == []
        assert result["launch_configurations"] == []
