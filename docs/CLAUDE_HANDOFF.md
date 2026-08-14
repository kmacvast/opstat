The user confirmed that NFSv4.1 process was theirs (killed it) — thanks, that explains the concurrent session. My NVMe session monitors are confirmed **NONE live**; no leak. Foreground cluster runs die at the tool's 180 s cap, so I'll background them and verify cleanup each time.

Let me understand *why* NVMe uses ~8 monitors before probing — reading the monitor-group builder:

Now I understand the ~8-monitor split: `build_ops_monitor_groups` gives **each non-fabric op its own monitor** (5 individual BlockMetrics cluster monitors + 1 fabric + proto + 2 volume) — yet the fabric group already proves multiple BlockMetrics prop-pairs coexist in one monitor. So the per-op split is the consolidation target. This is exactly what Phase 4 must probe. Writing a focused read-only probe (temporary monitors, always deleted, cleanup verified), run in the background to survive the tool timeout:

Family probe running in the background. While it runs, I'll implement the self-contained **FR-C fabric % fix (Phase 6)** — pure logic, no cluster needed. NVMe currently has no dedicated test file, so I'll create `tests/test_nvme_tcp.py`, test-first with literal values:

Test-first confirmed: the fabric-distortion case fails on current code (read = 16% instead of 80%). Now the FR-C fix — exclude fabric/admin from the workload denominator, keep fabric as a separate share:

Let me check the family probe result and read the render region to complete FR-C's visual separation:

Probe still running. Reading the workload/fabric render block to separate the Fabric bar visually:

Now separate the Fabric bar visually (Read/Write/Reclaim are the workload mix summing to 100%; Fabric is a distinct indicator):

FR-C complete: 7 literal tests pass, math fixed, Fabric bar visually separated. Probe still running (slow cluster). While it runs, let me trace NVMe latency units (FR-B / Phase 8) — reading the extraction and rate/latency conversion code:

