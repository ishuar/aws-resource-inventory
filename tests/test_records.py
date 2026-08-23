"""
Record seam: aws_resource_inventory.lib.records.Resource — the one definition of
the scan-record shape every producer constructs and every output
consumes. A malformed record fails at construction, not at report time.

CallerIdentity lives here too: the account + partition every constructed
ARN is built from.
"""

import dataclasses
from typing import Any

import pytest

from aws_resource_inventory.lib.records import CallerIdentity, Resource

REGION = "eu-central-1"


def make(**overrides: Any) -> Resource:
    fields: dict[str, Any] = {
        "region": REGION,
        "resource_type": "s3:bucket",
        "resource_id": "my-bucket",
        "resource_arn": "arn:aws:s3:::my-bucket",
        "arn_source": "constructed",
    }
    fields.update(overrides)
    return Resource(**fields)


def test_missing_required_field_fails_at_construction() -> None:
    with pytest.raises(TypeError):
        Resource(region=REGION, resource_type="s3:bucket", resource_id="b")  # type: ignore[call-arg]


def test_arn_source_is_required() -> None:
    # Every construction site must decide observed vs constructed —
    # there is deliberately no default.
    with pytest.raises(TypeError):
        Resource(  # type: ignore[call-arg]
            region=REGION,
            resource_type="s3:bucket",
            resource_id="b",
            resource_arn="arn:aws:s3:::b",
        )


def test_records_are_immutable() -> None:
    resource = make()
    with pytest.raises(dataclasses.FrozenInstanceError):
        resource.resource_id = "other"  # type: ignore[misc]


def test_to_record_without_name_matches_the_legacy_dict_exactly() -> None:
    # Key ORDER matters: JSON output must stay byte-identical with the
    # dicts producers used to build by hand.
    assert list(make().to_record().items()) == [
        ("region", REGION),
        ("resource_type", "s3:bucket"),
        ("resource_id", "my-bucket"),
        ("resource_arn", "arn:aws:s3:::my-bucket"),
    ]


def test_to_record_does_not_emit_arn_source_yet() -> None:
    # arn_source is carried on the dataclass but deliberately NOT
    # serialized: the JSON envelope chunk emits it. Until then the
    # serialized record changes only in id/arn values.
    assert "arn_source" not in make().to_record()
    assert "arn_source" not in make(resource_name="friendly").to_record()


def test_to_record_with_name_places_it_second_like_the_legacy_dicts() -> None:
    record = make(resource_name="friendly").to_record()
    assert list(record) == [
        "region",
        "resource_name",
        "resource_type",
        "resource_id",
        "resource_arn",
    ]
    assert record["resource_name"] == "friendly"


def test_name_defaults_to_absent() -> None:
    assert "resource_name" not in make().to_record()
    assert make().resource_name is None


def test_service_is_derived_from_the_resource_type_prefix() -> None:
    assert make(resource_type="ec2:instance").service == "ec2"
    assert make(resource_type="vpc:vpc").service == "vpc"


class TestCallerIdentity:
    def test_partition_comes_from_the_caller_arn(self) -> None:
        identity = CallerIdentity.from_caller_arn(
            "111122223333", "arn:aws:iam::111122223333:user/scanner"
        )
        assert identity == CallerIdentity(account="111122223333", partition="aws")

    def test_govcloud_and_china_partitions_are_preserved(self) -> None:
        # Never hardcode "aws": GovCloud and China callers must construct
        # ARNs in their own partition.
        gov = CallerIdentity.from_caller_arn(
            "111122223333", "arn:aws-us-gov:sts::111122223333:assumed-role/r/s"
        )
        assert gov.partition == "aws-us-gov"
        cn = CallerIdentity.from_caller_arn(
            "111122223333", "arn:aws-cn:iam::111122223333:user/scanner"
        )
        assert cn.partition == "aws-cn"

    def test_malformed_caller_arn_fails_loudly(self) -> None:
        with pytest.raises(ValueError):
            CallerIdentity.from_caller_arn("111122223333", "not-an-arn")
        with pytest.raises(ValueError):
            CallerIdentity.from_caller_arn("111122223333", "arn::missing:partition")

    def test_identity_is_immutable(self) -> None:
        identity = CallerIdentity(account="111122223333", partition="aws")
        with pytest.raises(dataclasses.FrozenInstanceError):
            identity.account = "444455556666"  # type: ignore[misc]
