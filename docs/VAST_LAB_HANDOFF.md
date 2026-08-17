Yes. I’d make the lab script fully self-contained and establish one hard rule:

Every transient artifact, API log, raw capture, journal, status file, and scratch file lives under one DTS-named directory beneath ~/kjmtmp/opstat/. The only thing written outside that tree is the final ZIP in $HOME.

I’d also explicitly redirect the process temp environment into that evidence directory via TMPDIR, TMP, and TEMP. That should catch anything using the normal Python/system temporary-directory mechanism. If opstat itself has a literal /tmp/... path hard-coded somewhere, the script will detect that condition rather than silently accepting it.

Here’s the complete one-shot script in the format you want:

#!/usr/bin/env bash
# ==============================================================================
# Script Name : opstat-telemetry-targeted-lab.sh
# Description : Executes the targeted FR1/FR3 telemetry-correctness evidence
#               pass against the VAST lab cluster.
#
#               The script:
#                 - updates and verifies the opstat repository
#                 - verifies the exact expected Git SHA
#                 - verifies credentials and load generators
#                 - captures client-side NFS and block evidence
#                 - redirects temporary files into the evidence directory
#                 - runs the telemetry correctness probe
#                 - captures API logs and service evidence
#                 - captures final repository state
#                 - packages everything into one ZIP file
#
# Artifact Rule:
#               ALL working artifacts are written below:
#
#                 $HOME/kjmtmp/opstat/<DTS>/
#
#               Nothing from this run should be written to /tmp.
#
# Final Output:
#
#                 $HOME/opstat-telemetry2-<DTS>.zip
#
# Dependencies: git, python3, systemctl, journalctl, zip, unzip, sha256sum
#
# Target:
#               var203.selab.vastdata.com
#
# Expected Git:
#               main @ 17b22240643a7433e43c30241bb6640eae324ca4
# ==============================================================================
set -uo pipefail
EXPECTED_SHA="17b22240643a7433e43c30241bb6640eae324ca4"
VMS="var203.selab.vastdata.com"
VMS_USER="admin"
VIEW_ANCHORS="/kmacs"
REPO="$HOME/git/opstat"
BASE="$HOME/kjmtmp/opstat"
DTS=$(date +%Y%m%d-%H%M%S)
EV="$BASE/$DTS"
RAW="$EV/raw"
RUNTMP="$EV/tmp"
ARCHIVE="$HOME/opstat-telemetry2-$DTS.zip"
export VAST_PASSWORD="${VAST_PASSWORD:-123456}"
mkdir -p "$RAW"
mkdir -p "$RUNTMP"
export TMPDIR="$RUNTMP"
export TMP="$RUNTMP"
export TEMP="$RUNTMP"
cd "$REPO" || exit 1
echo
echo "======================================================================"
echo "  OPSTAT TARGETED TELEMETRY CORRECTNESS LAB RUN"
echo "======================================================================"
echo
echo "Run ID       : $DTS"
echo "Evidence Dir : $EV"
echo "Final ZIP    : $ARCHIVE"
echo "Target VMS   : $VMS"
echo
echo
echo "======================================================================"
echo "  1. UPDATE AND VERIFY REPOSITORY"
echo "======================================================================"
git status --short
git fetch origin
git checkout main
git merge --ff-only origin/main
ACTUAL_SHA=$(git rev-parse HEAD)
BRANCH=$(git rev-parse --abbrev-ref HEAD)
echo
echo "Branch : $BRANCH"
echo "HEAD   : $ACTUAL_SHA"
echo "Expect : $EXPECTED_SHA"
if [ "$BRANCH" != "main" ]; then
    echo
    echo "ERROR: Repository is not on main."
    exit 1
fi
if [ "$ACTUAL_SHA" != "$EXPECTED_SHA" ]; then
    echo
    echo "ERROR: HEAD DOES NOT MATCH EXPECTED PROBE SHA."
    echo "Evidence collection will not run against an unexpected revision."
    exit 1
fi
if [ -n "$(git status --short)" ]; then
    echo
    echo "ERROR: WORKING TREE IS NOT CLEAN."
    git status --short
    exit 1
fi
echo
echo "Repository checkpoint verified."
echo
echo "======================================================================"
echo "  2. VERIFY CREDENTIALS"
echo "======================================================================"
if [ -n "${VAST_TOKEN:-}" ]; then
    echo "Credential : VAST_TOKEN present"
elif [ -n "${VAST_PASSWORD:-}" ]; then
    echo "Credential : VAST_PASSWORD present"
else
    echo
    echo "ERROR: No VAST credential available."
    exit 1