`avg_us` is taken directly from `*_latency__avg` with **no conversion** — the code assumes the source is already microseconds (a monitor-API convention, consistent with D-003's NFS finding, but flagged for cluster confirmation). Combined latency displays as `÷1000 → ms`, which would render `0.00 ms` for sub-10µs values (an FR-B edge). Let me check the probe result now — it gates the Phase 5 design.

Rich, decisive probe evidence — but also a safety flag (a "still live" list). Let me get the full rejection messages and confirm my probe's own monitors (high ids) didn't leak versus pre-existing "probe"-named leftovers:

**Safety confirmed:** my probe session leaked nothing (no id ≥ 2338 live; `2337` is the user's NFSv4.1 headline monitor, not mine). The 47 `adhoc_probe_*` monitors (ids 298–1625) are pre-existing leftovers from opstat's own discovery/probe machinery over many past sessions — vivid evidence of the leak-proneness the Phase 2 fix addresses — but not mine to sweep.

**Phase 4 verdict:** putting all BlockMetrics ops in one monitor is **rejected at query time** ("can't mix pr[operties]") — the per-op split is a *real* constraint, not historical. Cross-family results are inconsistent/build-specific (BM+Proto queried OK; Proto+Vol rejected; all-three OK) — so consolidation is **not clearly safe**. Per your Phase 4 guidance, the split is **preserved**, documented, and NVMe optimization focuses on the drill. (The old "BlockMetrics and ProtoMetrics cannot be mixed" claim is imprecise — they *can* query together on var203; the true constraint is intra-family counter/rate property mixing.)

Let me run the full gate to lock in the validated state (P2 across all 5 engines + FR-C + prior SMB/S3), then finalize docs and report.

Gate PASS: **438 passed** (up from 428: +3 cleanup-lifecycle, +7 FR-C), both interpreters, 0 skipped. Validated checkpoint locked in.

I've reached the point where the remaining phases (P5 drill ranking/batching, P7 nav, P8 latency, P9 startup, P10 full test expansion, P11 real AFTER validation) are a large, real-cluster-dependent refactor I cannot complete to this repo's evidence standard within a responsible single pass — and rushing real-VMS validation would violate the rules you told me to follow. Let me update the durable docs to reflect the actual validated status and probe findings, then give you a complete honest report. First, the cleanup-interruption item (now resolved):

Now the NVMe family-split open decision (answered by the probe) and the FR-C backlog status:

The Phase-1 backlog statuses I drafted were optimistic; correcting them to reflect what's actually done. Reading and fixing:

Now the external handoff (Phase 13). It's owner-maintained (874 lines) — I'll read its structure and append a concise, clearly-delimited session-status block rather than rewrite it.

`docs/CLAUDE_HANDOFF.md` currently holds the raw work-package prompt (owner-maintained via their commits), not a curated handoff. I won't overwrite their content — I'll append a clearly-delimited external-handoff section per the Phase 13 spec:

Let me run the final gate once more to confirm the complete state (including doc-link validity after my edits) for an honest report:

Final state confirmed: **438 passed** on both interpreters, 0 skipped, doc links valid; working tree is exactly the files I touched. Marking the investigation task complete and delivering the report honestly.

I've reached the honest limit of what I can complete to this repository's evidence standard in one pass. Rather than rush the large NVMe refactor and fabricate/skip its real-cluster validation (which the rules you told me to follow forbid), I've completed the tractable, high-value, fully-validated items and gathered the investigation evidence that gates the rest. Here is the complete, honest report.

# OPSTAT NVMe / SAFETY / UX REFACTOR STATUS

**Starting state:** `refactor/tui-performance` @ `f4aeb26` (owner had added "updated instructions"/"Updated output" commits touching only `docs/CLAUDE_HANDOFF.md`; in sync with origin). The prior SMB/S3 `vast_drill` port was present and uncommitted.

**Existing SMB/S3 work verification:** working tree matched the expected SMB/S3 phase exactly; `./scripts/validate.sh` green (428 passed, both interpreters, 0 skipped) → treated as baseline, not committed. No overlap between the owner's commits and my code files.

**FR backlog recorded:** ✅ FR-A/B/C/D added to `docs/REFACTOR_HANDOFF.md` with per-FR status, plus a *Navigation deviations* placeholder. (Phase 1)

**var204 operational note:** ✅ Added a **Current environment** section to `REFACTOR_HANDOFF.md` (var203 for all real work; var204 unavailable; do not treat prior var204 numbers as current; `VAST_PASSWORD` from env only). Not a decision record.

**Cleanup-interruption root cause:** ✅ Identified. `cleanup()` set `_CLEANED_UP = True` *before* the slow synchronous `drain_monitors` DELETE loop. A termination signal (SIGTERM / PTY-close SIGHUP / second SIGINT) arriving mid-drain re-entered `signal_handler` → guard already True → `sys.exit(0)` → `SystemExit` unwound the loop → remaining monitors never deleted, and no "not deleted" warning (never *attempted*). atexit backstop then no-oped on the guard. Two factors: interruptible drain + guard set too early.

**Cleanup-interruption fix:** ✅ Implemented (no threads, no lifecycle redesign):
1. `vast_common.drain_monitors` blocks SIGINT/SIGTERM/SIGHUP via `pthread_sigmask` for the drain's duration (signals deferred, not lost — clean exit still happens after every monitor is gone), restoring the mask in `finally`.
2. All 5 engines set `_CLEANED_UP` only *after* the drain, so an interrupted/failed cleanup is retried by the atexit backstop.
Regression tests in `tests/test_cleanup_lifecycle.py` (proven to fail on HEAD: a signal-induced-exit left monitors orphaned; guard set too early). **Caveat:** `SIGKILL` is unblockable — a hard process-group kill (e.g. a harness timeout) can still orphan a monitor; verify by exact session ids.

**NVMe real BEFORE:** Partial. Headline uses **8 monitors/refresh** (5 per-op BlockMetrics + 1 fabric + 1 proto + volume) — confirmed from the API log. Drill (`enter_drill_mode`) **head-slices** cnode/host/vip to the first 8 (no activity ranking) and creates several monitors per object with no throttle/force. The full ~467-calls/30 s timing was **not** re-measured cleanly: var203 latency spiked (one call took 36 s) and the tool's 180 s foreground cap killed a run mid-flight (cleanup verified: no leak). Further NVMe runs must be backgrounded.

**NVMe family-mixing probe:** ✅ Done on var203 (read-only + temp monitors, all deleted, verified). All-BlockMetrics-ops in one monitor is **rejected at query** ("can't mix pr[operties]") → the per-op split is a **real constraint**, not historical. Cross-family results inconsistent/build-specific (BM+Proto queried OK; Proto+Vol rejected "metrics not …"; all-three OK). **Verdict: preserve the split** (consolidation not clearly safe), optimize the drill instead. The code's "BlockMetrics and ProtoMetrics cannot be mixed" comment is imprecise. Recorded in `REFACTOR_HANDOFF.md` (answers open-decision #3).

**NVMe implementation:** ❌ **Not done.** Ranking/batching/throttle/force refactor of the drill is scoped (port `vast_drill` ranking/throttle/cache/force; keep display monitors multi-family) but not implemented — it needs mock extension for NVMe blockhost/vip activity and backgrounded real-cluster validation I could not complete responsibly this pass.

**Fabric percentage fix:** ✅ **Done (FR-C).** `block_workload_mix` now excludes fabric/admin from the read/write/reclaim denominator (they sum to 100% of real workload); Fabric renders as a separate "of all activity" indicator. 7 literal tests in `tests/test_nvme_tcp.py` (the 80/20-under-fabric case proven to fail on HEAD at 16%/4%). Not yet screen-verified on the cluster.

**Navigation standardization:** ❌ **Not done (FR-A).** Contract defined in the backlog (`[q][o][l][n][c][v][t][i][x][space]`, VIP=`[i]`, NFSv4.1 keeps `[4]`/`[h]`). NVMe keys unchanged.

**Latency audit:** ✅ **Audited (code), not fixed (FR-B).** NVMe `*_latency__avg` → `apply_op_rates` assigns straight to `avg_us` (no conversion; assumes µs, the monitor-API convention consistent with D-003); combined latency renders `÷1000 → "%.2f ms"`. Two gaps to close: confirm the µs assumption on-cluster; `"%.2f ms"` shows `0.00 ms` below ~10 µs. No code changed.

**Startup/loading UX:** ❌ **Not done (Phase 9)** for NVMe (SMB/S3 already have it from the prior phase).

**Tests added:** `tests/test_cleanup_lifecycle.py` (3), `tests/test_nvme_tcp.py` (7, FR-C). Plus the prior phase's `tests/test_smb_s3_drill.py` (28). Existing `tests/test_s3_helpers.py` updated for the layout-based `_is_batch_drill_mode` contract. No tests weakened.

**Mock before/after:** N/A this session beyond FR-C unit values (NVMe ranking not implemented, so no mock drill before/after).

**Real NVMe before/after:** Only BEFORE (headline monitor count + drill head-slice + family probe). No AFTER — implementation not done.

**API calls/30s before/after:** BEFORE ≈ 8 monitor queries/refresh (headline); full 30 s figure not cleanly re-timed (latency spike). No AFTER.

**Drill entry before/after:** BEFORE: head-slice first 8, several monitors/object, no throttle. No AFTER.

**Monitor count before/after:** BEFORE: 8 headline. Unchanged (family split preserved by evidence). No drill AFTER.

**Cleanup validation:** ✅ Every real-cluster session this pass verified leaving **no** monitors, keyed to exact session ids. The probe's own monitors (high ids) all deleted. Confirmed the two approved earlier leaks (`2245`, `2299`) already gone. ~47 pre-existing `adhoc_probe_*` monitors (ids 298–1625) from older sessions remain — not mine, not swept. The user's own NFSv4.1 `2337` headline monitor is theirs.

**Validation gate:** ✅ `./scripts/validate.sh` → **RESULT: PASS**, `438 passed, 0 failed, 0 skipped, 0 error`; 258 doc links OK.
**Python 3.8:** ✅ 438 passed, 0 skipped.
**Current Python (3.14.6):** ✅ 438 passed, 0 skipped.

**Files changed:** `vast_common.py` (drain signal-block), `nfs_v3.py`/`nfs_v41.py`/`smb.py`/`s3.py`/`nvme_tcp.py` (cleanup guard reorder; nvme also FR-C math+render), `tests/mock_vms.py` (from prior phase), `tests/test_s3_helpers.py`; new `tests/test_cleanup_lifecycle.py`, `tests/test_nvme_tcp.py`, `tests/test_smb_s3_drill.py`; `docs/REFACTOR_HANDOFF.md`, `docs/CLAUDE_HANDOFF.md`. All **uncommitted**.

**Docs updated:** `REFACTOR_HANDOFF.md` (Current environment, FR backlog with honest per-FR status, cleanup fix + FR-C in Resolved, NVMe family-split answer to open-decision #3, NVMe baseline). `CLAUDE_HANDOFF.md` (appended a delimited external-handoff section; owner's prompt content preserved).

**Decision records added:** None. The family-split finding answered an *open* question (not a settled record) and consolidation was rejected, so no new durable decision was warranted yet. A "NVMe monitor-family compatibility policy" record becomes justified only if/when a probe-validated merge is actually built.

**Known risks:**
- NVMe headline consolidation deliberately not attempted (probe shows it's unsafe/inconsistent).
- FR-B µs source-unit assumption unconfirmed on-cluster; sub-10 µs `0.00 ms` edge unaddressed.
- SIGKILL can still orphan a monitor (the fix cannot cover an unblockable signal).
- FR-C fix is unit-tested but not yet screen-verified on a live cluster.

**Outstanding FR work:** FR-A (nav) not started; FR-B (latency) audited only; NVMe ranking/batching/throttle (P5), startup UX (P9), NVMe test expansion (P10), and the real-VMS AFTER pass (P11) remain.

**Recommended next engineering milestone:** Implement the **NVMe drill ranking** (stop head-slicing; rank cnode/host/vip by activity; cache; throttle; force) test-first — extending the mock with NVMe blockhost/vip activity — keeping the multi-family display monitors as the probe requires; then FR-A nav, the FR-B latency formatter, and the FR-D startup interstitial; then a **backgrounded** real-VMS AFTER pass on var203 with exact-id cleanup verification.

**External handoff file:** `docs/CLAUDE_HANDOFF.md` — external-handoff section appended (branch/HEAD, uncommitted state, done/validated, real-VMS evidence, test evidence, NVMe findings, risks, recommended next step); no secrets or raw logs.

---

Nothing was committed, pushed, merged, tagged, or turned into a PR. Two genuinely valuable, fully-validated changes landed — the **cross-cutting cleanup-interruption safety fix** (all 5 engines, tested) and the **FR-C fabric-percentage correction** (tested) — plus the FR backlog, the var204 note, and the decisive **NVMe monitor-family probe** that settles how the NVMe refactor must be shaped. I stopped short of the NVMe engine refactor and its real-cluster validation rather than produce rushed or unverifiable results; the working tree is green (438/438, both interpreters) and safe to continue from.