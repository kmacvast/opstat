#!/usr/bin/env bash
# =============================================================================
# Script Name : opstat-lab-fr2-delegation-validation.sh
# Description : FR2 PRODUCTION validation of the NFSv4.1 delegation diagnostic
#               (D-008). Drives the real nfs_v41 engine in-process on the lab
#               host against the live cluster: prompt dispatch (including the
#               q-inside-a-path quit guard), tenant resolution from the real
#               view inventory, live/empty/invalid lookups on REAL workload
#               files (derived, never hard-coded), API-cost bounds, and
#               monitor cleanup verified per exact id. STRICTLY GET-ONLY on
#               the delegation endpoint; this script fails hard if the API
#               log contains a single non-GET nfs4_delegs line.
# Evidence    : everything lands under $HOME/kjmtmp/opstat/fr2val-<DTS>/
# Final ZIP   : $HOME/opstat-fr2-delegation-validation-<VMSTAG>-<DTS>.zip
#               (the ONE file to return).
# Dependencies: bash, git, python3, zip/unzip, sha256sum, systemctl;
#               VAST_PASSWORD or VAST_TOKEN in the environment.
# Target      : var204.selab.vastdata.com (VAST 5.5.0) - the VMS that owns
#               the lab NFSv4.1 mount. Set OPSTAT_EXPECTED_HEAD to hard-pin
#               the published SHA; the script always requires HEAD ==
#               origin/main after ff-only.
# =============================================================================
set -u

# ------------------------------------------------------------------ variables
REPO="${OPSTAT_REPO:-$HOME/git/opstat}"
VMS="${OPSTAT_VMS:-var204.selab.vastdata.com}"   # NFSv4.1 target: the VMS owning the mount
VMSTAG=${VMS%%.*}
REQUIRED_LOADGEN="nfs41-loadgen"       # live NFSv4.1 delegations need live opens
NFS41_MOUNT="${OPSTAT_NFS41_MOUNT:-/mnt/nfs41test}"   # client mount of the NFSv4.1 view
DTS=$(date +%Y%m%d-%H%M%S)
RUN="$HOME/kjmtmp/opstat/fr2val-$VMSTAG-$DTS"
ZIP="$HOME/opstat-fr2-delegation-validation-$VMSTAG-$DTS.zip"
FAILURES=0

say()  { echo "[$(date +%T)] $*"; }
pass() { say "PASS    : $*"; }
warn() { say "WARNING : $*"; }
err()  { say "ERROR   : $*"; FAILURES=$((FAILURES + 1)); }
section() { echo; echo "== $1 =================================================="; }

# ------------------------------ shared: manifest, packaging, final verdict

write_manifest() {
  { git log --oneline -3; git status --short; } > "$RUN/git-final-state.txt"
  {
    echo "opstat FR2 delegation-diagnostic validation - $DTS"
    echo "HEAD $HEAD  target $VMS  validator rc ${VAL_RC:-not-run}  script failures $FAILURES"
    echo
    echo "candidates.txt      : candidate discovery outcome"
    echo "candidate-files.txt : raw candidate list (helper output)"
    echo "validator-output.txt: full CHECK: verdicts from the production run"
    echo "frames.txt          : captured production frames (dashboard, prompt,"
    echo "                      live, empty, invalid, after-exit)"
    echo "raw/                : GET-only API log (verbatim requests/responses)"
    echo "logs/               : mounts, mountstats, loadgen status, tmp diff"
    echo
    echo "file inventory:"
    find "$RUN" -type f | sed "s#^$RUN/#  #" | sort
  } > "$RUN/MANIFEST.txt"
  cat "$RUN/MANIFEST.txt"
}

finish() {
  section "final packaging and verdict"
  write_manifest
  ( cd "$(dirname "$RUN")" && zip -qr "$ZIP" "$(basename "$RUN")" )
  if unzip -tq "$ZIP" >/dev/null 2>&1; then pass "ZIP integrity verified"
  else err "ZIP failed integrity check"; fi
  echo; unzip -l "$ZIP" | tail -8
  echo; ls -lh "$ZIP"; sha256sum "$ZIP"
  echo
  echo "======================================================================"
  if [ "$FAILURES" -eq 0 ]; then
    echo "RESULT: RUN VALID - return this ONE file:"
  else
    echo "RESULT: RUN FAILED ($FAILURES failure(s) - see ERROR lines above)."
    echo "The archive still contains the failure evidence; return it anyway:"
  fi
  echo
  echo "    $ZIP"
  echo "======================================================================"
  [ "$FAILURES" -eq 0 ] && exit 0 || exit 1
}


