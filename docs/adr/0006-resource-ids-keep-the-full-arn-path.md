# ADR-0006: Resource ids keep the full ARN path, not the last segment

- **Status:** accepted (2026-08-25)
- **Deciders:** ishuar

## Context

ADR-0005 decided that "where the API returns only an ARN we extract the
id from it", but never said *what* the id is when the ARN's resource
portion is itself a path. `lib/arn.py` answered it two different ways:

1. ELBv2 kept everything after the resource-type segment
   (`app/my-alb/50dc6c495c0c9188`), because AWS's own ARN format is
   `loadbalancer/app/${Name}/${LbId}`.
2. Everything else took `arn.split("/")[-1]`.

That default is correct only while an id contains no slash — true for
`vpc/vpc-0a1b2c3d`, `instance/i-0abc123`, `cluster/prod`. It is wrong
for ECS services. AWS's format is
`arn:aws:ecs:${Region}:${Account}:service/${ClusterName}/${ServiceName}`,
so the last segment is the *tail* of an id, not an id.

`ecs_service.py` compounded it by not using `lib/arn.py` at all: it set
`resource_id = serviceName` directly, the one producer bypassing the
module CLAUDE.md calls the single home of ARN id extraction.

The consequence is a genuine collision. Two services named `api`, one in
`prod` and one in `staging`, flattened to the same record:

```json
{ "region": "eu-central-1", "type": "ecs:service", "id": "api", "name": null }
{ "region": "eu-central-1", "type": "ecs:service", "id": "api", "name": null }
```

Identical except `arn`. `(region, type, id)` is also the envelope's sort
key, so the two rows are not just indistinguishable — their order is not
deterministic either, which contradicts ADR-0005's guarantee that
identical input always serializes identically.

## Decision

1. A resource's `id` is **everything after the ARN's resource-type
   segment** whenever AWS's own format for that type is a path. Never
   the last slash-separated segment.
2. `lib/arn.py` names those types in one place, `PATH_SHAPED_IDS`,
   today `("elasticloadbalancing:", "ecs:service")`. Sibling types stay
   out of it: `ecs:cluster` is `cluster/${Name}` and
   `ecs:task-definition` is `task-definition/${Family}:${Revision}` —
   both single-segment.
3. Every producer derives ids through `extract_resource_id_from_arn`.
   No processor hand-rolls an id from an API name field when the ARN
   carries the identity.
4. The test for adding a type to `PATH_SHAPED_IDS` is AWS's published
   ARN format, not whether the id looks tidy (CLAUDE.md rule 19).

## Consequences

- **Breaking (CLAUDE.md rule 20).** `ecs:service` ids change from
  `api` to `prod/api`. Anything keying on the old value must re-key.
  Pre-1.0, no migration path is offered. `schema_version` does not bump:
  no key is renamed, removed, or retyped — ADR-0005's bump policy covers
  shape, and this is a value correction within an existing key.
- Same-named ECS services in different clusters are now distinct
  records, and the envelope's sort key is total again for them.
- An ECS service tagged `Name=api` now reports `name: "api"` instead of
  `null`. `name_from_tags` drops a `Name` tag that merely repeats the
  id; the id is now `prod/api`, so the tag is no longer a copy of it.
  This is ADR-0005 point 4 working as written against a corrected id.
- ECS service ids round-trip: `prod/api` pastes back into
  `aws ecs describe-services --cluster prod --services api` with the
  cluster in hand, which the bare name never gave you.
- The rule is now stated once and shared, so the next path-shaped type
  is a one-line addition with a test, not a new special case.

## Alternatives rejected

- **Leave the id as `serviceName` and rely on `arn` to disambiguate.**
  Makes `arn` load-bearing for identity, which ADR-0005 explicitly
  assigns to `id`, and leaves the sort key non-total.
- **Add a separate `cluster` field to ECS records.** Breaks the
  rectangular six-key contract (ADR-0005 point 1) for one service.
- **Detect multi-segment ids by counting slashes at runtime.** Guesses
  from data what AWS publishes as a format; `task-definition/api:3`
  and a legacy `service/api` would be classified by accident rather
  than by AWS's spec.
