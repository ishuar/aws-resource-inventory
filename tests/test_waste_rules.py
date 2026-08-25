"""
Waste seams: the Finding record, the rules registry, and the ec2 rules.

Rules are pure functions over fixture dicts (no moto, no AWS): each one
is pinned on both sides — it fires on the state it names and stays
silent on the healthy shape. The state-rules provider's contract is
pinned too: a rule is skipped for a region whose scan data is missing a
service the rule reads (an errored service never fabricates findings,
PRODUCT.md decision 16), and a raw item the inventory skipped as
unidentifiable yields no finding.
"""

from datetime import datetime, timezone
from typing import Any

from aws_resource_inventory.lib.records import Resource
from aws_resource_inventory.waste.config import WasteConfig
from aws_resource_inventory.waste.findings import Finding
from aws_resource_inventory.waste.providers import evaluate_state_rules
from aws_resource_inventory.waste.registry import RULES

REGION = "eu-central-1"
NOW = datetime(2026, 8, 26, 12, 0, 0, tzinfo=timezone.utc)
CONFIG = WasteConfig(now=NOW)


def resource(resource_type: str, resource_id: str) -> Resource:
    return Resource(
        region=REGION,
        resource_type=resource_type,
        resource_id=resource_id,
        resource_arn=f"arn:aws:ec2:{REGION}:111122223333:{resource_type.split(':', 1)[1]}/{resource_id}",
        arn_source="constructed",
    )


def evaluate(scan_data: dict[str, Any], resources: list[Resource]) -> list[Finding]:
    return evaluate_state_rules(scan_data, resources, REGION, CONFIG)


def empty_ec2_data(**sections: list[dict[str, Any]]) -> dict[str, Any]:
    """A clean ec2 scan: every section present, overridden per test."""
    base: dict[str, Any] = {
        "instances": [],
        "volumes": [],
        "security_groups": [],
        "amis": [],
        "snapshots": [],
        "addresses": [],
    }
    base.update(sections)
    return {"ec2": base}


class TestFindingRecord:
    def test_serializes_resource_identity_plus_judgment(self) -> None:
        finding = Finding(
            resource=resource("ec2:volume", "vol-1"),
            rule="ebs-unattached",
            confidence="certain",
            evidence={"State": "available"},
            suggested_action="snapshot-then-delete",
        )
        assert finding.to_record() == {
            "region": REGION,
            "type": "ec2:volume",
            "id": "vol-1",
            "name": None,
            "arn": f"arn:aws:ec2:{REGION}:111122223333:volume/vol-1",
            "arn_source": "constructed",
            "rule": "ebs-unattached",
            "confidence": "certain",
            "evidence": {"State": "available"},
            "suggested_action": "snapshot-then-delete",
        }


class TestRegistry:
    def test_the_v1_rule_vocabulary_is_pinned(self) -> None:
        # The registry is the single source of truth; the rule-name
        # vocabulary is public output language, pinned like resource types.
        assert set(RULES) == {
            "ebs-unattached",
            "eip-unassociated",
            "ec2-long-stopped",
            "snapshot-orphaned",
            "ami-unused",
            "elb-no-targets",
            "rds-stopped",
            "efs-empty",
            "ecs-cluster-idle",
            "ecs-service-zero-tasks",
        }

    def test_every_registration_names_the_services_it_reads(self) -> None:
        for name, registration in RULES.items():
            assert registration.services, name


class TestEbsUnattached:
    def test_available_volume_is_certain_waste(self) -> None:
        data = empty_ec2_data(
            volumes=[
                {
                    "VolumeId": "vol-1",
                    "State": "available",
                    "CreateTime": datetime(2024, 1, 3, tzinfo=timezone.utc),
                    "Size": 100,
                }
            ]
        )
        findings = evaluate(data, [resource("ec2:volume", "vol-1")])
        assert [f.rule for f in findings] == ["ebs-unattached"]
        finding = findings[0]
        assert finding.confidence == "certain"
        assert finding.suggested_action == "snapshot-then-delete"
        assert finding.evidence == {
            "State": "available",
            "CreateTime": "2024-01-03T00:00:00+00:00",
            "Size": 100,
        }
        assert finding.resource.resource_id == "vol-1"

    def test_in_use_volume_is_not_waste(self) -> None:
        data = empty_ec2_data(volumes=[{"VolumeId": "vol-1", "State": "in-use"}])
        assert evaluate(data, [resource("ec2:volume", "vol-1")]) == []

    def test_volume_the_inventory_skipped_yields_no_finding(self) -> None:
        # The processor skips unidentifiable raw dicts; a finding without
        # an identity would violate the record contract, so the rule
        # skips it too (logged, never silent).
        data = empty_ec2_data(volumes=[{"VolumeId": "vol-1", "State": "available"}])
        assert evaluate(data, []) == []


