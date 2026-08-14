Read the startup path in all five engines. The gap is uniform and the shape is identical everywhere:

```
setup_keyboard()
get_current_cluster()        # blocking: auth + GET /clusters/
_capture_cluster_os()        # blocking
create_headline_monitors()   # blocking: probe + POST /monitors/
fetch_monitor_query()        # blocking: GET query
render_screen()              # ← first frame, only now
```

Nothing paints before that last line — in **any** engine, including SMB and S3 in this tree. Three findings shape the requirement: `CLUSTER_NAME` is unknown until the first call returns, so the message can't depend on cluster identity; `_render_frame()` already early-returns on empty rows with a bare `Waiting for data…` and no footer; and `nvme_tcp.py` has no status global at all. Here's the prompt.

````text
TASK: startup loading interstitial (FR-D / Phase 9), all five engines.

SCOPE: this item only. Do not touch performance, ranking, batching, throttles,
navigation keys, or latency formatting in this change. Do not fix unrelated
defects you notice — record them and move on.

--- THE PROBLEM ---

Every engine's main() has the same shape, and nothing reaches the terminal
until the last line:

    setup_keyboard()
    CLUSTER_ID, CLUSTER_NAME = get_current_cluster()   # auth + GET /clusters/
    _capture_cluster_os()
    [configure_volume_scope(ARGS)]                     # nvme_tcp only
    create_headline_monitors()  /  create_cluster_monitors()
    fetch_monitor_query()
    render_screen()                                    # <-- first frame

Verify this yourself in nfs_v3.py:1998, nfs_v41.py:2762, smb.py:2462,
s3.py:2341, nvme_tcp.py:1786. On var203 that is roughly 30 s of a blank
terminal with no indication the process is alive. That is the defect.

This is NOT NVMe-specific. SMB and S3 do not have a startup interstitial
either; drill-entry loading is a different thing. Fix all five.

--- REQUIREMENT ---

Paint a status frame BEFORE each blocking startup step, so the user always
sees what the process is waiting on.

Phase messages, in the existing house style (see vast_drill.LOADING_MESSAGES,
"Loading the VIEW drill-down, please stand by..."):

    before get_current_cluster()      "Connecting to {VMS}:{PORT}, please stand by..."
    before create_*_monitors()        "Preparing metrics on {CLUSTER_NAME}, please stand by..."
    before fetch_monitor_query()      "Gathering initial metrics, please stand by..."

Wording rules:
  * Do NOT promise a duration. "this will take a few seconds" is a measured
    lie on var203, where startup runs ~30 s. Say "please stand by...".
  * The first message cannot reference the cluster: CLUSTER_NAME is None
    until get_current_cluster() returns. Use VMS host:port there.
  * Adjust the exact strings if a better phrasing fits the engine, but keep
    them consistent across all five and keep the "please stand by..." tail.

--- DESIGN CONSTRAINTS ---

1. The message cannot animate or tick during a blocking call. The engines are
   single-threaded and adding a thread is an L1 decision (AGENTS.md). The
   design is therefore: set status -> render one frame -> block. The user sees
   the message CHANGE three times over the startup window, which is the
   progress signal. Do not attempt a spinner or elapsed counter.

2. Reuse the existing helper shape. vast_drill.with_loading_status(
   show_status, render, mode, work) already guarantees the exact ordering
   (status -> render -> work -> clear in finally). Either reuse it or add a
   sibling in the same module; do not open-code the ordering per engine.

3. State globals differ and must be reconciled:
     nfs_v3.py, smb.py, s3.py   have DRILL_STATUS
     nfs_v41.py                 has DRILL_STATUS and EXPORTER_STATUS
     nvme_tcp.py                has NO status global at all
   nvme_tcp needs one. Follow tests/test_globals_hygiene.py: any function
   assigning an ALL_CAPS module global must declare `global`. That AST test
   exists because a missing `global` once made the NFSv4.1 drill silently
   render nothing.

4. THE FOOTER MUST RENDER IN THE STARTUP FRAME. _render_frame() currently
   early-returns on empty rows:

       if not rows:
           print(f"Waiting for data…  VMS={VMS}:{PORT}  cluster={CLUSTER_NAME}")
           return

   That early return bypasses footer rendering — the exact pattern
   .claude/rules/tui-behavior.md was written about, and it appears to be
   uncovered by tests/test_render_navigation.py (no test references
   "Waiting for data"). Verify that, then make the startup/waiting frame take
   the common path that owns the footer. Do not add a second early return.

5. Frame must not exceed terminal width, and must degrade legibly on narrow
   terminals. Same budget as the rest of the TUI.

--- TESTS REQUIRED ---

Model them on tests/test_drill_loading.py, which asserts the literal event
ordering ["status", "render", "work", "status"].

  a. Ordering, per engine: the status is set AND a frame is rendered BEFORE
     get_current_cluster(), before create_*_monitors(), and before
     fetch_monitor_query(). Monkeypatch each blocking call to append to an
     events list; assert a render event precedes each one. Parametrize over
     all five engines.
  b. The status is cleared once the first real frame renders, and is cleared
     on the error path too (blocking call raises -> status not left behind).
  c. Footer presence in the startup/waiting frame, at the widths already
     parametrized in tests/test_render_navigation.py. Add the waiting mode to
     that suite rather than starting a new one, if it fits cleanly.
  d. nvme_tcp passes tests/test_globals_hygiene.py with its new status global.

Prove (c) fails before the change if the waiting-mode footer gap is real —
run the new test against HEAD in a worktree, per
.claude/rules/testing-and-evidence.md.

--- DONE MEANS ---

  * ./scripts/validate.sh -> RESULT: PASS, both interpreters, 0 skipped,
    collection count reported and expected.
  * Report the new count and what was added.
  * Real-cluster verification is the owner's step. State plainly that the
    interstitial is mock- and unit-verified only, and that the actual startup
    appearance on var203 is unverified, unless a real run backed it.

--- OUT OF SCOPE (record, do not fix here) ---

  * The startup duration itself. This change makes the wait legible, not
    shorter.
  * Quit-time cleanup: if the monitor drain blocks signals, it needs its own
    "Cleaning up N monitors, please stand by..." frame. Same class of problem,
    separate change.
  * Navigation key standardization (FR-A), latency units (FR-B).
````

Two things I'd hand over separately rather than fold in, since they're different changes: the quit-time cleanup frame (noted at the end of the prompt), and the `signal.pthread_sigmask` Windows guard — `release.yml` builds `opstat-windows-x86_64.exe`, `test.yml` runs Linux-only, so that one will never surface in CI.