mkdir -p "$RUN/raw" "$RUN/logs" "$RUN/tmp"
export TMPDIR="$RUN/tmp" TMP="$RUN/tmp" TEMP="$RUN/tmp"
export OPSTAT_API_LOG_DIR="$RUN/raw"

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

# ------------------------------- 3. credential + environment verification
section "3. credentials and NFSv4.1 environment"
if [ -n "${VAST_TOKEN:-}" ]; then pass "credential: VAST_TOKEN present"
elif [ -n "${VAST_PASSWORD:-}" ]; then pass "credential: VAST_PASSWORD present"
else err "no VAST_TOKEN/VAST_PASSWORD in environment - aborting before any cluster contact"; exit 1; fi
# Layered prerequisites (durable policy): required service -> mount health
# -> mount-to-VMS consistency (verified in the validator against the target
# cluster's own VIPs) -> real files -> production run. Never auto-start.
if ! systemctl is-active --quiet "$REQUIRED_LOADGEN.service"; then
  err "prerequisite: $REQUIRED_LOADGEN is not running - live NFSv4.1 delegations need live opens"
  say "start it with: sudo systemctl start $REQUIRED_LOADGEN"
  exit 1
fi
pass "$REQUIRED_LOADGEN active"
systemctl status "$REQUIRED_LOADGEN.service" --no-pager -l > "$RUN/logs/nfs41-loadgen-status.txt" 2>&1
mount | grep -iE "nfs|vast" > "$RUN/logs/mounts.txt" 2>&1
if ! grep -q " $NFS41_MOUNT " "$RUN/logs/mounts.txt"; then
  err "prerequisite: no NFSv4.1 mount at $NFS41_MOUNT - mount the target cluster's export first"
  exit 1
fi
grep " $NFS41_MOUNT " "$RUN/logs/mounts.txt" | grep -q "vers=4.1" \
  && pass "mount at $NFS41_MOUNT is NFS vers=4.1" \
  || { err "prerequisite: $NFS41_MOUNT is not an NFS v4.1 mount"; exit 1; }
ls "$NFS41_MOUNT" >/dev/null 2>&1 \
  && pass "mount is readable" \
  || { err "prerequisite: cannot read $NFS41_MOUNT"; exit 1; }

# --------------------------------- 4. mount facts + real file candidates
section "4. mount derivation and real file candidates (client-side truth)"
EXPORT=$(mount | awk -v mp="$NFS41_MOUNT" '$3 == mp {split($1, a, ":"); print a[2]}' | head -1)
SERVER_IP=$(mount | awk -v mp="$NFS41_MOUNT" '$3 == mp {split($1, a, ":"); print a[1]}' | head -1)
if [ -z "$EXPORT" ]; then
  err "no mount found at $NFS41_MOUNT; cannot derive the export path"; EXPORT="unknown"
else
  pass "mount: server $SERVER_IP export $EXPORT on $NFS41_MOUNT (API target $VMS)"
fi
# Real existing files, via the tested helper: open fds of any process,
# paths named on loadgen command lines, then a shallow walk (zero-byte
# files included). Bounded sampling; refuses (rc 1) when nothing exists.
python3 scripts/var203_validation/find_nfs41_candidates.py \
  --mountpoint "$NFS41_MOUNT" --wait 120 2>&1 | tee "$RUN/candidate-files.txt"
