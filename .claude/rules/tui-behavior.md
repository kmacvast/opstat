# TUI behavior

Permanent rule for `opstat`. Applies to rendering, drill entry, and anything
that changes what the user sees or how long they wait to see it.

Short form lives in [AGENTS.md](../../AGENTS.md).

## Why this rule exists

This is a full-screen terminal application whose calls take **seconds**. Mean
REST latency against the real cluster was 1.048 s, range 269 ms – 4135 ms. The
Prometheus exporter takes 1.2–2.4 s for `/prometheusmetrics/basic`, with ~2x
run-to-run variance. Every one of those is a window in which the interface must
still look alive and still tell the user how to get out.

Three defects on this branch came from getting that wrong.

**The navigation footer disappeared in NFSv4.1 drill modes.** `_render_frame`
had an early `return` in the drill path, so the panel took the controls with it.
The user was left in a mode with no visible way out. This was **pre-existing**,
not introduced by the refactor, and it was found by the repository owner running
against a real cluster — not by any test. 122 tests in
`tests/test_render_navigation.py` now guard footer presence in every mode and at
every width.

**Blocking drill entry looked like a hang.** Entering a drill fired a
multi-second VMS call with the previous frame still on screen and no indication
anything was happening. The fix is `vast_drill.with_loading_status`: show the
status, render a frame, *then* do the work, and clear in a `finally`.

**Adding footer keys silently overflowed the width budget.** New drill keys
pushed the footer to 134 characters against a 120-column cap. The existing width
tests caught it before it shipped; the cap was raised to 140 deliberately, not
by accident.

And one presentation defect worth remembering: three cNodes rendered
identically because their hostnames differed only in a trailing digit that fell
outside the column. Sub-microsecond latencies rendered as `0 µs`. Both were
"just formatting" and both destroyed the information the panel existed to show.

## Mandatory behavior

### The footer always renders

- Every mode: headline, every drill, error, loading, warm-up, empty.
- Every width, including narrow terminals.
- A panel that returns early must not bypass footer rendering. **The common
  path owns the footer** — that is the shape the fix took, and it is the shape
  to keep.
- No silent truncation of controls. The frame must never exceed the terminal
  width; the footer degrades legibly rather than vanishing or wrapping into
  garbage.

### Startup and shutdown are loading states too

- **Startup**: every engine's `main()` runs its blocking startup through
  `initialize()` and `vast_drill.with_startup_status`, painting a frame before
  each phase — `Connecting to {VMS}:{PORT}…` (the cluster name is unknown that
  early, so the host is named), `Preparing metrics on {CLUSTER_NAME}…`,
  `Gathering initial metrics…`. Real startup ran ~30–90 s on a live cluster
  with a previously blank terminal. The message *changing* is the progress
  signal — the engines are single-threaded, so there is no spinner, and adding
  one would require the L1 concurrency decision below.
- The startup/waiting frame renders through the footer-owning common path.
  `nfs_v3` and `nvme_tcp` once did a bare `print("Waiting for data…"); return`
  that bypassed the footer — the exact early-return defect this rule exists
  for.
- **Shutdown**: `cleanup()` announces `Cleaning up N temporary monitors,
  please stand by...` before the slow, signal-blocking monitor drain
  (~1 s/monitor on a real cluster). A silent multi-second quit reads as a
  hang; a truthful count is shown, never fake progress.
- `tests/test_startup_loading.py` and `tests/test_cleanup_lifecycle.py`
  guard the ordering; the waiting/startup footer cases live in
  `tests/test_render_navigation.py`.

### Navigation keys follow the canonical contract

Same concept → same key, label, and relative order in every engine:
`vast_drill.CANONICAL_CONTROLS`, built per engine via `nav_controls()` and
rendered by the shared `nav_legend()`. Protocol-specific controls come after
the common set. **VIP is `[i]`, never `[v]`; exit-drill is `[x]`, never
`[p]`.** The FR-A section of `tests/test_render_navigation.py` enforces this
across all five engines; deliberate deviations are documented in
[docs/REFACTOR_HANDOFF.md](../../docs/REFACTOR_HANDOFF.md).

