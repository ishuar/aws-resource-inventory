# PRODUCT.md — `aws-inventory waste`

**Status:** Spec agreed (grilling session, 2026-08-22). Groundwork shipped:
the RDS and EFS scanners (§4) and the typed `Resource` record are on main;
the `waste` verb itself is not yet implemented.
**One-liner:** Find AWS resources that are still generating costs but are no longer used, and report them with evidence and an estimated monthly saving — read-only, zero setup.

---

## 1. Problem

AWS accounts accumulate waste: volumes nobody detached from bills, Elastic IPs pointing at nothing, databases stopped months ago, resources created by hand and forgotten. Existing answers are either heavy (CUR + Athena pipelines), gated (Trusted Advisor needs a Business support plan), or require account-level opt-ins (Compute Optimizer). There is no tool you can point at *any* account with read-only credentials and get an actionable waste report in one command.

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
    resource_arn: str
    resource_type: str            # "ec2:volume"
    region: str
    rule: str                     # "ebs-unattached"
    confidence: str               # "certain" | "likely" | "review"
    evidence: dict                # {"status": "available", "create_time": "2024-01-03"}
    estimated_monthly_cost: float | None   # from static price table; None if unknown
    suggested_action: str         # "delete" | "snapshot-then-delete" | "review"
```

Confidence semantics:

| Level | Meaning | Example |
|---|---|---|
| `certain` | The resource state proves non-use | EBS volume with `Status: available` |
| `likely` | Strong signal, small chance of false positive | Instance stopped > 90 days |
| `review` | Worth a human look, no claim of waste | Resource missing the managed tag |

### SignalProvider — the one seam

```
evaluate(inventory, session, region, config) -> list[Finding]
```

Providers are registered in a dict, mirroring `services/registry.py`. No plugin framework — a registry dict is the whole abstraction. v1 ships **two** providers (one adapter is a hypothetical seam; two is a real one) — with the honest caveat that the tag-drift provider is opt-in: it runs only when `--managed-tag` is given, so a default run exercises just the state-rules provider. Both are still real, tested implementations behind the same seam.

### Rule — the unit inside the state-rules provider

A pure function: predicate + evidence + action over inventory data the scanner already fetched. Cheap to add (~30 min each, testable with a fixture dict). The expensive unit is a **service scanner** (~half a day each); each new scanner unlocks several rules.

## 4. v1 specification

### CLI

```bash
# State rules only
aws-inventory waste --regions eu-central-1

# + tag-drift provider (tag is REQUIRED to enable it — there is no default tag)
aws-inventory waste --managed-tag managed_by=terraform

