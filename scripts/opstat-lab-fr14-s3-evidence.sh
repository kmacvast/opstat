#!/usr/bin/env bash
# =============================================================================
# Script Name : opstat-lab-fr14-s3-evidence.sh
# Description : Produces the FR14 first-party S3 attribution evidence: drives a
#               CONTROLLED S3 workload at an endpoint proven to belong to the
#               cluster being probed, then runs the committed GET-only
#               host_view probe across that window.
#
#               This exists because the lab's persistent s3-loadgen is pinned
#               to http://172.200.202.2 - a different cluster - so its traffic
#               can never be first-party evidence for var203. This script does
#               NOT modify that service. It creates its own temporary workload
#               and removes only what it created.
#
# Modes       : OPSTAT_S3_DISCOVER_ONLY=1  read-only survey of the cluster's S3
#                                          VIPs and buckets, then exit. Run
#                                          this FIRST to choose the endpoint
#                                          and bucket.
#               (default)                  full evidence run.
#
# Required    : OPSTAT_VMS            cluster to probe
#               OPSTAT_S3_ENDPOINT    S3 endpoint URL for the workload, e.g.
#                                     http://172.200.13.180  (must be owned by
#                                     OPSTAT_VMS - hard-checked before any I/O)
#               OPSTAT_S3_BUCKET      bucket to write into. MUST be one created
#                                     or designated for this validation.
#               VAST_TOKEN or VAST_PASSWORD
#               AWS credentials for the S3 endpoint (AWS_PROFILE or the usual
#               AWS_* variables) - never printed by this script.
#
# Optional    : OPSTAT_EXPECTED_HEAD  hard-pin the published SHA
#               OPSTAT_S3_SECONDS     workload duration, default 90
#               OPSTAT_S3_OBJECTS     objects per round, default 40
#               OPSTAT_S3_KEEP=1      keep the objects this run created
#
# Evidence    : $HOME/kjmtmp/opstat/fr14s3-<DTS>/
# Final ZIP   : $HOME/opstat-fr14-s3-evidence-<DTS>.zip  (the ONE file to return)
#
# Safety      : GET-only against the VMS. The S3 workload writes ONLY under a
#               unique prefix this run creates, and deletes ONLY that prefix.
#               No bucket is created, emptied or removed. No cluster-side
#               configuration is changed. No systemd unit is modified. No /tmp
#               artifacts. Credentials are never echoed. Fails closed on an
#               endpoint/VMS mismatch. A negative attribution verdict still
#               produces the ZIP.
# =============================================================================
set -u

REPO="${OPSTAT_REPO:-$HOME/git/opstat}"
VMS="${OPSTAT_VMS:-var203.selab.vastdata.com}"
S3_ENDPOINT="${OPSTAT_S3_ENDPOINT:-}"
S3_BUCKET="${OPSTAT_S3_BUCKET:-}"
SECONDS_TO_RUN="${OPSTAT_S3_SECONDS:-90}"
OBJECTS="${OPSTAT_S3_OBJECTS:-40}"
DISCOVER_ONLY="${OPSTAT_S3_DISCOVER_ONLY:-0}"
KEEP="${OPSTAT_S3_KEEP:-0}"
DTS=$(date +%Y%m%d-%H%M%S)
RUN="$HOME/kjmtmp/opstat/fr14s3-$DTS"
ZIP="$HOME/opstat-fr14-s3-evidence-$DTS.zip"
PREFIX="opstat-fr14-evidence-$DTS"
FAILURES=0
WORKLOAD_PID=""

say()  { echo "[$(date +%T)] $*"; }
pass() { say "PASS    : $*"; }
warn() { say "WARNING : $*"; }
err()  { say "ERROR   : $*"; FAILURES=$((FAILURES + 1)); }
section() { echo; echo "== $1 =================================================="; }

mkdir -p "$RUN/raw" "$RUN/logs" "$RUN/tmp"
export TMPDIR="$RUN/tmp" TMP="$RUN/tmp" TEMP="$RUN/tmp"
export OPSTAT_API_LOG_DIR="$RUN/raw"
export OPSTAT_PROBE_OUT="$RUN/raw"

ls -1 /tmp/opstat-* > "$RUN/logs/tmp-before.txt" 2>/dev/null || : > "$RUN/logs/tmp-before.txt"

cleanup_workload() {
  if [ -n "$WORKLOAD_PID" ] && kill -0 "$WORKLOAD_PID" 2>/dev/null; then
    kill "$WORKLOAD_PID" 2>/dev/null || true
    wait "$WORKLOAD_PID" 2>/dev/null || true
    say "temporary S3 workload stopped (pid $WORKLOAD_PID)"
  fi
}
trap cleanup_workload EXIT INT TERM

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
pass "main @ $HEAD"
if [ -n "$(git status --short)" ]; then
  git status --short; err "working tree not clean"; exit 1
