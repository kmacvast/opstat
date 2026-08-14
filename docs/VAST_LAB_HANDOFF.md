echo "TRACKING MAIN:"
git rev-parse origin/main

echo
echo "ACTUAL REMOTE MAIN:"
git ls-remote origin refs/heads/main

All three should resolve to the same b69fb6b... SHA.

You can additionally prove the continuation branch still descends cleanly from the new main:

echo
echo "===== CONTINUATION ANCESTRY ====="

if git merge-base --is-ancestor origin/main origin/refactor/tui-performance-local-continuation-wip; then
    echo "GOOD: continuation branch cleanly descends from new main"
else
    echo "STOP: continuation ancestry is unexpected"
fi

You want GOOD.

5. Return to the current continuation branch

git switch refactor/tui-performance-local-continuation-wip

git merge --ff-only origin/refactor/tui-performance-local-continuation-wip

Verify:

echo
echo "===== CONTINUATION READY ====="
git branch --show-current
git status --short
git rev-parse HEAD
git log -8 --oneline --decorate

Expected:

branch:
refactor/tui-performance-local-continuation-wip

HEAD:
4cf105f39ecaf64545a60c93a19e226568b3
(venv) kmac@macbook:~/git/opstat [refactor/tui-performance-local-continuation-wip]$
(venv) kmac@macbook:~/git/opstat
(venv) kmac@macbook:~/git/opstat
(venv) kmac@macbook:~/git/opstat
(venv) kmac@macbook:~/git/opstat
(venv) kmac@macbook:~/git/opstat
(venv) kmac@macbook:~/git/opstat
(venv) kmac@macbook:~/git/opstat
(venv) kmac@macbook:~/git/opstat
(venv) kmac@macbook:~/git/opstat
(venv) kmac@macbook:~/git/opstat
(venv) kmac@macbook:~/git/opstat
(venv) kmac@macbook:~/git/opstat [refactor/tui-performance-local-continuation-wip]$
(venv) kmac@macbook:~/git/opstat [refactor/tui-performance-local-continuation-wip]$
(venv) kmac@macbook:~/git/opstat [refactor/tui-performance-local-continuation-wip]$ f
(venv) kmac@macbook:~/git/opstat [refactor/tui-performance-local-continuation-wip]$
(venv) kmac@macbook:~/git/opstat [refactor/tui-performance-local-continuation-wip]$
echo
echo "===== PREFLIGHT ====="
git status --short
git branch -vv

git fetch origin

echo





echo "===== REMOTE REFS ====="
echo "main:"
git rev-parse origin/main

echo
echo "completed refactor:"
git rev-parse origin/refactor/tui-performance

echo
echo "continuation:"
git rev-parse origin/refactor/tui-performance-local-continuation-wip


===== PREFLIGHT =====
  handoff/work-laptop-wip-20260814                03f72a2 [origin/handoff/work-laptop-wip-20260814] Updated output
  main                                            77549f0 [origin/main] Merge pull request #3 from blakegolliher/cleanup-dead-code
  refactor/tui-performance                        b69fb6b [origin/refactor/tui-performance] updated instructions
* refactor/tui-performance-local-continuation-wip 4cf105f [origin/refactor/tui-performance-local-continuation-wip] updated instructions

===== REMOTE REFS =====
main:
77549f064ad851fb6902394f55d1c76d80a34188

completed refactor:
b69fb6b0b66a8918f6a8cc46599d84b3fc46623e

continuation:
4cf105f39ecaf64545a60c93a19e226568b345e3
(venv) kmac@macbook:~/git/opstat [refactor/tui-performance-local-continuation-wip]$
echo
echo "===== ANCESTRY CHECK ====="

if git merge-base --is-ancestor origin/main origin/refactor/tui-performance; then
    echo "GOOD: refactor/tui-performance can fast-forward main"
else
    echo "STOP: refactor/tui-performance is not a direct descendant of main"
fi


===== ANCESTRY CHECK =====
GOOD: refactor/tui-performance can fast-forward main
(venv) kmac@macbook:~/git/opstat [refactor/tui-performance-local-continuation-wip]$
git switch refactor/tui-performance

git merge --ff-only origin/refactor/tui-performance

echo
echo "===== REFACTOR MILESTONE ====="
git status --short
git rev-parse HEAD
git log -5 --oneline --decorate


Switched to branch 'refactor/tui-performance'
Your branch is up to date with 'origin/refactor/tui-performance'.
Already up to date.