### Paint a loading frame before blocking work

- Any operation that can take more than a moment shows a user-visible status
  first, and the frame is actually rendered before the call is made.
- Use the shared helper `vast_drill.with_loading_status(show_status, render,
  mode, work)` rather than open-coding it. The ordering it guarantees —
  set status, render, work, clear in `finally` — is what
  `tests/test_drill_loading.py` asserts.
- The status must clear on the error path too.
- A warm-up state is a legitimate frame. The native NFSv4 drill shows one on
  first entry because cumulative counters need two scrapes to yield a rate, and
  it says so rather than showing zeros.

### Zero is not unavailable

- A measured `0.00 ops/s` renders as a real zero. It means "no traffic" and
  that is information — zero session churn is a healthy-cluster signal.
- `-` means "no data".
- Never collapse the two, in any panel, in any export.

### Preserve responsiveness

- The main loop is `select()`-driven. It was a `time.sleep(0.05)` spin waking
  20 times a second; idle CPU fell from 0.109 s to 0.042 s per 60 s when that
  changed. Do not reintroduce polling.
- Frame composition is on the hot path — 1.10 ms fell to 0.36 ms when
  `display_width` got an ASCII fast path. Treat per-frame cost as a budget.
- Drill refreshes are throttled independently of the headline refresh.
  Object-scoped families publish ~1/min; the exporter far slower still. Do not
  couple a drill to the 5-second tick.

### Background work is an architectural decision

Threads, async, or subprocesses in the engines are an **L1 decision** requiring
explicit approval. The engines are single-threaded with module-global state, and
that is load-bearing for their current correctness.

The synchronous exporter scrape stalls the TUI for 1.2–2.4 s on drill entry. A
loading frame makes it legible. Whether that is acceptable in practice is
**unvalidated** and is an open decision — see
[docs/decisions/D-005-native-telemetry-is-an-on-demand-throttled-drill.md](../../docs/decisions/D-005-native-telemetry-is-an-on-demand-throttled-drill.md).
Do not resolve it by quietly adding a thread.

### Presentation carries information

- If two rows can be identical on screen, they will be. Give them a
  distinguishing column.
- Do not round a value into meaninglessness. Sub-microsecond latencies are real.
- Label derived figures as derived. Label an attribution coverage fraction
  honestly rather than scaling numbers to look complete.

## What automated enforcement detects

- `tests/test_render_navigation.py` (122 tests) — footer present in every mode
  and at every tested width; frame never exceeds the terminal; other engines
  guarded against the same defect.
- `tests/test_drill_loading.py` (19 tests) — loading-interstitial ordering, and
  the rebuilt exporter-backed VIEW drill.
- `tests/test_tui_layout.py` — column layout and value formatting.
- `tests/test_drill_semantics.py` — throttle behavior and drill entry budgets.

Frames are captured from `_render_frame()` with no terminal attached.

## What automated enforcement CANNOT detect

- **Whether the interface feels responsive.** No committed test drives a
  pseudo-terminal. Perceived latency, keypress-to-repaint, and resize behavior
  under a real terminal are unmeasured by the suite; the benchmark harness that
  produced the numbers in
  [docs/REFACTOR_HANDOFF.md](../../docs/REFACTOR_HANDOFF.md) was a scratch
  script and is not committed.
- **Whether a panel is comprehensible.** Tests assert the footer is present, not
  that the screen makes sense. The footer defect, the identical hostnames and
  the `0 µs` latencies were all found by a human looking at a real screen.
- **Terminal emulator differences** — color handling, wide characters, actual
  wrapping behavior.
- **A new mode with no test.** The 122 render tests enumerate known modes. A
  mode added without a test has no footer guarantee.

Interactive behavior is validated by the repository owner against a real
cluster. That step is not optional and cannot be substituted with a mock run.

## Stopping conditions

Stop and report rather than proceeding when:

- A change would require an early `return` in a render path that bypasses the
  footer.
- A blocking call cannot be preceded by a rendered status frame.
- Making the interface acceptable would require background threading.
- The footer no longer fits the width budget and the fix is to hide controls.
- A panel would need to show a value the cluster did not return in order to look
  complete.
