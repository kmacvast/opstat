#!/usr/bin/env bash
# =============================================================================
# Script Name : opstat-lab-fr14-hostview-attribution-probe.sh
# Description : GET-only capability probe deciding, per protocol label,
#               whether /prometheusmetrics/host_view on the target cluster
#               can attribute per-view traffic - the evidence that decides
#               FR14: enabling the NFSv3 VIEW drill on 5.5.0.1-class builds
#               (D-016 reopen clause) and rebuilding the SMB VIEW / S3
#               BUCKET drills away from all-protocol ViewMetrics.
#               Creates NO monitors and issues NO non-GET request.
# Usage       : run once per (cluster, protocol) pairing while that
#               protocol's loadgen is ACTIVE, e.g.
#                 var204 + nfs3-loadgen   (decides NFS3)
#                 var203 + smb-loadgen    (decides SMB2)
#                 var203 + s3-loadgen     (decides S3)
#               Select the loadgen with OPSTAT_LOADGEN (default nfs3-loadgen)
#               and the target with OPSTAT_VMS.
# Evidence    : everything lands under $HOME/kjmtmp/opstat/fr14-<DTS>/
#               (raw/ exposition captures + API log, logs/, tmp/).
# Final ZIP   : $HOME/opstat-fr14-hostview-probe-<DTS>.zip (the ONE file to
#               return; nothing else is written outside the run dir).
# Dependencies: bash, git, python3, zip/unzip, sha256sum, systemctl;
#               VAST_PASSWORD or VAST_TOKEN in the environment.
# Target      : OPSTAT_VMS (default var204.selab.vastdata.com, VAST 5.5.0.1).
#               Set OPSTAT_EXPECTED_HEAD to hard-pin the published SHA; the
#               script always requires HEAD == origin/main after ff-only.
# =============================================================================
set -u

# ------------------------------------------------------------------ variables
REPO="${OPSTAT_REPO:-$HOME/git/opstat}"
VMS="${OPSTAT_VMS:-var204.selab.vastdata.com}"
LOADGEN="${OPSTAT_LOADGEN:-nfs3-loadgen}"
NFS3_MOUNT="${OPSTAT_NFS3_MOUNT:-/mnt/kmacs-root}"
SMB_MOUNT="${OPSTAT_SMB_MOUNT:-/mnt/smbtest}"
DTS=$(date +%Y%m%d-%H%M%S)
RUN="$HOME/kjmtmp/opstat/fr14-$DTS"
ZIP="$HOME/opstat-fr14-hostview-probe-$DTS.zip"
FAILURES=0

say()  { echo "[$(date +%T)] $*"; }
pass() { say "PASS    : $*"; }
warn() { say "WARNING : $*"; }
err()  { say "ERROR   : $*"; FAILURES=$((FAILURES + 1)); }
section() { echo; echo "== $1 =================================================="; }

mkdir -p "$RUN/raw" "$RUN/logs" "$RUN/tmp"
export TMPDIR="$RUN/tmp" TMP="$RUN/tmp" TEMP="$RUN/tmp"
export OPSTAT_API_LOG_DIR="$RUN/raw"
export OPSTAT_PROBE_OUT="$RUN/raw"

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
section "3. credentials and load generator"
if [ -n "${VAST_TOKEN:-}" ]; then pass "credential: VAST_TOKEN present"
elif [ -n "${VAST_PASSWORD:-}" ]; then pass "credential: VAST_PASSWORD present"
else err "no VAST_TOKEN/VAST_PASSWORD in environment - aborting before any cluster contact"; exit 1; fi
# PRESENCE ONLY - never proof. The lab's s3-loadgen is active while pointed
# at another cluster entirely, which is the failure this probe exists to
# prevent. The real gate is the /vips/ ownership check below. When the
# caller supplies OPSTAT_WORKLOAD_IP (a temporary first-party workload with
# no systemd unit), an inactive service is not a reason to abort.
state=$(systemctl is-active "$LOADGEN.service" 2>&1)
if [ "$state" = "active" ]; then
  pass "$LOADGEN is active (prerequisite only - not proof of traffic to $VMS)"
elif [ -n "${OPSTAT_WORKLOAD_IP:-}" ]; then
  warn "$LOADGEN is $state - caller supplied an explicit workload target; ownership still gates this run"
else
  err "$LOADGEN is $state and no OPSTAT_WORKLOAD_IP was supplied - nothing proves a workload exists; aborting"
  exit 1
fi
systemctl status "$LOADGEN.service" --no-pager -l > "$RUN/logs/loadgen-status.txt" 2>&1

# -------- 3b. derive the workload's ACTUAL server address, protocol-aware.
# The first FR14 NFS3 run proved why this is mandatory: 866k client ops went
# to 172.200.202.x while var204 was probed, and the probe reported a
# misleading PRESENT(idle). The probe itself now refuses to scrape unless
# this address is owned by the probed VMS (checked against live /vips/).
# This wrapper only DERIVES and REPORTS; it repairs nothing.
section "3b. workload target derivation"
# An explicitly supplied target wins: a controlled first-party workload is
# not described by any systemd unit. This does NOT weaken anything - the
# address still has to be owned by the probed VMS (section 5), which is the
# check that actually decides whether this run is evidence.
WORKLOAD_IP="${OPSTAT_WORKLOAD_IP:-}"
if [ -n "$WORKLOAD_IP" ]; then
  pass "workload target supplied explicitly: $WORKLOAD_IP (ownership still enforced)"
