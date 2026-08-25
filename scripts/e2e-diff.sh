#!/usr/bin/env bash
#
# e2e-diff.sh — end-to-end functional comparison of two revisions.
#
# Runs the same real AWS scan on two git refs (e.g. before and after a
# merge) and diffs the JSON output. The envelope's scan block carries
# started_at/duration_seconds, which differ between any two runs by
# design — so only .resources is compared: with unchanged infrastructure
# the resource arrays of functionally identical code are byte-identical
# after sorting.
#
# PREREQUISITES
#   1. Valid AWS credentials for a profile with read-only access
#      (aws sso login / aws configure). Pass the profile with --profile
#      or export AWS_PROFILE; otherwise the tool uses "default".
#   2. jq            — brew install jq
#   3. poetry        — with the project venv installed (poetry install)
#
# The script fetches origin itself, and the defaults compare
# origin/main~1 vs origin/main — with squash-merges that is exactly
# "before vs after the PR that just merged", independent of local
# branch state. Explicit --before/--after refs override the defaults.
#
# USAGE
#   scripts/e2e-diff.sh                          # origin/main~1 vs origin/main
#   scripts/e2e-diff.sh --before <ref> --after <ref>
#   scripts/e2e-diff.sh --regions eu-central-1,us-east-1
#   scripts/e2e-diff.sh --profile my-sso-profile
#   scripts/e2e-diff.sh --tag-key env --tag-value prod   # exercise the tag path
#
# EXIT CODES
#   0 outputs identical · 1 outputs differ · 2 prerequisite/setup failure
#
# Run it twice per merge: once plain (per-service path) and once with
# --tag-key/--tag-value (Resource Groups tag path) — they are different
# code paths.

set -euo pipefail

BEFORE_REF="origin/main~1"
AFTER_REF="origin/main"
REGIONS="eu-central-1"
PROFILE="${AWS_PROFILE:-default}"
TAG_KEY=""
TAG_VALUE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --before)    BEFORE_REF="$2"; shift 2 ;;
    --after)     AFTER_REF="$2"; shift 2 ;;
    --regions)   REGIONS="$2"; shift 2 ;;
    --profile)   PROFILE="$2"; shift 2 ;;
    --tag-key)   TAG_KEY="$2"; shift 2 ;;
    --tag-value) TAG_VALUE="$2"; shift 2 ;;
    -h|--help)   grep '^#' "$0" | grep -v '^#!' | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown argument: $1 (see --help)" >&2; exit 2 ;;
  esac
done

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

# --- prerequisites -----------------------------------------------------------
fail() { echo "ERROR: $*" >&2; exit 2; }

# Resolved once, from this repo, and reused for both worktree scans.
VENV_PYTHON="$(poetry env info --executable 2>/dev/null)" \
  || fail "no poetry environment found — run 'poetry install' first"

command -v jq >/dev/null || fail "jq is not installed (brew install jq)"
command -v poetry >/dev/null || fail "poetry is not installed"

echo "==> Fetching origin so remote refs are current"
git fetch --quiet origin || fail "git fetch origin failed"

git rev-parse --verify --quiet "${BEFORE_REF}^{commit}" >/dev/null \
  || fail "before-ref '${BEFORE_REF}' is not a known commit"
git rev-parse --verify --quiet "${AFTER_REF}^{commit}" >/dev/null \
  || fail "after-ref '${AFTER_REF}' is not a known commit"

echo "==> Checking AWS credentials for profile '${PROFILE}'"
poetry run python - "$PROFILE" <<'PY' || exit 2
import sys
import boto3
try:
    identity = boto3.Session(profile_name=sys.argv[1]).client("sts").get_caller_identity()
except Exception as exc:  # credentials problem, report and stop
    print(f"ERROR: AWS credentials check failed for profile '{sys.argv[1]}': {exc}", file=sys.stderr)
    raise SystemExit(1)
print(f"    OK: account {identity['Account']}, principal {identity['Arn']}")
PY

# --- setup -------------------------------------------------------------------
WORKDIR="$(mktemp -d "${TMPDIR:-/tmp}/aws-inventory-e2e.XXXXXX")"
cleanup() {
  git worktree remove --force "${WORKDIR}/before" 2>/dev/null || true
  git worktree remove --force "${WORKDIR}/after" 2>/dev/null || true
  rm -rf "${WORKDIR}"
}
trap cleanup EXIT

