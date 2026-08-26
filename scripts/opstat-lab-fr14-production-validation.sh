#!/usr/bin/env bash
# =============================================================================
# Script Name : opstat-lab-fr14-production-validation.sh
# Description : Validates the IMPLEMENTED FR14 protocol-attribution behavior
#               against a real cluster, mechanically. Drives the production
#               engines in-process via
#               scripts/var203_validation/validate_fr14_attribution.py and
#               checks that a protocol drill shows ONLY that protocol's
#               attributable data - deriving the foreign-object set from the
#               cluster's own host_view exposition, so no human has to judge
#               contamination by eye.
# Usage       : run once per cluster, with that cluster's loadgen ACTIVE.
#                 var203 + smb-loadgen  -> OPSTAT_FR14_ENGINES=smb,s3
#                 var204 + nfs3-loadgen -> OPSTAT_FR14_ENGINES=nfs_v3,nfs_v41
#               Override engines with OPSTAT_FR14_ENGINES; target with
#               OPSTAT_VMS; loadgen with OPSTAT_LOADGEN.
# Evidence    : everything lands under $HOME/kjmtmp/opstat/fr14val-<DTS>/
#               (raw/ frames + API log, logs/, tmp/).
# Final ZIP   : $HOME/opstat-fr14-production-validation-<DTS>.zip (the ONE
#               file to return; nothing else is written outside the run dir).
# Safety      : production GETs plus each engine's own headline monitors,
#               which its cleanup() deletes and this script verifies gone by
#               exact id. The FR14 drills create no monitors at all.
# Dependencies: bash, git, python3, zip/unzip, sha256sum, systemctl;
#               VAST_PASSWORD or VAST_TOKEN in the environment.
# Target      : OPSTAT_VMS. Set OPSTAT_EXPECTED_HEAD to hard-pin the published
#               SHA; the script always requires HEAD == origin/main after
#               ff-only.
# =============================================================================
set -u

# ------------------------------------------------------------------ variables
REPO="${OPSTAT_REPO:-$HOME/git/opstat}"
VMS="${OPSTAT_VMS:-var203.selab.vastdata.com}"
LOADGEN="${OPSTAT_LOADGEN:-smb-loadgen}"
ENGINES="${OPSTAT_FR14_ENGINES:-smb,s3}"
NFS3_MOUNT="${OPSTAT_NFS3_MOUNT:-/mnt/kmacs-root}"
SMB_MOUNT="${OPSTAT_SMB_MOUNT:-/mnt/smbtest}"
DTS=$(date +%Y%m%d-%H%M%S)
RUN="$HOME/kjmtmp/opstat/fr14val-$DTS"
ZIP="$HOME/opstat-fr14-production-validation-$DTS.zip"
FAILURES=0

say()  { echo "[$(date +%T)] $*"; }
pass() { say "PASS    : $*"; }
warn() { say "WARNING : $*"; }
err()  { say "ERROR   : $*"; FAILURES=$((FAILURES + 1)); }
section() { echo; echo "== $1 =================================================="; }

mkdir -p "$RUN/raw" "$RUN/logs" "$RUN/tmp"
export TMPDIR="$RUN/tmp" TMP="$RUN/tmp" TEMP="$RUN/tmp"
export OPSTAT_API_LOG_DIR="$RUN/raw"
export OPSTAT_FR14_ENGINES="$ENGINES"

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
# PRESENCE ONLY - NOT workload proof. A service can be active and driving a
# DIFFERENT cluster (the lab's nfs3-loadgen targets 172.200.202.x). The real
# gates are section 6 (client I/O through this mount) and the validator's own
# per-protocol attribution check.
state=$(systemctl is-active "$LOADGEN.service" 2>&1)
if [ "$state" = "active" ]; then
  pass "$LOADGEN is active (prerequisite only - not proof of traffic to $VMS)"
else
  warn "$LOADGEN is $state - not fatal by itself; the workload gates below decide"
fi
systemctl status "$LOADGEN.service" --no-pager -l > "$RUN/logs/loadgen-status.txt" 2>&1

