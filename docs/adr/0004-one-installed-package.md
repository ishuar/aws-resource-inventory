# ADR-0004: Everything ships inside one `aws_resource_inventory` package

- **Status:** accepted (2026-08-22)
- **Deciders:** ishuar

## Context

Fixing the 0.1.0 packaging bug (ADR-0002, PR #45) meant listing every
importable name in `[tool.poetry] packages`. That made the real shape of
the wheel visible:

```
aws_scanner.py    aws_scanner_lib/    cli.py    services/
```

Four top-level names installed straight into site-packages, two of them —
`cli` and `services` — about as generic as names get. In an isolated
install (`uv tool install`, `pipx`, `uvx`) that is harmless. But
`pip install aws-resource-inventory` into a shared project virtualenv
drops a top-level `cli` and `services` into that environment, where they
can shadow or collide with the project's own modules.

The `aws_scanner*` names were also left over from the rename to
`aws-resource-inventory`; nested under a package, the `aws_` prefix is
pure redundancy.

The timing forced the decision: 0.1.0 is broken and being yanked, 0.1.1
is not out. Nothing in the wild depends on the current layout, so this
costs nothing now and becomes a breaking change later.

## Decision

1. **One top-level name.** Everything moves under
   `aws_resource_inventory/`, and the wheel claims exactly that name plus
   its `dist-info`.
2. **Console scripts point inside it** —
   `aws_resource_inventory.cli:app` for both `aws-inventory` and
   `aws-resource-inventory`.
3. **Retire the `aws_scanner*` prefix** in the same move, since every
   affected import is already being rewritten:
   - `aws_scanner_lib/` → `aws_resource_inventory/lib/`
   - `aws_scanner.py` → `aws_resource_inventory/orchestrator.py`
     (it owns session handling, credential validation and the region
     fan-out — `orchestrator` says that; `aws_inventory` inside
     `aws_resource_inventory` would only stutter)
   - `cli.py` and `services/` keep their names, now namespaced.
4. **No new top-level modules, ever.** Recorded in CLAUDE.md's
   architecture notes so it is enforced in review.

Deliberately out of scope, because they are behaviour changes rather than
import-path changes and belong in their own PR: the on-disk cache
directory (`/tmp/aws_scanner_cache`, since moved by ADR-0008) and the
debug log filename (`aws_scanner_debug_<timestamp>.log`, still
outstanding).

## Consequences

- `pip install` into a shared virtualenv can no longer collide with a
  project's own `cli` or `services` modules.
- Import paths are `aws_resource_inventory.lib.*` and
  `aws_resource_inventory.services.*`. This is a breaking change for
  anyone importing the modules directly — acceptable only because it
  lands before 0.1.1, the first installable release.
- `release-please-config.json`'s `extra-files` now stamps the version
  into `aws_resource_inventory/__init__.py`. Missing this would silently
  stop version bumps.
- mypy `files`, ruff per-file-ignores, coverage `source` and the pytest
  `--cov` target all follow the new paths.
- This is a restructure, not a refactor by rule 4's definition: test
  imports had to change. The edits are mechanical — import paths only, no
  assertions touched.
- Verified end-to-end rather than by inspection: 135 tests pass, the
  wheel exposes one top-level name, and a clean-venv install runs both
  console scripts plus `--service <TAB>` completion.
