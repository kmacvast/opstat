#!/usr/bin/env bash
#
# opstat validation gate.
#
# One documented command for the normal full check, so nobody has to remember
# the two traps that make a green pytest run meaningless:
#
#   1. 171 of the 400 tests are gated on the `openssl` binary by module-level
#      pytestmark (tests/mock_vms.py needs it to generate a throwaway TLS
#      certificate). Without openssl those five suites skip CLEANLY and pytest
#      still exits 0 — losing every API-efficiency budget, every drill
#      assertion, monitor-cleanup checking, and all discovery and native
#      telemetry coverage.
#
#   2. Python 3.8 is the mandatory floor (CI runs 3.8/3.10/3.12/3.14) and the
#      development interpreter accepts syntax and stdlib APIs that 3.8 rejects.
#
# This script therefore FAILS LOUDLY rather than letting either pass silently.
#
# Usage:
#   ./scripts/validate.sh                        full gate: current Python + 3.8
#   ./scripts/validate.sh --fast                 current Python only (iteration)
#   ./scripts/validate.sh --allow-missing-openssl  run anyway; prints a
#                                                  VALIDATION INCOMPLETE banner
#
# Exit status is 0 only when every leg that ran passed and nothing skipped.

set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT" || exit 2

# Minimum number of tests expected to collect. A much smaller green run means
# suites are silently skipping. If tests are legitimately added or removed,
# update this floor in the same change and say so in the report.
#
# The floor sits ~8% below the current collection count rather than exactly at
# it: ordinary reorganization (merging parametrize cases, folding duplicate
# tests) should not trip the gate, but losing any whole suite must - the
# smallest suite this guards is worth ~8 tests, and the openssl-gated block is
# ~180. History: 395 when the suite collected 400; raised with the suite at 504.
MIN_TESTS=945

FAST=0
ALLOW_MISSING_OPENSSL=0
INCOMPLETE=0
FAILED=0

for arg in "$@"; do
  case "$arg" in
    --fast) FAST=1 ;;
    --allow-missing-openssl) ALLOW_MISSING_OPENSSL=1 ;;
    -h|--help) sed -n '3,26p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "validate.sh: unknown option '$arg'" >&2; exit 2 ;;
  esac
done

hr()   { printf '%s\n' "------------------------------------------------------------"; }
note() { printf '  %-16s: %s\n' "$1" "$2"; }

# Parse a pytest summary line into "collected passed failed skipped".
# Reads the captured output file given as $1.
summarize() {
  local out="$1" line
  line="$(grep -E '^(=+ )?[0-9]+ (passed|failed|error)' "$out" | tail -1)"
  if [ -z "$line" ]; then
    line="$(grep -E '(passed|failed|error|no tests ran)' "$out" | tail -1)"
  fi
  local passed failed skipped errors
  passed=$(printf '%s' "$line"  | grep -oE '[0-9]+ passed'  | grep -oE '[0-9]+' || true)
  failed=$(printf '%s' "$line"  | grep -oE '[0-9]+ failed'  | grep -oE '[0-9]+' || true)
  skipped=$(printf '%s' "$line" | grep -oE '[0-9]+ skipped' | grep -oE '[0-9]+' || true)
  errors=$(printf '%s' "$line"  | grep -oE '[0-9]+ error'   | grep -oE '[0-9]+' || true)
  printf '%s %s %s %s' "${passed:-0}" "${failed:-0}" "${skipped:-0}" "${errors:-0}"
}

hr
echo "opstat validation gate"
hr

# ---------------------------------------------------------------- tooling ---

echo "Tooling"

if command -v openssl >/dev/null 2>&1; then
  note "openssl" "$(openssl version 2>&1 | head -1)"
else
  note "openssl" "MISSING"
  if [ "$ALLOW_MISSING_OPENSSL" -eq 1 ]; then
    INCOMPLETE=1
  else
    echo
    echo "FAIL: openssl is not on PATH."
    echo
    echo "  tests/mock_vms.py generates a throwaway TLS certificate with the"
    echo "  openssl binary. Without it, 171 tests across five suites skip"
    echo "  cleanly and pytest still reports success. That is not validation."
    echo
    echo "  Install openssl, or re-run with --allow-missing-openssl and report"
    echo "  the VALIDATION INCOMPLETE banner verbatim."
    echo
    exit 1
  fi
fi

PY="${PYTHON:-python3}"
if command -v "$PY" >/dev/null 2>&1; then
  note "interpreter" "$("$PY" --version 2>&1)"
else
  echo "FAIL: '$PY' not found on PATH." >&2
  exit 2
fi

