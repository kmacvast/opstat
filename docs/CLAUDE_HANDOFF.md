This is a focused, well-scoped task. First I need to reconcile it with reality — I added a single-message `initialize()` to SMB and S3 in a prior phase (uncommitted in this tree), while nfs_v3/nfs_v41/nvme have no startup interstitial. This task supersedes that with a uniform 3-phase design across all five. Let me verify the actual current tree state before changing anything.

Confirmed: SMB/S3 have my single-message `initialize()`; nfs_v3/nfs_v41/nvme have none. The flat footer-bypassing early return (`print(f"Waiting for data… VMS=...")`) is in nfs_v3.py:1840 and nvme_tcp.py:1723. Let me read the render frames to design a uniform footer-owning startup/waiting path. Starting with nfs_v3 and nvme (the flat early-returns) and nfs_v41:

nfs_v3's flat early-return at 1840 bypasses the title AND the keys footer. Let me see the full footer block to refactor it to the footer-owning path:

Now I understand the shape. This touches a shared helper, 5 differently-structured render frames, 5 `main()`s, and globals. Let me build it systematically. First, the shared 3-phase sibling helper in `vast_drill` (with callable messages so a later step can name the cluster):

Let me refresh the task list for this focused task.

Now the exact startup call sequence in each engine's `main()`, so `initialize()` wraps the right functions:

Startup sequences mapped. Now checking whether nvme imports `vast_drill` and how `test_render_navigation.py` drives frames (to add the waiting-mode footer test there):

Let me see how the existing footer tests assert presence and parametrize engines/widths:

Let me read the per-engine footer-token test to reuse its fixtures and token map for the waiting-frame test:

Now I have the test structure. Let me check the empty-rows handling in nvme and nfs_v41 render frames (to write the footer test correctly), then write tests.

Now I have the full picture: nvme and nfs_v3 have flat early-returns (no footer); smb/s3 render footer via the common path; nfs_v41's "Waiting for data" is in its drill panel. nvme uses `[q] Quit` footer style. Writing the tests (test-first) — ordering + error-clear in a new file, plus a waiting-frame footer test into `test_render_navigation.py`:

Now the waiting-frame footer test — targeting the two engines with the flat footer-bypassing early return (nfs_v3, nvme). Adding it to `test_render_navigation.py`:

Proving the new tests fail before I implement the startup changes (the current tree is pre-change for the startup feature on all engines — nfs_v3/nfs_v41/nvme have no `initialize`, and nfs_v3/nvme have the flat early-return):

Test-first fully confirmed: ordering fails on all 5 (3 have no `initialize`, smb/s3 are single-phase); the footer gap fails on nfs_v3/nvme at every width (`'Waiting for data… cluster=None'` — no footer); smb/s3 already own the footer. Now implementing. Let me find each engine's module-level state block and the `import` situation:

Test-first fully proven. nfs_v3/nfs_v41 already import `vast_drill`; nvme doesn't. Now implementing. Starting with **nfs_v3** (the htop-style template) — adding the state global, `initialize()`, main rewire, and the footer-owning waiting/startup frame. First the module global:

Now nfs_v3's `_render_frame` — footer-owning waiting/startup frame. Rewriting the function:

Now nfs_v3 `_set_startup_status` + `initialize()` + main rewire:

nfs_v3 done. Verifying it before replicating the pattern:

nfs_v3 green. Now **nfs_v41** — box-style. Let me read its `_render_frame` and how it handles empty rows / footer:

nfs_v41 already owns its footer via `_render_nav_footer` (no flat early-return) — it just needs a STARTUP_STATUS branch + the `CLUSTER_NAME or "?"` guard. Implementing:

nfs_v41 `initialize()` + `_set_startup_status` + main rewire:

Now **nvme_tcp** — needs `import vast_drill`, the state global, and the flat early-return fixed. Reading its full `_render_frame`:

nvme's footer is `_render_help_bar` at the end; the flat early-return bypasses everything. Rewriting to render title/meta + status + help bar:

Now nvme's `import vast_drill`, state global, `_set_startup_status`, `initialize()`, and main rewire. Adding the import and global:

Now nvme `initialize()` + `_set_startup_status` + main rewire (with `configure_volume_scope` in the prepare phase):

Now converting smb/s3 from single-phase to the 3-phase pattern:

All 5 engines done. Running the full startup test suite + render navigation + globals hygiene:

Some pre-existing nfs_v41 exporter-drill tests now error (`NoneType ... 'error'` at nfs_v41.py:1489). My `_render_frame` edit is functionally identical when `STARTUP_STATUS` is None, so this smells like state leaking from my new tests. Let me isolate:

`_render_hosts_panel` hits `HOSTVIEW.error` with `HOSTVIEW=None`. These passed at the last 438-gate, so my nfs_v41 edit is implicated. Let me confirm exactly what I changed and how the fixture sets HOSTVIEW:

These exporter tests passed at the 438-gate, so my nfs_v41 edit implicated them. Let me read the v41 fixture and `_render_exporter_panels` to see the HOSTVIEW=None path and width dependence:

This looks like a pre-existing fragile test dependency (the v41 fixture never sets `HOSTVIEW`; those exporter tests rely on an earlier test having called `init_config`). Let me settle it definitively with a pristine worktree at HEAD, per the testing rules:

**Confirmed pre-existing:** those exporter tests fail on pristine HEAD too when run in isolation — they depend on an earlier test (`test_drill_loading`) having called `init_config` to set `HOSTVIEW`. My change didn't cause it; my partial run just excluded `test_drill_loading`. This is an out-of-scope test-fragility defect — I'll record it, not fix it. Cleaning up and running the full gate (correct ordering):

The 2 failures are my *own* earlier test (`test_startup_paints_gathering_frame_before_blocking_work` in test_smb_s3_drill.py from the SMB/S3 phase) — it asserted the *first* frame says "Gathering initial metrics", which was the old single-message design. Now the first frame says "Connecting to…" (phase 1). Updating that test to the new 3-phase reality:

This test is now superseded by the comprehensive `test_startup_loading.py`, but it adds real-transport integration value — updating its assertions to the 3-phase reality (first frame = "Connecting…", "Gathering…" appears in a later frame, first API after a frame):

**Gate PASS: 466 passed** (438 → 466, +28 startup tests), both interpreters, 0 skipped, doc links OK. nvme's new `STARTUP_STATUS`/`_set_startup_status` pass globals-hygiene. Let me update the handoff's startup-UX section (was "SMB/S3 only") and record the out-of-scope items, then mark tasks done.

Marking tasks complete and confirming the working-tree state:

Done. All five engines now have the startup interstitial, scoped exactly to this item.

## Startup loading interstitial — complete

**What was added**

- **`vast_drill.with_startup_status(show_status, render, steps)`** — a sibling to `with_loading_status`, guaranteeing the ordering *set status → render → block* for each step, with a `finally` that clears the status (so it never survives startup, including on error). Messages may be callables so a later phase can name the cluster once phase 1 resolves it.

- **All five engines** (`nfs_v3`, `nfs_v41`, `smb`, `s3`, `nvme_tcp`) now run a uniform `initialize()` from `main()` that paints a frame before each blocking step, message changing three times over the startup window:
  1. `Connecting to {VMS}:{PORT}, please stand by...` — before `get_current_cluster()` (names the host; cluster is still `None`)
  2. `Preparing metrics on {CLUSTER_NAME}, please stand by...` — before `create_headline_monitors()`/`create_cluster_monitors()` (nvme also runs `configure_volume_scope` here)
  3. `Gathering initial metrics, please stand by...` — before `fetch_monitor_query()`

- **State globals reconciled:** each engine gained `STARTUP_STATUS` + `_set_startup_status()` with a proper `global` declaration (nvme had none, and also gained `import vast_drill`); smb/s3 were converted from the earlier single-message version. Passes `test_globals_hygiene.py`.

- **Footer now owned by the startup/waiting frame.** The flat `print("Waiting for data…"); return` in `nfs_v3._render_frame` and `nvme_tcp._render_frame` — which bypassed the nav footer — is gone; both now render title + status + footer through the common path. `nfs_v41` gained a `STARTUP_STATUS` branch (it already owned its footer); smb/s3 already did. Titles guard `CLUSTER_NAME or "?"` so phase 1 renders cleanly before the cluster is known. No second early return; no navigation-key changes.

**Design constraints honored:** no threads/spinner (message-change is the signal); reused the shared helper shape rather than open-coding per engine; frame stays within the width budget (footer tested at 200/120/80/40 columns).

**Tests (test-first, proven to fail before the change in a pristine HEAD worktree):**
- `tests/test_startup_loading.py` — per engine (×5): a frame renders before each of the three blocking calls; status set before each; status cleared after success **and** on the error path; first message names the host not the cluster.
- `tests/test_render_navigation.py` — new `test_waiting_frame_keeps_the_footer` (nfs_v3/nvme at 4 widths — the flat-early-return gap; failed on HEAD) and `test_startup_status_frame_keeps_the_footer` (all 5 engines).
- Updated one now-outdated assertion in `test_smb_s3_drill.py` (it expected the old single "Gathering…" first frame; now asserts the 3-phase sequence) — visible, not a stren­gth reduction.

**Validation gate:** `./scripts/validate.sh` → **RESULT: PASS**, `466 passed, 0 failed, 0 skipped, 0 error` on **Python 3.14.6 and Python 3.8**; openssl suites ran; 258 doc links OK. Count went **438 → 466** (+28: 15 in `test_startup_loading.py`, 13 in `test_render_navigation.py`).

**Real-cluster status:** the interstitial is **mock- and unit-verified only**. The actual startup appearance on var203 is **unverified** this pass — no real run backed it; real-cluster verification remains the owner's step.

**Recorded, not fixed (out of scope), in `docs/REFACTOR_HANDOFF.md`:**
- **Quit-time cleanup frame** — the signal-blocking drain makes a slow quit a silent wait too; it should get its own `Cleaning up N monitors…` frame. Separate change (your call-out).
- **`pthread_sigmask` Windows guard** — POSIX-only; already `getattr`-guarded so it no-ops on Windows, but `release.yml`'s Windows build is never exercised by `test.yml` (Linux-only), so it won't surface in CI (your call-out).
- **Exporter footer tests are order-dependent** — they rely on `test_drill_loading` having set `HOSTVIEW`/`NFS4`; in isolation (`-k exporter`) they error on `HOSTVIEW.error` (None) on pristine HEAD too. Pre-existing; the v41 fixture should set those collectors.

Nothing committed, pushed, merged, or tagged. Working tree: 10 modified (5 engines, `vast_drill.py`, `vast_common.py`, `mock_vms.py`, 2 test files, `REFACTOR_HANDOFF.md`) + 4 new test files — the startup work plus the prior uncommitted phases.