===== REFACTOR MILESTONE =====
b69fb6b0b66a8918f6a8cc46599d84b3fc46623e
(venv) kmac@macbook:~/git/opstat [refactor/tui-performance]$ git log -5 --oneline --decorate | cat
b69fb6b (HEAD -> refactor/tui-performance, origin/refactor/tui-performance) updated instructions
65e5c15 Updated output
c67ad8f updated instructions
fa1cd7e Updated output
963151c updated instructions
(venv) kmac@macbook:~/git/opstat [refactor/tui-performance]$ ./scripts/validate.sh

------------------------------------------------------------
opstat validation gate
------------------------------------------------------------
Tooling
  openssl         : OpenSSL 3.6.3 9 Jun 2026 (Library: OpenSSL 3.6.3 9 Jun 2026)
  interpreter     : Python 3.14.6
  uv              : uv 0.12.4 (77803aa22 2026-08-13 aarch64-apple-darwin)

Documentation
  links           : 257 relative documentation links OK (6 known-broken references skipped -- see KNOWN_BROKEN)

Collection
  collected       : 400 (floor 395)

Suite: current Python (Python 3.14.6)
........................................................................ [ 18%]
........................................................................ [ 36%]
........................................................................ [ 54%]
........................................................................ [ 72%]
........................................................................ [ 90%]
........................................                                 [100%]
400 passed in 38.00s
  result          : 400 passed, 0 failed, 0 skipped, 0 error

Suite: Python 3.8 (uv)
........................................................................ [ 18%]
........................................................................ [ 36%]
........................................................................ [ 54%]
........................................................................ [ 72%]
........................................................................ [ 90%]
........................................                                 [100%]
400 passed in 37.71s
  result          : 400 passed, 0 failed, 0 skipped, 0 error

------------------------------------------------------------
RESULT: PASS
  Current Python and Python 3.8 both green, nothing skipped.
------------------------------------------------------------
(venv) kmac@macbook:~/git/opstat [refactor/tui-performance]$ git switch main

git merge --ff-only origin/main

echo
echo "===== MAIN BEFORE PROMOTION ====="
git rev-parse HEAD
git status --short

Switched to branch 'main'
Your branch is up to date with 'origin/main'.
Already up to date.

===== MAIN BEFORE PROMOTION =====
77549f064ad851fb6902394f55d1c76d80a34188
(venv) kmac@macbook:~/git/opstat [main]$
git merge --ff-only origin/refactor/tui-performance

