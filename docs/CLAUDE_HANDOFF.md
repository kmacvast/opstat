# opstat — cross-AI engineering handoff

Concise technical handoff for moving reasoning between AI assistants (Claude,
ChatGPT, or another agent) and machines. Readable without any prior chat
transcript. Point-in-time: verify every SHA and count against the repository
before relying on it. Depth lives elsewhere — this file is the map:

- [../AGENTS.md](../AGENTS.md) — the behavioral contract (governs all agents).
- [REFACTOR_HANDOFF.md](REFACTOR_HANDOFF.md) — full branch state and evidence.
- [decisions/](decisions/) — settled decisions; reopening one needs new
  evidence and explicit owner approval.

---

## Where things stand

| | |
|---|---|
| Branch | `refactor/tui-performance-local-continuation-wip` |
| Last commit | `96f284e` (continuation branch published through the round-1/round-2 lab runs) |
| Working tree | **Uncommitted round-2 reconciliation** (footer wrap, NVMe key dispatch + drill loading frame, validator fixes, docs). Do not discard; do not commit/push without explicit owner instruction |
| Gate | `./scripts/validate.sh` → PASS on current Python and Python 3.8, 0 skipped; doc links valid (counts in REFACTOR_HANDOFF) |
| Real clusters | `var203.selab.vastdata.com` only, from the owner's **work laptop** only. `var204` unavailable until the owner says otherwise. No cluster is reachable from the personal laptop |

## Objective

Make `opstat` (stdlib-only Python 3.8+ terminal dashboard for VAST clusters)
fast, API-frugal and honest: request volume is a regression dimension, panels
are evidence-gated, zero ≠ unavailable, and nothing is displayed that the
cluster did not return.

## Complete and validated on real VMS (earlier passes)

- NFSv3 + NFSv4.1 engines: merged probe-validated monitors, ranked/batched/
  throttled drills, native NFSv4 exporter telemetry (`Nfs4Metrics`,
  `host_view`) — semantics proven and recorded in `decisions/` D-001…D-012.
- SMB/S3 view/tenant/bucket drills ported to `vast_drill.DrillSession`
  (var203-validated: entry 18 → 7 calls; SMB view ranking 104 s → 9 s).
- Cleanup-interruption fix (signal-blocked drain, guard-after-drain) across
  all five engines.

## Complete locally in the current working tree (this pass)

All mock/unit-proven, on both interpreters:

- **FR-A navigation, all five engines.** Canonical contract in
  `vast_drill.CANONICAL_CONTROLS` / `nav_controls()` / shared `nav_legend()`
  renderer. VIP=`[i]` (never `v`), exit=`[x]` (never `p`), `[space]` refresh;
  protocol-specific controls after common ones. NFSv3's htop-style footer
  restyled (keys unchanged); NVMe stale `v`/`p` help+README text corrected.
  Deviations documented in REFACTOR_HANDOFF (S3 `[b]` Bucket appended after
  common; NFSv3 lacks `[n]`; NVMe `[r]` collision noted).
- **NVMe drill ranking + batching.** Head-slice of first 8 objects replaced by
  activity ranking (batched BlockMetrics read_req+write_req rank monitor,
  bounding-samples deltas, rank cache; topn deliberately unused — D-007).
  Display monitors batched per op-group across objects with splittability
  validation and per-object fallback. Mock-measured: entry 65 → 13 calls,
  re-poll 64 → 8 queries. Proven failing pre-change (busy cNode at mock index
  10 was never selected).
- **FR-B latency audit complete** (table in REFACTOR_HANDOFF). Two display
  defects fixed: S3 sub-5 µs no longer renders `0.00 ms`; NVMe combined
  latency now uses the shared auto-scaling formatter. No source-unit
  conversions changed. No ns-sourced value exists anywhere.
- **FR-C fabric percentages**: render audit confirms math+panel agree; no
  metadata category exists in BlockMetrics, so none is shown.
- Exporter render tests self-initialize (pass in isolation now); collection
  floor raised 395 → 465; startup/shutdown + navigation invariants promoted
  into `.claude/rules/tui-behavior.md`.

## IMPLEMENTED / REAL-VMS VALIDATION PENDING

Implementation is done and deterministically tested; var203 only confirms:

1. NVMe batch monitors accepted (multi-`object_id` at cnode/vip/blockhost
   scope) — engine falls back per-object if not.
2. NVMe blockhost drill end-to-end (mock deliberately does not model
   `/blockhosts/`).
3. NVMe real BEFORE/AFTER call counts; startup + shutdown UX appearance;
   FR-A footers on real screens; FR-C BLOCK screen.

## BLOCKED ON REAL-VMS EVIDENCE

Implementation deliberately not attempted without live evidence:

1. **NVMe headline consolidation** — var203 probe showed all-BlockMetrics-ops
   in one monitor is rejected at query time ("can't mix pr[operties]") and
   cross-family results are build-inconsistent. Split preserved.
2. **Unproven latency source units** — `host_view` `latency` gauge; NVMe
   BlockMetrics/VolumeMetrics µs assumption; SMB/S3 per-op corroboration.
   Displays unchanged and marked UNVERIFIED until compared against a known-µs
   metric under load.