fi
pass "working tree clean"

# --------------------------------------------------- 2. credentials
section "2. credentials"
if [ -n "${VAST_TOKEN:-}" ]; then pass "VMS credential: VAST_TOKEN present"
elif [ -n "${VAST_PASSWORD:-}" ]; then pass "VMS credential: VAST_PASSWORD present"
else err "no VAST_TOKEN/VAST_PASSWORD - aborting before any cluster contact"; exit 1; fi

# --------------------------------------------------- 3. discovery (GET-only)
section "3. S3 target discovery (read-only)"
python3 scripts/var203_validation/discover_s3_targets.py 2>&1 \
  | tee "$RUN/logs/s3-discovery.txt"
DISC_RC=${PIPESTATUS[0]}
[ "$DISC_RC" -eq 0 ] || err "discovery failed rc=$DISC_RC"

if [ "$DISCOVER_ONLY" != "0" ]; then
  echo
  say "OPSTAT_S3_DISCOVER_ONLY=1 - stopping after discovery. No workload ran."
  say "Choose an endpoint and a SAFE bucket, then re-run without that flag."
  ( cd "$(dirname "$RUN")" && zip -qr "$ZIP" "$(basename "$RUN")" )
  unzip -tq "$ZIP" >/dev/null 2>&1 && pass "ZIP integrity verified"
  ls -lh "$ZIP"; sha256sum "$ZIP"
  echo; echo "    $ZIP"
  exit 0
fi

# --------------------------------------------- 4. required inputs
section "4. workload inputs"
if [ -z "$S3_ENDPOINT" ] || [ -z "$S3_BUCKET" ]; then
  err "OPSTAT_S3_ENDPOINT and OPSTAT_S3_BUCKET are required for an evidence run."
  say "        Run with OPSTAT_S3_DISCOVER_ONLY=1 first to choose them."
  exit 1
fi
WORKLOAD_IP=$(printf '%s' "$S3_ENDPOINT" | sed -E 's#^[a-zA-Z]+://##; s#[:/].*$##')
if ! printf '%s' "$WORKLOAD_IP" | grep -qE '^([0-9]{1,3}\.){3}[0-9]{1,3}$'; then
  err "could not derive an IPv4 address from OPSTAT_S3_ENDPOINT='$S3_ENDPOINT'"
  exit 1
fi
export OPSTAT_WORKLOAD_IP="$WORKLOAD_IP"
pass "endpoint $S3_ENDPOINT -> $WORKLOAD_IP ; bucket $S3_BUCKET ; prefix $PREFIX"
command -v aws >/dev/null 2>&1 || { err "aws CLI not found - required for the workload"; exit 1; }

# --------------------- 5. ownership: the endpoint must belong to THIS cluster
section "5. endpoint ownership (GET-only, /vips/)"
python3 - "$VMS" "$WORKLOAD_IP" <<'OWNEOF' 2>&1 | tee "$RUN/logs/endpoint-ownership.txt"
import os, ssl, sys
sys.path.insert(0, os.path.join(os.getcwd(), "scripts", "var203_validation"))
sys.path.insert(0, os.getcwd())
import vast_common
from probe_hostview_attribution import collect_vip_addresses, vms_owns_ip

vms, ip = sys.argv[1], sys.argv[2]
port = int(os.environ.get("OPSTAT_PORT", "443"))
base = ("https://%s/api" % vms) if port == 443 else ("https://%s:%d/api" % (vms, port))
headers, _a, _p = vast_common.resolve_auth(
    os.environ.get("OPSTAT_USER", "admin"), vms, None, "opstat/fr14-s3-evidence")
vast_common.configure_connection(base, headers, ssl._create_unverified_context())
literals, ranges = collect_vip_addresses(vast_common.request("GET", "/vips/"))
owned = vms_owns_ip(ip, literals, ranges)
print("S3 endpoint %s owned by %s: %s" % (ip, vms, owned))
sys.exit(0 if owned else 3)
OWNEOF
OWN_STATUS=("${PIPESTATUS[@]}")
OWN_RC=${OWN_STATUS[0]}
OWN_TEE=${OWN_STATUS[1]:-1}
[ "$OWN_TEE" -eq 0 ] || { err "could not write the ownership evidence"; exit 1; }
case "$OWN_RC" in
  0) pass "S3 endpoint $WORKLOAD_IP belongs to $VMS" ;;
  3) err "ENDPOINT MISMATCH: $WORKLOAD_IP is not owned by $VMS."
     say "        This is the 202.x failure again. Point the workload at an S3"
     say "        VIP on $VMS (see section 3) - do NOT relax this check."
     exit 1 ;;
  *) err "ownership helper failed rc=$OWN_RC - ownership UNPROVEN, refusing to run a workload"
     exit 1 ;;