fi
echo
echo "======================================================================"
echo "  3. VERIFY LOAD GENERATORS"
echo "======================================================================"
LOADGEN_OK=1
for u in nfs3-loadgen nfs41-loadgen block-loadgen smb-loadgen; do
    STATE=$(systemctl is-active "$u.service" 2>&1 || true)
    printf "%-18s : %s\n" "$u" "$STATE"
    case "$u" in
        nfs3-loadgen|block-loadgen)
            if [ "$STATE" != "active" ]; then
                LOADGEN_OK=0
            fi
            ;;
    esac
done
echo
if [ "$LOADGEN_OK" -ne 1 ]; then
    echo "ERROR: A required load generator is not active."
    echo
    echo "Required for this pass:"
    echo "  nfs3-loadgen.service"
    echo "  block-loadgen.service"
    echo
    echo "Fix the workload before collecting evidence."
    exit 1
fi
echo "Required load generators are active."
echo
echo "======================================================================"
echo "  4. CAPTURE PRE-RUN STATE"
echo "======================================================================"
{
    echo "run id        : $DTS"
    echo "hostname      : $(hostname)"
    echo "collected     : $(date '+%F %T %Z')"
    echo "branch        : $(git rev-parse --abbrev-ref HEAD)"
    echo "HEAD          : $(git rev-parse HEAD)"
    echo "expected HEAD : $EXPECTED_SHA"
    echo "python        : $(python3 -V 2>&1)"
    echo "target        : $VMS"
    echo "view anchors  : $VIEW_ANCHORS"
    echo "TMPDIR        : $TMPDIR"
    if [ -n "${VAST_TOKEN:-}" ]; then
        echo "credential    : VAST_TOKEN present"
    else
        echo "credential    : VAST_PASSWORD present"
    fi
    echo
    echo "working tree:"
    git status --short
    echo
    echo "load generators:"
    for u in nfs3-loadgen nfs41-loadgen block-loadgen smb-loadgen; do
        echo "$u : $(systemctl is-active "$u.service" 2>&1 || true)"
    done
    echo
    echo "recent commits:"
    git log -10 --oneline --decorate
    echo
    echo "tags at HEAD:"
    git tag --points-at HEAD
} | tee "$EV/prereqs.txt"
echo
echo "======================================================================"
echo "  5. CAPTURE LOAD GENERATOR STATE"
echo "======================================================================"
for u in nfs3-loadgen nfs41-loadgen block-loadgen smb-loadgen; do
    systemctl status "$u.service" --no-pager -l \
        > "$EV/${u}-status-before.txt" 2>&1 || true
    journalctl -u "$u.service" -n 200 --no-pager \
        > "$EV/${u}-journal-before.txt" 2>&1 || true
done
echo
echo "======================================================================"
echo "  6. CAPTURE NFS CLIENT STATE BEFORE PROBE"
echo "======================================================================"
mount > "$EV/mounts-all.txt" 2>&1
mount | grep -iE 'type nfs|nfs3|nfs4|vast' \
    > "$EV/mounts-nfs.txt" 2>&1 || true
cat /proc/self/mountstats \
    > "$EV/mountstats-before.txt" 2>&1 || true
if command -v nfsiostat >/dev/null 2>&1; then
    nfsiostat 1 3 \
        > "$EV/nfsiostat-before.txt" 2>&1 || true
else
    echo "nfsiostat not installed" \
        > "$EV/nfsiostat-before.txt"
fi
ps -ef \
    > "$EV/processes-before.txt"
ps -ef | grep -E '[f]io|[n]fs3|[n]fs41|[b]lock-loadgen' \
    > "$EV/loadgen-processes-before.txt" 2>&1 || true
echo
echo "======================================================================"
echo "  7. CAPTURE TEMPORARY-FILE BASELINE"
echo "======================================================================"
find "$RUNTMP" -maxdepth 2 -type f -print \
    > "$EV/runtime-files-before.txt"
find /tmp -maxdepth 1 \
    -name 'opstat-api-telemetry-probe-*' \
    -printf '%T@ %p\n' 2>/dev/null \
    > "$EV/preexisting-tmp-opstat-files.txt" || true
echo
echo "======================================================================"
echo "  8. RUN TARGETED TELEMETRY PROBE"
echo "======================================================================"
date '+PROBE-START %F %T %Z' \
    | tee "$EV/timestamps.txt"
python3 scripts/var203_validation/probe_telemetry_correctness.py \
    --vms "$VMS" \
    --user "$VMS_USER" \
    --view-paths "$VIEW_ANCHORS" \
    --evidence-dir "$RAW" \
    2>&1 | tee "$EV/probe-output.txt"