## Settled by round 2 (Linux lab host, cluster-adjacent, 2026-08-14)

Round 2 ran `run_var203_validation.py` beside var203 (**VAST OS 5.4.6**, not
5.5.0.1 as first recorded), so its wall-clock is trustworthy:

- **FR-C is REAL-VMS VALIDATED.** Live BLOCK frame: Read 72.5 / Write 27.1 /
  Reclaim 0.4 (= 100.0%), Fabric separate at 80.3% "of all activity"; idle
  frame showed 0/0/0 with Fabric 100% and no fabricated workload share.
- **Navigation contract validated on real screens** (`nav.*` all PASS); but a
  real-use **footer-width regression** was found on an ordinary laptop
  terminal — only `[q] [o] [l]` visible, working keys undiscoverable. Fixed:
  the legend now wraps (`nav_legend_lines`), never drops a control; literal
  q/o/l-only repro test added.
- **Real defect found and fixed: queued keys were dropped.** Poll cycles block
  30–80 s on this cluster; multiple keys arrive in one read and the old
  handling fired one action and discarded the rest (the lab log shows a
  buffered space swallowing `x` and `i` — every drill-window FAIL in the
  validator report traces to this). All five engines now dispatch every
  queued key in arrival order through the shared
  `vast_drill.dispatch_queued_keys`; multi-key buffers are regression-tested
  per engine (`tests/test_key_dispatch.py`).
- **Real defect found and fixed: NVMe had no drill-entry loading frame**, and
  its entry legitimately runs ~2 min on this cluster. Now routed through
  `with_loading_status`.
- **Startup = 157 s of honest serial API time** (26.6 s `/clusters/` + ~59 s
  for 8 creates + ~67 s for 8 first queries; per-call 2–38 s). No duplicate
  work exists to cut; the one lever (fewer startup monitors) is BLOCKED on
  the new merge-legality probes in `probe_var203.py`. The "206 s" first
  report included ~50 s of validator dead-wait on a wrong marker (fixed).
- **D-013 shape settled**: unsplittable responses DO carry an `object_id`
  column — rows just never match; and at vip scope the cluster silently
  rewrites requested BlockMetrics props to `TopNMetrics`.
- **Latency units still UNVERIFIED** — the known-µs reference read 0 again
  (idle NFS4 during the window) and `host_view` published no latency series.

## Settled by round 1 of real-VMS validation (2026-08-14)

- **NVMe batch layout is scope-dependent** — [D-013](decisions/D-013-nvme-drill-batching-is-scope-dependent.md).
  `cnode` batches and splits per `object_id`; `vip` and `blockhost` accept
  creation *and* query yet return no per-object rows, so the engine's run-time
  splittability check falls them back to per-object. Regression coverage now
  reproduces both unsplittable shapes; removing the check fails four tests.
- **Rank monitors work at `cnode` scope** — real per-object `read_req` deltas
  (1062.353/s vs 0.0/s), so a measured zero is distinguishable from no data.
- **Exact-id cleanup works** — six probe monitors created, all six verified
  deleted per id.
- **Latency units remain UNVERIFIED.** The reference metric returned 0 in that
  window, so 488.99 from BlockMetrics proves nothing on its own. No display
  behaviour was changed on the strength of it.

Round 1 ran over a tethered link, so **all wall-clock from it was discarded**;
counts, shapes and cleanup results were kept.

## Remaining live dependencies

One command on the Linux lab host answers the rest, unattended:

```bash
python3 scripts/var203_validation/run_var203_validation.py
```

It drives `opstat` through a PTY (startup/shutdown UX, drill entry cost and
cadence, ranking, navigation, Fabric screen, per-id cleanup) and writes
`/tmp/opstat-var203-validation.txt`. See
[../scripts/var203_validation/](../scripts/var203_validation/README.md).

## Outstanding work (not started)

- NFSv3 VIEW drill: possible `host_view` rebuild (carries `protocol=NFS3`).
- Delegation diagnostic (needs a path-entry interaction; D-008).
- Background-threading question for the synchronous exporter scrape (open
  consequence in D-005; L1).
- Windows build path untested in CI (`pthread_sigmask` is getattr-guarded but
  never exercised; `test.yml` is Linux-only, `release.yml` ships an .exe).
- Logical commit breakdown + final real-VMS pass, then publication (owner).

## Recommended next step

Commit the round-2 reconciliation (owner-approved split), then re-run
`run_var203_validation.py` on the lab host with the fixed validator: it should
now show the cNode drill fully green, the true drill AFTER costs, vip/blockhost
fallback behavior, the merge-legality probe verdicts (startup lever), and —
with NFS4 load active during the probe window — a usable latency cross-check.

## Ground rules for any AI resuming here

Read `AGENTS.md` first; it governs. Never push/merge/tag/PR or commit
unbidden. Never fabricate telemetry, metric semantics, or mock behavior for
unproven APIs. Real-VMS work happens only from the owner's work laptop with
`VAST_PASSWORD`/`VAST_TOKEN` from the environment. Run `./scripts/validate.sh`
before claiming green — a bare `pytest` hides 180+ silently-skipped tests when
`openssl` is missing.