Updating 77549f0..b69fb6b
Fast-forward
 .claude/agents/api-efficiency-reviewer.md                                 |   74 ++++
 .claude/agents/telemetry-semantics-reviewer.md                            |   82 +++++
 .claude/agents/tui-render-reviewer.md                                     |   80 +++++
 .claude/rules/git-and-approval.md                                         |  133 ++++++++
 .claude/rules/testing-and-evidence.md                                     |  174 ++++++++++
 .claude/rules/tui-behavior.md                                             |  146 ++++++++
 .claude/rules/vast-api-safety.md                                          |  163 +++++++++
 .claude/settings.json                                                     |  136 ++++++++
 .claude/skills/audit-api-efficiency/SKILL.md                              |   92 +++++
 .claude/skills/run-quality-gates/SKILL.md                                 |   94 ++++++
 .claude/skills/run-vms-discovery/SKILL.md                                 |   95 ++++++
 .claude/skills/update-refactor-handoff/SKILL.md                           |   87 +++++
 .claude/skills/validate-against-real-vms/SKILL.md                         |  103 ++++++
 .cursor/rules/lab-git-sync.mdc                                            |   55 +++
 .gitignore                                                                |    8 +
 AGENTS.md                                                                 |  449 +++++++++++++++++++++++++
 CLAUDE.local.md.example                                                   |   59 ++++
 CLAUDE.md                                                                 |  114 +++++++
 NFSv3_README.md                                                           |   50 ++-
 NFSv41_README.md                                                          |   56 ++-
 NVMe_TCP_README.md                                                        |    2 +
 README.md                                                                 |   52 ++-
 S3_README.md                                                              |    1 +
 SETUP.md                                                                  |    2 +
 SMB_README.md                                                             |    5 +-
 docs/CLAUDE_HANDOFF.md                                                    |   61 ++++
 docs/README.md                                                            |   59 ++++
 docs/REFACTOR_HANDOFF.md                                                  |  615 +++++++++++++++++++++++++++++++++
 docs/WORKSTATION_BOOTSTRAP.md                                             |  258 ++++++++++++++
 docs/decisions/D-001-monitor-api-and-exporter-are-separate-paths.md       |   89 +++++
 docs/decisions/D-002-nfs4metrics-counters-are-cumulative.md               |   58 ++++
 docs/decisions/D-003-nfs4metrics-latency-is-microseconds.md               |   54 +++
 docs/decisions/D-004-heavy-exporter-endpoints-off-the-refresh-path.md     |   62 ++++
 docs/decisions/D-005-native-telemetry-is-an-on-demand-throttled-drill.md  |   63 ++++
 docs/decisions/D-006-host-view-is-the-nfs4-attribution-source.md          |   71 ++++
 docs/decisions/D-007-topn-is-unusable-for-protocol-attribution.md         |   50 +++
 docs/decisions/D-008-delegations-are-a-file-scoped-diagnostic.md          |   57 ++++
 docs/decisions/D-009-panels-are-evidence-gated.md                         |   65 ++++
 docs/decisions/D-010-merged-monitors-are-probe-validated-with-fallback.md |   73 ++++
 docs/decisions/D-011-newest-complete-sample-scoped-per-family.md          |   69 ++++
 docs/decisions/D-012-terminology-v4-hosts.md                              |   43 +++
 docs/decisions/README.md                                                  |   42 +++
 nfs4_native.py                                                            |  467 ++++++++++++++++++++++++++
 nfs_v3.py                                                                 |  489 +++++++++++----------------
 nfs_v41.py                                                                | 1980 +++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++----------
 nvme_tcp.py                                                               |    2 +-
 s3.py                                                                     |  123 ++++---
 scripts/Invoke-SmbOpstatLoad.ps1                                          |  672 ++++++++++++++++++++++++++++++++++++
 scripts/README-systemd.md                                                 |  341 +++++++++++++++++++
 scripts/block-loadgen.sh                                                  |  230 +++++++++++++
 scripts/check_docs_links.py                                               |  135 ++++++++
 scripts/nfs3-loadgen.sh                                                   |  216 ++++++++++++
 scripts/nfs41-loadgen.sh                                                  |  149 ++++++++
 scripts/s3-loadgen.sh                                                     |  126 +++++++
 scripts/smb-loadgen.sh                                                    |  181 ++++++++++
 scripts/systemd/block-loadgen.service                                     |   19 ++
 scripts/systemd/install-lab-loadgen-units.sh                              |   50 +++
 scripts/systemd/loadgen-status.sh                                         |  122 +++++++
 scripts/systemd/nfs3-loadgen.service                                      |   19 ++
 scripts/systemd/nfs41-loadgen.service                                     |   19 ++
 scripts/systemd/s3-loadgen.service                                        |   26 ++
 scripts/systemd/smb-loadgen.service                                       |   19 ++
 scripts/validate.sh                                                       |  229 +++++++++++++
 smb.py                                                                    |  122 ++++---
 tests/mock_vms.py                                                         |  736 ++++++++++++++++++++++++++++++++++++++++
 tests/test_api_efficiency.py                                              |  457 +++++++++++++++++++++++++
 tests/test_drill_loading.py                                               |  243 ++++++++++++++
 tests/test_drill_semantics.py                                             |  545 ++++++++++++++++++++++++++++++
 tests/test_globals_hygiene.py                                             |   72 ++++
 tests/test_nfs41_discovery.py                                             |  878 ++++++++++++++++++++++++++++++++++++++++++++++++
 tests/test_nfs4_native.py                                                 |  481 ++++++++++++++++++++++++++
 tests/test_render_navigation.py                                           |  236 +++++++++++++
 tui_layout.py                                                             |   18 +-
 vast_api_log.py                                                           |   23 +-
 vast_common.py                                                            |  324 ++++++++++++++++--
 vast_discovery.py                                                         |  484 ++++++++++++++++++++++++++
 vast_drill.py                                                             |  623 ++++++++++++++++++++++++++++++++++
 77 files changed, 14339 insertions(+), 618 deletions(-)
 create mode 100644 .claude/agents/api-efficiency-reviewer.md
 create mode 100644 .claude/agents/telemetry-semantics-reviewer.md
 create mode 100644 .claude/agents/tui-render-reviewer.md
 create mode 100644 .claude/rules/git-and-approval.md
 create mode 100644 .claude/rules/testing-and-evidence.md
 create mode 100644 .claude/rules/tui-behavior.md
 create mode 100644 .claude/rules/vast-api-safety.md
 create mode 100644 .claude/settings.json
 create mode 100644 .claude/skills/audit-api-efficiency/SKILL.md
 create mode 100644 .claude/skills/run-quality-gates/SKILL.md
 create mode 100644 .claude/skills/run-vms-discovery/SKILL.md










 create mode 100644 .claude/skills/update-refactor-handoff/SKILL.md
 create mode 100644 .claude/skills/validate-against-real-vms/SKILL.md
 create mode 100644 .cursor/rules/lab-git-sync.mdc
 create mode 100644 AGENTS.md
 create mode 100644 CLAUDE.local.md.example
 create mode 100644 CLAUDE.md
 create mode 100644 docs/CLAUDE_HANDOFF.md
 create mode 100644 docs/README.md
 create mode 100644 docs/REFACTOR_HANDOFF.md
 create mode 100644 docs/WORKSTATION_BOOTSTRAP.md
 create mode 100644 docs/decisions/D-001-monitor-api-and-exporter-are-separate-paths.md
 create mode 100644 docs/decisions/D-002-nfs4metrics-counters-are-cumulative.md
 create mode 100644 docs/decisions/D-003-nfs4metrics-latency-is-microseconds.md
 create mode 100644 docs/decisions/D-004-heavy-exporter-endpoints-off-the-refresh-path.md
 create mode 100644 docs/decisions/D-005-native-telemetry-is-an-on-demand-throttled-drill.md
 create mode 100644 docs/decisions/D-006-host-view-is-the-nfs4-attribution-source.md
 create mode 100644 docs/decisions/D-007-topn-is-unusable-for-protocol-attribution.md
 create mode 100644 docs/decisions/D-008-delegations-are-a-file-scoped-diagnostic.md
 create mode 100644 docs/decisions/D-009-panels-are-evidence-gated.md
 create mode 100644 docs/decisions/D-010-merged-monitors-are-probe-validated-with-fallback.md
 create mode 100644 docs/decisions/D-011-newest-complete-sample-scoped-per-family.md
 create mode 100644 docs/decisions/D-012-terminology-v4-hosts.md
 create mode 100644 docs/decisions/README.md
 create mode 100644 nfs4_native.py
 create mode 100644 scripts/Invoke-SmbOpstatLoad.ps1
 create mode 100644 scripts/README-systemd.md
 create mode 100755 scripts/block-loadgen.sh
 create mode 100755 scripts/check_docs_links.py
 create mode 100755 scripts/nfs3-loadgen.sh
 create mode 100755 scripts/nfs41-loadgen.sh
 create mode 100755 scripts/s3-loadgen.sh
 create mode 100755 scripts/smb-loadgen.sh
 create mode 100644 scripts/systemd/block-loadgen.service
 create mode 100755 scripts/systemd/install-lab-loadgen-units.sh
 create mode 100755 scripts/systemd/loadgen-status.sh
 create mode 100644 scripts/systemd/nfs3-loadgen.service
 create mode 100644 scripts/systemd/nfs41-loadgen.service
 create mode 100644 scripts/systemd/s3-loadgen.service
 create mode 100644 scripts/systemd/smb-loadgen.service
 create mode 100755 scripts/validate.sh
 create mode 100644 tests/mock_vms.py
 create mode 100644 tests/test_api_efficiency.py
 create mode 100644 tests/test_drill_loading.py
 create mode 100644 tests/test_drill_semantics.py
 create mode 100644 tests/test_globals_hygiene.py
 create mode 100644 tests/test_nfs41_discovery.py
 create mode 100644 tests/test_nfs4_native.py
 create mode 100644 tests/test_render_navigation.py
 create mode 100644 vast_discovery.py
 create mode 100644 vast_drill.py
