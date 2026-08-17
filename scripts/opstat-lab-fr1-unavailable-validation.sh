#!/usr/bin/env bash
# =============================================================================
# Script Name : opstat-lab-fr1-unavailable-validation.sh
# Description : One-shot Linux lab validation of the implemented FR1 behavior:
#               the NFSv3 VIEW drill's honest unavailable state on VAST 5.4.6
#               (D-016). Drives the production nfs_v3 engine in-process via
#               scripts/var203_validation/validate_fr1_view_unavailable.py
#               under proven live NFSv3 load, with zero-API-cost entry and
#               exact-id monitor cleanup verified from the API log.
# Evidence    : everything lands under $HOME/kjmtmp/opstat/fr1-<DTS>/
#               (raw/ frames + API log, logs/, tmp/).
# Final ZIP   : $HOME/opstat-fr1-unavailable-validation-<DTS>.zip (the ONE
#               file to return; nothing else is written outside the run dir).
# Dependencies: bash, git, python3, zip/unzip, sha256sum, systemctl;
#               VAST_PASSWORD or VAST_TOKEN in the environment.
# Target      : var203.selab.vastdata.com (VAST 5.4.6), repo main.
#               Set OPSTAT_EXPECTED_HEAD to hard-pin the published SHA; the
#               script always requires HEAD == origin/main after ff-only.
# =============================================================================
set -u

# ------------------------------------------------------------------ variables
REPO="${OPSTAT_REPO:-$HOME/git/opstat}"
VMS="${OPSTAT_VMS:-var203.selab.vastdata.com}"
NFS3_MOUNT="/mnt/kmacs-root"     # client mount carrying the NFSv3 workload
DTS=$(date +%Y%m%d-%H%M%S)
RUN="$HOME/kjmtmp/opstat/fr1-$DTS"
ZIP="$HOME/opstat-fr1-unavailable-validation-$DTS.zip"
FAILURES=0

say()  { echo "[$(date +%T)] $*"; }
pass() { say "PASS    : $*"; }
warn() { say "WARNING : $*"; }
err()  { say "ERROR   : $*"; FAILURES=$((FAILURES + 1)); }
section() { echo; echo "== $1 =================================================="; }

mkdir -p "$RUN/raw" "$RUN/logs" "$RUN/tmp"
export TMPDIR="$RUN/tmp" TMP="$RUN/tmp" TEMP="$RUN/tmp"
export OPSTAT_API_LOG_DIR="$RUN/raw"
export OPSTAT_FRAME_OUT="$RUN/raw/rendered-frames.txt"

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

# --------------------------------------------------- 2. clean-tree guard
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
state=$(systemctl is-active nfs3-loadgen.service 2>&1)
if [ "$state" = "active" ]; then pass "nfs3-loadgen active"; else
  err "nfs3-loadgen is $state - the unavailable state must be proven against LIVE NFSv3 load"; fi
systemctl status nfs3-loadgen.service --no-pager -l > "$RUN/logs/nfs3-loadgen-status.txt" 2>&1

# --------------------------------------------- 4. pre-run environment capture
section "4. pre-run capture"
{
  echo "hostname      : $(hostname)"
  echo "collected     : $(date '+%F %T %Z')"
  echo "HEAD          : $HEAD"
  echo "python        : $(python3 -V 2>&1)"
  echo "target        : $VMS"
  echo "run dir       : $RUN"
} | tee "$RUN/prereqs.txt"
mount | grep -iE "nfs|vast" > "$RUN/logs/mounts.txt" 2>&1
cat /proc/self/mountstats > "$RUN/logs/mountstats-before.txt" 2>/dev/null
ls -1 /tmp/opstat-* > "$RUN/logs/tmp-before.txt" 2>/dev/null || : > "$RUN/logs/tmp-before.txt"
date '+VALIDATE-START %F %T' | tee "$RUN/timestamps.txt"

# ------------------------------ 5. production-path validation, in-process
section "5. FR1 unavailable-state validation (production engine)"
python3 scripts/var203_validation/validate_fr1_view_unavailable.py \
  2>&1 | tee "$RUN/validate-output.txt"
VAL_RC=${PIPESTATUS[0]}
echo "VALIDATE-RC $VAL_RC" | tee -a "$RUN/timestamps.txt"
date '+VALIDATE-END %F %T' | tee -a "$RUN/timestamps.txt"
[ "$VAL_RC" -eq 0 ] && pass "validation rc=0" || err "validation rc=$VAL_RC"

# ------------------------------------------ 6. post-run workload proof
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

# ----------------------------------------------- 7. /tmp policy verification
section "7. /tmp policy check"
ls -1 /tmp/opstat-* > "$RUN/logs/tmp-after.txt" 2>/dev/null || : > "$RUN/logs/tmp-after.txt"
NEW_TMP=$(comm -13 "$RUN/logs/tmp-before.txt" "$RUN/logs/tmp-after.txt")
if [ -n "$NEW_TMP" ]; then
  echo "$NEW_TMP" | tee "$RUN/logs/tmp-policy-violation.txt"
  err "tooling-policy failure: new opstat artifacts appeared in /tmp (listed above)"
else
  pass "no new opstat artifacts in /tmp"
fi
API_IN_TREE=$(find "$RUN/raw" -maxdepth 1 -name 'opstat-api-*' | head -1)
[ -n "$API_IN_TREE" ] && pass "API log inside the run tree: $API_IN_TREE" \
  || err "API log not found beneath the run directory"

# --------------------------------------------- 8. final git state + manifest
section "8. manifest"
{ git log --oneline -3; git status --short; } > "$RUN/git-final-state.txt"
{
  echo "opstat FR1 unavailable-state validation - $DTS"
  echo "HEAD $HEAD  target $VMS  validate rc $VAL_RC  script failures $FAILURES"
  echo
  echo "prereqs.txt              : environment and SHA"
  echo "timestamps.txt           : VALIDATE-START/END, rc"
  echo "validate-output.txt      : full CHECK: verdicts from the production run"
  echo "nfs3-workload-deltas.txt : client-side NFSv3 proof over the window"
  echo "raw/                     : rendered frames, API log"
  echo "logs/                    : mounts, mountstats, loadgen status, tmp diff"
  echo
  echo "file inventory:"
  find "$RUN" -type f | sed "s#^$RUN/#  #" | sort
} > "$RUN/MANIFEST.txt"
cat "$RUN/MANIFEST.txt"

# --------------------------------------- 9. package, verify, hand back
section "9. package"
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
