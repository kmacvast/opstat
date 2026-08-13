---
name: audit-api-efficiency
description: >-
  Measure opstat's VMS request volume against the instrumented mock — per
  startup, per refresh tick, per drill entry and per drill re-poll — and compare
  against the recorded baselines. Use before and after any change to fetch,
  monitor or drill paths.
---

# audit-api-efficiency

Measure request volume rather than reasoning about it.

`tests/mock_vms.py` is an in-process HTTPS mock of the VMS REST surface that
**records every call**, and can inject latency, failures and capability
differences. It is the measurement instrument, not just a stub.

## Preconditions

- `openssl` is available — without it the mock cannot generate its certificate
  and every measurement suite skips silently.
- You have a baseline to compare against: either a run on the current `HEAD`
  before your change, or the recorded numbers in `docs/REFACTOR_HANDOFF.md`.
- Read `AGENTS.md` ("API efficiency principles") and
  `.claude/rules/vast-api-safety.md` first. The invariants are constraints, not
  targets to trade against.

## Workflow

1. **Establish the baseline before changing anything.** If the change is already
   made, measure the prior commit in a worktree rather than guessing:

   ```bash
   git worktree add /tmp/opstat-baseline <prior-sha>
   ```

   Remove the worktree when finished.

2. **Run the accounting suites and read what they assert.**

   ```bash
   python3 -m pytest tests/test_api_efficiency.py -q
   python3 -m pytest tests/test_drill_semantics.py -q
   ```

   These encode startup budgets, per-refresh query counts, merged-monitor
   budgets and fallbacks, drill entry call budgets, cNode batching and throttle
   behavior.

3. **Measure the dimensions that matter**, from the mock's recorded call log:
   - calls at startup (there must be **one** `GET /clusters/`)
   - monitor queries **per refresh tick** (target: 1 per engine)
   - calls on **drill entry**, and on each throttled re-poll
   - live monitors held concurrently during a drill
   - TLS connections per session (target: **1**, keep-alive reused)
   - any `/prometheusmetrics/*` request, and where it was triggered from

4. **Check the invariants explicitly**, and state each verdict:
   - no `/prometheusmetrics/*` reachable from a refresh path
   - no `/prometheusmetrics/all` at all
   - candidates ranked by activity, not by API order
   - drills batched into one monitor and sliced by `object_id`
   - drill and exporter refreshes throttled independently of the headline tick
   - monitors cleaned up on every path — `vms.live_monitors() == {}` after
     teardown, including on the error path

5. **If a budget changed, add or update the assertion** in the accounting suite
   in the same change. A budget that is documented but not asserted will drift.

6. **Compare against the recorded baselines** in `docs/REFACTOR_HANDOFF.md`
   ("Performance / API-efficiency work completed"). Report any regression
   against them as a regression, even if the suite passes.

## Expected output

A before/after table with real counts, one row per dimension measured, plus an
explicit verdict on each invariant, plus what was not measured.

Distinguish **mock measurements** from **real-cluster measurements**. The mock's
loopback latency is ~1 ms; the real cluster's mean call latency is 1.048 s.
Wall-clock numbers from the mock are not cluster wall-clock numbers — only the
*counts* transfer.

## Stopping conditions

- Stop if `openssl` is missing; the measurement cannot be made and a skipped
  suite is not a result.
- Stop and report if a change would move a `/prometheusmetrics/*` request onto a
  refresh path, or request `/prometheusmetrics/all`. Both are L1 decisions.
- Stop and report if reducing API calls would cost freshness or correctness.
  Fewer calls is not worth stale or wrong data.
- Do not weaken or delete a budget assertion to accommodate a change.
