# PRODUCT.md — `aws-inventory waste`

**Status:** Spec agreed (grilling session, 2026-08-22; reshaped 2026-08-26 —
cleanup-first, cost estimation deferred, 10 state rules, CloudWatch provider
pulled forward to v2). Groundwork shipped: the RDS and EFS scanners (§4), the
typed `Resource` record, and partial-scan honesty (`scan.errors`, ADR-0010)
are on main; the `waste` verb itself is not yet implemented.
**One-liner:** Find AWS resources that are abandoned — cluttering the account, often still on the bill — and report them with evidence and an honest confidence level, read-only, zero setup.

---

## 1. Problem

AWS accounts accumulate abandoned resources: volumes nobody detached from bills, Elastic IPs pointing at nothing, databases stopped months ago, empty ECS clusters, resources created by hand and forgotten. Existing answers are either heavy (CUR + Athena pipelines), gated (Trusted Advisor needs a Business support plan), or require account-level opt-ins (Compute Optimizer). There is no tool you can point at *any* account with read-only credentials and get an actionable cleanup report in one command.

**Cleanup is the primary motivation at this stage; cost saving is the
frequent side effect, not the gate** (decision 9). A resource that costs
nothing but signals abandonment is a finding — confidence states how strong
the signal is, and evidence states what backs it.

We already have the hard part: a multi-region, multi-service inventory scanner. The product adds a judgment layer on top of it.

## 2. Product guarantees (non-negotiable)

1. **Read-only, forever.** Runs with `ReadOnlyAccess`-class credentials. Never mutates, never asks for write IAM. Trust is the moat for an audit tool.
2. **Zero setup.** Works instantly on any account. Any signal source that needs enablement (Cost Optimization Hub, CUR) must degrade to a one-line hint, never an error.
3. **Evidence, not vibes.** Every finding carries the raw facts that triggered it and an honest confidence level. The tool never claims certainty it doesn't have.

## 3. Core concepts

### Finding — the domain type everything shares

```python
@dataclass(frozen=True)
class Finding:
    resource: Resource            # the scanned identity: region, type, id, name, arn, arn_source
    rule: str                     # "ebs-unattached"
    confidence: str               # "certain" | "likely" | "review"
    evidence: dict                # verbatim AWS field names: {"State": "available", ...}
    suggested_action: str         # "delete" | "snapshot-then-delete" | "review"
```

No cost field: cost estimation is out of v1 entirely (decision 10) — the
field arrives with the feature (roadmap v4), never as a speculative slot.
Evidence keys mirror AWS's own field spelling (engineering rule: mirror AWS
terminology), so a consumer can cross-reference them against describe-call
documentation without translation.

Confidence semantics:

| Level | Meaning | Example |
|---|---|---|
| `certain` | The resource state proves non-use | EBS volume with `Status: available` |
| `likely` | Strong signal, small chance of false positive | Instance stopped > 90 days |
| `review` | Worth a human look, no claim of waste | Resource missing the managed tag |

### SignalProvider — the one seam

```
evaluate(scan_data, resources, region, config) -> list[Finding]
```

Providers are **pure functions** (decision 13): they receive the raw scan
sections (`scan_data` — the per-service describe payloads, where the state
lives) and the flattened `Resource` list (where the identity lives), joined
through a `(region, type, id)` index built by the waste orchestration. No
session, no AWS calls — every fetch lives in an inventory scanner, so waste
adds **zero** API calls of its own. Inputs a provider needs that come from
elsewhere (the tag-drift provider's tagged set, fetched via the existing
Tagging API path) are fetched by the orchestration and passed in.

Providers are registered in a dict, mirroring `services/registry.py`. No plugin framework — a registry dict is the whole abstraction. v1 ships **two** providers (one adapter is a hypothetical seam; two is a real one) — with the honest caveat that the tag-drift provider is opt-in: it runs only when `--managed-tag` is given, so a default run exercises just the state-rules provider. Both are still real, tested implementations behind the same seam.

### Rule — the unit inside the state-rules provider

A pure function: predicate + evidence + action over inventory data the scanner already fetched. Cheap to add (~30 min each, testable with a fixture dict). The expensive unit is a **service scanner** (~half a day each); each new scanner unlocks several rules — and extending an existing scanner by one describe call is minutes, thanks to the declarative engine.

Each rule **declares the raw sections it reads**, and is skipped for a
region where any of those sections errored (`scan.errors`, ADR-0010): a
cross-referencing rule fed partial data would otherwise invent findings —
`snapshot-orphaned` reading an errored (empty) volumes section would flag
every snapshot (decision 16). The skip is visible: the errored section is
already in the document's `scan.errors`.

## 4. v1 specification

### CLI

