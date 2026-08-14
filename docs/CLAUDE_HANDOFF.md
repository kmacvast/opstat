Starting by reconciling this work package against the actual tree and confirming the baseline.

HEAD moved only for the owner's `CLAUDE_HANDOFF.md` prompt edits; my code work is intact and uncommitted; imports clean. 

Reconciliation of phases against the tree: **B** cleanup *correctness* is done (signal-blocked drain + guard-after-drain + 3 tests) — the *shutdown UX* is not; **D** Block Fabric % is done (`block_workload_mix` + 7 tests); **A** startup UX implemented (needs real validation); **C** NVMe perf, **E** nav, **F** latency, **G/H** remain.

I'll work through them, batching real-var203 at the end. Starting with **Phase B — shutdown UX** (extends the done cleanup fix). Adding the shared helpers:

Now the uniform shutdown-message insertion into all 5 engine `cleanup()`s:

Now the Phase B tests (shutdown UX + continue-after-failure + exact-ownership). Adding to `test_cleanup_lifecycle.py`:

Let me simplify that convoluted test:

Phase B complete (correctness + shutdown UX, 8 cleanup tests green). Now the navigation inventory for **Phase E** — reading every engine's keypress handler and footer to build the BEFORE matrix.

Let me read nvme's full keypress block and all five footer strings to complete the matrix:

I have the full BEFORE nav matrix. Key finding: **NVMe is the outlier** — `v`=VIP (violates FR: VIP must be `i`, never `v`), `p`=exit (others use `x`), no `space` refresh, toggle model. The other four largely conform. NVMe's main loop and drill are touched by both Phase C (ranking/throttle) and Phase E (keys), so I'll do them as one coordinated NVMe pass.

First, the fresh Phase C audit — does the mock model NVMe, and what are the current call counts?

The mock models `/volumes/`, `/cnodes/`, `/vips/` but not `/blockhosts/`. Given the practical scope of this very large package, I'm going to make a disciplined decision about what I can complete to the repo's *validated* standard this turn versus what must be honestly deferred — and I'll be transparent about it in the report rather than rush unvalidated NVMe-ranking or fabricate mock semantics the rules forbid.

Let me first lock in the concrete, fully-testable FR fixes. Implementing the **NVMe nav standardization** (Phase E: `v`→`i` VIP, `p`→`x` exit, add `space` refresh, footer common-first) plus a drill **throttle + forced manual refresh** (the deterministic part of Phase C). Reading nvme's drill query + poll + help bar region:

nvme queries the drill every tick with no throttle and has no manual refresh. Implementing the throttle + force + `manual_refresh`, then the nav changes. First the throttle globals and `fetch_drill_query`:

Now the nvme key handler (`v`→`i`, `p`→`x`, add `space`, force on entry) and the help-bar footer reorder:

NVMe drill ranking-by-activity needs real blockhost-scope probe evidence the mock can't provide (rules forbid fabricating mock semantics), so I'm delivering the **deterministic** Phase C wins now — throttle + forced refresh + nav — and will document ranking as remaining with a probe plan. Adding NVMe throttle/nav tests:

Usage limit reached, You’ve reached your usage limit. Try again after your limit resets.  

