#!/usr/bin/env bash
# =============================================================================
# Script Name : opstat-lab-fr3-fr1-validation.sh
# Description : One-shot Linux lab run for the Telemetry Correctness milestone:
#               validates the implemented FR3 host_view millisecond correction
#               (D-014) against live cluster data, and performs the FR1
#               root-view surgical check (ViewMetrics on view ids 1 and 217
#               under proven NFSv3 load).
# Evidence    : everything lands under $HOME/kjmtmp/opstat/fr3-fr1-<DTS>/
#               (raw/ probe evidence incl. the API log, logs/, tmp/).
# Final ZIP   : $HOME/opstat-fr3-fr1-validation-<DTS>.zip  (the ONE file to
#               return; nothing else is written outside the run directory).
# Dependencies: bash, git, python3, zip/unzip, sha256sum, systemctl;
#               VAST_PASSWORD or VAST_TOKEN in the environment.
# Target      : var203.selab.vastdata.com (VAST 5.4.6), repo main.
#               Set OPSTAT_EXPECTED_HEAD to hard-pin the exact published SHA;
#               the script always requires HEAD == origin/main after ff-only.
# =============================================================================
set -u

# ------------------------------------------------------------------ variables
REPO="${OPSTAT_REPO:-$HOME/git/opstat}"
VMS="${OPSTAT_VMS:-var203.selab.vastdata.com}"
VIEW_IDS="1,217"                 # FR1 surgical anchors: both path-"/" view objects
VIEW_PATHS="/kmacs"              # exact-path sanity anchor (no such view expected)
NFS3_MOUNT="/mnt/kmacs-root"     # client mount carrying the NFSv3 workload
DTS=$(date +%Y%m%d-%H%M%S)
RUN="$HOME/kjmtmp/opstat/fr3-fr1-$DTS"
ZIP="$HOME/opstat-fr3-fr1-validation-$DTS.zip"
FAILURES=0

say()  { echo "[$(date +%T)] $*"; }
pass() { say "PASS    : $*"; }
warn() { say "WARNING : $*"; }
err()  { say "ERROR   : $*"; FAILURES=$((FAILURES + 1)); }
section() { echo; echo "== $1 =================================================="; }

mkdir -p "$RUN/raw" "$RUN/logs" "$RUN/tmp"
export TMPDIR="$RUN/tmp" TMP="$RUN/tmp" TEMP="$RUN/tmp"
export OPSTAT_API_LOG_DIR="$RUN/logs"

# ------------------------------------------- 1. repo sync + exact SHA guard
section "1. repository state"
cd "$REPO" || { err "repo not found at $REPO"; exit 1; }
git fetch origin && git checkout main && git merge --ff-only origin/main
HEAD=$(git rev-parse HEAD)
ORIGIN=$(git rev-parse origin/main)
if [ "$HEAD" != "$ORIGIN" ]; then
  err "HEAD $HEAD != origin/main $ORIGIN after ff-only"; exit 1
fi
if [ -n "${OPSTAT_EXPECTED_HEAD:-}" ] && [ "$HEAD" != "$OPSTAT_EXPECTED_HEAD" ]; then
  err "HEAD $HEAD != expected $OPSTAT_EXPECTED_HEAD"; exit 1
fi
pass "main @ $HEAD (matches origin/main${OPSTAT_EXPECTED_HEAD:+ and expected SHA})"

# ------------------------------------------------- 2. clean-tree guard
section "2. working tree"
if [ -n "$(git status --short)" ]; then
  git status --short; err "working tree not clean"; exit 1
fi
pass "working tree clean"

# ------------------------------- 3. credential + load-generator verification
section "3. credentials and load generators"
if [ -n "${VAST_TOKEN:-}" ]; then pass "credential: VAST_TOKEN present"
elif [ -n "${VAST_PASSWORD:-}" ]; then pass "credential: VAST_PASSWORD present"
else err "no VAST_TOKEN/VAST_PASSWORD in environment - aborting before any cluster contact"; exit 1; fi
for u in nfs3-loadgen block-loadgen; do
  state=$(systemctl is-active "$u.service" 2>&1)
  if [ "$state" = "active" ]; then pass "$u active"; else warn "$u: $state (evidence may be idle)"; fi
  systemctl status "$u.service" --no-pager -l > "$RUN/logs/$u-status.txt" 2>&1
done

# --------------------------------------------- 4. pre-run environment capture
section "4. pre-run capture"
{
  echo "hostname      : $(hostname)"
  echo "collected     : $(date '+%F %T %Z')"
  echo "HEAD          : $HEAD"
  echo "python        : $(python3 -V 2>&1)"
  echo "target        : $VMS"
  echo "view ids      : $VIEW_IDS   view paths: $VIEW_PATHS"
  echo "run dir       : $RUN"
} | tee "$RUN/prereqs.txt"
mount | grep -iE "nfs|vast" > "$RUN/logs/mounts.txt" 2>&1
cat /proc/self/mountstats > "$RUN/logs/mountstats-before.txt" 2>/dev/null
ls -1 /tmp/opstat-* > "$RUN/logs/tmp-before.txt" 2>/dev/null || : > "$RUN/logs/tmp-before.txt"
date '+PROBE-START %F %T' | tee "$RUN/timestamps.txt"