```bash
# State rules only
aws-inventory waste --regions eu-central-1

# + tag-drift provider (tag is REQUIRED to enable it — there is no default tag)
aws-inventory waste --managed-tag managed_by=terraform

# Org guarantees "tagged = maintained": drift findings upgrade review -> likely.
# Requires --managed-tag.
aws-inventory waste --managed-tag managed_by=terraform --trust-tags

# Findings as JSON, for downstream systems (file or stdout)
aws-inventory waste --regions eu-central-1 --output findings.json
aws-inventory waste --regions eu-central-1 --output -
```

`waste` **runs the scan in-process** (decision 14): the scan JSON cannot
feed it, because the envelope deliberately carries identity only — the
state a rule reads (`State`, `AssociationId`, `CreateTime`) exists only in
the raw describe data of a live run. Evidence is as fresh as the run.

Terminal output: a findings table ordered `certain` → `likely` → `review`,
then a summary line of counts by confidence (`9 findings: 2 certain ·
5 likely · 2 review`), stating partialness when `scan.errors` is non-empty.
Exit codes mirror `scan` (ADR-0010): 0 complete, 3 partial, 1 no usable
inventory.

JSON output is first-class (decision 15) — the findings document feeds
downstream systems that validate deletability:

```
schema_version   own counter, starts at 1 (a distinct document type;
                 consumers distinguish it by findings[] vs resources[])
scan             the same block the inventory envelope carries,
                 including errors
summary          total, by_confidence, by_rule
findings[]       the envelope's record vocabulary (region/type/id/name/
                 arn/arn_source) + rule, confidence, evidence,
                 suggested_action; sorted region → type → id
```

Findings only — the document never embeds the inventory it judged (run
`scan` for that; the two documents join on `arn`).

### Provider 1: state rules

Deterministic checks over the existing inventory plus two new scanners (**RDS** — including Aurora cluster snapshots — and **EFS**; both shipped, registered in `services/registry.py`). Elastic Beanstalk is deliberately skipped: its resources are EC2/ASG/ELB underneath and already visible.

| Rule | Trigger | Confidence | Action |
|---|---|---|---|
| `ebs-unattached` | volume `State == "available"` | certain | snapshot-then-delete |
| `eip-unassociated` | no `AssociationId` | certain | delete |
| `elb-no-targets` | target group with zero registered targets | likely | review |
| `ec2-long-stopped` | stopped > N days (default 90, configurable) | likely | review |
| `snapshot-orphaned` | source volume/AMI no longer exists | likely | delete |
| `ami-unused` | no instance references, older than N days | likely | delete |
| `rds-stopped` | DB stopped (still billing storage; auto-restarts after 7 days) | likely | review |
| `efs-empty` | `SizeInBytes` ≈ 0 or no mount targets | likely | review |
| `ecs-cluster-idle` | `runningTasksCount == 0` with capacity behind it | likely / review | review |
| `ecs-service-zero-tasks` | `desiredCount == 0` | review | review |

Thresholds (the `N`s) are implementation details, configurable via flags, decided during build.

`ecs-cluster-idle` carries the capacity breakdown in its evidence
(decision 12): **likely** when EC2-backed — registered container instances,
or an EC2 capacity provider whose Auto Scaling group still runs instances
(billing instances no other rule sees, since `ec2-long-stopped` only sees
*stopped* ones) — **review** when Fargate-only or empty (idle Fargate costs
nothing; the finding is cleanup signal, per decision 9). S3 has **no rule
in v1**: every candidate (empty bucket, incomplete multipart uploads) needs
per-bucket API fan-out — backlogged in §6 pending its own grilling.

### Scanner extensions v1 needs (decision 11)

Two rules read data no scanner fetched; the fix extends the scanners — the
single fetch layer — never a waste-side fetcher:

1. **EC2 `describe_addresses`** — Elastic IPs become a first-class
   inventory type (`ec2:elastic-ip`), feeding `eip-unassociated` and the
   plain `scan` alike.
2. **ELBv2 `describe_target_health`** — per-target-group health, feeding
   `elb-no-targets`. Evidence-only raw data: no new resource type, nothing
   new in the envelope.

### Provider 2: tag-drift

`inventory ∖ tagged-set = unmanaged`. Left side: the per-service scanners (describe calls see *everything*, including never-tagged resources). Right side: `resource_groups_utils.py` (already built). **No Cost Explorer involved** — it was never needed for the diff, only for ranking (v3).

- Runs **only** when `--managed-tag KEY[=VALUE]` is given. No default tag — the tool never assumes an org's convention.
- Findings default to `confidence: review` (drift ≠ proof of waste).
- `--trust-tags` upgrades them to `likely` — the user declaring "in our org, untagged means abandoned" is a runtime input, not a baked-in assumption.