#######################
Last login: Fri Aug 14 09:55:30 on ttys000
(venv) kmac@macbook:~$
(venv) kmac@macbook:~$ cd git/opstat
(venv) kmac@macbook:~/git/opstat [refactor/tui-performance]$ ll
total 1520
drwxr-xr-x@ 29 kevin.mcdonald  staff   928B Aug 14 11:04 __pycache__
drwxr-xr-x@ 42 kevin.mcdonald  staff   1.3K Aug 14 11:09 .
drwxr-xr-x@ 10 kevin.mcdonald  staff   320B Jul 22 10:41 ..
drwxr-xr-x@  6 kevin.mcdonald  staff   192B Aug 13 17:02 .claude
drwxr-xr-x@  3 kevin.mcdonald  staff    96B Aug 13 11:10 .cursor
drwxr-xr-x@ 16 kevin.mcdonald  staff   512B Aug 14 11:01 .git
drwxr-xr-x@  3 kevin.mcdonald  staff    96B Jul 15 14:14 .github
-rw-r--r--@  1 kevin.mcdonald  staff   5.0K Aug 13 17:02 .gitignore
drwxr-xr-x@  6 kevin.mcdonald  staff   192B Jul 22 16:18 .pytest_cache
-rw-r--r--@  1 kevin.mcdonald  staff    20K Aug 13 17:02 AGENTS.md
-rw-r--r--@  1 kevin.mcdonald  staff   1.7K Aug 13 17:02 CLAUDE.local.md.example
-rw-r--r--@  1 kevin.mcdonald  staff   5.2K Aug 13 17:02 CLAUDE.md
drwxr-xr-x@  7 kevin.mcdonald  staff   224B Aug 14 11:00 docs
drwxr-xr-x@ 12 kevin.mcdonald  staff   384B Jul 15 14:14 images
-rw-r--r--@  1 kevin.mcdonald  staff   1.0K Jul 15 13:51 LICENSE
-rwxr-xr-x@  1 kevin.mcdonald  staff    76K Aug 14 11:03 nfs_v3.py
-rw-r--r--@  1 kevin.mcdonald  staff   113K Aug 14 11:03 nfs_v41.py
-rw-r--r--@  1 kevin.mcdonald  staff    17K Aug 13 17:02 nfs4_native.py
-rw-r--r--@  1 kevin.mcdonald  staff    10K Aug 13 11:22 NFSv3_README.md
-rw-r--r--@  1 kevin.mcdonald  staff   9.9K Aug 13 11:22 NFSv41_README.md
-rw-r--r--@  1 kevin.mcdonald  staff   9.6K Aug 13 11:22 NVMe_TCP_README.md
-rw-r--r--@  1 kevin.mcdonald  staff    69K Aug 14 11:09 nvme_tcp.py
-rw-r--r--@  1 kevin.mcdonald  staff   6.7K Jul 22 16:04 openmetrics.py
-rwxr-xr-x@  1 kevin.mcdonald  staff    11K Jul 22 16:00 opstat
-rw-r--r--@  1 kevin.mcdonald  staff   103B Jul 22 16:15 pytest.ini
-rw-r--r--@  1 kevin.mcdonald  staff    17K Aug 13 17:02 README.md
drwxr-xr-x@  4 kevin.mcdonald  staff   128B Jul 23 12:04 releases
-rw-r--r--@  1 kevin.mcdonald  staff   597B Jul 22 16:18 requirements.txt
-rw-r--r--@  1 kevin.mcdonald  staff   7.9K Aug 13 11:22 S3_README.md
-rw-r--r--@  1 kevin.mcdonald  staff    87K Aug 14 11:03 s3.py
drwxr-xr-x@ 15 kevin.mcdonald  staff   480B Aug 13 17:02 scripts
-rw-r--r--@  1 kevin.mcdonald  staff   9.9K Aug 13 11:22 SETUP.md
-rw-r--r--@  1 kevin.mcdonald  staff    20K Jul 15 14:14 SMB_OPCODES.md
-rw-r--r--@  1 kevin.mcdonald  staff   6.8K Aug 13 11:22 SMB_README.md
-rw-r--r--@  1 kevin.mcdonald  staff    92K Aug 14 11:03 smb.py
drwxr-xr-x@ 25 kevin.mcdonald  staff   800B Aug 14 11:10 tests
-rw-r--r--@  1 kevin.mcdonald  staff   7.9K Aug 13 17:02 tui_layout.py
-rw-r--r--@  1 kevin.mcdonald  staff   3.3K Aug 13 09:56 vast_api_log.py
-rw-r--r--@  1 kevin.mcdonald  staff    28K Aug 14 11:03 vast_common.py
-rw-r--r--@  1 kevin.mcdonald  staff    18K Aug 13 14:57 vast_discovery.py
-rw-r--r--@  1 kevin.mcdonald  staff    26K Aug 14 10:40 vast_drill.py
-rw-r--r--@  1 kevin.mcdonald  staff    16K Jul 23 12:04 wizard.py
(venv) kmac@macbook:~/git/opstat [refactor/tui-performance]$ f













(venv) kmac@macbook:~/git/opstat [refactor/tui-performance]$ git status --short
 M docs/CLAUDE_HANDOFF.md
 M docs/REFACTOR_HANDOFF.md
 M nfs_v3.py
 M nfs_v41.py
 M nvme_tcp.py
 M s3.py
 M smb.py
 M tests/mock_vms.py
 M tests/test_render_navigation.py
 M tests/test_s3_helpers.py
 M vast_common.py
 M vast_drill.py
?? tests/test_cleanup_lifecycle.py
?? tests/test_nvme_tcp.py
?? tests/test_smb_s3_drill.py
?? tests/test_startup_loading.py
(venv) kmac@macbook:~/git/opstat [refactor/tui-performance]$ git branch --show-current
refactor/tui-performance
(venv) kmac@macbook:~/git/opstat [refactor/tui-performance]$ git rev-parse HEAD
c67ad8fdf78b8b3008eb44e82d25fa66f2064283
(venv) kmac@macbook:~/git/opstat [refactor/tui-performance]$ git diff --stat
(venv) kmac@macbook:~/git/opstat [refactor/tui-performance]$ git diff --stat | cat
 docs/CLAUDE_HANDOFF.md          | 572 ++--------------------------------------
 docs/REFACTOR_HANDOFF.md        | 231 +++++++++++++++-
 nfs_v3.py                       | 115 +++++---
 nfs_v41.py                      |  52 +++-
 nvme_tcp.py                     | 165 +++++++++---
 s3.py                           | 221 ++++++++++------
 smb.py                          | 209 +++++++++------
 tests/mock_vms.py               |  20 +-
 tests/test_render_navigation.py |  57 ++++
 tests/test_s3_helpers.py        |  10 +-
 vast_common.py                  |  45 +++-
 vast_drill.py                   |  25 ++
 12 files changed, 911 insertions(+), 811 deletions(-)
(venv) kmac@macbook:~/git/opstat [refactor/tui-performance]$ git ls-files --others --exclude-standard
tests/test_cleanup_lifecycle.py
tests/test_nvme_tcp.py
tests/test_smb_s3_drill.py
tests/test_startup_loading.py
(venv) kmac@macbook:~/git/opstat [refactor/tui-performance]$