class TestEipUnassociated:
    def test_unassociated_address_is_certain_waste(self) -> None:
        data = empty_ec2_data(
            addresses=[
                {
                    "AllocationId": "eipalloc-1",
                    "PublicIp": "192.0.2.10",
                    "Domain": "vpc",
                }
            ]
        )
        findings = evaluate(data, [resource("ec2:elastic-ip", "eipalloc-1")])
        assert [f.rule for f in findings] == ["eip-unassociated"]
        assert findings[0].confidence == "certain"
        assert findings[0].suggested_action == "delete"
        assert findings[0].evidence == {
            "AssociationId": None,
            "PublicIp": "192.0.2.10",
            "Domain": "vpc",
        }

    def test_associated_address_is_not_waste(self) -> None:
        data = empty_ec2_data(
            addresses=[
                {
                    "AllocationId": "eipalloc-1",
                    "PublicIp": "192.0.2.10",
                    "AssociationId": "eipassoc-1",
                }
            ]
        )
        assert evaluate(data, [resource("ec2:elastic-ip", "eipalloc-1")]) == []


class TestEc2LongStopped:
    def stopped_instance(self, since: str) -> dict[str, Any]:
        return {
            "InstanceId": "i-1",
            "State": {"Name": "stopped"},
            "StateTransitionReason": f"User initiated ({since})",
        }

    def test_stopped_beyond_threshold_is_likely_waste(self) -> None:
        data = empty_ec2_data(
            instances=[self.stopped_instance("2026-01-01 10:00:00 GMT")]
        )
        findings = evaluate(data, [resource("ec2:instance", "i-1")])
        assert [f.rule for f in findings] == ["ec2-long-stopped"]
        finding = findings[0]
        assert finding.confidence == "likely"
        assert finding.suggested_action == "review"
        assert finding.evidence["State"] == "stopped"
        assert finding.evidence["days_stopped"] == 237
        assert "StateTransitionReason" in finding.evidence

    def test_recently_stopped_is_not_waste(self) -> None:
        data = empty_ec2_data(
            instances=[self.stopped_instance("2026-08-01 10:00:00 GMT")]
        )
        assert evaluate(data, [resource("ec2:instance", "i-1")]) == []

    def test_running_instance_is_not_waste(self) -> None:
        data = empty_ec2_data(
            instances=[{"InstanceId": "i-1", "State": {"Name": "running"}}]
        )
        assert evaluate(data, [resource("ec2:instance", "i-1")]) == []

    def test_stopped_without_a_parseable_date_is_not_claimed(self) -> None:
        # AWS clears StateTransitionReason on some paths. Without a date
        # there is no evidence for "long" — the rule claims nothing
        # rather than guessing (evidence, not vibes).
        data = empty_ec2_data(
            instances=[
                {
                    "InstanceId": "i-1",
                    "State": {"Name": "stopped"},
                    "StateTransitionReason": "",
                }
            ]
        )
        assert evaluate(data, [resource("ec2:instance", "i-1")]) == []


class TestSnapshotOrphaned:
    def test_snapshot_of_deleted_volume_is_likely_waste(self) -> None:
        data = empty_ec2_data(
            snapshots=[
                {
                    "SnapshotId": "snap-1",
                    "VolumeId": "vol-gone",
                    "StartTime": datetime(2024, 1, 3, tzinfo=timezone.utc),
                }
            ]
        )
        findings = evaluate(data, [resource("ec2:snapshot", "snap-1")])
        assert [f.rule for f in findings] == ["snapshot-orphaned"]
        assert findings[0].confidence == "likely"
        assert findings[0].suggested_action == "delete"
        assert findings[0].evidence == {
            "VolumeId": "vol-gone",
            "StartTime": "2024-01-03T00:00:00+00:00",
        }

    def test_snapshot_of_existing_volume_is_not_waste(self) -> None:
        data = empty_ec2_data(
            volumes=[{"VolumeId": "vol-1", "State": "in-use"}],
            snapshots=[{"SnapshotId": "snap-1", "VolumeId": "vol-1"}],
        )
        resources = [
            resource("ec2:volume", "vol-1"),
            resource("ec2:snapshot", "snap-1"),
        ]
        assert evaluate(data, resources) == []

    def test_ami_backing_snapshot_is_not_waste(self) -> None:
        # A snapshot referenced by a registered image is in use even if
        # its source volume is long gone.
        data = empty_ec2_data(
            snapshots=[{"SnapshotId": "snap-1", "VolumeId": "vol-gone"}],
            amis=[
                {
                    "ImageId": "ami-1",
                    "BlockDeviceMappings": [{"Ebs": {"SnapshotId": "snap-1"}}],
                }
            ],
        )
        resources = [
            resource("ec2:snapshot", "snap-1"),
            resource("ec2:image", "ami-1"),
        ]
        assert evaluate(data, resources) == []