(venv) kmac@macbook:~/git/opstat [main]$
echo
echo "===== MAIN AFTER LOCAL FAST-FORWARD ====="
git status --short
git rev-parse HEAD
git log -10 --oneline --decorate

===== MAIN AFTER LOCAL FAST-FORWARD =====
b69fb6b0b66a8918f6a8cc46599d84b3fc46623e
(venv) kmac@macbook:~/git/opstat [main]$ git log -10 --oneline --decorate | cat
b69fb6b (HEAD -> main, origin/refactor/tui-performance, refactor/tui-performance) updated instructions
65e5c15 Updated output
c67ad8f updated instructions
fa1cd7e Updated output
963151c updated instructions
1de0ebf Updated output
f4aeb26 updated instructions
f7f19fb Updated output
3883d57 updated instructions
36ead38 Updated output
(venv) kmac@macbook:~/git/opstat [main]$ git push origin main

Total 0 (delta 0), reused 0 (delta 0), pack-reused 0 (from 0)
To github.com:kmacvast/opstat.git
   77549f0..b69fb6b  main -> main
(venv) kmac@macbook:~/git/opstat [main]$
git fetch origin

echo
echo "===== MAIN VERIFICATION ====="

echo "LOCAL MAIN:"
git rev-parse main

echo
echo "TRACKING MAIN:"
git rev-parse origin/main

