# CLAUDE.md

**aws-resource-inventory** — Python CLI that inventories AWS resources
across regions/services (boto3, typer, rich). Entry points:
`aws-inventory`, `aws-resource-inventory`. The old `aws-scanner` and
`aws-scan` commands were removed before first release — do not
reintroduce them.

## Product context

- Today: a read-only multi-region **inventory** scanner covering eight
  services (ec2, s3, ecs, efs, elb, vpc, rds, autoscaling) with tag
  filtering (Resource Groups Tagging API path). Releases are automated
  (release-please + trusted PyPI publishing gated by a manual approval).
- Next: **`aws-inventory waste`** — find resources still generating
  costs but no longer used. `PRODUCT.md` is the spec of record: the
  `Finding` type, the `SignalProvider` seam, v1 rules, roadmap, and the
  decision log. Product guarantees (non-negotiable): read-only forever,
  zero setup, evidence with honest confidence levels.

## Non-negotiable engineering rules

1. **Best practice over workaround — always.** Fix the cause, not the
   symptom. If a tool's default is wrong for us, configure it explicitly
   and say why in a comment; never patch around it silently. Transitional
   measures must be named as such and completed (the minimal ruff
   `select` pin was transitional; the curated rule set completed it).
2. **Explicit over implicit configuration.** Tool behaviour must never
   change as a side effect of a version bump. Example: `ruff.lint.select`
   is pinned in pyproject because ruff's built-in defaults drift between
   releases. Expanding a rule set is its own deliberate PR.
3. **Test-first for every behaviour change.** Write the failing test,
   confirm it is red, then fix. Update only the tests that pin the
   behaviour being deliberately changed.
4. **Pure refactors ship with zero test edits.** If a refactor needs a
   test change, it is not a refactor — split the PR.
5. **One concern per PR.** Version bumps, behaviour changes, refactors,
   and lint migrations never share a diff.
6. **PR titles are release notes.** Squash-merge titles land verbatim in
   the release-please changelog, so a title must describe the change in
   words any user understands — never internal codenames or session
   shorthand ("candidate 3", "part 1 of 3", "the wave"). Same for PR
   descriptions: what changed, why, and how it was verified.
7. **Don't hand-roll what the platform provides.** botocore's adaptive
   retry mode owns transient-error retries (the old retry_with_backoff
   wrapper is deleted — do not reintroduce one). Same instinct applies to
   pagination (use paginators, always) and diffing.