# Org guarantees "tagged = maintained": drift findings upgrade review -> likely
# and count toward the savings total. Requires --managed-tag.
aws-inventory waste --managed-tag managed_by=terraform --trust-tags
```

Output: existing table/JSON pipeline. Summary line: findings count + total estimated monthly savings (only `certain` + `likely` findings are summed).

### Provider 1: state rules

Deterministic checks over the existing inventory plus two new scanners (**RDS** — including Aurora cluster snapshots — and **EFS**; both shipped, registered in `services/registry.py`). Elastic Beanstalk is deliberately skipped: its resources are EC2/ASG/ELB underneath and already visible.

| Rule | Trigger | Confidence | Action |
|---|---|---|---|
| `ebs-unattached` | `Status == "available"` | certain | snapshot-then-delete |
| `eip-unassociated` | no `AssociationId` | certain | delete |
| `elb-no-targets` | LB/target group with zero registered targets | likely | review |
| `ec2-long-stopped` | stopped > N days (default 90, configurable) | likely | review |
| `snapshot-orphaned` | source volume/AMI no longer exists | likely | delete |
| `ami-unused` | no instance references, older than N days | likely | delete |
| `rds-stopped` | DB stopped (still billing storage; auto-restarts after 7 days) | likely | review |
| `efs-empty` | `SizeInBytes` ≈ 0 or no mount targets | likely | review |

Thresholds (the `N`s) are implementation details, configurable via flags, decided during build.

### Provider 2: tag-drift

`inventory ∖ tagged-set = unmanaged`. Left side: the per-service scanners (describe calls see *everything*, including never-tagged resources). Right side: `resource_groups_utils.py` (already built). **No Cost Explorer involved** — it was never needed for the diff, only for ranking (v3).

- Runs **only** when `--managed-tag KEY[=VALUE]` is given. No default tag — the tool never assumes an org's convention.
- Findings default to `confidence: review` (drift ≠ proof of waste).
- `--trust-tags` upgrades them to `likely` and includes them in savings totals — the user declaring "in our org, untagged means abandoned" is a runtime input, not a baked-in assumption.

### Cost estimation

Bundled static price table (approximate on-demand prices for the ~10 resource types v1 covers: gp3 GB-month, EIP-hour, snapshot GB-month, RDS storage GB-month, EFS GB-month, …). Labeled "estimate" in output. No Pricing API calls in v1 (its SKU/filter model is real per-service work — later improvement).

### Explicitly out of scope for v1

Cost Explorer, Cost Optimization Hub, CloudWatch metrics, CUR, EOL detection, any remediation output, multi-account.

## 5. Roadmap

| Version | Feature | Why this order |
|---|---|---|
| **v1** | State rules + tag-drift providers, RDS/EFS scanners, static prices | Zero setup, validates `Finding` + the seam with two real adapters |
| **v2** | Cost Optimization Hub provider (`ListRecommendations`) | Free, one API call, AWS-computed *idle running* resources + savings — the class v1 can't see. Graceful "enable it here" hint when the account isn't enrolled. Dedup vs v1 by ARN |
| **v3** | Cost ranking: Cost Explorer service-level spend to prioritize findings; optional CUR adapter for orgs that have it | Ranking, not discovery — needs validated findings first |
| **v4** | CloudWatch DIY signals for gaps AWS doesn't cover (idle NAT gateways, zero-connection RDS) | Most expensive signal class; only where v2 doesn't reach |
| **Later** | EOL/lifecycle report (`aws-inventory lifecycle`?) — deprecated Lambda runtimes, RDS engine EOL, EKS versions | Different axis: "must upgrade", not "can delete". Separate verb so it doesn't blur the waste story |

## 6. Improvement backlog (brainstormed, unordered, post-v1)

**Report usefulness**
- `.wasteignore` / suppression file: accepted findings stay out of future reports (ARN + rule + optional expiry).
- Run-over-run diff: "new waste since last scan" (needs a stored previous report; local JSON is enough).
- HTML report artifact for sharing with a team; Slack/email delivery.
- CI mode: stable JSON schema + non-zero exit when estimated savings exceed a threshold ("waste budget").

**Signal quality**
- Partial-inventory honesty in `waste` (decision log #8, required before v1 ships): when the scan envelope's `scan.errors` is non-empty, downgrade the confidence of findings in affected regions/services or refuse to report on them — a resource in an errored region must never produce a false "unused" finding.
- Terraform state cross-check: instead of *trusting tags*, parse tfstate the user points at — provenance from the source of truth. Strictly stronger than `--trust-tags`. Must support many small states (monorepo norm): repeatable `--tfstate` with globs and/or an S3 backend prefix; extract each resource's `arn` (fallback `id`), **union all states into one managed set**, diff = inventory ∖ union. Overlaps collapse in the union; foreign-account entries never match by ARN; warn on stale states (report per-file `serial`/last-modified). A third adapter behind the same `SignalProvider` seam.
- More zero-cost rules: CloudWatch log groups without retention, incomplete S3 multipart uploads, buckets without lifecycle policies, unused security groups, idle VPC endpoints, unassociated route tables, default-VPC clutter.
- Live prices via Pricing API (replace/augment the static table, per resource type as needed).
- Compute Optimizer as an alternative/companion adapter to the Hub.

**Scale**
- Multi-account: assume-role fan-out across an AWS Organization; aggregate report.
- Scheduled runs (cron/CI) feeding the diff feature.

**Related idea (unscheduled):** support `--all-services` without tag filters — complements the tag-drift provider (Tagging API only sees tagged/previously-tagged resources; the per-service scanners remain the authoritative left side of the diff).

## 7. Decision log (grilling session, 2026-08-22)

1. **Identity:** new verb `waste` in this repo — not a separate package, not a pivot.
2. **v1 scope:** state rules + tag-drift providers; add RDS + EFS scanners; skip Elastic Beanstalk.
3. **Cost numbers in v1:** static bundled price table, labeled estimates. No Cost Explorer for discovery — inventory itself is the left side of the diff.
4. **Remediation:** report-only, forever read-only IAM. Findings carry a `suggested_action` string but the tool never mutates.
5. **Verb name:** `waste`.
6. **Tag semantics:** no default managed tag; `--managed-tag` is required to enable tag-drift; `--trust-tags` upgrades drift findings and requires `--managed-tag`.
7. **Confidence honesty (PR #22 review):** `elb-no-targets` downgraded certain → likely — zero targets is a strong signal, not proof (a freshly provisioned LB legitimately has none). `certain` is reserved for states that prove non-use, per §3's own definition.
8. **The envelope always states its own completeness (grilling session, 2026-08-26):** partial-scan visibility is a product guarantee, not internal quality — the scan envelope is the evidence base `waste` consumes, and §2.3 is hollow if that evidence can be silently incomplete. `scan.errors` (always present, `[]` when clean) + exit codes 0/3/1; engineering decisions in ADR-0010. Consequence for `waste`: it must downgrade confidence or refuse when its input inventory is partial (see §6).

## 8. Open questions (decide during implementation)

- Exact thresholds (`ec2-long-stopped` days, `ami-unused` age) and their flag names.
- Report layout: group by rule, by service, or by cost (descending cost is the likely default).
- Where `Finding` and the provider registry live (`aws_resource_inventory/lib/waste/`?) — follow existing package conventions.
