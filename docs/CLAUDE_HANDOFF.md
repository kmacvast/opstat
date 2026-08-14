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
| Last commit | `03f72a2` (on top of WIP checkpoint `779cd6e`; base `main` @ `77549f06`) |
| Working tree | **Large uncommitted continuation pass** (see below). Do not discard; do not commit/push without explicit owner instruction |
| Gate | `./scripts/validate.sh` → PASS: **504 collected / 504 passed / 0 skipped** on current Python and Python 3.8; doc links valid |
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

Every remaining live dependency is scripted in
[../scripts/var203_validation/](../scripts/var203_validation/) — one
work-laptop trip answers all of it.

## Outstanding work (not started)

- NFSv3 VIEW drill: possible `host_view` rebuild (carries `protocol=NFS3`).
- Delegation diagnostic (needs a path-entry interaction; D-008).
- Background-threading question for the synchronous exporter scrape (open
  consequence in D-005; L1).
- Windows build path untested in CI (`pthread_sigmask` is getattr-guarded but
  never exercised; `test.yml` is Linux-only, `release.yml` ships an .exe).
- Logical commit breakdown + final real-VMS pass, then publication (owner).

## Recommended next step

Owner runs the var203 validation package from the work laptop and returns its
output; then reconcile, split the working tree into the proposed logical
commits (list in the session report / REFACTOR_HANDOFF), and validate on the
real cluster before any publication.

## Ground rules for any AI resuming here

Read `AGENTS.md` first; it governs. Never push/merge/tag/PR or commit
unbidden. Never fabricate telemetry, metric semantics, or mock behavior for
unproven APIs. Real-VMS work happens only from the owner's work laptop with
`VAST_PASSWORD`/`VAST_TOKEN` from the environment. Run `./scripts/validate.sh`
before claiming green — a bare `pytest` hides 180+ silently-skipped tests when
`openssl` is missing.
