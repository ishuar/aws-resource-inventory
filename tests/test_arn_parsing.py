"""
ARN parsing seam: aws_resource_inventory.lib.arn (id extraction, shared by
both scan paths) and aws_resource_inventory.lib.resource_groups_utils
(service/type extraction for the tag path).

These functions decide how every ARN-identified resource is identified in
the output. Expected values are worked examples from the AWS ARN
documentation format: arn:partition:service:region:account:resource.
"""

from aws_resource_inventory.lib.arn import extract_resource_id_from_arn
from aws_resource_inventory.lib.resource_groups_utils import (
    _extract_service_and_type_from_arn,
    should_use_resource_groups_api,
)


class TestExtractServiceAndType:
    def test_slash_separated_resource(self) -> None:
        arn = "arn:aws:ec2:eu-central-1:111122223333:instance/i-0abcd1234"
        assert _extract_service_and_type_from_arn(arn) == ("ec2", "instance")

    def test_colon_separated_resource(self) -> None:
        arn = "arn:aws:sns:eu-central-1:111122223333:my-topic"
        assert _extract_service_and_type_from_arn(arn) == ("sns", "my-topic")

    def test_s3_bucket_arn_resolves_to_the_bucket_type(self) -> None:
        # arn:aws:s3:::bucket-name carries no type segment; the type is
        # "bucket", matching the per-service scanner vocabulary — not the
        # bucket's own name.
        arn = "arn:aws:s3:::my-bucket"
        assert _extract_service_and_type_from_arn(arn) == ("s3", "bucket")

    def test_malformed_arn_yields_empty_pair(self) -> None:
        assert _extract_service_and_type_from_arn("not-an-arn") == ("", "")
        assert _extract_service_and_type_from_arn("") == ("", "")


class TestExtractResourceId:
    def test_s3_bucket_id_is_the_bucket_name(self) -> None:
        arn = "arn:aws:s3:::my-bucket"
        assert extract_resource_id_from_arn(arn, "s3:bucket") == "my-bucket"

    def test_load_balancer_keeps_type_name_and_id(self) -> None:
        arn = (
            "arn:aws:elasticloadbalancing:eu-central-1:111122223333:"
            "loadbalancer/app/my-alb/50dc6c495c0c9188"
        )
        assert (
            extract_resource_id_from_arn(arn, "elasticloadbalancing:loadbalancer")
            == "app/my-alb/50dc6c495c0c9188"
        )

    def test_target_group_keeps_name_and_id(self) -> None:
        arn = (
            "arn:aws:elasticloadbalancing:eu-central-1:111122223333:"
            "targetgroup/my-tg/73e2d6bc24d8a067"
        )
        assert (
            extract_resource_id_from_arn(arn, "elasticloadbalancing:targetgroup")
            == "my-tg/73e2d6bc24d8a067"
        )

    def test_listener_keeps_the_full_path_after_the_type_segment(self) -> None:
        # ELBv2 ids ARE multi-slash paths: AWS's own ARN format is
        # listener/app/${LoadBalancerName}/${LoadBalancerId}/${ListenerId}
        # (Service Authorization Reference), so the id keeps everything
        # after the resource-type segment.
        arn = (
            "arn:aws:elasticloadbalancing:eu-central-1:111122223333:"
            "listener/app/my-alb/50dc6c495c0c9188/f2f7dc8efc522ab2"
        )
        assert (
            extract_resource_id_from_arn(arn, "elasticloadbalancing:listener")
            == "app/my-alb/50dc6c495c0c9188/f2f7dc8efc522ab2"
        )

    def test_listener_rule_keeps_the_full_path_after_the_type_segment(self) -> None:
        arn = (
            "arn:aws:elasticloadbalancing:eu-central-1:111122223333:"
            "listener-rule/app/my-alb/50dc6c495c0c9188/f2f7dc8efc522ab2/9683b2d02a6cabee"
        )
        assert (
            extract_resource_id_from_arn(arn, "elasticloadbalancing:listener-rule")
            == "app/my-alb/50dc6c495c0c9188/f2f7dc8efc522ab2/9683b2d02a6cabee"
        )

    def test_generic_slash_resource_takes_last_segment(self) -> None:
        arn = "arn:aws:ec2:eu-central-1:111122223333:instance/i-0abcd1234"
        assert extract_resource_id_from_arn(arn, "ec2:instance") == "i-0abcd1234"

    def test_colon_only_resource_takes_last_segment(self) -> None:
        arn = "arn:aws:sns:eu-central-1:111122223333:my-topic"
        assert extract_resource_id_from_arn(arn, "sns:my-topic") == "my-topic"

    def test_elb_arn_without_a_path_yields_none(self) -> None:
        # An elasticloadbalancing ARN with no slash carries no id path.
        assert (
            extract_resource_id_from_arn(
                "arn:aws:elasticloadbalancing:eu-central-1:111122223333:listener",
                "elasticloadbalancing:listener",
            )
            is None
        )


class TestShouldUseResourceGroupsApi:
    def test_any_tag_triggers_the_tag_path(self) -> None:
        assert should_use_resource_groups_api("env", "prod") is True
        assert should_use_resource_groups_api("env", None) is True
        assert should_use_resource_groups_api(None, "prod") is True

    def test_no_tags_means_traditional_path(self) -> None:
        assert should_use_resource_groups_api(None, None) is False