class TestAmiUnused:
    def old_image(self) -> dict[str, Any]:
        return {"ImageId": "ami-1", "CreationDate": "2024-01-03T00:00:00.000Z"}

    def test_unreferenced_old_image_is_likely_waste(self) -> None:
        data = empty_ec2_data(amis=[self.old_image()])
        findings = evaluate(data, [resource("ec2:image", "ami-1")])
        assert [f.rule for f in findings] == ["ami-unused"]
        assert findings[0].confidence == "likely"
        assert findings[0].suggested_action == "delete"
        assert findings[0].evidence == {"CreationDate": "2024-01-03T00:00:00.000Z"}

    def test_image_referenced_by_an_instance_is_not_waste(self) -> None:
        data = empty_ec2_data(
            amis=[self.old_image()],
            instances=[
                {"InstanceId": "i-1", "ImageId": "ami-1", "State": {"Name": "running"}}
            ],
        )
        resources = [resource("ec2:image", "ami-1"), resource("ec2:instance", "i-1")]
        assert evaluate(data, resources) == []

    def test_young_image_is_not_waste(self) -> None:
        data = empty_ec2_data(
            amis=[{"ImageId": "ami-1", "CreationDate": "2026-08-01T00:00:00.000Z"}]
        )
        assert evaluate(data, [resource("ec2:image", "ami-1")]) == []


class TestPartialDataGuard:
    def test_rules_are_skipped_when_their_service_is_absent(self) -> None:
        # An errored service never reaches the region's scan data
        # (ADR-0010) — and absent data must yield no findings, or
        # cross-referencing rules would invent them (decision 16).
        assert evaluate({}, [resource("ec2:volume", "vol-1")]) == []

    def test_present_but_empty_sections_still_evaluate(self) -> None:
        # Present-and-empty is a real answer ("nothing exists"), distinct
        # from absent ("could not look").
        data = empty_ec2_data(
            addresses=[{"AllocationId": "eipalloc-1", "PublicIp": "192.0.2.10"}]
        )
        findings = evaluate(data, [resource("ec2:elastic-ip", "eipalloc-1")])
        assert len(findings) == 1


def elb_data(target_groups: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "elb": {
            "load_balancers": [],
            "target_groups": target_groups,
            "listeners": [],
            "listener_rules": [],
        }
    }


def rds_data(db_instances: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "rds": {
            "db_instances": db_instances,
            "db_clusters": [],
            "db_snapshots": [],
            "db_cluster_snapshots": [],
        }
    }


def efs_data(file_systems: list[dict[str, Any]]) -> dict[str, Any]:
    return {"efs": {"file_systems": file_systems}}


def ecs_data(
    clusters: list[dict[str, Any]] | None = None,
    services: list[dict[str, Any]] | None = None,
    capacity_providers: list[dict[str, Any]] | None = None,
    asgs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "ecs": {
            "clusters": clusters or [],
            "services": services or [],
            "task_definitions": [],
            "capacity_providers": capacity_providers or [],
        },
        "autoscaling": {
            "auto_scaling_groups": asgs or [],
            "launch_templates": [],
            "launch_configurations": [],
        },
    }


