# 0011 — The waste findings document

**Status:** Accepted (2026-08-26)

## Context

`aws-inventory waste` (PRODUCT.md, decisions 9–18) judges the inventory
and must report findings to both humans and downstream systems. The
inventory already has a self-describing envelope (ADR-0005) whose
`scan` block states account, regions, filters, timings and failures
(ADR-0010). The findings output needed a serialized form that:

1. feeds downstream deletability checks (JSON is first-class,
   decision 15);
2. states its own completeness — a partial scan must be visible in the
   document, not only in the terminal (decision 16);
3. joins back to the inventory without translation.

## Decision

One self-describing JSON findings document, a sibling of the envelope,
built by the pure `build_findings_document`
(`aws_resource_inventory/waste/document.py`):

- **`schema_version`** — its own counter, starting at 1. The two
  documents are distinguished by `findings[]` vs `resources[]`, never
  by version number.
- **`scan`** — exactly the envelope's block, built by the shared
  `scan_block` helper in `lib/envelope.py` (one home for the shape),
  including `errors`. Exit codes mirror scan: 0 complete, 3 partial,
  1 no usable inventory.
- **`waste`** — the judgment inputs: `managed_tag`, `trust_tags`, and
  the rule thresholds. Recorded so a finding can be reproduced and
  re-judged; additive to the shape agreed in decision 15.
- **`summary`** — `total`, `by_confidence` (all three levels always
  present), `by_rule` (every registered rule reports, 0 when silent;
  `tag-drift` appears only when the opt-in provider ran) — stable keys
  so consumers diff runs.
- **`findings[]`** — the envelope's record vocabulary
  (`region/type/id/name/arn/arn_source`) plus `rule`, `confidence`,
  `evidence` (AWS field names verbatim; derived values snake_case),
  `suggested_action`. Sorted region → type → id → rule (one resource
  can carry several findings). The terminal table instead orders by
  confidence — the human sort.

## Consequences

- Downstream systems join findings to inventory on `arn` with zero
  translation, and detect partial evidence from `scan.errors` plus the
  exit code.
- The schema is pinned by tests/test_waste_document.py; changing any
  key is a deliberate act. `schema_version` bumps only on breaking
  changes; additive fields don't bump it.
- A findings document never embeds the inventory it judged — run
  `scan` for that. Anything the rules computed lives in `evidence`;
  anything AWS said lives there verbatim.
