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

from aws_resource_inventory.lib.records import (
    CallerIdentity,
    Resource,
    name_from_tags,
)

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


def test_to_record_emits_the_bare_key_envelope_record() -> None:
    # The serialized vocabulary is the envelope's bare keys, in this
    # exact order. The dataclass attributes keep their resource_-prefixed
    # names — they are the internal API; only serialization renames.
    assert list(make(resource_name="friendly").to_record().items()) == [
        ("region", REGION),
        ("type", "s3:bucket"),
        ("id", "my-bucket"),
        ("name", "friendly"),
        ("arn", "arn:aws:s3:::my-bucket"),
        ("arn_source", "constructed"),
    ]


def test_to_record_without_name_serializes_an_explicit_null() -> None:
    # name is always present — None (JSON null) when AWS supplies no
    # name — so every record has the same keys and the data loads into
    # pandas/Parquet/SQL without ragged rows.
    record = make().to_record()
    assert "name" in record
    assert record["name"] is None


def test_to_record_emits_arn_source() -> None:
    # Whether the ARN was observed from an AWS API response or
    # constructed from the caller identity is part of the public record.
    assert make().to_record()["arn_source"] == "constructed"
    assert make(arn_source="observed").to_record()["arn_source"] == "observed"


def test_name_defaults_to_none_and_still_serializes() -> None:
    assert make().resource_name is None
    assert make().to_record()["name"] is None


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


class TestNameFromTags:
    """The one reader of AWS's Name tag, shared by every producer."""

    def test_returns_the_name_tag_value(self) -> None:
        assert name_from_tags([{"Key": "Name", "Value": "web"}], "i-1") == "web"

    def test_reads_the_lowercase_ecs_tag_shape(self) -> None:
        # ECS is the one API whose tags are key/value, not Key/Value.
        assert name_from_tags([{"key": "Name", "value": "prod"}], "c-1") == "prod"

    def test_none_when_aws_supplies_no_name_tag(self) -> None:
        assert name_from_tags([{"Key": "env", "Value": "prod"}], "i-1") is None
        assert name_from_tags([], "i-1") is None
        assert name_from_tags(None, "i-1") is None

    def test_a_name_tag_that_repeats_the_id_is_not_a_name(self) -> None:
        # Common on ASGs and RDS instances, whose Name tag mirrors the
        # identifier: a name that repeats the id says nothing, so the
        # record keeps None and the never-a-copy-of-the-id guarantee
        # holds on the tag path too.
        assert name_from_tags([{"Key": "Name", "Value": "web-asg"}], "web-asg") is None

    def test_the_first_name_tag_wins(self) -> None:
        # AWS allows one tag per key, so the first match is the only one.
        tags = [{"Key": "Name", "Value": "web"}, {"Key": "Name", "Value": "other"}]
        assert name_from_tags(tags, "i-1") == "web"