CAND_RC=${PIPESTATUS[0]}
CLIENT_FILES=$(grep -v "^NO-CANDIDATES" "$RUN/candidate-files.txt" | paste -sd, -)
{
  echo "export path     : $EXPORT"
  echo "candidate rc    : $CAND_RC"
  echo "client files    : ${CLIENT_FILES:-none}"
} | tee "$RUN/candidates.txt"
ls -la "$NFS41_MOUNT" "$NFS41_MOUNT"/* > "$RUN/logs/mount-listing.txt" 2>&1
cat /proc/self/mountstats > "$RUN/logs/mountstats-before.txt" 2>/dev/null
ls -1 /tmp/opstat-* > "$RUN/logs/tmp-before.txt" 2>/dev/null || : > "$RUN/logs/tmp-before.txt"
{
  echo "hostname : $(hostname)"; echo "collected: $(date '+%F %T %Z')"
  echo "HEAD     : $HEAD"; echo "python   : $(python3 -V 2>&1)"
  echo "target   : $VMS"; echo "run dir  : $RUN"
} | tee "$RUN/prereqs.txt"
date '+VALIDATION-START %F %T' | tee "$RUN/timestamps.txt"

# Refuse to run against nothing: without a real workload file the live-
# delegation objective cannot be met, and a normal-looking ZIP would mislead.
if [ "$CAND_RC" -ne 0 ] || [ -z "$CLIENT_FILES" ]; then
  err "prerequisite failure: no real existing file beneath $NFS41_MOUNT - refusing to run the validator"
  VAL_RC="not-run"
  ls -1 /tmp/opstat-* > "$RUN/logs/tmp-after.txt" 2>/dev/null || : > "$RUN/logs/tmp-after.txt"
  finish
fi

# --------------------------- 5. production engine validation (GET-only)
section "5. FR2 delegation diagnostic - production engine run"
python3 scripts/var203_validation/validate_fr2_delegation.py \
  --vms "$VMS" --user admin \
  --mountpoint "$NFS41_MOUNT" --export-path "$EXPORT" \
  --mount-server "$SERVER_IP" \
  --client-files "$CLIENT_FILES" \
  --frame-out "$RUN/frames.txt" 2>&1 | tee "$RUN/validator-output.txt"
VAL_RC=${PIPESTATUS[0]}
echo "VALIDATOR-RC $VAL_RC" | tee -a "$RUN/timestamps.txt"
date '+VALIDATION-END %F %T' | tee -a "$RUN/timestamps.txt"
[ "$VAL_RC" -eq 0 ] && pass "validator rc=0" || err "validator rc=$VAL_RC"

# --------------------------------------- 6. HARD safety check: GET only
section "6. read-only verification (API log)"
APILOG=$(find "$RUN/raw" -maxdepth 1 -name 'opstat-api-*' | head -1)
if [ -z "$APILOG" ]; then
  err "API log not found beneath the run directory"
else
  pass "API log inside the run tree: $APILOG"
  NONGET=$(grep -E " (POST|DELETE|PUT|PATCH) https?://" "$APILOG" | grep -c "nfs4_deleg")
  if [ "$NONGET" -eq 0 ]; then
    pass "API log contains ZERO non-GET delegation requests (D-008 honored)"
  else
    grep -E " (POST|DELETE|PUT|PATCH) https?://" "$APILOG" | grep "nfs4_deleg" | head -5
    err "SAFETY FAILURE: $NONGET non-GET delegation request(s)"
  fi
fi

# ------------------------------------------ 7. post-run state + /tmp policy
section "7. post-run state and /tmp policy"
cat /proc/self/mountstats > "$RUN/logs/mountstats-after.txt" 2>/dev/null
ls -1 /tmp/opstat-* > "$RUN/logs/tmp-after.txt" 2>/dev/null || : > "$RUN/logs/tmp-after.txt"
NEW_TMP=$(comm -13 "$RUN/logs/tmp-before.txt" "$RUN/logs/tmp-after.txt")
if [ -n "$NEW_TMP" ]; then
  echo "$NEW_TMP" | tee "$RUN/logs/tmp-policy-violation.txt"
  err "tooling-policy failure: new opstat artifacts appeared in /tmp"
else
  pass "no new opstat artifacts in /tmp"
fi
{ git log --oneline -3; git status --short; } > "$RUN/git-final-state.txt"

# ------------------------------------- 8. minimum-success machine check
section "8. minimum success check"
if grep -q "CHECK:deleg.live.records *PASS" "$RUN/validator-output.txt" \
   && grep -q "^RESULT: PASS" "$RUN/validator-output.txt"; then
  pass "minimum success: a REAL workload file returned live delegation records and every production check passed"
else
  err "minimum success NOT met: live-delegation objective or a production check failed (see validator-output.txt)"
fi

finish
