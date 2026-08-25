# ADR-0010: Scan output records its own failures

- **Status:** accepted (2026-08-26)
- **Deciders:** ishuar

## Context

An errored region and a genuinely empty region both read
`"eu-west-1": 0` in the envelope. Worse, most real AWS errors died at
**service** level: `scan_service`, the engine's parallel-collection
guard, and the tagging path's nested catches all logged the exception
and returned empty data, so an `AccessDenied` on ec2 read as "ok
region, zero ec2". A scan where every region errored still exited 0.

ADR-0005 claimed `by_region` "makes a partially-failed scan visible" —
disproven: a `0` that could mean *empty* or *errored* is not visible.
PRODUCT.md §2.3 ("evidence, honest confidence") makes this a product
guarantee: the envelope is the evidence base the `waste` verb will
consume, and evidence that can be silently incomplete is not honest.
Decision recorded in PRODUCT.md's decision log: **the envelope always
states its own completeness.**

## Decision

1. **Failures are data, not log lines.** Every swallow layer now
   propagates or records: the engine's `run_parallel`/`map_parallel`
   guards are deleted (expected per-item conditions like s3's
   `NoSuchTagSet` are each scanner's own narrow catch), `scan_service`
   propagates, `scan_region` catches per service and records,
   `scan_all_tagged_resources`' nested catches are deleted, and
   `perform_scan` returns `(results, errors)`.
2. **`scan.errors` — always present, `[]` when clean.** An array of
   `{"region", "service", "message"}` objects, sorted region → service;
   `service: null` means the whole region failed (tagging path, region
   timeout, crash). Always-present-but-empty is the honesty mechanism:
   `[]` states the scan is complete; a missing key only means the
   document predates this ADR. Additive — `schema_version` stays 1.
   `summary.by_region` stays plain ints; `scan.errors` is the single
   source of failure truth.
3. **Exit codes state how much of the scan ran:** `0` complete, `3`
   partial (some scan units errored, envelope written), `1` no usable
   inventory — pre-scan hard failure or every region wholly failed
   (the envelope is still written first: evidence first, verdict
   second). A region is wholly failed when it errored region-wide
   (`service: null`) **or** every service scanned in it errored —
   either way nothing was inventoried there. `2` is never used: click
   owns it for usage errors. `--refresh` mode keeps looping regardless
   of per-scan failures.

## Consequences

- A consumer reading only the JSON knows whether the inventory is
  complete; scripts distinguish "trust fully" (0) from "usable but
  partial" (3) without parsing it.
- **Breaking:** scripts relying on exit 0 for partial scans must adapt
  (pre-1.0, no migration path — rule 20's spirit).
- A service scan that fails part-way now errors that service in that
  region rather than emitting partial results: granularity is
  service × region, not per describe call.
- The `waste` verb must downgrade or refuse on a partial inventory
  (roadmap note in PRODUCT.md; not built yet).
- ADR-0005's "visible via `by_region`" claim is corrected to point
  here.

## Rejected alternatives

- **`scan.regions_status` map (region → ok/error):** duplicates the
  region list a third time, its "ok" rows carry no information, and it
  cannot express service-level failures without nested structure.
- **Absent-when-clean `errors` key:** makes "complete scan" and
  "pre-ADR-0010 document" indistinguishable forever, since additive
  fields never bump `schema_version`.
- **`by_region: int | null`:** a breaking type change and a second
  place stating the same fact.
- **Exit 2 for partial:** collides with click's usage-error convention.