class TestElbNoTargets:
    TG_ARN = (
        "arn:aws:elasticloadbalancing:eu-central-1:111122223333:"
        "targetgroup/my-tg/abc123"
    )

    def target_group(self, health: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "TargetGroupArn": self.TG_ARN,
            "TargetGroupName": "my-tg",
            "TargetType": "instance",
            "TargetHealthDescriptions": health,
        }

    def tg_resource(self) -> Resource:
        return Resource(
            region=REGION,
            resource_type="elb:targetgroup",
            resource_id="my-tg/abc123",
            resource_arn=self.TG_ARN,
            arn_source="observed",
        )

    def test_zero_registered_targets_is_likely_waste(self) -> None:
        findings = evaluate(elb_data([self.target_group([])]), [self.tg_resource()])
        assert [f.rule for f in findings] == ["elb-no-targets"]
        assert findings[0].confidence == "likely"
        assert findings[0].suggested_action == "review"
        assert findings[0].evidence == {
            "TargetHealthDescriptions": [],
            "TargetType": "instance",
        }

    def test_registered_targets_are_not_waste(self) -> None:
        health = [{"Target": {"Id": "i-1"}, "TargetHealth": {"State": "healthy"}}]
        assert (
            evaluate(elb_data([self.target_group(health)]), [self.tg_resource()]) == []
        )

    def test_health_never_fetched_is_not_claimed(self) -> None:
        # A cached scan from before health attachment lacks the key.
        # Missing data must never read as "zero targets" (decision 16).
        tg = self.target_group([])
        del tg["TargetHealthDescriptions"]
        assert evaluate(elb_data([tg]), [self.tg_resource()]) == []


class TestRdsStopped:
    def db(self, status: str) -> dict[str, Any]:
        return {
            "DBInstanceIdentifier": "orders-db",
            "DBInstanceStatus": status,
            "Engine": "postgres",
            "AllocatedStorage": 100,
        }

    def test_stopped_db_is_likely_waste(self) -> None:
        findings = evaluate(
            rds_data([self.db("stopped")]), [resource("rds:db", "orders-db")]
        )
        assert [f.rule for f in findings] == ["rds-stopped"]
        assert findings[0].confidence == "likely"
        assert findings[0].suggested_action == "review"
        assert findings[0].evidence == {
            "DBInstanceStatus": "stopped",
            "Engine": "postgres",
            "AllocatedStorage": 100,
        }

    def test_available_db_is_not_waste(self) -> None:
        findings = evaluate(
            rds_data([self.db("available")]), [resource("rds:db", "orders-db")]
        )
        assert findings == []


class TestEfsEmpty:
    def file_system(self, **overrides: Any) -> dict[str, Any]:
        base: dict[str, Any] = {
            "FileSystemId": "fs-1",
            "LifeCycleState": "available",
            "SizeInBytes": {"Value": 6144},
            "NumberOfMountTargets": 1,
        }
        base.update(overrides)
        return base

    def test_metadata_only_file_system_is_likely_waste(self) -> None:
        findings = evaluate(
            efs_data([self.file_system()]), [resource("efs:file-system", "fs-1")]
        )
        assert [f.rule for f in findings] == ["efs-empty"]
        assert findings[0].confidence == "likely"
        assert findings[0].evidence == {
            "SizeInBytes": {"Value": 6144},
            "NumberOfMountTargets": 1,
        }

    def test_unmounted_file_system_with_data_is_likely_waste(self) -> None:
        fs = self.file_system(SizeInBytes={"Value": 10_000_000}, NumberOfMountTargets=0)
        findings = evaluate(efs_data([fs]), [resource("efs:file-system", "fs-1")])
        assert [f.rule for f in findings] == ["efs-empty"]

    def test_mounted_file_system_with_data_is_not_waste(self) -> None:
        fs = self.file_system(SizeInBytes={"Value": 10_000_000})
        assert evaluate(efs_data([fs]), [resource("efs:file-system", "fs-1")]) == []

    def test_file_system_still_creating_is_not_claimed(self) -> None:
        fs = self.file_system(LifeCycleState="creating")
        assert evaluate(efs_data([fs]), [resource("efs:file-system", "fs-1")]) == []