echo
echo "ACTUAL REMOTE MAIN:"
git ls-remote origin refs/heads/main


===== MAIN VERIFICATION =====
LOCAL MAIN:
b69fb6b0b66a8918f6a8cc46599d84b3fc46623e

TRACKING MAIN:
b69fb6b0b66a8918f6a8cc46599d84b3fc46623e

ACTUAL REMOTE MAIN:
b69fb6b0b66a8918f6a8cc46599d84b3fc46623e    refs/heads/main
(venv) kmac@macbook:~/git/opstat [main]$
echo
echo "===== CONTINUATION ANCESTRY ====="

if git merge-base --is-ancestor origin/main origin/refactor/tui-performance-local-continuation-wip; then
    echo "GOOD: continuation branch cleanly descends from new main"
else
    echo "STOP: continuation ancestry is unexpected"
fi


===== CONTINUATION ANCESTRY =====
GOOD: continuation branch cleanly descends from new main
(venv) kmac@macbook:~/git/opstat [main]$
git switch refactor/tui-performance-local-continuation-wip

git merge --ff-only origin/refactor/tui-performance-local-continuation-wip

Switched to branch 'refactor/tui-performance-local-continuation-wip'
Your branch is up to date with 'origin/refactor/tui-performance-local-continuation-wip'.
Already up to date.
(venv) kmac@macbook:~/git/opstat [refactor/tui-performance-local-continuation-wip]$
echo
echo "===== CONTINUATION READY ====="
git branch --show-current
git status --short
git rev-parse HEAD
git log -8 --oneline --decorate | cat


===== CONTINUATION READY =====
refactor/tui-performance-local-continuation-wip
4cf105f39ecaf64545a60c93a19e226568b345e3
4cf105f (HEAD -> refactor/tui-performance-local-continuation-wip, origin/refactor/tui-performance-local-continuation-wip) updated instructions
d2214d1 docs: reconcile refactor state and add var203 validation package
7b8b70e nvme: rank drill candidates and batch drill monitors
73e7035 latency: preserve meaningful sub-ms values
9ac9d64 tests: isolate exporter render fixtures and tighten validation floor
03c4405 nav: canonical cross-protocol navigation contract
03f72a2 (origin/handoff/work-laptop-wip-20260814, handoff/work-laptop-wip-20260814) Updated output
779cd6e wip: preserve interrupted opstat refactor state
(venv) kmac@macbook:~/git/opstat [refactor/tui-performance-local-continuation-wip]$ ./scripts/validate.sh

------------------------------------------------------------
opstat validation gate
------------------------------------------------------------
Tooling
  openssl         : OpenSSL 3.6.3 9 Jun 2026 (Library: OpenSSL 3.6.3 9 Jun 2026)
  interpreter     : Python 3.14.6
  uv              : uv 0.12.4 (77803aa22 2026-08-13 aarch64-apple-darwin)

Documentation
  links           : 269 relative documentation links OK (6 known-broken references skipped -- see KNOWN_BROKEN)

Collection
  collected       : 504 (floor 465)

Suite: current Python (Python 3.14.6)
........................................................................ [ 14%]
........................................................................ [ 28%]
........................................................................ [ 42%]
........................................................................ [ 57%]
........................................................................ [ 71%]
........................................................................ [ 85%]
........................................................................ [100%]
504 passed in 42.03s
  result          : 504 passed, 0 failed, 0 skipped, 0 error

Suite: Python 3.8 (uv)
........................................................................ [ 14%]
........................................................................ [ 28%]
........................................................................ [ 42%]
........................................................................ [ 57%]
........................................................................ [ 71%]
........................................................................ [ 85%]
........................................................................ [100%]
504 passed in 41.26s
  result          : 504 passed, 0 failed, 0 skipped, 0 error

------------------------------------------------------------
RESULT: PASS
  Current Python and Python 3.8 both green, nothing skipped.
------------------------------------------------------------
(venv) kmac@macbook:~/git/opstat [refactor/tui-performance-local-continuation-wip]$
export VAST_PASSWORD='123456'