# --------------------------------- 5. probe: FR3 validation + FR1 surgical
section "5. telemetry-correctness probe"
python3 scripts/var203_validation/probe_telemetry_correctness.py \
  --vms "$VMS" --user admin \
  --view-ids "$VIEW_IDS" --view-paths "$VIEW_PATHS" \
  --evidence-dir "$RUN/raw" 2>&1 | tee "$RUN/probe-output.txt"
PROBE_RC=${PIPESTATUS[0]}
echo "PROBE-RC $PROBE_RC" | tee -a "$RUN/timestamps.txt"
date '+PROBE-END %F %T' | tee -a "$RUN/timestamps.txt"
[ "$PROBE_RC" -eq 0 ] && pass "probe rc=0" || err "probe rc=$PROBE_RC"

# ------------------------------------------ 6. post-run workload + state
section "6. post-run capture and workload proof"
cat /proc/self/mountstats > "$RUN/logs/mountstats-after.txt" 2>/dev/null
python3 - "$RUN" "$NFS3_MOUNT" <<'PYEOF' | tee "$RUN/nfs3-workload-deltas.txt"
import io, re, sys
run, mnt = sys.argv[1], sys.argv[2]
def ops(path):
    txt = io.open(path).read()
    if ("mounted on %s" % mnt) not in txt:
        return None
    sec = txt.split("mounted on %s" % mnt)[1].split("device")[0]
    out = {}
    for op in ("READ", "WRITE", "GETATTR", "SETATTR", "LOOKUP", "CREATE",
               "REMOVE", "MKDIR"):
        m = re.search(op + r":\s*\n?\s*([0-9]+)", sec)
        if m:
            out[op] = int(m.group(1))
    return out
try:
    b = ops(run + "/logs/mountstats-before.txt")
    a = ops(run + "/logs/mountstats-after.txt")
except IOError:
    b = a = None
if not b or not a:
    print("WARNING : no mountstats for %s - NFSv3 workload unproven" % mnt)
else:
    total = 0
    for k in b:
        delta = a.get(k, b[k]) - b[k]
        total += delta
        print("  %-8s delta %10d" % (k, delta))
    verdict = "PASS" if total > 1000 else "WARNING"
    print("%s    : NFSv3 client ops during run: %d (%s)" % (verdict, total, mnt))
PYEOF

# ------------------------------------ 7. verdict summary + cleanup check
section "7. probe verdicts"
grep -E "^PROBE:" "$RUN/probe-output.txt" || warn "no PROBE verdict lines found"
grep -q "PROBE:cleanup.exact_ids PASS" "$RUN/probe-output.txt" \
  && pass "temporary monitors exact-id verified gone" \
  || err "monitor cleanup NOT verified - report the ids in probe-output.txt"
grep -q "PROBE:fr3.production_conversion PASS" "$RUN/probe-output.txt" \
  && pass "FR3 D-014 conversion verified against live data" \
  || warn "FR3 production-conversion verdict not PASS - see probe output"

# ----------------------------------------------- 8. /tmp policy verification
section "8. /tmp policy check"
ls -1 /tmp/opstat-* > "$RUN/logs/tmp-after.txt" 2>/dev/null || : > "$RUN/logs/tmp-after.txt"
NEW_TMP=$(comm -13 "$RUN/logs/tmp-before.txt" "$RUN/logs/tmp-after.txt")
if [ -n "$NEW_TMP" ]; then
  echo "$NEW_TMP" | tee "$RUN/logs/tmp-policy-violation.txt"
  err "tooling-policy failure: this run created new opstat artifacts in /tmp (listed above); fix the responsible tooling"
else
  pass "no new opstat artifacts in /tmp"
fi

# --------------------------------------------- 9. final git state + manifest
section "9. manifest"
{ git log --oneline -3; git status --short; } > "$RUN/git-final-state.txt"
{
  echo "opstat FR3+FR1 lab validation - $DTS"
  echo "HEAD $HEAD  target $VMS  probe rc $PROBE_RC  script failures $FAILURES"
  echo
  echo "prereqs.txt                : environment, SHA, anchors"
  echo "timestamps.txt             : PROBE-START/END, rc"
  echo "probe-output.txt           : full probe console incl. PROBE: verdicts"
  echo "nfs3-workload-deltas.txt   : client-side NFSv3 proof over the window"
  echo "raw/                       : probe evidence (samples, expositions,"
  echo "                             catalog, ranked views, API log)"
  echo "logs/                      : mounts, mountstats, loadgen status,"
  echo "                             tmp before/after"
  echo
  echo "file inventory:"
  find "$RUN" -type f | sed "s#^$RUN/#  #" | sort
} > "$RUN/MANIFEST.txt"
cat "$RUN/MANIFEST.txt"

# --------------------------------------- 10. package, verify, hand back
section "10. package"
( cd "$(dirname "$RUN")" && zip -qr "$ZIP" "$(basename "$RUN")" )
if unzip -tq "$ZIP" >/dev/null 2>&1; then pass "ZIP integrity verified"; else err "ZIP failed integrity check"; fi
echo; unzip -l "$ZIP" | tail -8
echo; ls -lh "$ZIP"; sha256sum "$ZIP"
echo
echo "======================================================================"
if [ "$FAILURES" -eq 0 ]; then
  echo "RESULT: PASS - return this ONE file:"
else
  echo "RESULT: $FAILURES failure(s) noted above - return the archive anyway:"
fi
echo
echo "    $ZIP"
echo "======================================================================"
