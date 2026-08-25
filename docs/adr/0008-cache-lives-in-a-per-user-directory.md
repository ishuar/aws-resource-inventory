# ADR-0008: The scan cache lives in a per-user directory

- **Status:** accepted (2026-08-24)
- **Deciders:** ishuar

## Context

The scan cache was written to `/tmp/aws_scanner_cache` — a fixed path in
a world-writable directory, holding pickled scan results.

1. The entries are an account's resource inventory: ids, ARNs, names,
   and the account number in every ARN. On a shared host any local user
   could read them.
2. The path is predictable and the loader is `pickle.load`. A user who
   creates the directory first controls what the next scan deserialises.
3. `aws_scanner` is the CLI name retired before the first release
   (CLAUDE.md), still spelled in a runtime path.
4. ADR-0004 named this exact deferral: "Deliberately out of scope …
   the on-disk cache directory (`/tmp/aws_scanner_cache`)". This ADR
   completes half of it (CLAUDE.md rule 1: transitional measures get
   completed, not left).

## Decision

1. The cache directory is `$XDG_CACHE_HOME/aws-resource-inventory`, or
   `~/.cache/aws-resource-inventory` when `XDG_CACHE_HOME` is unset.
2. The same path on every platform. macOS convention would be
   `~/Library/Caches`, but one documented path beats per-platform
   branching for a 10-minute scratch cache (CLAUDE.md rule 12).
3. `default_cache_dir()` is public, so the location is a tested
   behaviour rather than a literal — `CACHE_DIR` still exists as the
   module constant the tests redirect.
4. The store creates the tree with `parents=True, mode=0o700`: unlike
   `/tmp`, `~/.cache` is not guaranteed to exist.
5. **Owner-only is asserted on every write, not assumed from creation.**
   `mkdir` applies its mode only when it actually creates the
   directory, so a directory left behind at a wider mode — by an
   earlier version, or by whoever got there first — would silently keep
   it. The store therefore `chmod`s the directory to `0o700` each time.
6. **Entries are opened `0o600`, not written and then tightened.** The
   directory is the outer guard; the file mode is defence in depth for
   a cache that is copied, or whose directory guard is weakened.
   `os.open` with an explicit mode is used rather than a write-then-
   `chmod`, because umask can only clear permission bits and never set
   them — so an entry is never briefly world-readable.

## Alternatives rejected

1. **A `platformdirs` dependency.** It is the correct general answer and
   would give `~/Library/Caches` on macOS. A new runtime dependency is
   its own justified PR, and 4 lines of stdlib cover a scratch cache.
2. **Keeping `/tmp`, hardened** (per-user suffix, `0o700`). It narrows
   the window without closing it — the directory is still in a
   world-writable parent, and still spells the retired name.
3. **Dropping the cache.** It earns its keep: repeated scans in a
   session are the normal workflow, and `--no-cache` already exists for
   anyone who wants none of it.

## Consequences

- Warm entries under the old path are orphaned, not migrated. The TTL is
  10 minutes, so the first scan after upgrading simply repopulates.
- The cache now survives a reboot. That is a behaviour change, bounded
  by the same 10-minute TTL.
- Entries are still pickles. Moving to a data-only format is a separate
  decision; this ADR removes the exposure that made it urgent.
- The other half of ADR-0004's deferral is untouched: the debug log
  filename still reads `aws_scanner_debug_<timestamp>.log`.