### Rule management

Rules live in a **registry dict in code** (decision 18), keyed by rule name,
grouped one module per service — the same shape and muscle memory as
`services/registry.py`. All rules always run in v1: no config file, no
enable/disable flags — `confidence` separates signal strength, and JSON
consumers filter trivially. A suppression file (`.wasteignore`) stays in the
backlog until acceptance workflows are real.

### Explicitly out of scope for v1

Cost estimation of any kind (no price table, no savings totals — decision
10), Cost Explorer, Cost Optimization Hub, CloudWatch metrics (traffic rules
are v2 — decision 17), CUR, EOL detection, any remediation output,
multi-account.

## 5. Roadmap

| Version | Feature | Why this order |
|---|---|---|
| **v1** | State rules + tag-drift providers, EIP/target-health scanner extensions | Zero setup, validates `Finding` + the seam with two real adapters |
| **v2** | CloudWatch signal provider — `targetgroup-low-traffic` (requests/min below threshold) first; idle NAT gateways, zero-connection RDS | Pulled forward (decision 17): cleanup-first needs usage signals, and default ALB/NAT metrics keep zero-setup true. Needs its own grilling: measurement window, health-check traffic |
| **v3** | Cost Optimization Hub provider (`ListRecommendations`) | Free, one API call, AWS-computed *idle running* resources — dedup vs earlier providers by ARN. Graceful "enable it here" hint when the account isn't enrolled |
| **v4** | Cost estimation + ranking: static price table or Pricing API on findings; Cost Explorer spend to prioritize; optional CUR adapter | Deferred from v1 (decision 10) — ranking and dollar figures need validated findings first |
| **Later** | EOL/lifecycle report (`aws-inventory lifecycle`?) — deprecated Lambda runtimes, RDS engine EOL, EKS versions | Different axis: "must upgrade", not "can delete". Separate verb so it doesn't blur the waste story |

## 6. Improvement backlog (brainstormed, unordered, post-v1)

**Report usefulness**
- `.wasteignore` / suppression file: accepted findings stay out of future reports (ARN + rule + optional expiry).
- Run-over-run diff: "new waste since last scan" (needs a stored previous report; local JSON is enough).
- HTML report artifact for sharing with a team; Slack/email delivery.
- CI mode: stable JSON schema + non-zero exit when findings exceed a threshold ("waste budget" — by count until cost estimation ships, then by savings).

**Signal quality**
- S3 rules (own grilling first — every candidate needs per-bucket API fan-out across potentially hundreds of buckets): `s3-incomplete-multipart-uploads` (abandoned parts bill invisible storage — the only S3 rule with real cost behind it), `s3-bucket-empty` (clutter, review at best).
- `ecs-task-definition-inactive` and other free-clutter signals too weak for v1.
- Terraform state cross-check: instead of *trusting tags*, parse tfstate the user points at — provenance from the source of truth. Strictly stronger than `--trust-tags`. Must support many small states (monorepo norm): repeatable `--tfstate` with globs and/or an S3 backend prefix; extract each resource's `arn` (fallback `id`), **union all states into one managed set**, diff = inventory ∖ union. Overlaps collapse in the union; foreign-account entries never match by ARN; warn on stale states (report per-file `serial`/last-modified). A third adapter behind the same `SignalProvider` seam.
- More zero-cost rules: CloudWatch log groups without retention, buckets without lifecycle policies, unused security groups, idle VPC endpoints, unassociated route tables, default-VPC clutter.
- Compute Optimizer as an alternative/companion adapter to the Hub.
- `--from-scan file.json` input mode — possible only if the envelope ever grows evidence fields (decision 14); not planned.

**Scale**
- Multi-account: assume-role fan-out across an AWS Organization; aggregate report.
- Scheduled runs (cron/CI) feeding the diff feature.

**Related idea (unscheduled):** support `--all-services` without tag filters — complements the tag-drift provider (Tagging API only sees tagged/previously-tagged resources; the per-service scanners remain the authoritative left side of the diff).

## 7. Decision log