fi
case "${WORKLOAD_IP:+supplied}${WORKLOAD_IP:-$LOADGEN}" in
  supplied) ;;
  nfs3-loadgen|nfs41-loadgen)
    SRC=$(findmnt -no SOURCE "$NFS3_MOUNT" 2>/dev/null)
    WORKLOAD_IP="${SRC%%:*}"
    [ -n "$WORKLOAD_IP" ] && pass "NFS mount $NFS3_MOUNT served by $WORKLOAD_IP"       || err "cannot derive server from mount $NFS3_MOUNT (findmnt: '$SRC')"
    ;;
  smb-loadgen)
    WORKLOAD_IP=$(grep -F " $SMB_MOUNT " /proc/mounts | grep -oE 'addr=[0-9.]+' | head -1 | cut -d= -f2)
    [ -n "$WORKLOAD_IP" ] && pass "CIFS mount $SMB_MOUNT served by $WORKLOAD_IP"       || err "cannot derive server from CIFS mount $SMB_MOUNT"
    ;;
  s3-loadgen)
    WORKLOAD_IP=$(systemctl status "$LOADGEN.service" --no-pager -l 2>/dev/null       | grep -oE '\-\-s3endpoints[= ]+https?://[0-9.]+' | grep -oE '[0-9.]+$' | head -1)
    [ -n "$WORKLOAD_IP" ] && pass "s3-loadgen endpoint is $WORKLOAD_IP"       || err "cannot derive --s3endpoints address from $LOADGEN"
    ;;
  *)
    err "no workload-target derivation rule for loadgen '$LOADGEN'"
    ;;
esac
if [ -z "$WORKLOAD_IP" ]; then
  err "workload target underivable - refusing to produce unattributable evidence"
  exit 1
fi
export OPSTAT_WORKLOAD_IP="$WORKLOAD_IP"

# --------------------------------------------- 4. pre-run environment capture
section "4. pre-run capture"
{
  echo "hostname      : $(hostname)"
  echo "collected     : $(date '+%F %T %Z')"
  echo "HEAD          : $HEAD"
  echo "python        : $(python3 -V 2>&1)"
  echo "target        : $VMS"
  echo "loadgen       : $LOADGEN"
  echo "workload_ip   : $WORKLOAD_IP"
  echo "run dir       : $RUN"
} | tee "$RUN/prereqs.txt"
mount | grep -iE "nfs|vast|cifs|smb" > "$RUN/logs/mounts.txt" 2>&1
cat /proc/self/mountstats > "$RUN/logs/mountstats-before.txt" 2>/dev/null
ls -1 /tmp/opstat-* > "$RUN/logs/tmp-before.txt" 2>/dev/null || : > "$RUN/logs/tmp-before.txt"
date '+PROBE-START %F %T' | tee "$RUN/timestamps.txt"

# --------------------------------------------------- 5. GET-only probe
section "5. host_view attribution probe (GET-only)"
python3 scripts/var203_validation/probe_hostview_attribution.py \
  2>&1 | tee "$RUN/probe-output.txt"
PROBE_RC=${PIPESTATUS[0]}
echo "PROBE-RC $PROBE_RC" | tee -a "$RUN/timestamps.txt"
date '+PROBE-END %F %T' | tee -a "$RUN/timestamps.txt"
if [ "$PROBE_RC" -eq 0 ]; then pass "probe rc=0"
elif [ "$PROBE_RC" -eq 3 ]; then
  err "TARGET MISMATCH: the workload at $WORKLOAD_IP does not belong to $VMS (see probe-output.txt). This run is NOT evidence; fix the mount/loadgen target."
else err "probe rc=$PROBE_RC"; fi

# ------------------------------------------ 6. post-run workload proof
section "6. post-run capture and client-side workload proof"
cat /proc/self/mountstats > "$RUN/logs/mountstats-after.txt" 2>/dev/null
python3 - "$RUN" "$NFS3_MOUNT" <<'PYEOF' | tee "$RUN/client-workload-deltas.txt"
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
    print("NOTE    : no NFS mountstats for %s - for SMB/S3 probes the loadgen"
          " status file is the client-side evidence" % mnt)
else:
    total = 0
    for k in b:
        delta = a.get(k, b[k]) - b[k]
        total += delta
        print("  %-8s delta %10d" % (k, delta))
    verdict = "PASS" if total > 1000 else "WARNING"
    print("%s    : NFS client ops during probe window: %d (%s)"
          % (verdict, total, mnt))
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
if [ -n "$API_IN_TREE" ] && grep -qE "\b(POST|DELETE|PUT|PATCH)\s" "$API_IN_TREE"; then
  err "non-GET request found in the API log - the probe must be GET-only"
else
  pass "API log carries GET requests only"
fi

# --------------------------------------------- 8. final git state + manifest
section "8. manifest"
{ git log --oneline -3; git status --short; } > "$RUN/git-final-state.txt"
{
  echo "opstat FR14 host_view attribution probe - $DTS"
  echo "HEAD $HEAD  target $VMS  loadgen $LOADGEN  probe rc $PROBE_RC  script failures $FAILURES"
  echo
  echo "prereqs.txt                       : environment and SHA"
  echo "timestamps.txt                    : PROBE-START/END, rc"
  echo "probe-output.txt                  : scrape log + per-protocol verdicts"
  echo "client-workload-deltas.txt        : client-side load proof (NFS mounts)"
  echo "raw/host_view-NN.txt              : raw exposition captures"
  echo "raw/hostview-probe-summary.json   : machine-readable per-sample summary"
  echo "raw/opstat-api-*.log              : API log (GET-only proof)"
  echo "logs/                             : mounts, mountstats, loadgen, tmp diff"
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
