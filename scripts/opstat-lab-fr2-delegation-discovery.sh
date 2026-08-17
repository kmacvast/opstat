#!/usr/bin/env bash
# =============================================================================
# Script Name : opstat-lab-fr2-delegation-discovery.sh
# Description : One-shot read-only discovery of the NFSv4 delegation endpoint
#               (FR2 Stage A, D-008). Enumerates real files the NFSv4.1
#               loadgen holds open on the /kmacs/nfstest mount, queries
#               GET /tenants/{id}/nfs4_delegs/ for each (plus availability,
#               directory-path and nonexistent-path cases), and captures the
#               raw responses. STRICTLY GET-ONLY: the probe's transport
#               refuses mutating methods, and this script fails hard if the
#               API log contains a single non-GET line.
# Evidence    : everything lands under $HOME/kjmtmp/opstat/fr2-<DTS>/
# Final ZIP   : $HOME/opstat-fr2-delegation-discovery-<DTS>.zip (the ONE file
#               to return).
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
NFS41_MOUNT="${OPSTAT_NFS41_MOUNT:-/mnt/nfs41test}"   # client mount of the NFSv4.1 view
FIO_WAIT_S=90                          # bounded wait for a loadgen fio phase
DTS=$(date +%Y%m%d-%H%M%S)
RUN="$HOME/kjmtmp/opstat/fr2-$DTS"
ZIP="$HOME/opstat-fr2-delegation-discovery-$DTS.zip"
FAILURES=0

say()  { echo "[$(date +%T)] $*"; }
pass() { say "PASS    : $*"; }
warn() { say "WARNING : $*"; }
err()  { say "ERROR   : $*"; FAILURES=$((FAILURES + 1)); }
section() { echo; echo "== $1 =================================================="; }

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
state=$(systemctl is-active nfs41-loadgen.service 2>&1)
if [ "$state" = "active" ]; then pass "nfs41-loadgen active (open files give the best delegation odds)"
else warn "nfs41-loadgen is $state - shape/empty evidence still lands"; fi
systemctl status nfs41-loadgen.service --no-pager -l > "$RUN/logs/nfs41-loadgen-status.txt" 2>&1
mount | grep -iE "nfs|vast" > "$RUN/logs/mounts.txt" 2>&1
grep -q "$NFS41_MOUNT" "$RUN/logs/mounts.txt" \
  && pass "NFSv4.1 mount present: $NFS41_MOUNT" \
  || warn "$NFS41_MOUNT not mounted; file candidates may be empty"

# --------------------------------- 4. mount facts + real file candidates
section "4. mount derivation and real file candidates (client-side truth)"
EXPORT=$(mount | awk -v mp="$NFS41_MOUNT" '$3 == mp {split($1, a, ":"); print a[2]}' | head -1)
if [ -z "$EXPORT" ]; then
  err "no mount found at $NFS41_MOUNT; cannot derive the export path"; EXPORT="unknown"
else
  pass "mount: server export $EXPORT on $NFS41_MOUNT"