echo "==> Materializing refs in temporary worktrees"
git worktree add --quiet --detach "${WORKDIR}/before" "${BEFORE_REF}"
git worktree add --quiet --detach "${WORKDIR}/after" "${AFTER_REF}"
echo "    before: ${BEFORE_REF} ($(git rev-parse --short "${BEFORE_REF}"))"
echo "    after:  ${AFTER_REF} ($(git rev-parse --short "${AFTER_REF}"))"

# No --format: ADR-0007 removed it, and every ref this script accepts
# already writes the envelope to -o regardless (the pre-ADR-0007 default
# wrote it too), so one argument list works on both sides of that change.
SCAN_ARGS=(scan -r "${REGIONS}" --profile "${PROFILE}" --no-cache)
if [[ -n "${TAG_KEY}" ]]; then SCAN_ARGS+=(--tag-key "${TAG_KEY}"); fi
if [[ -n "${TAG_VALUE}" ]]; then SCAN_ARGS+=(--tag-value "${TAG_VALUE}"); fi

run_scan() { # $1 = worktree, $2 = output file
  # Must cd into the worktree: `python -m` puts the CWD at sys.path[0],
  # ahead of PYTHONPATH, so running from the repo root would import this
  # checkout's package for BOTH scans and always report "no differences" —
  # silently turning this gate into a no-op.
  #
  # Hence the resolved interpreter rather than `poetry run`: each worktree
  # has its own pyproject.toml, so poetry would target the wrong venv.
  #
  # The guard is not paranoia. The venv carries an editable install of this
  # project (`aws_resource_inventory.pth`), so when a worktree does NOT
  # provide the package, the import falls through to THIS checkout's code
  # and the run reports "identical" after comparing main against itself.
  # Refs older than the ADR-0004 layout hit exactly that, so refuse them.
  [[ -f "$1/aws_resource_inventory/cli.py" ]] || fail \
    "worktree $1 has no aws_resource_inventory/ — refs predating the
     ADR-0004 package layout cannot be compared; the venv's editable
     install would silently substitute the current checkout's code."

  # Same refusal mechanism for the JSON envelope: refs from before the
  # envelope (aws_resource_inventory/lib/envelope.py, ADR-0005) emit a
  # flat array with resource_-prefixed keys, which .resources-only
  # comparison cannot honestly diff against envelope output. No
  # transitional dual-shape code — refuse outright.
  [[ -f "$1/aws_resource_inventory/lib/envelope.py" ]] || fail \
    "worktree $1 predates the JSON envelope — its flat-array output
     cannot be compared against the envelope's .resources. Both refs
     must include the envelope commit
     (aws_resource_inventory/lib/envelope.py)."

  ( cd "$1" && "$VENV_PYTHON" -m aws_resource_inventory.cli \
      "${SCAN_ARGS[@]}" -o "$2" ) >"$2.log" 2>&1 \
    || { echo "ERROR: scan failed for $1 — log follows" >&2; tail -30 "$2.log" >&2; exit 2; }
}

# --- scans (back-to-back so real infra drift stays minimal) -------------------
echo "==> Scanning with BEFORE code (${REGIONS})"
run_scan "${WORKDIR}/before" "${WORKDIR}/before.json"
echo "==> Scanning with AFTER code (${REGIONS})"
run_scan "${WORKDIR}/after" "${WORKDIR}/after.json"

# --- compare ------------------------------------------------------------------
# .resources only: started_at/duration_seconds in the scan block are
# non-deterministic by design and must never fail the comparison.
SORT='.resources | sort_by(.region, .type, .id)'
jq -S "${SORT}" "${WORKDIR}/before.json" > "${WORKDIR}/before.sorted.json"
jq -S "${SORT}" "${WORKDIR}/after.json" > "${WORKDIR}/after.sorted.json"

BEFORE_COUNT="$(jq '.resources | length' "${WORKDIR}/before.json")"
AFTER_COUNT="$(jq '.resources | length' "${WORKDIR}/after.json")"
echo "==> Resources found: before=${BEFORE_COUNT} after=${AFTER_COUNT}"

if diff -u "${WORKDIR}/before.sorted.json" "${WORKDIR}/after.sorted.json"; then
  echo "==> RESULT: FUNCTIONALLY IDENTICAL ✅"
else
  echo "==> RESULT: OUTPUTS DIFFER ❌ (diff above; could also be real infra drift — rerun to confirm)"
  exit 1
fi
