# ADR-0009: Scan documents live in a per-user directory

- **Status:** accepted (2026-08-25)
- **Deciders:** ishuar

## Context

ADR-0008 moved the scan **cache** out of `/tmp/aws_scanner_cache`
because a predictable path in a world-writable directory exposed an
account's ids, ARNs, names, and the account number in every ARN to any
local user.

The scan **document** was left behind. With no `--output`, the CLI wrote
to `/tmp/aws_resource_inventory/<generated>.json` — the same data, the
same predictable shared path, and worse in two ways:

1. It is **durable**. The cache expires after ten minutes; the document
   sits there until something clears `/tmp`.
2. It was **undocumented**. The README said only "an auto-generated
   path", so a user running `aws-inventory scan` had no idea their
   inventory had been written somewhere world-readable.

ADR-0008's own reasoning applied to it verbatim and did not mention it.

## Decision

1. **The default document path is `$XDG_DATA_HOME/aws-resource-inventory`,
   falling back to `~/.local/share/aws-resource-inventory`.** Data, not
   cache: a scan result is an artifact the user may want to keep, and
   nothing retires it on a timer.
2. **One resolver, in `lib/paths.py`.** `user_dir(xdg_variable,
   fallback_base)` answers "where does this tool put files" once;
   `default_cache_dir()` and `default_output_dir()` both go through it.
   Two real cases make the seam real (CLAUDE.md rule 15) — before this,
   there was one.
3. **We harden only the directory we own.** The default directory is
   created `0o700` and `chmod`ed to `0o700` on every write, because
   `mkdir` applies its mode only when it actually creates the directory
   (the gap ADR-0008 closed for the cache). A directory the user names
   with `--output` is left exactly as they have it — their path, their
   permissions.
4. **A document at our default path is written `0o600`;** one at a path
   the user named is written normally. Same reasoning ADR-0008 gave for
   cache entries — the directory is the outer guard, and a file left
   world-readable is exposed the moment it is copied or that guard is
   weakened. `os.open` with an explicit mode rather than
   write-then-`chmod`, so it is never briefly world-readable.
5. **The fallback is the same on every platform.** No `~/Library`
   branch on macOS, matching ADR-0008: one path to document.

## Consequences

- **Behaviour change.** `aws-inventory scan` with no `--output` now
  writes to `~/.local/share/aws-resource-inventory/` instead of
  `/tmp/aws_resource_inventory/`. Documents already in `/tmp` are not
  migrated; they were never meant to be durable storage. Anything
  scripted against the old path must pass `--output` explicitly, which
  was always the supported way to control it.
- The document survives a reboot, which `/tmp` did not guarantee. That
  is the point of moving it to a data directory rather than a cache one.
- Documents at the default path are `0o600`, so `--output` is the way
  to produce one meant for sharing.
- The README now names the default path. "An auto-generated path" was
  not a location a user could act on.
- `/tmp` no longer appears in any runtime path. `scripts/e2e-diff.sh`
  still uses `mktemp -d` for its throwaway worktrees, which is correct:
  those are ephemeral build artifacts, not account data.

## Alternatives rejected

1. **The current working directory** (`./aws-resources-*.json`). The
   codebase has a precedent — `DEFAULT_DEBUG_LOG_DIR = Path.cwd() /
   ".debug_logs"` — and it is the most discoverable option. Rejected
   because a scan run from anywhere would drop a file there, and
   repeated scans litter whatever directory you happened to be in.
2. **Requiring `--output`.** The most explicit answer: the tool would
   never write anywhere the user did not name. Rejected because it
   breaks PRODUCT.md's zero-setup guarantee, and it would be a second
   breaking CLI change immediately after ADR-0007 removed `--format`.
3. **Keeping `/tmp`, hardened** (per-user suffix, `0o700`). Already
   rejected by ADR-0008 for the cache; nothing about a durable document
   makes the argument stronger.
4. **A `platformdirs` dependency.** Same reasoning as ADR-0008: a new
   runtime dependency is its own justified PR, and the stdlib covers
   this in four lines.