Entries 1–7: grilling session 2026-08-22. Entry 8: 2026-08-26 (with PR #69).
Entries 9–18: reshaping grilling session, 2026-08-26.

1. **Identity:** new verb `waste` in this repo — not a separate package, not a pivot.
2. **v1 scope:** state rules + tag-drift providers; add RDS + EFS scanners; skip Elastic Beanstalk.
3. **Cost numbers in v1:** static bundled price table, labeled estimates. No Cost Explorer for discovery — inventory itself is the left side of the diff.
4. **Remediation:** report-only, forever read-only IAM. Findings carry a `suggested_action` string but the tool never mutates.
5. **Verb name:** `waste`.
6. **Tag semantics:** no default managed tag; `--managed-tag` is required to enable tag-drift; `--trust-tags` upgrades drift findings and requires `--managed-tag`.
7. **Confidence honesty (PR #22 review):** `elb-no-targets` downgraded certain → likely — zero targets is a strong signal, not proof (a freshly provisioned LB legitimately has none). `certain` is reserved for states that prove non-use, per §3's own definition.
8. **The envelope always states its own completeness (grilling session, 2026-08-26):** partial-scan visibility is a product guarantee, not internal quality — the scan envelope is the evidence base `waste` consumes, and §2.3 is hollow if that evidence can be silently incomplete. `scan.errors` (always present, `[]` when clean) + exit codes 0/3/1; engineering decisions in ADR-0010. Consequence for `waste`: refined by decision 16.
9. **Cleanup over cost (reframe):** the primary motivation at this stage is cleanup — abandoned resources are findings even when they cost nothing. §1 and the one-liner amended; the verb stays `waste`, its meaning widened to "cost *or* clutter".
10. **Cost estimation is out of v1 entirely:** no price table, no `estimated_monthly_cost` field on `Finding` (a field arrives with its feature, never as a speculative slot), no savings totals — the summary reports counts by confidence. Cost estimation + ranking moves to roadmap v4.
11. **All 8 original rules stay; the missing data is fetched by extending scanners, never by a waste-side fetcher.** EC2 gains `describe_addresses` (`ec2:elastic-ip` becomes a first-class inventory type), ELBv2 gains `describe_target_health` (evidence-only raw data). Waste itself makes zero AWS calls — scanners are the single fetch layer.
12. **Two ECS rules added under the cleanup lens:** `ecs-cluster-idle` — `runningTasksCount == 0`; **likely** when EC2-backed (registered container instances, or an EC2 capacity provider whose ASG still runs instances — billing capacity no other rule sees), **review** when Fargate-only/empty (idle Fargate is free; the finding is clutter signal); evidence carries the capacity breakdown, no dollar figures — and `ecs-service-zero-tasks` (`desiredCount == 0`, review; scale-to-zero is often intentional). 10 state rules total. S3 rules deliberately excluded from v1 (per-bucket fan-out needs its own grilling; backlog §6).
13. **The provider seam is pure:** `evaluate(scan_data, resources, region, config) -> list[Finding]` — raw describe sections for state, flattened `Resource`s for identity, joined via a `(region, type, id)` index. No session argument, no AWS calls inside providers; orchestration fetches what a provider needs (the tag-drift tagged set) and passes it in. Chosen over enriching `Resource` with raw payloads (breaks hashability/equality of the pinned domain type) and over reshaping all 8 processors to emit pairs.
14. **`waste` runs the scan in-process.** The scan JSON cannot feed it: the envelope deliberately carries identity only (ADR-0005) — rule state exists only in a live run's raw data. Also avoids stale-evidence findings. `--from-scan` stays out unless the envelope ever grows evidence fields.
15. **The findings document:** findings only, never the inventory it judged; same `scan` block as the envelope (including `errors`); `summary` with `by_confidence`/`by_rule`; `findings[]` records use the envelope's exact vocabulary + `rule`/`confidence`/`evidence`/`suggested_action`, evidence keys verbatim AWS field names, sorted region → type → id (machine sort; the *table* orders by confidence for humans). Own `schema_version` counter. JSON is first-class — built to feed downstream deletability checks.
16. **Partial scans — report and mark, don't refuse:** the findings document carries `scan.errors`; exit codes mirror `scan` (0 complete, 3 partial, 1 no usable inventory). One hard rule-level guard: each rule declares the raw sections it reads and is skipped for a region where any of them errored — cross-referencing rules fed partial data would invent findings (`snapshot-orphaned` over an errored volumes section flags every snapshot). Refines decision 8's "downgrade or refuse".
17. **Traffic rules are v2, not v1:** "target group under N requests/minute" is CloudWatch data no describe call returns. The CloudWatch signal provider is pulled forward to v2 (default ALB/NAT metrics keep zero-setup true; it was parked at v4 only for query cost), displacing the Hub to v3. v1 stays state-only. The v2 grilling owes answers on measurement window and health-check traffic.
18. **Rules are a registry dict in code:** all rules always run in v1 — no config file, no enable/disable flags (speculative surface; `confidence` + JSON filtering cover today's needs). Thresholds are CLI flags with defaults. Code lives in `aws_resource_inventory/waste/` — a sibling feature package (findings, registry, one state-rules module per service, tag-drift), not under `lib/` (plumbing stays feature-free); rendering stays in `cli.py`.

## 8. Open questions (decide during implementation)

- Exact thresholds (`ec2-long-stopped` days, `ami-unused` age) and their flag names.
