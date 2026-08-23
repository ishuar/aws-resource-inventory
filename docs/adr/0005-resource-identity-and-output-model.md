# ADR-0005: Resource identity and output model

- **Status:** accepted (2026-08-23)
- **Deciders:** ishuar

## Context

The scan output is a bare flat array with no metadata about the scan
that produced it, and the records inside it are unreliable as identity:

1. `resource_id`/`resource_arn` fall back to `"N/A"` on paths where the
   API response lacks them, so consumers cannot key on either.
2. `resource_name` is sometimes synthesised (`VPC-10.0.0.0/16`,
   `HTTPS:443`) and sometimes a copy of the id — a reader cannot tell a
   real AWS name from an invention.
3. The type vocabulary is inconsistent (`elbv2:*` under the `elb`
   service key, bare `vpc`), so output does not round-trip into
   `--service`.
4. The `aws-inventory waste` roadmap (PRODUCT.md) needs findings to
   reference resources by stable identity, and downstream analysis
   (pandas/SQL/jq) needs rectangular, self-describing data.

## Decision

### Resource identity: six keys, always

1. Every resource carries the same six keys — `region`, `type`, `id`,
   `name`, `arn`, `arn_source` — no key is ever missing.
2. `id` and `arn` are always real values, never `"N/A"`. Where AWS does
   not return an ARN we construct it; where the API returns only an ARN
   we extract the id from it.
3. `arn_source` is `"observed"` (returned by AWS) or `"constructed"`
   (built by us), so consumers know which ARNs to trust byte-for-byte.
4. `name` is the resource's real AWS name or `null` — never synthesised,
   never a copy of the id. `null` (not a missing key) keeps rows
   rectangular for pandas/Parquet/SQL.
5. `type` is strictly `<CLI service key>:<AWS type>` (e.g.
   `ec2:instance`, `vpc:vpc`, `elb:listener`): the left half is always a
   valid `--service` value, so output round-trips into the CLI.

### JSON envelope

The single scan output is a JSON document: `schema_version`, a `scan`
block (tool, account, partition, regions, source, filters, started_at,
duration_seconds), a `summary` (total, by_region, by_type), and
`resources[]` sorted by region → type → id.

```json
{
  "schema_version": 1,
  "scan": {
    "tool": { "name": "aws-resource-inventory", "version": "0.1.1" },
    "account": "123456789012",
    "partition": "aws",
    "regions": ["eu-central-1"],
    "source": "services",
    "filters": {
      "services": ["ec2", "vpc", "elb"],
      "tag_key": null,
      "tag_value": null,
      "all_services": false
    },
    "started_at": "2026-08-23T09:14:22Z",
    "duration_seconds": 12.4
  },
  "summary": {
    "total": 3,
    "by_region": { "eu-central-1": 3 },
    "by_type": { "ec2:instance": 1, "elb:listener": 1, "vpc:vpc": 1 }
  },
  "resources": [
    {
      "region": "eu-central-1",
      "type": "ec2:instance",
      "id": "i-0abc123def456789a",
      "name": "web-server-prod-01",
      "arn": "arn:aws:ec2:eu-central-1:123456789012:instance/i-0abc123def456789a",
      "arn_source": "constructed"
    },
    {
      "region": "eu-central-1",
      "type": "elb:listener",
      "id": "app/my-alb/1a2b3c4d5e6f7g8h/9i8j7k6l",
      "name": null,
      "arn": "arn:aws:elasticloadbalancing:eu-central-1:123456789012:listener/app/my-alb/1a2b3c4d5e6f7g8h/9i8j7k6l",
      "arn_source": "observed"
    },
    {
      "region": "eu-central-1",
      "type": "vpc:vpc",
      "id": "vpc-0f1e2d3c",
      "name": null,
      "arn": "arn:aws:ec2:eu-central-1:123456789012:vpc/vpc-0f1e2d3c",
      "arn_source": "constructed"
    }
  ]
}
```

Field notes:

- `partition` is read from the caller's own ARN, never hardcoded, so
  GovCloud and China produce correct constructed ARNs.
- `by_region` makes a partially-failed scan visible — an errored region
  shows a low count instead of silently vanishing.
- `summary.by_service` is deliberately absent: derivable from `by_type`
  by splitting on `:`.
- `started_at` and `duration_seconds` make the envelope
  non-deterministic by design; comparisons (e.g. `e2e-diff`) must
  compare `.resources` only.

### schema_version bump policy

- Bumps **only** on breaking changes: renaming or removing a key,
  changing a field's type, meaning, or the sort order.
- Additive fields never bump. Version 1 is expected to survive the
  whole waste-verb roadmap as long as changes stay additive.

## Rejected alternatives

1. **Bare flat array, no envelope.** The output cannot say which
   account, regions, filters, or tool version produced it — every file
   is context-free and unverifiable. The envelope makes each file
   self-describing at the cost of one wrapper object.
2. **`account` as a per-resource field today.** A resource does belong
   to exactly one account, but a scan observes exactly one account, so
   the value is constant per file — and every ARN already carries it.
   It lives in `scan.account`. If multi-account scanning arrives, the
   direction is decided now: add a flat per-record `account` field.
   Nesting per-account blocks (e.g. `scans[].resources[]`) is rejected —
   it relocates the account-per-row need instead of removing it (every
   consumer that flattens must re-attach the account correctly, every
   time), it undoes the flat rectangular array this schema exists to
   provide, and it turns cross-account questions into merge-then-
   reattach instead of a `GROUP BY`. The industry norm agrees: AWS
   Config aggregators, Steampipe, CloudQuery, and the Cost & Usage
   Report all use flat rows with an account column. A zero-cost interim
   exists today: loop over profiles, one self-describing file per
   account.
3. **Splitting `resource_type` into two fields** (`service` + `type`).
   Two fields invite drift between them; one string with a strict
   `<service key>:<AWS type>` grammar is both, and splits trivially on
   `:` when needed.
4. **Keeping markdown as a scan output format.** One scan format keeps
   the scan verb simple (CLAUDE.md rule 12). Reporting is a downstream
   concern: a future `aws-inventory report results.json` verb can render
   any report from the JSON without the scanner carrying formatters.

## Consequences

- Consumers can key on `id` or `arn` unconditionally; findings from the
  waste verb reference resources by this identity.
- Constructed ARNs are best-effort reconstructions — `arn_source` exists
  precisely so consumers can tell them apart from observed ones.
- Some resources now show `name: null` where a synthesised name used to
  appear; renderers must fall back to the id for display.
- The record contract test (`tests/test_resource_shape.py`) pins the six
  keys and the type vocabulary; changing either is a deliberate act and
  a `schema_version` review.
- Diff tooling must compare `.resources` only, because the `scan` block
  is non-deterministic by design.
