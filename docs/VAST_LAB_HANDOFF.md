vastdata@kevin-mcdonald-ubu-01:~/git/opstat$ cd ~/git/opstat
git status --short
git branch --show-current

refactor/tui-performance-local-continuation-wip
vastdata@kevin-mcdonald-ubu-01:~/git/opstat$ git status --short
vastdata@kevin-mcdonald-ubu-01:~/git/opstat$ git fetch origin

git switch refactor/tui-performance-local-continuation-wip

git merge --ff-only origin/refactor/tui-performance-local-continuation-wip
remote: Enumerating objects: 58, done.
remote: Counting objects: 100% (58/58), done.
remote: Compressing objects: 100% (8/8), done.
remote: Total 38 (delta 30), reused 38 (delta 30), pack-reused 0 (from 0)
Unpacking objects: 100% (38/38), 26.34 KiB | 252.00 KiB/s, done.
From github.com:kmacvast/opstat
   96f284e..d0d42ad  refactor/tui-performance-local-continuation-wip -> origin/refactor/tui-performance-local-continuation-wip
Already on 'refactor/tui-performance-local-continuation-wip'
Your branch is behind 'origin/refactor/tui-performance-local-continuation-wip' by 5 commits, and can be fast-forwarded.
  (use "git pull" to update your local branch)
Updating 96f284e..d0d42ad
Fast-forward
 docs/CLAUDE_HANDOFF.md                                         |  50 +++++++++++++++++++++++++++-----
 docs/REFACTOR_HANDOFF.md                                       |  86 +++++++++++++++++++++++++++++++++++++++++++++++++------
 docs/VAST_LAB_HANDOFF.md                                       | 140 ++++++++++++++++++++++++++++++++++++++++++++++++-----------------------------------------
 docs/decisions/D-013-nvme-drill-batching-is-scope-dependent.md |  26 +++++++++++------
 nfs_v3.py                                                      |  69 +++++++++++++++++++++-----------------------
 nfs_v41.py                                                     |  60 +++++++++++++++++++++++----------------
 nvme_tcp.py                                                    | 104 +++++++++++++++++++++++++++++++++++++++---------------------------
 s3.py                                                          |  42 ++++++++++++++++-----------
 scripts/var203_validation/probe_var203.py                      |  60 +++++++++++++++++++++++++++++++++++++++
 scripts/var203_validation/run_var203_validation.py             | 112 ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++------------
 smb.py                                                         |  40 ++++++++++++++++----------
 tests/test_key_dispatch.py                                     | 186 +++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
 tests/test_nvme_drill.py                                       |  78 ++++++++++++++++++++++++++++++++++++++++++++++++++
 tests/test_render_navigation.py                                |  82 +++++++++++++++++++++++++++++++++++++++++++++++++++++
 vast_drill.py                                                  |  79 +++++++++++++++++++++++++++++++++++++++++++++++++++
 15 files changed, 974 insertions(+), 240 deletions(-)
 create mode 100644 tests/test_key_dispatch.py
vastdata@kevin-mcdonald-ubu-01:~/git/opstat$ echo "===== WORK LAPTOP READY ====="
git branch --show-current
git rev-parse HEAD
git status --short
git log -7 --oneline --decorate
===== WORK LAPTOP READY =====
refactor/tui-performance-local-continuation-wip
d0d42adbe0b2f4359fddee038f2c23dd8033e166
d0d42ad (HEAD -> refactor/tui-performance-local-continuation-wip, origin/refactor/tui-performance-local-continuation-wip) updated instructions from Sheila
25220c2 docs: reconcile round-2 var203 evidence
23a5e45 validation: state-aware lab driver and merge-legality probes
eb65cde input: honor every queued keystroke in all five engines
5a41b46 nav: wrap the footer legend instead of truncating it
96f284e updated instructions from Sheila
4c449cc validation: automated lab driver and round-1 evidence reconciliation
vastdata@kevin-mcdonald-ubu-01:~/git/opstat$ ./scripts/validate.sh
------------------------------------------------------------
opstat validation gate
------------------------------------------------------------
Tooling
  openssl         : OpenSSL 3.0.13 30 Jan 2024 (Library: OpenSSL 3.0.13 30 Jan 2024)
  interpreter     : Python 3.12.3
  uv              : uv 0.12.5 (x86_64-unknown-linux-gnu)

Documentation
  links           : 276 relative documentation links OK (6 known-broken references skipped -- see KNOWN_BROKEN)

Collection
  collected       : 563 (floor 465)

Suite: current Python (Python 3.12.3)
........................................................................ [ 12%]
........................................................................ [ 25%]
........................................................................ [ 38%]
........................................................................ [ 51%]
........................................................................ [ 63%]
........................................................................ [ 76%]
........................................................................ [ 89%]
...........................................................              [100%]
563 passed in 380.72s (0:06:20)
  result          : 563 passed, 0 failed, 0 skipped, 0 error

Suite: Python 3.8 (uv)
........................................................................ [ 12%]
........................................................................ [ 25%]
........................................................................ [ 38%]
........................................................................ [ 51%]
........................................................................ [ 63%]
........................................................................ [ 76%]
........................................................................ [ 89%]
...........................................................              [100%]
563 passed in 388.61s (0:06:28)
  result          : 563 passed, 0 failed, 0 skipped, 0 error

------------------------------------------------------------
RESULT: PASS
  Current Python and Python 3.8 both green, nothing skipped.
------------------------------------------------------------
vastdata@kevin-mcdonald-ubu-01:~/git/opstat$