if [ -n "$VAST_PASSWORD" ]; then
    echo "VAST_PASSWORD is set"
else
    echo "VAST_PASSWORD is NOT set"
fi

cat scripts/var203_validation/README.md



VAST_PASSWORD is set
# var203 validation package — continuation pass

One work-laptop trip that answers every remaining live-cluster dependency of
the uncommitted continuation pass (FR-A/B/C, NVMe ranking + batching, startup/
shutdown UX). Target **var203.selab.vastdata.com only**; var204 is unavailable.

Safety, for every step below: credentials come from `VAST_PASSWORD` /
`VAST_TOKEN` in the environment (never argv); nothing modifies VMS
configuration; only temporary `adhoc_opstat*` monitors are created, their
exact ids recorded and deleted, and cleanup is verified by id — **never**
touch other `adhoc_opstat_*` monitors (concurrent sessions exist on this
shared cluster). Exit the TUI with a clean `q`; never SIGKILL.

Run the steps in order; each says what it proves and what to bring back.
Total time ≈ 30–40 min with loadgens running.

---

## Step 0 — Preconditions

```bash
cd ~/git/opstat && git status --short     # this working tree, as reviewed
export VAST_PASSWORD=...                  # or VAST_TOKEN
./scripts/validate.sh --fast              # local sanity before touching the cluster
```

Start the **block/NVMe loadgen** and the **NFSv4.1 loadgen**
(`scripts/README-systemd.md`) and leave both running throughout — idle
counters cannot prove rates or units.

## Step 1 — Automated probes (~5 min)

```bash
python3 scripts/var203_validation/probe_var203.py \
  --vms var203.selab.vastdata.com --user admin \
  > /tmp/opstat-var203-probe.txt 2>&1
tail -25 /tmp/opstat-var203-probe.txt     # RESULT SUMMARY + cleanup verdict
```

**Proves:** (a) whether multi-`object_id` BlockMetrics monitors are accepted
and splittable at cnode/vip/blockhost scope → decides batch vs per-object
fallback for the NVMe drill; (b) whether the 2-counter rank monitor is
accepted → decides ranked vs stable-order candidates; (c) latency source
units — BlockMetrics and `host_view` values printed next to the proven-µs
NFS4Common reference for the same moment of load.

**Bring back:** the whole `/tmp/opstat-var203-probe.txt`.

## Step 2 — NVMe real BEFORE/AFTER (~10 min)

The BEFORE numbers are already established (entry 65 calls, 64 queries/poll,
mock + architecture audit); this measures AFTER on real hardware.

```bash
./opstat --block --nvme-over-tcp --vms var203.selab.vastdata.com --user admin --log-api-calls
```

1. Note wall-clock: launch → first `Connecting…` frame → normal dashboard.
2. Let it sit 60 s (headline cadence).
3. `c` → cNode drill. Note entry wall-clock; leave 60 s; press `space` once.
4. `x`, then `i` → VIP drill, 30 s. `x`, then `h` → Host drill, 30 s
   (blockhost is unmodeled in the mock — this is its only test).
5. `x`, quit with `q`. Note the `Cleaning up N temporary monitors…` line.

Then, from the log (`/tmp/opstat-api-nvme-tcp-*.log`):

```bash
L=$(ls -t /tmp/opstat-api-nvme-tcp-*.log | head -1)
grep -c 'POST /api/monitors/' "$L"                      # monitors created
grep -c 'GET .*/query/' "$L"                            # total queries
grep -oE 'POST /api/monitors/[^ ]*' "$L" | head -30     # creation sequence
grep -iE 'batch|rank' "$L" | head -20                   # batch/rank monitor names
```

**Proves:** drill entry ≈ 13 calls (batch) or the per-object fallback count;
8 queries per drill re-poll; ranked candidates rather than the first 8 by API
order (check the drill panel names against the busiest initiators you expect);
15 s drill throttle; `space` forcing an immediate query; exact-id cleanup on
quit (no `adhoc_opstat_*` from THIS pid remain — verify with the pid in the
log name).

**Bring back:** the four command outputs, the panel's ranked names (photo or
copy), the wall-clock notes, and the pid-scoped leftover check.

## Step 3 — Startup, shutdown, navigation, fabric screens (~10 min)

One run per engine (NFSv3, NFSv4.1, SMB, S3, NVMe):

```bash
./opstat --nfs --version=3.0 --vms var203.selab.vastdata.com --user admin   # etc.
```

