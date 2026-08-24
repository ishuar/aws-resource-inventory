"""
ARN id extraction, shared by both scan paths.

The per-service processors (ELBv2 ids) and the Resource Groups tag path
both derive a resource's id from an observed ARN; this module is the one
place that logic lives. ARN format:
arn:partition:service:region:account:resource.
"""

# Resource types whose AWS id is itself a multi-segment path: the id is
# everything after the resource-type segment, not the last slash.
#   ELBv2  loadbalancer/app/${Name}/${LbId}, targetgroup/${Name}/${TgId},
#          listener/app/${Name}/${LbId}/${ListenerId}, listener-rule/…
#   ECS    service/${ClusterName}/${ServiceName}
# Taking the last segment instead would return the tail of an id, not an
# id: two ECS services with one name in different clusters would collide.
# Sibling ECS types are single-segment and must not be listed here —
# cluster/${Name} and task-definition/${Family}:${Revision}.
PATH_SHAPED_IDS = ("elasticloadbalancing:", "ecs:service")


def extract_resource_id_from_arn(arn: str, resource_type: str) -> str | None:
    """Extract the resource id from an AWS ARN based on resource type.

    Returns None when the ARN carries no extractable id — callers decide
    whether that means skip or fail; it must never surface as "N/A".
    """
    if resource_type == "s3:bucket":
        # S3 buckets: arn:aws:s3:::bucket-name
        return arn.split(":::")[-1] if ":::" in arn else None
    if resource_type.startswith(PATH_SHAPED_IDS):
        _, sep, rest = arn.partition("/")
        return rest if sep and rest else None
    if "/" in arn:
        # Most resources: arn:aws:service:region:account:resource-type/resource-id
        return arn.split("/")[-1]
    # Some resources use a colon separator instead.
    return arn.split(":")[-1]