esac

# ------------------------------------- 6. pre-flight: bucket must be reachable
section "6. bucket pre-flight"
export AWS_ENDPOINT_URL_S3="$S3_ENDPOINT" AWS_ENDPOINT_URL="$S3_ENDPOINT"
if aws s3 ls "s3://$S3_BUCKET" >/dev/null 2>"$RUN/logs/bucket-preflight.txt"; then
  pass "bucket s3://$S3_BUCKET is reachable at $S3_ENDPOINT"
else
  err "cannot list s3://$S3_BUCKET at $S3_ENDPOINT (see logs/bucket-preflight.txt)"
  say "        Check the AWS profile/credentials and that the bucket exists."
  exit 1
fi
{ echo "endpoint : $S3_ENDPOINT"; echo "bucket   : $S3_BUCKET"
  echo "prefix   : $PREFIX"; echo "objects  : $OBJECTS"
  echo "seconds  : $SECONDS_TO_RUN"; } | tee "$RUN/logs/workload-config.txt"

# ------------------------------------- 7. start the controlled S3 workload
section "7. controlled first-party S3 workload"
cat > "$RUN/tmp/workload.sh" <<'WLEOF'
#!/usr/bin/env bash
# Writes, reads and lists under ONE prefix that this run created. Touches
# nothing else in the bucket.
set -u
END="$1"; BUCKET="$2"; PREFIX="$3"; SECS="$4"; OBJS="$5"; OUT="$6"
export AWS_ENDPOINT_URL_S3="$END" AWS_ENDPOINT_URL="$END"
payload="$(mktemp)"; head -c 262144 /dev/urandom > "$payload"
deadline=$(( $(date +%s) + SECS ))
ops=0
while [ "$(date +%s)" -lt "$deadline" ]; do
  for i in $(seq 1 "$OBJS"); do
    [ "$(date +%s)" -lt "$deadline" ] || break
    key="$PREFIX/obj-$i.bin"
    aws s3 cp "$payload" "s3://$BUCKET/$key" >/dev/null 2>&1 && ops=$((ops+1))
    aws s3 cp "s3://$BUCKET/$key" - >/dev/null 2>&1 && ops=$((ops+1))
    aws s3api head-object --bucket "$BUCKET" --key "$key" >/dev/null 2>&1 && ops=$((ops+1))
  done
  aws s3 ls "s3://$BUCKET/$PREFIX/" >/dev/null 2>&1 && ops=$((ops+1))
  echo "$ops" > "$OUT"
done
rm -f "$payload"
echo "$ops" > "$OUT"
WLEOF
chmod +x "$RUN/tmp/workload.sh"
: > "$RUN/logs/workload-ops.txt"
"$RUN/tmp/workload.sh" "$S3_ENDPOINT" "$S3_BUCKET" "$PREFIX" \
  "$SECONDS_TO_RUN" "$OBJECTS" "$RUN/logs/workload-ops.txt" \
  > "$RUN/logs/workload-stdout.txt" 2>&1 &
WORKLOAD_PID=$!
say "workload started (pid $WORKLOAD_PID) for ${SECONDS_TO_RUN}s"
sleep 15                                   # let attribution accumulate
if ! kill -0 "$WORKLOAD_PID" 2>/dev/null; then
  err "the S3 workload exited immediately - see logs/workload-stdout.txt"
  exit 1
fi
pass "workload running; probing while it runs"

# ----------------------------------------------- 8. GET-only host_view probe
section "8. host_view attribution probe (GET-only)"
export OPSTAT_PROBE_PROTOCOLS="${OPSTAT_PROBE_PROTOCOLS:-S3,SMB2,NFS3,NFS4,BLOCK,NDB}"
python3 scripts/var203_validation/probe_hostview_attribution.py \
  2>&1 | tee "$RUN/probe-output.txt"
PROBE_RC=${PIPESTATUS[0]}
echo "PROBE-RC $PROBE_RC" > "$RUN/timestamps.txt"
case "$PROBE_RC" in
  0) pass "probe rc=0" ;;
  3) err "TARGET MISMATCH inside the probe - this run is NOT evidence" ;;
  *) err "probe rc=$PROBE_RC" ;;