Verify and note per engine:

- **Startup:** `Connecting to <host>:443…` appears before any delay, is
  replaced by `Preparing metrics on <cluster>…`, then
  `Gathering initial metrics…`, then the dashboard; footer visible in every
  startup frame.
- **Navigation:** footer reads the canonical order —
  `[q] Quit | [o] Ops | [l] Lat | [n] Name | [c] cNode | [v] View | [t] Tenant | [i] VIP | [x] Exit drill | [space] Refresh`
  (each engine shows only its supported subset), protocol-specific keys after
  (`[4]`/`[h]` on v4.1; `[b]` on S3; `[h]`/`[r]` on NVMe; `[r]`/`[w]` on v3).
  Confirm `v` does nothing on NVMe and `p` does nothing anywhere.
- **NVMe fabric panel:** Read/Write/Reclaim bars sum to ~100% of real I/O
  while the Fabric bar shows its own `of all activity` share; with the block
  loadgen running, read/write percentages must NOT shrink when fabric traffic
  spikes. Sub-ms combined latency must show a µs value, never `0.00 ms`.
- **Shutdown:** on `q`, the cleanup message appears, the process exits, and
  no monitors from that session remain (spot-check ids from the API log).

**Bring back:** per-engine PASS/FAIL notes + a capture of any screen that
looks wrong (that is how the last three real defects were found).

## Step 4 — Optional narrow-terminal spot check (~2 min)

Resize to ~100 and ~60 columns on any engine: footer truncates legibly,
frame never wraps into garbage.

---

## What comes back feeds

- `docs/decisions/` — a family/batch-compatibility record if Step 1 is
  decisive; latency-unit records if Step 1c is conclusive under load.
- `docs/REFACTOR_HANDOFF.md` — NVMe AFTER numbers; FR statuses flip from
  "REAL-VMS VALIDATION PENDING" to validated.
- The logical commit breakdown (already proposed) — commit after, not before,
  the evidence returns.
(venv) kmac@macbook:~/git/opstat [refactor/tui-performance-local-continuation-wip]$ python3 scripts/var203_validation/probe_var203.py --vms var203.selab.vastdata.com --user admin > /tmp/opstat-var203-probe.txt 2>&1


(venv) kmac@macbook:~/git/opstat [refactor/tui-performance-local-continuation-wip]$
(venv) kmac@macbook:~/git/opstat [refactor/tui-performance-local-continuation-wip]$ cat /tmp/opstat-var203-probe.txt

var203 continuation-pass probe - 2026-08-14T18:09:51Z
target var203.selab.vastdata.com:443 as admin; time_frame 10m
cluster: selab-var-203 (id 1)

=== batch monitor probe: object_type=cnode ===
  /cnodes/ -> 2 objects (using first 4)
  created monitor 2345 (adhoc_opstat_probe_batch_cnode_1786731014)
PROBE:batch.cnode.create PASS ids=[4, 3]
PROBE:batch.cnode.query PASS prop_list=['timestamp', 'object_id', 'BlockMetrics,read_req', 'BlockMetrics,read_latency__avg']
PROBE:batch.cnode.splittable PASS rows_per_object={"4": 120, "3": 120}

=== batch monitor probe: object_type=vip ===
  /vips/ -> 378 objects (using first 4)
  created monitor 2346 (adhoc_opstat_probe_batch_vip_1786731054)
PROBE:batch.vip.create PASS ids=[755, 55, 56, 57]
PROBE:batch.vip.query PASS prop_list=['timestamp', 'object_id', 'TopNMetrics,read_req', 'TopNMetrics,read_latency__avg']
PROBE:batch.vip.splittable FAIL rows_per_object={"755": 0, "55": 0, "56": 0, "57": 0}

=== batch monitor probe: object_type=blockhost ===
  /blockhosts/ -> 6 objects (using first 4)
  created monitor 2347 (adhoc_opstat_probe_batch_blockhost_1786731074)
PROBE:batch.blockhost.create PASS ids=[1, 2, 3, 4]
PROBE:batch.blockhost.query PASS prop_list=['timestamp', 'object_id', 'BlockMetrics,read_req', 'BlockMetrics,read_latency__avg']
PROBE:batch.blockhost.splittable FAIL rows_per_object={"1": 0, "2": 0, "3": 0, "4": 0}

=== rank monitor probe: object_type=cnode ===
  /cnodes/ -> 2 objects (using first 8)
  created monitor 2348 (adhoc_opstat_probe_rank_cnode_1786731110)