if [ "$FAST" -eq 0 ]; then
  if command -v uv >/dev/null 2>&1; then
    note "uv" "$(uv --version 2>&1 | head -1)"
  else
    note "uv" "MISSING"
    echo
    echo "FAIL: uv is not on PATH, so the mandatory Python 3.8 leg cannot run."
    echo
    echo "  Install uv (https://docs.astral.sh/uv/), or run the 3.8 suite by"
    echo "  hand with any 3.8 interpreter:"
    echo "      python3.8 -m pytest -q"
    echo "  Re-run with --fast only for inner-loop iteration, never for sign-off."
    echo
    exit 1
  fi
fi

echo

# ------------------------------------------------------- documentation ------

echo "Documentation"
DOC_OUT="$(mktemp)"
if "$PY" scripts/check_docs_links.py >"$DOC_OUT" 2>&1; then
  note "links" "$(cat "$DOC_OUT")"
else
  note "links" "FAILED"
  cat "$DOC_OUT"
  FAILED=1
fi
rm -f "$DOC_OUT"
FR_OUT="$(mktemp)"
if "$PY" scripts/check_fr_backlog.py >"$FR_OUT" 2>&1; then
  note "backlog" "$(cat "$FR_OUT")"
else
  note "backlog" "FAILED"
  cat "$FR_OUT"
  FAILED=1
fi
rm -f "$FR_OUT"
echo

# ------------------------------------------------------------- collection ---

echo "Collection"
COLLECT_OUT="$(mktemp)"
"$PY" -m pytest --collect-only >"$COLLECT_OUT" 2>&1
COLLECTED="$(grep -oE '[0-9]+ tests? collected' "$COLLECT_OUT" | grep -oE '[0-9]+' | tail -1)"
COLLECTED="${COLLECTED:-0}"
note "collected" "$COLLECTED (floor $MIN_TESTS)"
if [ "$COLLECTED" -lt "$MIN_TESTS" ]; then
  echo
  echo "FAIL: only $COLLECTED tests collected, expected at least $MIN_TESTS."
  echo "  Either suites are not being discovered, or tests were removed. If the"
  echo "  removal was deliberate, lower MIN_TESTS in this script in the same"
  echo "  change and say so in the report."
  rm -f "$COLLECT_OUT"
  exit 1
fi
rm -f "$COLLECT_OUT"
echo

# -------------------------------------------------------- current Python ----

echo "Suite: current Python ($("$PY" --version 2>&1))"
CUR_OUT="$(mktemp)"
"$PY" -m pytest 2>&1 | tee "$CUR_OUT"
CUR_STATUS=${PIPESTATUS[0]}
read -r CP CF CS CE <<<"$(summarize "$CUR_OUT")"
rm -f "$CUR_OUT"
note "result" "$CP passed, $CF failed, $CS skipped, $CE error"
[ "$CUR_STATUS" -eq 0 ] || FAILED=1
if [ "$CS" -gt 0 ]; then
  FAILED=1
  echo "  FAIL: $CS tests skipped. The only skip condition in this suite is a"
  echo "        missing openssl binary, so a skip means the mock-backed suites"
  echo "        did not run."
fi
echo

# ----------------------------------------------------------- Python 3.8 ----

if [ "$FAST" -eq 1 ]; then
  echo "Suite: Python 3.8  -- SKIPPED (--fast)"
  INCOMPLETE=1
  echo
else
  echo "Suite: Python 3.8 (uv)"
  P38_OUT="$(mktemp)"
  uv run --python 3.8 --no-project --with pytest -- python -m pytest 2>&1 | tee "$P38_OUT"
  P38_STATUS=${PIPESTATUS[0]}
  read -r PP PF PS PE <<<"$(summarize "$P38_OUT")"
  rm -f "$P38_OUT"
  note "result" "$PP passed, $PF failed, $PS skipped, $PE error"
  [ "$P38_STATUS" -eq 0 ] || FAILED=1
  if [ "$PS" -gt 0 ]; then
    FAILED=1
    echo "  FAIL: $PS tests skipped on the 3.8 leg."
  fi
  echo
fi

# --------------------------------------------------------------- summary ---

hr
if [ "$FAILED" -ne 0 ]; then
  echo "RESULT: FAIL"
  hr
  exit 1
fi

if [ "$INCOMPLETE" -ne 0 ]; then
  echo "############################################################"
  echo "#                 VALIDATION INCOMPLETE                    #"
  echo "#                                                          #"
  echo "#  Everything that ran passed, but coverage was reduced:   #"
  [ "$ALLOW_MISSING_OPENSSL" -eq 1 ] && \
  echo "#   - openssl missing: 171 mock-backed tests did not run.  #"
  [ "$FAST" -eq 1 ] && \
  echo "#   - --fast: the mandatory Python 3.8 leg did not run.    #"
  echo "#                                                          #"
  echo "#  Do NOT report this as a passing gate. Quote this banner #"
  echo "#  in the CHECKS section of your completion report.        #"
  echo "############################################################"
  hr
  exit 1
fi

echo "RESULT: PASS"
echo "  Current Python and Python 3.8 both green, nothing skipped."
hr