esac

# ------------------------------------------- 9. stop workload, count the work
section "9. stop workload and record client-side work"
cleanup_workload; WORKLOAD_PID=""
OPS=$(cat "$RUN/logs/workload-ops.txt" 2>/dev/null || echo 0)
say "client-side S3 operations issued during the window: ${OPS:-0}"
if [ "${OPS:-0}" -gt 0 ]; then
  pass "controlled first-party S3 workload proven ($OPS operations)"
else
  err "ZERO S3 operations completed - this run is not first-party evidence"
fi
echo "client_s3_operations=${OPS:-0}" > "$RUN/workload-proof.txt"

# ------------------------------------------- 10. remove ONLY what we created
section "10. cleanup (only this run's own objects)"
if [ "$KEEP" != "0" ]; then
  warn "OPSTAT_S3_KEEP=$KEEP - leaving s3://$S3_BUCKET/$PREFIX/ in place"
else
  aws s3 rm "s3://$S3_BUCKET/$PREFIX/" --recursive \
    > "$RUN/logs/cleanup.txt" 2>&1 && \
    pass "removed only s3://$S3_BUCKET/$PREFIX/ (created by this run)" || \
    warn "cleanup of s3://$S3_BUCKET/$PREFIX/ reported an error - see logs/cleanup.txt"
fi

# ------------------------------------------------ 11. policy + GET-only proof
section "11. policy checks"
ls -1 /tmp/opstat-* > "$RUN/logs/tmp-after.txt" 2>/dev/null || : > "$RUN/logs/tmp-after.txt"
NEW_TMP=$(comm -13 "$RUN/logs/tmp-before.txt" "$RUN/logs/tmp-after.txt")
if [ -n "$NEW_TMP" ]; then
  echo "$NEW_TMP" | tee "$RUN/logs/tmp-policy-violation.txt"
  err "tooling-policy failure: new opstat artifacts appeared in /tmp (listed above)"
else
  pass "no new opstat artifacts in /tmp"
fi
API=$(find "$RUN/raw" -maxdepth 1 -name 'opstat-api-*' | head -1)
if [ -n "$API" ]; then
  pass "API log inside the run tree: $API"
  if grep -qE "\b(POST|DELETE|PUT|PATCH)\s+https?://" "$API"; then
    err "non-GET VMS request found - the probe must be GET-only"
  else
    pass "VMS API log carries GET requests only"
  fi
else
  err "no API log captured beneath the run directory"
fi

# ------------------------------------------------------- 12. manifest + zip
section "12. manifest and package"
{ git log --oneline -3; git status --short; } > "$RUN/git-final-state.txt"
{
  echo "opstat FR14 first-party S3 attribution evidence - $DTS"
  echo "HEAD $HEAD  VMS $VMS"
  echo "endpoint $S3_ENDPOINT  bucket $S3_BUCKET  prefix $PREFIX"
  echo "client S3 operations ${OPS:-0}  probe rc $PROBE_RC  failures $FAILURES"
  echo
  echo "probe-output.txt          : per-protocol verdicts + raw scrape log"
  echo "workload-proof.txt        : client-side operation count"
  echo "logs/s3-discovery.txt     : cluster S3 VIPs and buckets"
  echo "logs/endpoint-ownership.txt: proof the endpoint belongs to this VMS"
  echo "logs/workload-config.txt  : endpoint, bucket, prefix, duration"
  echo "raw/host_view-*.txt       : raw exposition captures"
  echo "raw/hostview-probe-summary.json : machine-readable per-sample summary"
  echo "raw/opstat-api-*.log      : GET-only proof"
  echo
  find "$RUN" -type f | sed "s#^$RUN/#  #" | sort
} > "$RUN/MANIFEST.txt"
cat "$RUN/MANIFEST.txt"

( cd "$(dirname "$RUN")" && zip -qr "$ZIP" "$(basename "$RUN")" )
unzip -tq "$ZIP" >/dev/null 2>&1 && pass "ZIP integrity verified" || err "ZIP failed integrity check"
echo; ls -lh "$ZIP"; sha256sum "$ZIP"
echo
echo "======================================================================"
if [ "$FAILURES" -eq 0 ] && [ "$PROBE_RC" -eq 0 ]; then
  echo "RESULT: evidence collected - return this ONE file:"
  echo; echo "    $ZIP"; echo "======================================================================"
  exit 0
fi
echo "RESULT: $FAILURES failure(s), probe rc $PROBE_RC - return the archive anyway,"
echo "        it contains the failure evidence:"
echo; echo "    $ZIP"; echo "======================================================================"
exit 1