8. **Unused features get deleted, not fixed.** `--compare`/deepdiff were
   removed after proving zero successful executions (the locked deepdiff
   couldn't even be imported). Apply the deletion test with evidence
   before investing in a fix.
9. **Every scanning client comes from
   `aws_resource_inventory.lib.clients.get_scan_client`** — never
   `session.client()` directly. It owns pool size, timeouts, adaptive
   retries, the creation lock (boto3 sessions are not thread-safe for
   client creation), and the `aws-resource-inventory` user-agent stamp.
10. **Verify merges against real AWS**: `scripts/e2e-diff.sh` (no args)
   fetches origin and compares `origin/main~1` vs `origin/main` scan
   output. Run it after merging anything that touches scan behaviour;
   add `--tag-key/--tag-value` to cover the Resource Groups tag path.
   Refs older than ADR-0004's package layout are refused outright: the
   venv's editable install would otherwise substitute the current
   checkout and report a false "identical". Refs predating the JSON
   envelope (lib/envelope.py) are refused the same way: the script
   compares `.resources` only (started_at/duration_seconds differ by
   design) and cannot honestly diff a flat-array output against it.
11. **Squash merges + stacked PRs**: after a squash lands, rebase any
    dependent branch onto main (`git rebase --onto main <old-base>`).
12. **Keep it simple; readable beats clever.** The simplest design that
    works wins. Code a maintainer can't follow in one read gets
    simplified, not documented around. Complexity must buy something
    measurable, and the burden of proof is on the complexity.
13. **Features get grilled before they get built.** "Makes sense" is not
    a spec. Every new feature starts with a grilling session
    (`/grilling`): walk the decision tree, one question at a time, and
    record the outcome in `PRODUCT.md`'s decision log before writing
    code. This applies even more to features that seem obviously good.
14. **Boy-scout rule — never silently swallow a finding.** When work
    reveals an adjacent problem (a stale doc line, a dead path, a
    misleading name, a missing test), do not ignore it: fix it in the
    same PR when it is trivial and the same concern; otherwise say it
    out loud and queue it (issue, PRODUCT.md backlog, or the roadmap
    note here) in the same session it was found. For doc staleness
    specifically, rules 16 and 18 apply.
15. **Two real implementations before an abstraction.** Don't introduce
    a seam, interface, or config knob for a hypothetical second case.
    Registry dicts over plugin frameworks
    (`aws_resource_inventory/services/registry.py` is the house
    pattern). One adapter is a hypothetical seam; two make it real.
16. **Docs ship in the same PR as the change.** A change to behaviour,
    CLI, architecture, or product scope updates every document that
    describes it — README, this file, `PRODUCT.md`, the relevant ADR —
    in the same diff. A doc that contradicts the code is a bug and gets
    triaged like one. (This does not break rule 5: the doc update is
    part of the concern, not a second concern.)
17. **Engineering decisions land in ADRs.** Every technical decision —
    architecture, domain modelling, language or tooling choice, an
    adopted best practice — gets a dedicated ADR in
    `docs/adr/NNNN-short-slug.md`, written when the decision is made,
    not reconstructed later. Format: Status / Context / Decision /
    Consequences. Superseded ADRs are marked superseded and kept —
    the history is the point. Division of record: `PRODUCT.md`'s
    decision log owns product decisions (*what* to build, rule 13);
    ADRs own engineering decisions (*how* to build it).
    `docs/adr/0001-record-decisions-in-adrs.md` is both the first
    instance and the template.
19. **Mirror AWS's own terminology exactly.** Names in output match
    what the AWS API returns: if the ARN segment is `internet-gateway`,
    emit `internet-gateway` — never `internet_gateway`. No snake_case
    normalisation, no house synonyms (`image`, not `ami`;
    `loadbalancer`, not `load_balancer`; `autoScalingGroup` keeps AWS's
    camelCase). Derive the spelling from the ARN resource-type segment
    or the API response field, never from what reads nicely — users
    grep and cross-reference against AWS consoles, docs and other
    tools, and a house vocabulary forces a translation step.
20. **Breaking a contract to get closer to AWS is allowed — and must be
    recorded.** Prefer the break over a house workaround; do not
    propose a migration path for a pre-1.0 output contract. Every
    deliberate break lands in an ADR (rule 17) stating what broke, why,
    and what consumers must change.

18. **Stale instructions are worse than no instructions.** This file
    and the ADRs are loaded as context into every AI session; anything
    wrong in them gets confidently repeated. Prune or update them in
    the same PR that invalidates them; delete roadmap/status notes once
    they ship. When a doc and reality disagree, fix the doc first
    (rule 16), then check what else trusted it.

## Git rules

- **Never commit or push to `main` — no exceptions.** This includes
  indirect writes such as `gh api PUT /contents` or any other API call
  that creates a commit on `main`.
- All changes go through a new branch (create one with `git switch -c`
  or a `git worktree`) and a pull request into `main`.

## Python style

- Type hints on every public function; `from __future__` not needed
  (3.10+). Frozen `@dataclass` for domain types (`ServiceRegistration` today;
  `Finding` arrives with the waste verb, see PRODUCT.md) — not dicts,
  not classes with behaviour.
- Pure functions for logic (rules, transforms); side effects (AWS calls,
  console output, cache) stay at the edges. Return results, don't mutate
  arguments.
- No ABCs, metaclasses, or deep class hierarchies unless two concrete
  implementations already demand them (rule 14).
- stdlib and existing dependencies first. A new runtime dependency is
  its own justified PR (what it buys, why stdlib can't).
- New modules mirror the existing layout: one service = one
  `aws_resource_inventory/services/<name>_service.py` + one registry
  entry; shared logic lives in `aws_resource_inventory/lib`.
- Comments state constraints the code can't show — never narrate what
  the next line does.

## Documentation style

- All documentation — README, `PRODUCT.md`, ADRs, and this file — is
  written using the **i-have-adhd** output style
  (`i-have-adhd@i-have-adhd` plugin skill): lead with the action,
  numbered steps with one bounded action each, no preamble, no closing
  filler, lists capped at 5. Use the skill both when generating docs
  and as the checklist when reviewing them.

## Testing

- The suite runs with **zero AWS credentials**: moto fakes AWS, conftest
  forces fake creds and redirects the pickle cache per-test.
- Tests are **characterization tests at seams** (public interfaces):
  scan_service/scan_region, the flattened resource-record shape, output
  formats, cache round-trips, each scanner against moto. Don't test
  internals; don't stub the dispatch mechanism.
- Waste rules (when built) are pure functions — test them with fixture
  dicts, no moto needed.
- CI (`.github/workflows/ci.yml`) is the merge gate; Codecov enforces
  80% patch coverage (project check tolerates 1% refactor wobble).

## Architecture notes

- **One installed package: `aws_resource_inventory/`.** Everything lives
  under it so the wheel claims a single top-level name in site-packages
  (ADR-0004). Inside it: `cli.py` (typer app and the only place rich
  rendering belongs), `orchestrator.py` (region fan-out), `lib/` (engine,
  cache, outputs, envelope, clients, logging), and `services/` (per-AWS-service
  scanners + `services/registry.py`, the single source of truth mapping
  service name → scanner + output processor). Import paths are
  `aws_resource_inventory.lib.*` and `aws_resource_inventory.services.*`;
  never add a new top-level module.
- Adding a service = one module + one `SERVICES` registry entry.
- Serialized scan output is **one self-describing JSON document**
  (`schema_version: 1`, ADR-0005): a `scan` block (tool, account,
  partition, regions, source, filters, started_at, duration_seconds,
  errors), a `summary` (total, by_region, by_type — no by_service,
  derivable), and `resources[]` with bare keys
  (region/type/id/name/arn/arn_source), sorted region → type → id.
  `scan.errors` is always present — `[]` when clean, one
  `{region, service, message}` per failed scan unit, `service: null` =
  whole region (ADR-0010); failures are data, never just log lines —
  no layer may swallow an exception back to empty results. Exit codes:
  0 complete, 3 partial, 1 no usable inventory, 2 left to click.
  Built by the pure `build_envelope` in
  `aws_resource_inventory/lib/envelope.py`; the schema is pinned by
  tests/test_envelope.py and the per-record shape by
  tests/test_resource_shape.py — changing either is a deliberate act.
  `schema_version` bumps only on breaking changes; additive fields
  don't bump it.
- Shipped: the shared scanning engine
  (`aws_resource_inventory/lib/engine.py`) —
  every scanner runs on it; pagination, parallel collection,
  ordered fan-out, and tag matching live there and nowhere else. Fully
  declarative scanners are a `Describe` dict + a 3-line function;
  imperative ones stay plain functions calling the engine helpers.
- Shipped: the typed record —
  `aws_resource_inventory/lib/records.py` `Resource`
  (frozen dataclass) is what every processor constructs and every
  output consumes; `to_record()` owns serialization to the envelope's
  bare-key record (the dataclass attributes keep their
  resource_-prefixed names — they are the internal API). `output_results`
  takes a required `source` ("services" | "tagging"), a required
  `identity`, and the envelope's scan metadata (regions, filters,
  started_at, duration_seconds — the CLI owns the clock); the tag scan is a
  hybrid whose service-shaped sections are declared by the producer
  (`SERVICE_SHAPED_SECTIONS` in resource_groups_utils) — never guessed
  from data shape.
- Shipped: real identity on every record — `resource_id` and
  `resource_arn` are never "N/A". ARNs the AWS APIs don't return are
  constructed from `CallerIdentity` (account + partition, read from the
  caller's own STS ARN by `validate_aws_credentials` — never hardcode
  the partition) using formats verified against the AWS Service
  Authorization Reference; every record states `arn_source`
  ("observed" | "constructed"). Ids are extracted from observed ARNs by
  `aws_resource_inventory/lib/arn.py`, the one home of ARN id extraction
  for both scan paths — no processor hand-rolls one. Where AWS's own ARN
  format is a path the id keeps everything after the resource-type
  segment, never the last slash (`PATH_SHAPED_IDS`, ADR-0006: ELBv2 and
  `ecs:service`). An unidentifiable raw dict is skipped
  with a log line, never emitted. Valid credentials are therefore
  required to produce output (the old show-cached-anyway fallback is
  gone).
- Test layout: the original six scanners live in
  tests/test_service_scanners.py; every newer service gets its own
  tests/test_<service>_scanner.py. The flattened-record contract for
  every producer is pinned centrally in tests/test_resource_shape.py —
  a new processor must be added there.
- Resource names are **real or null**: `resource_name` is a name AWS
  itself supplies (a Name/name attribute or the `Name` tag) or `None` —
  never synthesized, never a copy of the id — and the serialized record
  always carries the key (JSON `null` when absent). The `Name` tag has
  exactly one reader, `lib/records.py` `name_from_tags`, called by every
  producer that has tags and by the tag-scan processor, so the same
  `Name` tag yields the same name on either scan path; it absorbs ECS's
  lowercase `key`/`value` shape and RDS's `TagList` field, and drops a
  tag that merely repeats the id. Every scanner must fetch the tags AWS
  will give it — skipping a fetch is the one way that guarantee breaks.
  A name from a name *attribute* is service-path only: the Tagging API
  returns an ARN and tags, never the attribute (ADR-0005
  Consequences). Pinned per type in tests/test_resource_shape.py;
  displays fall back to the id.
- Remaining deepening work, in order: give the tag scan the five
  attribute-sourced names it cannot see (`ec2:security-group`,
  `ec2:image`, `elb:loadbalancer-*`, `elb:targetgroup`,
  `autoscaling:launch-template`) by batching one describe per type per
  region over the ARNs the Tagging API returned; unify
  the six copies of the scan-path predicate (`all_services or tag_key
  or tag_value`) behind one helper; a progress-event seam so rich
  rendering lives only in aws_resource_inventory/cli.py (plus shrinking
  the logging module
  and adding CLI-level tests); one shared scan interface over the
  per-service and tag-scan paths so retries/cancellation/progress
  apply to both. Check open PRs for live status.

## Future vision

- The product turn: **`aws-inventory waste`** per `PRODUCT.md` — the
  `Finding` type and `SignalProvider` registry sit on top of the typed
  `Resource` and the scanners; the RDS and EFS scanners it needs are
  already shipped. Roadmap `PRODUCT.md` §5 (state rules + tag drift →
  Cost Optimization Hub → cost ranking → CloudWatch signals), backlog
  §6, decisions §7.
- Optional post-roadmap chore: migrate poetry → uv (decided: not
  before; CI stays on poetry until then).