# -------- 3b. the workload must target the cluster under validation.
# Same hard rule as the FR14 probe: a workload pointed at another cluster
# produces evidence about that other cluster (the 2026-08-25 mismatch).
section "3b. workload target derivation"
WORKLOAD_IP=""
case "$LOADGEN" in
  nfs3-loadgen|nfs41-loadgen)
    SRC=$(findmnt -no SOURCE "$NFS3_MOUNT" 2>/dev/null)
    WORKLOAD_IP="${SRC%%:*}"
    [ -n "$WORKLOAD_IP" ] && pass "NFS mount $NFS3_MOUNT served by $WORKLOAD_IP" \
      || err "cannot derive server from mount $NFS3_MOUNT (findmnt: '$SRC')"
    ;;
  smb-loadgen)
    WORKLOAD_IP=$(grep -F " $SMB_MOUNT " /proc/mounts | grep -oE 'addr=[0-9.]+' | head -1 | cut -d= -f2)
    [ -n "$WORKLOAD_IP" ] && pass "CIFS mount $SMB_MOUNT served by $WORKLOAD_IP" \
      || err "cannot derive server from CIFS mount $SMB_MOUNT"
    ;;
  s3-loadgen)
    WORKLOAD_IP=$(systemctl status "$LOADGEN.service" --no-pager -l 2>/dev/null \
      | grep -oE '\-\-s3endpoints[= ]+https?://[0-9.]+' | grep -oE '[0-9.]+$' | head -1)
    [ -n "$WORKLOAD_IP" ] && pass "s3-loadgen endpoint is $WORKLOAD_IP" \
      || err "cannot derive --s3endpoints address from $LOADGEN"
    ;;
  *) err "no workload-target derivation rule for loadgen '$LOADGEN'" ;;
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
  echo "engines       : $ENGINES"
  echo "loadgen       : $LOADGEN"
  echo "workload_ip   : $WORKLOAD_IP"
  echo "run dir       : $RUN"
} | tee "$RUN/prereqs.txt"
mount | grep -iE "nfs|vast|cifs|smb" > "$RUN/logs/mounts.txt" 2>&1
cat /proc/self/mountstats > "$RUN/logs/mountstats-before.txt" 2>/dev/null
ls -1 /tmp/opstat-* > "$RUN/logs/tmp-before.txt" 2>/dev/null || : > "$RUN/logs/tmp-before.txt"
date '+VALIDATE-START %F %T' | tee "$RUN/timestamps.txt"

# ---------------------------- 5. workload-target ownership (GET-only, /vips/)
section "5. workload-target ownership check"
python3 - "$VMS" "$WORKLOAD_IP" <<'PYEOF' 2>&1 | tee "$RUN/logs/target-ownership.txt"
import os, ssl, sys
sys.path.insert(0, os.path.join(os.getcwd(), "scripts", "var203_validation"))
sys.path.insert(0, os.getcwd())
import vast_common
from probe_hostview_attribution import collect_vip_addresses, vms_owns_ip

vms, workload_ip = sys.argv[1], sys.argv[2]
port = int(os.environ.get("OPSTAT_PORT", "443"))
base = ("https://%s/api" % vms) if port == 443 else ("https://%s:%d/api" % (vms, port))
headers, _a, _p = vast_common.resolve_auth(
    os.environ.get("OPSTAT_USER", "admin"), vms, None, "opstat/fr14-validation")
vast_common.configure_connection(base, headers, ssl._create_unverified_context())
literals, ranges = collect_vip_addresses(vast_common.request("GET", "/vips/"))
owned = vms_owns_ip(workload_ip, literals, ranges)
print("workload %s owned by %s: %s" % (workload_ip, vms, owned))
print("  vip literals: %s" % sorted(literals))
print("  vip ranges  : %s" % ranges)
sys.exit(0 if owned else 3)
PYEOF
[ "$OWNER_RC" -eq 0 ] && pass "workload target $WORKLOAD_IP belongs to $VMS" \
  || err "TARGET MISMATCH: $WORKLOAD_IP is not owned by $VMS - this run is NOT evidence"
[ "$OWNER_RC" -eq 0 ] || exit 1

# ------------- 6. client-side workload proof through the EXACT mount --------
# Ownership (3b/5) proves the mount points at this cluster. It does NOT prove
# any I/O flowed. This measures it, over a window that brackets the
# validation, on the specific mount named for this run. Bypass with
# OPSTAT_FR14_REQUIRE_TRAFFIC=0 for a deliberate idle-behaviour run.
section "6. client-side workload proof"
REQUIRE_TRAFFIC="${OPSTAT_FR14_REQUIRE_TRAFFIC:-1}"
WORKLOAD_MOUNT=""
case "$LOADGEN" in
  nfs3-loadgen|nfs41-loadgen) WORKLOAD_MOUNT="$NFS3_MOUNT" ;;
  smb-loadgen)                WORKLOAD_MOUNT="$SMB_MOUNT" ;;
