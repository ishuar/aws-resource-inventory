# ADR-0007: One scan output format, with `--output -` for stdout

- **Status:** accepted (2026-08-25)
- **Deciders:** ishuar

## Context

`aws-inventory scan` carried three output formats behind `--format`:
`table`, `json`, and `md`/`markdown`, plus an unknown-format error path.
ADR-0005 already recorded markdown-as-a-scan-format as a **rejected
alternative** — "one scan format keeps the scan verb simple (CLAUDE.md
rule 12); reporting is a downstream concern" — but the flag survived it.

Three formats meant `outputs.py` branched three ways over one set of
records, and the markdown branch wrote a hand-rolled report that no
longer matched the envelope the other branches produced. A scanner that
carries formatters couples discovery to presentation.

Separately, piping results into `jq` had no first-class path. `--format
json` printed the document to stdout, but so did the banner, the
configuration panel, the progress display, and every log line.

## Decision

1. **`--format` is removed.** The scan has one output: it renders a
   table in the terminal and writes the JSON envelope (ADR-0005).
   `generate_markdown_summary`, the markdown branch, and the
   unknown-format error path are deleted.
2. **`--output -` streams the envelope to stdout** and nothing else.
   Every decorative writer — banner, panels, progress display, results
   table — goes quiet, and nothing touches the disk in that mode, not
   even a generated filename.
3. **Diagnostics move to stderr in that mode; they are never silenced.**
   The log console's stream is chosen *before* logging is configured,
   because the debug banner is emitted while it is being configured.
   stdout is data, stderr is everything else — the Unix contract.
4. **Rendering a report from the JSON belongs to a future
   `aws-inventory report results.json` verb**, not to the scan verb.

## Consequences

- **Breaking (CLAUDE.md rule 20).** `--format`/`-f` no longer exists.
  Consumers who passed `--format json` should drop the flag — JSON is
  always written. `--format md` has no replacement in the scan verb;
  render from the JSON instead. Pre-1.0, no migration path is offered.
- `... scan --output - | jq` works, `--debug` included. Debug logging
  keeps its file handler untouched.
- `outputs.py` loses ~144 lines and holds one output shape.
- A latent bug is fixed on the way: `AWSLogger.configure` early-returned
  whenever the logger was already configured and `debug` was false, so
  it ignored its own arguments. Importing any module that calls
  `get_logger()` configured the logger, so the CLI's own call was always
  the second one and never took effect. This ADR taught the guard to
  reconfigure when a handler-shaping setting changed. *Amended
  2026-08-26:* the follow-up fix removed the cause instead —
  `get_logger()` no longer configures at import time and the guard is
  deleted. `configure_logging` is the only configurator; every call
  applies every argument, last call wins.
- The terminal table is now unconditional for file output. Anyone who
  used `--format json` to suppress it should use `--output -`.

## Alternatives rejected

- **Keep `--format json` and add nothing.** It never gave a clean pipe:
  the banner, panels, progress and logs all shared stdout with the
  document.
- **Silence the log handler in stdout mode instead of moving it.** This
  is what the first implementation did — it removed the `RichHandler`.
  A user piping to `jq` then gets an exit code and no explanation.
  Diagnostics are not decoration.
- **Keep markdown and fix its drift from the envelope.** Two renderers
  of one dataset stay in sync only by discipline; ADR-0005 already
  rejected this and nothing has changed.