fi
# Prefer files the loadgen holds OPEN right now (best delegation odds):
# wait bounded time for an fio phase, then read /proc/<pid>/fd symlinks.
OPEN_FILES=""
DEADLINE=$(( $(date +%s) + FIO_WAIT_S ))
while [ "$(date +%s)" -lt "$DEADLINE" ]; do
  for pid in $(pgrep -x fio 2>/dev/null); do
    for fd in /proc/$pid/fd/*; do
      tgt=$(readlink "$fd" 2>/dev/null) || continue
      case "$tgt" in
        "$NFS41_MOUNT"/*) OPEN_FILES="$OPEN_FILES$tgt"$'\n' ;;
      esac
    done
  done
  [ -n "$OPEN_FILES" ] && break
  sleep 5
done
OPEN_FILES=$(printf '%s' "$OPEN_FILES" | sort -u | head -4)
# Always also enumerate existing regular files (works between fio phases).
FOUND_FILES=$(find "$NFS41_MOUNT" -maxdepth 4 -type f -size +0 2>/dev/null | head -6)
CLIENT_FILES=$(printf '%s\n%s\n' "$OPEN_FILES" "$FOUND_FILES" | grep . | sort -u | head -6 | paste -sd, -)
{
  echo "export path     : $EXPORT"
  echo "open-by-fio     : ${OPEN_FILES:-none}"
  echo "found files     : ${FOUND_FILES:-none}"
  echo "client files    : ${CLIENT_FILES:-none}"
} | tee "$RUN/candidates.txt"
ls -la "$NFS41_MOUNT" "$NFS41_MOUNT"/* > "$RUN/logs/mount-listing.txt" 2>&1
if [ -n "$CLIENT_FILES" ]; then pass "real file candidates discovered"
else err "no real files found beneath $NFS41_MOUNT - targeting cannot be proven"; fi
cat /proc/self/mountstats > "$RUN/logs/mountstats-before.txt" 2>/dev/null
ls -1 /tmp/opstat-* > "$RUN/logs/tmp-before.txt" 2>/dev/null || : > "$RUN/logs/tmp-before.txt"
{
  echo "hostname : $(hostname)"; echo "collected: $(date '+%F %T %Z')"
  echo "HEAD     : $HEAD"; echo "python   : $(python3 -V 2>&1)"
  echo "target   : $VMS"; echo "run dir  : $RUN"
} | tee "$RUN/prereqs.txt"
date '+PROBE-START %F %T' | tee "$RUN/timestamps.txt"

# --------------------------------- 5. read-only delegation discovery probe
section "5. delegation endpoint discovery (GET-only)"
python3 scripts/var203_validation/probe_fr2_delegations.py \
  --vms "$VMS" --user admin \
  --mountpoint "$NFS41_MOUNT" --export-path "$EXPORT" \
  --client-files "$CLIENT_FILES" \
  --dir-paths "$EXPORT/nfs41_loadgen" \
  --evidence-dir "$RUN/raw" 2>&1 | tee "$RUN/probe-output.txt"
PROBE_RC=${PIPESTATUS[0]}
echo "PROBE-RC $PROBE_RC" | tee -a "$RUN/timestamps.txt"
date '+PROBE-END %F %T' | tee -a "$RUN/timestamps.txt"
[ "$PROBE_RC" -eq 0 ] && pass "probe rc=0" || err "probe rc=$PROBE_RC"

# --------------------------------------- 6. HARD safety check: GET only
section "6. read-only verification (API log)"
APILOG=$(find "$RUN/raw" -maxdepth 1 -name 'opstat-api-fr2-*' | head -1)
if [ -z "$APILOG" ]; then
  err "API log not found beneath the run directory"
else
  pass "API log inside the run tree: $APILOG"
  NONGET=$(grep -cE " (POST|DELETE|PUT|PATCH) https?://" "$APILOG")
  if [ "$NONGET" -eq 0 ]; then
    pass "API log contains ZERO non-GET requests (D-008 honored)"
  else
    grep -E " (POST|DELETE|PUT|PATCH) https?://" "$APILOG" | head -5
    err "SAFETY FAILURE: $NONGET non-GET request(s) in a read-only discovery"
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

# --------------------------------------------------------- 8. manifest
section "8. manifest"
{
  echo "opstat FR2 delegation discovery - $DTS"
  echo "HEAD $HEAD  target $VMS  probe rc $PROBE_RC  script failures $FAILURES"
  echo
  echo "candidates.txt      : server-side file/dir paths queried"
  echo "probe-output.txt    : full PROBE: verdicts incl. observed record fields"
  echo "raw/                : verbatim endpoint responses + GET-only API log"
  echo "logs/               : mounts, mountstats (DELEGRETURN/OPEN context),"
  echo "                      loadgen status, tmp before/after"
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