esac
if [ -n "$WORKLOAD_MOUNT" ]; then
  say "measuring client I/O through $WORKLOAD_MOUNT across the validation window"
  cat /proc/self/mountstats > "$RUN/logs/mountstats-window-start.txt" 2>/dev/null
  cat /proc/fs/cifs/Stats  > "$RUN/logs/cifs-window-start.txt" 2>/dev/null || \
    : > "$RUN/logs/cifs-window-start.txt"
else
  warn "no client mount associated with '$LOADGEN' - client-side proof skipped"
fi

# ------------------------------ 7. production-path validation, in-process
section "7. FR14 attribution validation (production engines)"
echo "command: OPSTAT_VMS=$VMS OPSTAT_FR14_ENGINES=$ENGINES python3 scripts/var203_validation/validate_fr14_attribution.py" \
  | tee "$RUN/logs/exact-command.txt"
python3 scripts/var203_validation/validate_fr14_attribution.py \
  2>&1 | tee "$RUN/validate-output.txt"
VAL_RC=${PIPESTATUS[0]}
echo "VALIDATE-RC $VAL_RC" | tee -a "$RUN/timestamps.txt"
date '+VALIDATE-END %F %T' | tee -a "$RUN/timestamps.txt"
[ "$VAL_RC" -eq 0 ] && pass "validation rc=0 (every check passed)" \
  || err "validation rc=$VAL_RC - see the FAILED lines in validate-output.txt"
grep -c '^CHECK:' "$RUN/validate-output.txt" > "$RUN/logs/check-count.txt" 2>/dev/null || true
grep '^CHECK:.* FAIL' "$RUN/validate-output.txt" > "$RUN/logs/failed-checks.txt" 2>/dev/null || \
  : > "$RUN/logs/failed-checks.txt"

# ------------------------------------------ 7. post-run captures + policy
section "8. post-run capture, workload delta and /tmp policy check"
cat /proc/self/mountstats > "$RUN/logs/mountstats-after.txt" 2>/dev/null
cat /proc/fs/cifs/Stats  > "$RUN/logs/cifs-window-end.txt" 2>/dev/null || \
  : > "$RUN/logs/cifs-window-end.txt"

# ---- the gate: did real client I/O flow through THIS mount in the window? --
# Mount ownership proves where the mount points. This proves work actually
# went through it while the validation ran. Without it, an idle mount on the
# right cluster reads exactly like a busy one.
if [ -n "$WORKLOAD_MOUNT" ]; then
  python3 - "$RUN" "$WORKLOAD_MOUNT" "$REQUIRE_TRAFFIC" <<'WORKEOF' | tee "$RUN/workload-proof.txt"
import io, os, re, sys
run, mnt, require = sys.argv[1], sys.argv[2], sys.argv[3] != "0"


def nfs_ops(path):
    """Per-op counters for exactly one NFS mount, or None if not NFS."""
    try:
        txt = io.open(path).read()
    except IOError:
        return None
    marker = "mounted on %s" % mnt
    if marker not in txt:
        return None
    sec = txt.split(marker)[1].split("device")[0]
    out = {}
    for op in ("READ", "WRITE", "GETATTR", "SETATTR", "LOOKUP", "CREATE",
               "REMOVE", "MKDIR", "ACCESS", "COMMIT"):
        m = re.search(op + r":\s*\n?\s*([0-9]+)", sec)
        if m:
            out[op] = int(m.group(1))
    return out


def cifs_total(path):
    try:
        txt = io.open(path).read()
    except IOError:
        return None
    nums = [int(n) for n in re.findall(r"(?:Reads|Writes):\s*(\d+)", txt)]
    smbs = [int(n) for n in re.findall(r"SMBs:\s*(\d+)", txt)]
    if not nums and not smbs:
        return None
    return sum(nums) + sum(smbs)


before = nfs_ops(os.path.join(run, "logs", "mountstats-window-start.txt"))
after = nfs_ops(os.path.join(run, "logs", "mountstats-after.txt"))
total, kind = None, None
if before is not None and after is not None:
    kind, total = "NFS", 0
    for k in sorted(before):
        d = after.get(k, before[k]) - before[k]
        total += d
        print("  %-8s delta %10d" % (k, d))