PROBE:rank.cnode.accepted PASS scores(read_req d/s)={"4": 1062.353, "3": 0.0}
  NOTE: 2/2 objects yielded a delta; zeros on an idle cluster are expected (run the block loadgen for a rate signal)

=== latency unit cross-checks ===
  created monitor 2349 (adhoc_opstat_probe_lat_ref_1786731136)
  reference NFS4Common read_latency__avg (PROVEN us): {'timestamp': '2026-08-14T18:12:13Z', 'object_id': 1, 'ProtoMetrics,proto_name=NFS4Common,read_latency__avg': 0} @ 2026-08-14T18:12:13Z
PROBE:latency.reference PASS values={'timestamp': '2026-08-14T18:12:13Z', 'object_id': 1, 'ProtoMetrics,proto_name=NFS4Common,read_latency__avg': 0}
  created monitor 2350 (adhoc_opstat_probe_lat_block_1786731142)
  BlockMetrics read_latency__avg (unit UNPROVEN): {'timestamp': '2026-08-14T18:12:33Z', 'object_id': 1, 'BlockMetrics,read_latency__avg': 488.99} @ 2026-08-14T18:12:33Z
PROBE:latency.blockmetrics PASS values={'timestamp': '2026-08-14T18:12:33Z', 'object_id': 1, 'BlockMetrics,read_latency__avg': 488.99}
  host_view latency series (unit UNPROVEN):
PROBE:latency.host_view FAIL 0 latency series
  INTERPRETATION: same order of magnitude as the reference for the same traffic -> microseconds; ~1000x smaller -> ms; ~1000x larger -> ns.

=== cleanup ===
  deleted monitor 2345
  deleted monitor 2346
  deleted monitor 2347
  deleted monitor 2348
  deleted monitor 2349
  deleted monitor 2350
PROBE:cleanup.exact_ids PASS all 6 session ids confirmed gone by per-id GET

=== RESULT SUMMARY ===
PROBE:batch.cnode.create PASS ids=[4, 3]
PROBE:batch.cnode.query PASS prop_list=['timestamp', 'object_id', 'BlockMetrics,read_req', 'BlockMetrics,read_latency__avg']
PROBE:batch.cnode.splittable PASS rows_per_object={"4": 120, "3": 120}
PROBE:batch.vip.create PASS ids=[755, 55, 56, 57]
PROBE:batch.vip.query PASS prop_list=['timestamp', 'object_id', 'TopNMetrics,read_req', 'TopNMetrics,read_latency__avg']
PROBE:batch.vip.splittable FAIL rows_per_object={"755": 0, "55": 0, "56": 0, "57": 0}
PROBE:batch.blockhost.create PASS ids=[1, 2, 3, 4]
PROBE:batch.blockhost.query PASS prop_list=['timestamp', 'object_id', 'BlockMetrics,read_req', 'BlockMetrics,read_latency__avg']
PROBE:batch.blockhost.splittable FAIL rows_per_object={"1": 0, "2": 0, "3": 0, "4": 0}
PROBE:rank.cnode.accepted PASS scores(read_req d/s)={"4": 1062.353, "3": 0.0}
PROBE:latency.reference PASS values={'timestamp': '2026-08-14T18:12:13Z', 'object_id': 1, 'ProtoMetrics,proto_name=NFS4Common,read_latency__avg': 0}
PROBE:latency.blockmetrics PASS values={'timestamp': '2026-08-14T18:12:33Z', 'object_id': 1, 'BlockMetrics,read_latency__avg': 488.99}
PROBE:latency.host_view FAIL 0 latency series
PROBE:cleanup.exact_ids PASS all 6 session ids confirmed gone by per-id GET
monitors created this run: [2345, 2346, 2347, 2348, 2349, 2350]
(venv) kmac@macbook:~/git/opstat [refactor/tui-performance-local-continuation-wip]$



#######################################################################################################################


Regarding Steps 2 through 4 in: scripts/var203_validation/README.md, I am in a car as a passenger and tethered to my phone.  The internet connection is weak, so any results for timing running opstat on the laptop are way long.  I started the first one but it took forever just because all of those API calls are going over this terrible network.

I ran the commands but the timing he asked me for wont be accurate.  Dont make this a blocker though, lets proceed for now.  I did provide all of the output files from the testing. 

The other option is that I run the tests on the linux lab server so its close to the clusters latency wise.  But I want him to hand me a script that does all of the work, I cant do interactive work well while in the car. 