class TestEcsClusterIdle:
    CLUSTER_ARN = "arn:aws:ecs:eu-central-1:111122223333:cluster/batch"
    ASG_ARN = (
        "arn:aws:autoscaling:eu-central-1:111122223333:autoScalingGroup:"
        "uuid:autoScalingGroupName/batch-asg"
    )

    def cluster(self, **overrides: Any) -> dict[str, Any]:
        base: dict[str, Any] = {
            "clusterArn": self.CLUSTER_ARN,
            "clusterName": "batch",
            "runningTasksCount": 0,
            "pendingTasksCount": 0,
            "activeServicesCount": 0,
            "registeredContainerInstancesCount": 0,
            "capacityProviders": [],
        }
        base.update(overrides)
        return base

    def cluster_resource(self) -> Resource:
        return Resource(
            region=REGION,
            resource_type="ecs:cluster",
            resource_id="batch",
            resource_arn=self.CLUSTER_ARN,
            arn_source="observed",
        )

    def test_idle_cluster_with_registered_instances_is_likely_waste(self) -> None:
        data = ecs_data(clusters=[self.cluster(registeredContainerInstancesCount=3)])
        findings = evaluate(data, [self.cluster_resource()])
        assert [f.rule for f in findings] == ["ecs-cluster-idle"]
        finding = findings[0]
        assert finding.confidence == "likely"
        assert finding.evidence["ec2_backed"] is True
        assert finding.evidence["registeredContainerInstancesCount"] == 3
        assert finding.evidence["runningTasksCount"] == 0

    def test_idle_cluster_whose_capacity_provider_asg_runs_instances(self) -> None:
        # Instances that exist but never registered still bill — visible
        # only through the capacity provider's Auto Scaling group.
        data = ecs_data(
            clusters=[self.cluster(capacityProviders=["batch-cp"])],
            capacity_providers=[
                {
                    "name": "batch-cp",
                    "autoScalingGroupProvider": {"autoScalingGroupArn": self.ASG_ARN},
                }
            ],
            asgs=[
                {
                    "AutoScalingGroupARN": self.ASG_ARN,
                    "AutoScalingGroupName": "batch-asg",
                    "Instances": [{"InstanceId": "i-1"}],
                }
            ],
        )
        findings = evaluate(data, [self.cluster_resource()])
        assert [f.rule for f in findings] == ["ecs-cluster-idle"]
        assert findings[0].confidence == "likely"
        assert findings[0].evidence["ec2_backed"] is True

    def test_idle_fargate_only_cluster_is_review_grade_clutter(self) -> None:
        data = ecs_data(
            clusters=[self.cluster(capacityProviders=["FARGATE", "FARGATE_SPOT"])]
        )
        findings = evaluate(data, [self.cluster_resource()])
        assert [f.rule for f in findings] == ["ecs-cluster-idle"]
        assert findings[0].confidence == "review"
        assert findings[0].evidence["ec2_backed"] is False

    def test_cluster_running_tasks_is_not_waste(self) -> None:
        data = ecs_data(clusters=[self.cluster(runningTasksCount=4)])
        assert evaluate(data, [self.cluster_resource()]) == []

    def test_cluster_with_pending_tasks_is_not_claimed(self) -> None:
        # Mid-deploy is not idle: tasks are starting.
        data = ecs_data(clusters=[self.cluster(pendingTasksCount=2)])
        assert evaluate(data, [self.cluster_resource()]) == []

    def test_rule_is_skipped_without_autoscaling_data(self) -> None:
        # The EC2-backed check reads the autoscaling section; with it
        # errored (absent), the rule stays silent rather than judging on
        # partial data — while single-service ecs rules still run.
        data = ecs_data(
            clusters=[self.cluster(registeredContainerInstancesCount=3)],
            services=[
                {
                    "serviceArn": (
                        "arn:aws:ecs:eu-central-1:111122223333:" "service/batch/worker"
                    ),
                    "serviceName": "worker",
                    "desiredCount": 0,
                    "runningCount": 0,
                }
            ],
        )
        del data["autoscaling"]
        service_resource = Resource(
            region=REGION,
            resource_type="ecs:service",
            resource_id="batch/worker",
            resource_arn="arn:aws:ecs:eu-central-1:111122223333:service/batch/worker",
            arn_source="observed",
        )
        findings = evaluate(data, [self.cluster_resource(), service_resource])
        assert [f.rule for f in findings] == ["ecs-service-zero-tasks"]


class TestEcsServiceZeroTasks:
    SERVICE_ARN = "arn:aws:ecs:eu-central-1:111122223333:service/batch/worker"

    def service(self, desired: int) -> dict[str, Any]:
        return {
            "serviceArn": self.SERVICE_ARN,
            "serviceName": "worker",
            "desiredCount": desired,
            "runningCount": desired,
        }

    def service_resource(self) -> Resource:
        return Resource(
            region=REGION,
            resource_type="ecs:service",
            resource_id="batch/worker",
            resource_arn=self.SERVICE_ARN,
            arn_source="observed",
        )

    def test_scaled_to_zero_service_is_review_grade_clutter(self) -> None:
        findings = evaluate(
            ecs_data(services=[self.service(0)]), [self.service_resource()]
        )
        assert [f.rule for f in findings] == ["ecs-service-zero-tasks"]
        assert findings[0].confidence == "review"
        assert findings[0].suggested_action == "review"
        assert findings[0].evidence == {"desiredCount": 0, "runningCount": 0}

    def test_running_service_is_not_waste(self) -> None:
        findings = evaluate(
            ecs_data(services=[self.service(2)]), [self.service_resource()]
        )
        assert findings == []