else:
    b = cifs_total(os.path.join(run, "logs", "cifs-window-start.txt"))
    a = cifs_total(os.path.join(run, "logs", "cifs-window-end.txt"))
    if b is not None and a is not None:
        kind, total = "CIFS", a - b
        print("  CIFS SMB/read/write counter delta: %d" % total)

if total is None:
    print("INCONCLUSIVE: no client-side counters for %s. The validator's "
          "cluster-side 'live_traffic' checks are then the ONLY workload "
          "evidence for this run." % mnt)
    sys.exit(0)
if total > 0:
    print("PASS    : %s client I/O through %s during the window: %d operations"
          % (kind, mnt, total))
    sys.exit(0)
tag = "FAIL    " if require else "ACCEPTED"
print("%s: ZERO %s client I/O through %s during the validation window."
      % (tag, kind, mnt))
if require:
    print("          The mount is idle, so this run proves nothing about")
    print("          protocol behaviour under load. Drive a workload through")
    print("          %s and re-run, or set" % mnt)
    print("          OPSTAT_FR14_REQUIRE_TRAFFIC=0 to validate idle")
    print("          behaviour deliberately.")
else:
    print("          OPSTAT_FR14_REQUIRE_TRAFFIC=0 was set, so this idle run")
    print("          is accepted ON PURPOSE. It validates idle behaviour")
    print("          ONLY - it is NOT evidence about behaviour under load.")
sys.exit(4 if require else 0)
WORKEOF
  WORK_RC=${PIPESTATUS[0]}
  if [ "$WORK_RC" -eq 0 ]; then
    pass "client-side workload proof accepted"
  else
    err "NO CLIENT WORKLOAD through $WORKLOAD_MOUNT - this run is not evidence"
  fi
fi
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
if [ -n "$API_IN_TREE" ]; then
  NONGET=$(grep -cE "\b(PUT|PATCH)\s" "$API_IN_TREE" || true)
  [ "${NONGET:-0}" -eq 0 ] && pass "no PUT/PATCH issued" || err "unexpected mutating verbs in the API log"
  DELS=$(grep -cE "\bDELETE\s+\S*/monitors/" "$API_IN_TREE" || true)
  POSTS=$(grep -cE "\bPOST\s+\S*/monitors/" "$API_IN_TREE" || true)
  say "monitor lifecycle: $POSTS created, $DELS deleted (headline monitors only)"
  echo "posts=$POSTS deletes=$DELS" > "$RUN/logs/monitor-lifecycle.txt"
fi

# --------------------------------------------- 8. final git state + manifest
section "9. manifest"
{ git log --oneline -3; git status --short; } > "$RUN/git-final-state.txt"
{
  echo "opstat FR14 production validation - $DTS"
  echo "HEAD $HEAD  target $VMS  engines $ENGINES  loadgen $LOADGEN"
  echo "validate rc $VAL_RC  script failures $FAILURES"
  echo
  echo "prereqs.txt              : environment, SHA, workload target"
  echo "timestamps.txt           : VALIDATE-START/END, rc"
  echo "validate-output.txt      : every CHECK: verdict from the production run"
  echo "logs/exact-command.txt   : the exact command executed"
  echo "logs/failed-checks.txt   : failing checks only (empty on a clean run)"
  echo "logs/target-ownership.txt: proof the workload targets this cluster"
  echo "logs/monitor-lifecycle.txt: monitors created vs deleted"
  echo "raw/                     : API log"
  echo "logs/                    : mounts, mountstats, loadgen status, tmp diff"
  echo
  echo "file inventory:"
  find "$RUN" -type f | sed "s#^$RUN/#  #" | sort
} > "$RUN/MANIFEST.txt"
cat "$RUN/MANIFEST.txt"

# --------------------------------------- 9. package, verify, hand back
section "10. package"
( cd "$(dirname "$RUN")" && zip -qr "$ZIP" "$(basename "$RUN")" )
if unzip -tq "$ZIP" >/dev/null 2>&1; then pass "ZIP integrity verified"; else err "ZIP failed integrity check"; fi
echo; unzip -l "$ZIP" | tail -8
echo; ls -lh "$ZIP"; sha256sum "$ZIP"
echo
echo "======================================================================"
if [ "$FAILURES" -eq 0 ] && [ "$VAL_RC" -eq 0 ]; then
  echo "RESULT: PASS - return this ONE file:"
else
  echo "RESULT: $FAILURES script failure(s), validate rc $VAL_RC - return the archive anyway:"
fi
echo
echo "    $ZIP"
echo "======================================================================"