PROBE_RC=${PIPESTATUS[0]}
echo "PROBE-RC $PROBE_RC" \
    | tee -a "$EV/timestamps.txt"
date '+PROBE-END %F %T %Z' \
    | tee -a "$EV/timestamps.txt"
echo
echo "Probe return code: $PROBE_RC"
echo
echo "======================================================================"
echo "  9. CAPTURE NFS CLIENT STATE AFTER PROBE"
echo "======================================================================"
cat /proc/self/mountstats \
    > "$EV/mountstats-after.txt" 2>&1 || true
if command -v nfsiostat >/dev/null 2>&1; then
    nfsiostat 1 3 \
        > "$EV/nfsiostat-after.txt" 2>&1 || true
else
    echo "nfsiostat not installed" \
        > "$EV/nfsiostat-after.txt"
fi
ps -ef | grep -E '[f]io|[n]fs3|[n]fs41|[b]lock-loadgen' \
    > "$EV/loadgen-processes-after.txt" 2>&1 || true
echo
echo "======================================================================"
echo "  10. CAPTURE BLOCK CLIENT LATENCY EVIDENCE"
echo "======================================================================"
if sudo -n true >/dev/null 2>&1; then
    sudo -n journalctl \
        -u block-loadgen.service \
        -n 400 \
        --no-pager \
        > "$EV/block-loadgen-journal-after.txt" 2>&1 || true
else
    journalctl \
        -u block-loadgen.service \
        -n 400 \
        --no-pager \
        > "$EV/block-loadgen-journal-after.txt" 2>&1 || true
fi
echo
echo "======================================================================"
echo "  11. CAPTURE POST-RUN LOAD GENERATOR STATE"
echo "======================================================================"
for u in nfs3-loadgen nfs41-loadgen block-loadgen smb-loadgen; do
    systemctl status "$u.service" --no-pager -l \
        > "$EV/${u}-status-after.txt" 2>&1 || true
    journalctl -u "$u.service" -n 200 --no-pager \
        > "$EV/${u}-journal-after.txt" 2>&1 || true
done
echo
echo "======================================================================"
echo "  12. LOCATE THIS RUN'S API LOG"
echo "======================================================================"
find "$RUNTMP" -type f -print \
    > "$EV/runtime-files-after.txt" 2>&1 || true
APILOG=$(find "$EV" \
    -type f \
    -name 'opstat-api-telemetry-probe-*' \
    -printf '%T@ %p\n' 2>/dev/null \
    | sort -nr \
    | head -1 \
    | cut -d' ' -f2-)
if [ -n "${APILOG:-}" ] && [ -f "$APILOG" ]; then
    echo "API log: $APILOG" \
        | tee "$EV/api-log-location.txt"
else
    echo "API log not found beneath evidence directory." \
        | tee "$EV/api-log-location.txt"
fi
echo
echo "======================================================================"
echo "  13. VERIFY NOTHING NEW WAS WRITTEN TO /tmp"
echo "======================================================================"
find /tmp -maxdepth 1 \
    -name 'opstat-api-telemetry-probe-*' \
    -printf '%T@ %p\n' 2>/dev/null \
    > "$EV/postrun-tmp-opstat-files.txt" || true
python3 - "$EV/preexisting-tmp-opstat-files.txt" \
          "$EV/postrun-tmp-opstat-files.txt" \
          "$EV/tmp-artifact-check.txt" <<'PY'
import sys
before_path, after_path, out_path = sys.argv[1:4]
def read(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return set(line.rstrip("\n") for line in fh if line.strip())
    except OSError:
        return set()
before = read(before_path)
after = read(after_path)
new = sorted(after - before)
with open(out_path, "w", encoding="utf-8") as fh:
    if new:
        fh.write("FAIL: new opstat telemetry artifacts appeared in /tmp\n")
        for line in new:
            fh.write(line + "\n")
    else:
        fh.write("PASS: no new opstat telemetry artifacts appeared in /tmp\n")
if new:
    print("WARNING: probe created new opstat telemetry artifacts in /tmp")
else:
    print("PASS: no new opstat telemetry artifacts created in /tmp")
PY
TMP_CHECK_RC=$?
echo
echo "======================================================================"
echo "  14. CAPTURE FINAL REPOSITORY STATE"
echo "======================================================================"
git fetch origin
{
    echo "branch        : $(git rev-parse --abbrev-ref HEAD)"
    echo "HEAD          : $(git rev-parse HEAD)"
    echo "origin/main   : $(git rev-parse origin/main)"
    echo "expected HEAD : $EXPECTED_SHA"
    echo
    echo "working tree:"
    git status --short
    echo
    echo "recent history:"
    git log -10 --oneline --decorate
    echo
    echo "tags at HEAD:"
    git tag --points-at HEAD
} > "$EV/git-final-state.txt"
echo
echo "======================================================================"
echo "  15. BUILD MANIFEST"
echo "======================================================================"
{
    echo "OPSTAT TARGETED TELEMETRY EVIDENCE MANIFEST"
    echo
    echo "Run ID          : $DTS"
    echo "Host            : $(hostname)"
    echo "Start/End:"
    cat "$EV/timestamps.txt"
    echo
    echo "Repository:"
    echo "  Branch        : $(git rev-parse --abbrev-ref HEAD)"
    echo "  HEAD          : $(git rev-parse HEAD)"
    echo "  Expected HEAD : $EXPECTED_SHA"
    echo
    echo "Probe:"
    echo "  Return code   : $PROBE_RC"
    echo
    echo "Target:"
    echo "  VMS           : $VMS"
    echo "  View anchors  : $VIEW_ANCHORS"
    echo
    echo "Artifact root:"
    echo "  $EV"
    echo
    echo "/tmp policy:"
    cat "$EV/tmp-artifact-check.txt"
    echo
    echo "API log:"
    cat "$EV/api-log-location.txt"
} > "$EV/MANIFEST.txt"
echo
echo "======================================================================"
echo "  16. INVENTORY EVIDENCE DIRECTORY"
echo "======================================================================"
find "$EV" \
    -type f \
    -printf '%P\t%k KB\n' \
    | sort \
    > "$EV/file-inventory.txt"
cat "$EV/file-inventory.txt"
echo
echo "======================================================================"
echo "  17. BUILD FINAL ZIP"
echo "======================================================================"
rm -f "$ARCHIVE"
(
    cd "$BASE" || exit 1
    zip -qr "$ARCHIVE" "$DTS"
)
if [ ! -f "$ARCHIVE" ]; then
    echo
    echo "ERROR: ZIP archive was not created."
    exit 1
fi
echo
echo "======================================================================"
echo "  18. VERIFY FINAL ZIP"
echo "======================================================================"
unzip -t "$ARCHIVE"
echo
echo "Archive inventory:"
unzip -l "$ARCHIVE"
echo
echo "======================================================================"
echo "  19. FINAL RESULT"
echo "======================================================================"
echo
echo "Probe return code : $PROBE_RC"
echo "Repository HEAD   : $(git rev-parse HEAD)"
echo "Evidence directory:"
echo
echo "  $EV"
echo
echo "Final deliverable:"
echo
echo "  $ARCHIVE"
echo
echo "Archive size:"
ls -lh "$ARCHIVE"
echo
echo "SHA256:"
sha256sum "$ARCHIVE"
echo
echo "Evidence files in /tmp:"
cat "$EV/tmp-artifact-check.txt"
echo
echo "======================================================================"
if [ "$PROBE_RC" -ne 0 ]; then
    echo
    echo "WARNING: Probe returned non-zero."
    echo "The evidence archive was still preserved for analysis."
fi
if [ "$TMP_CHECK_RC" -ne 0 ]; then
    echo
    echo "WARNING: Unexpected /tmp artifact check condition."
fi
echo
echo "RETURN THIS ONE FILE:"
echo
echo "  $ARCHIVE"
echo
echo "======================================================================"

One thing I want Claude to change permanently

The shell script sets:

TMPDIR="$EV/tmp"
TMP="$EV/tmp"
TEMP="$EV/tmp"

so any sane temp-file implementation will stay under your run directory. But because we’ve historically seen:

/tmp/opstat-api-telemetry-probe-...

I don’t want to merely hope the logging code respects TMPDIR.

The script therefore compares /tmp before and after and explicitly tells us whether the probe cheated.

If it reports:

PASS: no new opstat telemetry artifacts appeared in /tmp

we’re done.

If it reports a new /tmp/opstat-api-... file, then the next Claude instruction should be to make the API-log destination honor TMPDIR or an explicit log-directory option. That’s a tooling hygiene change, not production telemetry behavior.

From here forward, I’d use this filesystem convention for every lab run:

$HOME/
├── git/
│   └── opstat/
│
├── kjmtmp/
│   └── opstat/
│       ├── 20260817-153512/
│       │   ├── MANIFEST.txt
│       │   ├── prereqs.txt
│       │   ├── raw/
│       │   ├── tmp/
│       │   └── ...
│       │
│       └── 20260817-181304/
│           └── ...
│
└── opstat-telemetry2-20260817-153512.zip

Nice clean crime-scene bags: every run isolated, timestamped, self-describing, and one ZIP sitting in $HOME waiting to be hauled back.
