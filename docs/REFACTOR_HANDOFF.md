# Opstat Refactoring Handoff

Engineering handoff for the `refactor/tui-performance` branch. Written so a
fresh session — human or Claude — can resume without reconstructing the
reasoning behind decisions already made and validated.

**Read [../AGENTS.md](../AGENTS.md) first.** It holds the permanent behavioral
contract — architecture, the Python 3.8 floor, the command contract, the
decision hierarchy, API-efficiency invariants, VMS safety, TUI requirements and
the definition of done.

**This document is point-in-time.** It holds branch state, measurements,
per-protocol status and open work. Durable decisions — the ones a future session
must not casually reopen — live in [decisions/](decisions/) with the evidence
that settled them, and are linked from here rather than restated.

> **Verify before trusting.** Every SHA, count and status below was accurate at
> the time of writing. Re-derive them from the repository (`git log`,
> `git status`, `pytest`) before relying on them. Where this document and the
> repository disagree, the repository is right — and please correct this file.

---

## Purpose and scope

### In scope

Make `opstat` faster, more responsive and dramatically more economical with the
VMS API, without changing what it reports — plus a substantial NFSv4.1
telemetry investigation that turned into new functionality.

### Explicitly out of scope

- Redesigning the visual language of the dashboards.
- Adding runtime dependencies (the project is stdlib-only, Python 3.8+).
- Changing CLI flags, config formats, key bindings, CSV/OpenMetrics output
  formats, or data interpretation, except where documented as a deliberate
  change.
- Merging to `main` or publishing anything. The repository owner controls that.

---

## Current environment

**Real-VMS validation cluster: `var203.selab.vastdata.com`.** Use it for all
real-cluster work.

**`var204.selab.vastdata.com` is unavailable** (being reinstalled; expected back
~2026-08-17 but will need reconfiguration). Treat it as unavailable until the
repository owner explicitly says otherwise, and **do not treat prior var204
measurements as current** — the NFSv3/NFSv4.1 real-cluster numbers below were
taken on var204 and cannot be re-verified live until it returns (mock coverage
still runs). This is a temporary operational condition, not a decision record.

Auth for real-cluster runs comes from the `VAST_PASSWORD` / `VAST_TOKEN`
environment variables — never on the command line, in tracked files, in API
logs, or in this handoff.

## Repository / branch baseline

| Item | Value at time of writing |
|---|---|
| Branch | **`main` — the milestone is merged and closed; main is the source of truth for subsequent work** |
| Milestone merge | `38b66ce` — *Merge opstat TUI performance refactor + real-VMS validated NVMe remediation* (`--no-ff`, gate-validated before and after) |
| Continuation branch | `refactor/tui-performance-local-continuation-wip` @ `85f6075` — retained, published, an ancestor of `main` |
| Final real-VMS validation | Round 5B, `main` @ `1aaa359`, var203, 2026-08-16 — all checks PASS (see Round-5B closeout below) |
| Tests | 603 collected, all passing, 0 skipped, on Python 3.8 and the current interpreter |

Determine the live state rather than trusting the table:

```bash
git rev-parse --abbrev-ref HEAD
git rev-parse HEAD
git log --oneline --reverse origin/main..HEAD
git status --short
```

If HEAD is ahead of origin, unpublished work exists — read those commits before
assuming this document covers them.

---

## Architectural map

### Protocol engines (one per protocol, module-global state, single-threaded)

| Module | Protocol | Refactor status |
|---|---|---|
| `nfs_v3.py` | NFS v3 | Fully refactored, real-VMS validated |
| `nfs_v41.py` | NFS v4.1 | Fully refactored + native exporter telemetry |
| `smb.py` | SMB/SMB2 | Partially refactored |
| `s3.py` | S3 | Partially refactored |
| `nvme_tcp.py` | NVMe-oTCP | Least refactored — see NVMe status |

### Shared modules

| Module | Responsibility |
|---|---|
| `vast_common.py` | Auth, keep-alive HTTPS transport (`request`, `request_text`), monitor lifecycle, signals, terminal/keyboard, **sample selection** (`latest_complete_row`, `latest_complete_values`, `bounding_samples`), metric-catalog reader |
| `vast_drill.py` | Shared drill machinery: candidate ranking (topn → batched monitors → chunked scan), probe-validated batch monitor creation, re-query throttle, view/tenant row builders, **drill loading-status helper** |
| `vast_discovery.py` | Read-only survey of the VMS observability surface: OpenAPI, Prometheus exporter parsing, REST probing, counter-delta analysis. Discovery-time only |
| `nfs4_native.py` | Runtime native NFSv4 telemetry: exporter parsing, cumulative-counter derivation, `Nfs4Collector`, `HostViewCollector`, host→view aggregation |
| `tui_layout.py` | Column layout, display width, value formatting |
| `openmetrics.py`, `vast_api_log.py` | Optional JSONL export; `--log-api-calls` REST logging |
| `wizard.py` | Interactive setup when run with no args on a TTY |

### Test infrastructure

| File | Purpose |
|---|---|
| `tests/mock_vms.py` | In-process HTTPS mock of the VMS REST surface + Prometheus exporter. Records every call; injectable latency, failures, capability differences |
| `tests/conftest.py` | Loads the extensionless `opstat` CLI via `runpy`; resets shared state between tests |

---

## Refactoring history

Source of truth is `git log --reverse origin/main..HEAD`. Logical grouping:

**Transport and main loop**
- `f20e068` Keep-alive HTTPS connection reuse; dropped the duplicate startup
  `/clusters/` fetch; added `wait_for_input`.
- `0c770ba` All five engines moved off the 50 ms poll spin; NFS engines merged
  their headline monitors (probe-validated, with split fallback).
- `83db913` ASCII fast path in `display_width`.

**Test infrastructure**
- `93253e1` `tests/mock_vms.py`, API-efficiency regression tests, AST
  globals-hygiene check.
- `5b7579f` SMB/S3 headline + probe monitor merge.

**Sample selection**
- `63ff213` Select the newest *complete* monitor sample rather than the
  still-filling newest bucket. Affected every engine.

**Drill-down machinery**
- `b600f36` NFSv3 fast drill ranking, batched cNode monitors, throttled polling.
- `c87ee2f` Extracted that machinery into `vast_drill.py`.
- `cb6e5f8` NFSv4.1 drill made scope-correct, ranked, batched, throttled.
- `6869efe` Restored the navigation footer; scoped row selection per metric
  family; first real metric discovery.

**Discovery campaign** (`86d24ce`, `e76c198`, `ce3eca7`, `42f62be`, `11eb3d3`,
`636f59c`, `48c0c61`) — progressively widened `--discover-metrics` from the
metric catalog to the whole VMS observability surface, and interrogated the
`Nfs4Metrics` family until its semantics were proven.

**Native NFSv4 telemetry**
- `9c091d0` The native NFSv4 drill and host attribution drill.
- `9c6d6e5` Loading interstitial, exporter-backed VIEW drill, presentation fixes.

Unrelated commits from the repository owner (`fcc3e6b`, `3aea561`, `344fe79`)
add the lab load generators under `scripts/`.

---

## Performance / API-efficiency work completed

Measured with a real `opstat` process driven in a PTY against the instrumented
mock, 32-second sessions at `--refresh 5`.

```text
Metric                              Before      After
------------------------------------------------------
API calls, 32s session
  nfs v3                            20          11
  nfs v4.1                          48          12
  smb                               25          15
  s3                                21          11
  nvme                              74          73     (VMS family constraint)
Monitor queries per refresh
  nfs v3 / v4.1 / smb / s3          2 / 5 / 2 / 2    1 / 1 / 1 / 1
TLS connections per session         1 per API call   1
GET /clusters/ at startup           2           1
Process CPU, 32s active session     0.13-0.19s  0.05-0.07s
Idle CPU (60s, no refresh due)      0.109s      0.042s
Idle loop wakeups                   20/s        ~0
Frame composition                   1.10 ms     0.36 ms
Startup to first frame (0.4s/call)  2.53s       1.67s
```

Drill-down, measured at the real cluster's 1.048 s mean call latency:

```text
VIEW drill entry, 429 views         47.5 s / 45 calls   4.2 s / 4 calls
cNode drill entry                   17.9 s / 17 calls   4.3 s / 4 calls
Queries per 6 refresh ticks         54                  6
Live monitors during cNode drill    9                   2
```

### Invariants that must not regress

Listed in [../AGENTS.md](../AGENTS.md#api-efficiency-principles). The most
consequential: **the 5-second refresh path must never scrape
`/prometheusmetrics/*`**, and `/prometheusmetrics/all` must never be requested
at all — see [D-004](decisions/D-004-heavy-exporter-endpoints-off-the-refresh-path.md).

---

## NFSv3 status

Fully refactored and validated against the real cluster.

- Headline: one merged cluster monitor carrying `NfsMetrics` + `ProtoMetrics`,
  probe-validated with automatic fallback to the historical two monitors.
- Drill-down: ranking via `vast_drill` (topn → batched rank monitors →
  chunked scan), cached 5 minutes; batch display monitor; 15 s re-query
  throttle; `Space` forces.
- cNode drill batches into one monitor, probe-validated with per-object
  fallback.
- Drill panel states attribution coverage honestly rather than scaling numbers.

**Open item:** the NFSv3 VIEW drill still uses `ViewMetrics`. The same
attribution weakness found for NFSv4.1 may apply — `host_view` carries
`protocol=NFS3` series, so the exporter-backed rebuild done for NFSv4.1 is
directly portable. Not yet investigated for v3.

---

## NFSv4.1 status

The most heavily worked area. Read this section before touching `nfs_v41.py`
or `nfs4_native.py`.

### Where the evidence now lives

The NFSv4.1 investigation produced a set of findings that are **durable** — they
describe the cluster and the protocol, not this branch. They have been moved to
[decisions/](decisions/) with the evidence that settled them, and must not be
casually reopened:

| Question | Record |
|---|---|
| Does the monitor API expose NFSv4 protocol state? (No. The exporter does.) | [D-001](decisions/D-001-monitor-api-and-exporter-are-separate-paths.md) |
| Are `Nfs4Metrics` counters cumulative or instantaneous? | [D-002](decisions/D-002-nfs4metrics-counters-are-cumulative.md) |
| What unit is `Nfs4Metrics` latency in? | [D-003](decisions/D-003-nfs4metrics-latency-is-microseconds.md) |
| What do the exporter endpoints cost, and which is used? | [D-004](decisions/D-004-heavy-exporter-endpoints-off-the-refresh-path.md) |
| Why is native telemetry a throttled drill with a synchronous scrape? | [D-005](decisions/D-005-native-telemetry-is-an-on-demand-throttled-drill.md) |
| Where does NFSv4 host/view attribution come from? | [D-006](decisions/D-006-host-view-is-the-nfs4-attribution-source.md) |
| Can `/monitors/topn/` attribute by protocol? (No.) | [D-007](decisions/D-007-topn-is-unusable-for-protocol-attribution.md) |
| What about delegations, and which NFSv4 ops have no counters at all? | [D-008](decisions/D-008-delegations-are-a-file-scoped-diagnostic.md) |
| Why "v4 hosts" and not "v4 clients"? | [D-012](decisions/D-012-terminology-v4-hosts.md) |

Two further findings did not need their own record:

- **`NfsSampledMetrics` is not the missing NFSv4 source.** 185 names, all
  `socket_nfs_{op}_latency__*`, over the **NFSv3** procedure set (`fsinfo`,
  `fsstat`, `pathconf`, `mknod`, `readdirplus` are v3-specific). Socket-layer
  measurement of v3 operations. Queryable at cluster (185/185) and cNode scope;
  rejected at view and tenant scope.
- **The `ViewMetrics`-based view drill was internally correct.** Its data source
  was wrong, not its logic — the detail is in
  [D-006](decisions/D-006-host-view-is-the-nfs4-attribution-source.md), and it
  is worth reading before concluding that a drill showing nothing is buggy.

### Implementation state on this branch

- Headline: one merged monitor, probe-validated with fallback
  ([D-010](decisions/D-010-merged-monitors-are-probe-validated-with-fallback.md)).
  No exporter request on the refresh path at all.
- `c` / `t` / `v` drills: ranked, batched, throttled, with a loading
  interstitial via `vast_drill.with_loading_status`.
- `4` — native NFSv4 telemetry drill: session/state, file/state, operation mix,
  per-cNode panels. Warm-up frame on first entry; `space` completes it.
- `h` — v4 hosts; `v` — NFSv4 views. Both from one shared `host_view` collector.
- pNFS panel present in code and evidence-gated, so it stays hidden on VAST OS
  5.5.0.1 and appears automatically if a future build exports the counters.
- Navigation footer renders in every mode, guarded by 122 render tests.

### Still open for NFSv4.1

- The rebuilt VIEW drill shows only views with **current NFS4 traffic**, not
  every configured view. Deliberate trade; the panel says so.
- The synchronous exporter scrape stalls the TUI for 1.2–2.4 s on drill entry.
  Legible, but unvalidated as acceptable — the open consequence in
  [D-005](decisions/D-005-native-telemetry-is-an-on-demand-throttled-drill.md).
- The delegation lookup is not implemented; it needs a path-entry interaction
  ([D-008](decisions/D-008-delegations-are-a-file-scoped-diagnostic.md)).
---

## SMB / S3 status

**Done:** keep-alive transport, single startup `/clusters/` fetch, event-driven
main loop, merged headline + probe monitors (probe-validated with fallback),
corrected sample selection (`_latest_row`, `_values_from_result`,
`_delta_rate_from_samples`, `_avg_from_sum_count_deltas` all route through the
shared selector).

**Done — view/tenant/bucket drill ported to `vast_drill.DrillSession`** (this
effort, real-VMS validated on `var203.selab.vastdata.com`; changes are in the
working tree, not yet committed). Both engines now import `vast_drill` and route
the view/tenant (SMB) and bucket/tenant (S3) drills through `DrillSession.rank`
(topn → adaptive batched rank monitors → cached) and `DrillSession.create_monitors`
(batch with automatic per-object fallback). `_is_batch_drill_mode` is now keyed
on the actual monitor layout (`batch_active`), not the mode, so a batch that
falls back to per-object is queried correctly. `fetch_drill_query(force=False)`
throttles view/tenant/bucket re-polls via `DRILL.should_query`; the space bar
(`manual_refresh`) forces. Loading interstitial and startup frame now go through
`vast_drill.with_loading_status` (see *Startup / loading UX* below).

Real before → after (`var203`, high and variable REST latency; counts transfer,
wall-clock does not):

```text
                 entry API calls    ranking wall-clock    rank monitors
  SMB view       18 → 7             104 s → 9 s           5 chunks → 1 batch
  SMB tenant      9 → 7              34 s → 37 s          2 chunks → 1 batch
  S3  bucket     18 → 7              47 s → 45 s          5 chunks → 1 batch
  S3  tenant      9 → 7              59 s → 10 s          2 chunks → 1 batch
```

topn returned no usable view/bucket ranking on this cluster (few active views),
so ranking fell to **one** batched rank monitor rather than the topn shortcut —
still far better than the pre-port serial chunk-per-32 scan, and re-entry inside
the cache TTL now issues **no** rank monitors. Wall-clock gains vary with the
cluster's per-call latency (one call was observed taking 36 s during this pass);
the deterministic win is the call-count drop and the rank cache. All runs exited
cleanly (`q`) with **no leaked monitors**, verified against each session's exact
monitor ids.

**cNode drill unchanged** (still per-object, head-sliced) by design — not batched
opportunistically. **S3 VIP unchanged** and re-verified on the real cluster: topn
ranking, `192.168.*` filtering, per-object monitors, unthrottled re-poll, and the
topn-only fallback (now exercised by a mock that rejects `object_type=vip`).

---

## Startup / loading UX

Real-cluster runs exposed a startup window of ~30–90 s (auth, cluster
resolution, headline-monitor creation/probe, first sample fetch, aux context)
during which the terminal showed nothing. SMB and S3 now paint a
`Gathering initial metrics, please stand by...` status frame **before** that
blocking work via `vast_drill.with_loading_status` (a new `"startup"` entry in
`LOADING_MESSAGES`), through a shared `initialize()` helper called from `main()`.
The status reaches the terminal before the first API call; the normal dashboard
replaces it automatically when ready; the footer renders correctly once the
normal frame appears. No threads were introduced.

This is a candidate general invariant ("no blank/frozen terminal during
multi-second startup; status before blocking work; shared helper").

**Now implemented in all five engines** (FR-D / Phase 9). Each `main()` calls a
uniform `initialize()` that runs three phases through the shared
`vast_drill.with_startup_status(show_status, render, steps)` helper — a frame is
painted before each blocking step, and the message changes as startup
progresses (single-threaded, so the message change *is* the progress signal;
no spinner):

1. `Connecting to {VMS}:{PORT}, please stand by...` — before `get_current_cluster`
   (the cluster name is unknown this early, so the host is named, not the cluster).
2. `Preparing metrics on {CLUSTER_NAME}, please stand by...` — before monitor creation.
3. `Gathering initial metrics, please stand by...` — before the first query.

The status is cleared once the first real frame renders, and in a `finally` on
the error path. Each engine gained a `STARTUP_STATUS` global + `_set_startup_status`
(globals-hygiene compliant), and the startup/waiting frame now renders through
the footer-owning common path. The `nfs_v3` and `nvme_tcp` `_render_frame`
functions previously did a bare `print("Waiting for data…"); return` that
bypassed the navigation footer — that flat early return is gone; both now render
title + status + footer. `tests/test_startup_loading.py` (ordering + clear-on-
error, per engine) and new waiting/startup footer cases in
`tests/test_render_navigation.py` guard it.

**Mock/unit-verified only.** The actual startup appearance on var203 is
unverified; real-cluster verification is the owner's step (in the work-laptop
validation package). The invariant is now recorded in
[.claude/rules/tui-behavior.md](../.claude/rules/tui-behavior.md).

**Shutdown feedback is implemented too.** `cleanup()` in every engine prints
`Cleaning up N temporary monitors, please stand by...`
(`vast_common.cleanup_message` / `pending_monitor_count`) to stderr before the
slow, signal-blocking drain, so a multi-second quit is announced rather than
silent. `tests/test_cleanup_lifecycle.py` covers the message and the count.

**Still open, recorded here for follow-up:**
- **`signal.pthread_sigmask` Windows guard.** The cleanup fix's sigmask call is
  POSIX-only. `release.yml` builds `opstat-windows-x86_64.exe` but `test.yml` is
  Linux-only, so CI never exercises Windows. The call is `getattr`-guarded
  (absent on Windows → skipped), but the Windows build path is untested.

*(Resolved since first written: the exporter footer tests' order-dependence is
fixed — the `v41` fixture now constructs the `NFS4`/`HOSTVIEW` collectors
itself, and `pytest tests/test_render_navigation.py -k exporter` passes in
isolation.)*

## NVMe / block status

**Done (earlier passes):** keep-alive transport, single `/clusters/` fetch,
event-driven loop, corrected sample selection, drill re-poll throttle (15 s,
space forces), FR-C fabric-percentage correction, FR-A navigation (`[i]` VIP /
`[x]` exit / `[space]` refresh).

**Done (this pass) — drill ranking and batching, mock-proven, REAL-VMS
VALIDATION PENDING:**

- **Ranking by activity.** `enter_drill_mode` no longer head-slices the first
  8 objects by API order. Candidates (cnode/vip/blockhost) are ranked through
  `vast_drill.DrillSession.rank` with a single batched rank monitor carrying
  `BlockMetrics,read_req` + `write_req` (both cumulative counters, same
  family), scored per object by differencing over the monitor's own time
  series via `delta_rate_from_samples` — the bounding-samples derivation,
  because the newest bucket is partially filled. **topn is deliberately not
  used**: it has no protocol label ([D-007](decisions/D-007-topn-is-unusable-for-protocol-attribution.md)),
  so it would rank NVMe candidates by all-protocol traffic. Ranking is cached
  (5 min TTL); re-entry creates no rank monitors.
- **Batched display monitors.** The per-op monitor *group split is preserved*
  (a real constraint — see open-decision №3 below), but each group now covers
  every selected object in one monitor, validated splittable-by-`object_id`
  at entry and **falling back to the per-object layout** when the cluster
  refuses. Cleanup covers both layouts; `tests/test_nvme_drill.py`
  (11 tests) proves ranking, budgets, fallback and cleanup, and was proven
  failing against the pre-change code (busy cNode at mock index 10 of 12 was
  never selected; entry cost 65 calls).

Mock-measured budgets (counts transfer to a real cluster; wall-clock does not):

```text
                              before      after
  cnode drill entry calls     65          13    (re-entry in TTL: 10)
  drill queries per re-poll   64          8     (one per op group + proto)
  startup calls / monitors    9 / 8       9 / 8 (headline unchanged)
  cluster queries per tick    8           8     (family split is real)
```

**Not done / constraints:**
- The **headline** still queries 8 monitors per tick. Consolidation is blocked
  by the family-mixing probe result (open-decision №3): all-BlockMetrics-ops
  in one monitor is rejected at query time, and cross-family behavior is
  build-inconsistent.
- **Batch acceptance is now settled and is scope-dependent** — see
  [D-013](decisions/D-013-nvme-drill-batching-is-scope-dependent.md). A var203
  probe returned: `cnode` create/query/**splittable** PASS (ids `[4, 3]`, 120
  rows each); `vip` and `blockhost` create PASS, query PASS, **splittable
  FAIL** (0 rows per object, ids `[755,55,56,57]` and `[1,2,3,4]`). So a
  successful create proves nothing, and the run-time validation is load-
  bearing rather than defensive. `cnode` gets the batch layout on this
  cluster; `vip`/`blockhost` fall back to per-object and keep their old cost.
  Rank monitors are accepted at `cnode` scope (`read_req` deltas: object 4 =
  1062.353/s, object 3 = 0.0/s), so activity ranking is viable there.
  Regression coverage reproduces both unsplittable response shapes.
  **Round 3 measured the fallback cost and it forced a redesign** (see the
  round-3 block below and D-013): vip 43 monitors / 464 s, blockhost 41
  monitors / 720 s, zero usable rows from either.
- `/blockhosts/` is now **modeled in the mock from real probe evidence**
  (three var203 rounds: six objects, `name`/`nqn` fields, BlockMetrics echoed
  unrewritten, zero per-object rows on that build). The host drill has mock
  coverage for both the dead-scope contract (populations 6/10/100/500/1000,
  fixed create budget) and the live-scope case a future build would present.
- `--volumes` scoping unchanged (its VolumeMetrics monitors were already
  per-scope).

**Round-2 lab findings (var203 = VAST OS 5.4.6, cluster-adjacent host):**

- **Startup is 157 s of honest serial API time, not a hang and not network
  noise.** From the session's own log: `GET /clusters/` 26.6 s + 8 monitor
  creates ≈ 59.3 s (median ~2.2 s with 31 s and 14 s spikes) + the first 8
  queries ≈ 67.3 s. 17 serial calls, no duplicates, no re-probes, nothing
  reusable — per-call latency on this 5.4.6 cluster runs 2–38 s. The reported
  206 s included ~50 s of the *validator* dead-waiting for a "TOTAL IOPS"
  string the panel never renders (marker fixed). The only structural
  reduction is fewer startup monitors, which needs the merge-legality probe
  now in `probe_var203.py` (`merge.data_pairs`, `merge.data_plus_fabric`) —
  BLOCKED ON REAL-VMS EVIDENCE until it runs. No blind change was made.
- **Real defect: queued keys were dropped.** Keys are read between poll
  cycles, and a cycle blocks 30–80 s on this cluster, so several keys arrive
  in one read; the old handling fired ONE action per read and discarded the
  rest. The lab log proves it: after cNode entry, a buffered space consumed
  the queued `x` and `i` — the drill's monitors were never deleted until
  shutdown, VIP/HOST never entered, and every drill-window FAIL in the
  validator report follows from those swallowed keys. Fixed in **all five
  engines**: each input buffer now routes through the shared
  `vast_drill.dispatch_queued_keys` with a per-engine `_dispatch_key` —
  every queued key honored in arrival order, one action per key, one
  deferred repaint per batch, bindings unchanged. Regression tests replay
  the literal ` x<drill>` buffer per engine (`tests/test_key_dispatch.py`).
- **Real defect: NVMe had no drill-entry loading frame** — the one engine
  without it, and its entry blocks the longest (~2 min real). `c` froze the
  previous frame for the whole entry. Now routed through
  `vast_drill.with_loading_status` (status → render → work → clear), tested.
- **Shutdown `q` during a blocked poll** exits only after the current cycle
  completes (single-threaded by design) — the lab run's SIGTERM fallback at
  180 s was the validator's budget expiring mid-drain, not a hang; cleanup
  then ran and all 17 session monitors were verified deleted per-id.

---

## Real-VMS observations

Cluster: `var204.selab.vastdata.com`, VAST OS 5.5.0.1, 3 cNodes, 429 views,
38–39 tenants.

**Empirically observed on the real cluster** (not mock behavior):

- Mean REST call latency ~1.048 s; range 269 ms – 4135 ms.
- VMS publishes a **still-filling newest sample**: a cluster monitor row had
  2 of 46 metrics populated; a `ViewMetrics` row had exactly one
  (`read_md_iops__rate`), everything else null.
- Object-scoped families publish ~1/min: nine consecutive 5 s polls returned
  byte-identical payloads with the same newest sample timestamp.
- `NFS4Common` data counters can read **zero/null** even under load — a
  documented condition the engine compensates for.
- Monitor `prop_list` ordering in responses **differs from the requested
  order**; always index by the returned `prop_list`.
- Exporter latency varies ~2x run to run in both directions.
- Under load: SEQUENCE 1553 ops/s, PUTFH 1551, READ 992, WRITE 482,
  GETATTR 62, OPEN 6.1, CLOSE 6.1.

**Mock-only** (do not mistake for cluster behavior): scrape latency ~1 ms on
loopback, synthetic metric values, the planted "busy views" used to prove
ranking correctness.

---

## Round-3 lab findings (var203 = VAST OS 5.4.6, cluster-adjacent host)

The third unattended run validated the round-2 fixes and exposed the remaining
structural problems; all remediated locally in this pass, REAL-VMS VALIDATION
PENDING (round 4).

**Validated on the real cluster:**
- Startup UX: all three phases in order, footer present; dashboard at 166 s.
- cNode drill end-to-end: ranking, batch layout, splittable rows
  (`cnode-3-7`/`cnode-3-8` rendered), 21-call entry, forced space refresh.
- FR-C fabric separation on live screens - **FR-C is REAL-VMS VALIDATED**
  (Read 72.5 / Write 27.1 / Reclaim 0.4 = 100% workload; Fabric 80.3% of all
  activity, separate; fabric-only idle frame fabricates no workload share).
- Exact-id cleanup accounting; probe cleanup 8/8.
- Merge legality settled: `merge.data_pairs` and `merge.data_plus_fabric`
  both **rejected at query (HTTP 400 "can't mix properties")** - headline
  consolidation is ruled out on this build. Open-decision item closed.

**Real defects found, remediated locally this pass:**
1. **Unbounded per-object fallback** (vip 43 monitors/464 s, blockhost 41/720 s,
   zero rows) -> telemetry-evidence-gated: a scope whose rank/batch responses
   carry no per-object rows renders an explicit no-telemetry notice with zero
   display monitors; a scope with telemetry but no batch gets a bounded
   fallback (top-4 ranked, data-I/O groups only). Session monitor ceiling
   falls from ~206 to ~25.
2. **Keys unprocessable for 150+ s** (cnode exit_x FAIL): the loop re-fetched
   8 headline monitors every 5 s tick during a drill (2-38 s per call, always
   behind schedule). Now: queued input aborts the remaining queries of a
   cycle (`vast_common.input_pending`), and an open drill moves the headline
   to the 15 s drill cadence (space still forces everything).
3. **First paint blocked on the first query cycle** (~80 s of the 166 s):
   the dashboard now renders after monitor creation with the truthful
   "Gathering initial metrics" status on the footer-owning waiting frame;
   the first cycle starts immediately with keys live.
4. **Shutdown observed as a hang**: the round-3 "leak" (2413-2420 + 2618) was
   a still-running drain - 206 monitors at ~2 s each outlasted the 362 s
   observation window; ids 2413-2420 are the headline set (first eight
   creates), 2618 the last drill monitor. Primary fix is the fan-out
   elimination; the drain also now reports truthful `k/N` progress every ten
   deletes and continues past a raising delete.
5. **Validator toggle-state artifact**: `nav.p_does_not_exit FAIL` was the
   validator sending `c` while the drill was already open (its exit had
   failed) - the toggle closed it and the check blamed `p`. `p` is bound
   nowhere in the repository; cross-engine retired-alias tests prove it.
   **Correction (round 4):** the dashboard-state gating this section
   originally claimed as landed had NOT actually shipped - the patch script
   that carried it aborted before writing and the round-3 commit message
   overstated its content. It genuinely landed in the round-4 remediation,
   along with the `NO_TELEMETRY_MARKER` definition whose absence crashed the
   round-4 validator (the marker is now imported from `nvme_tcp`, so the
   contract cannot drift or dangle).

**Latency (round 3):** the NFS4Common known-µs reference read 0 again (idle
NFS4 in the probe window despite the loadgen), so BlockMetrics (546.5) and
host_view (now 6 series, 0.75-3.94, incl. `protocol="BLOCK"` on the same
traffic BlockMetrics measures) remain **UNVERIFIED**. The ~650x ratio between
host_view BLOCK latency and BlockMetrics read latency for concurrent block
traffic is recorded as suggestive (ms vs µs) but is NOT proof; no display
change was made.

## Round-4 lab findings and remediation (var203, cluster-adjacent host)

**Validated on the real cluster (round 4):** startup phases + footer with
first dashboard at **67.62 s** (was 166 s); cNode drill fully green - 13-call
entry, batch layout, real rows, 28.04 s, forced refresh, `x` exits; merge
probes re-confirmed rejected; probe cleanup 8/8.

**The VIP monitor storm, root-caused from the pid-3930933 log:** the VIP rank
scan created **189 monitors in pairs** (`rank_vip_0,2,...,376`) over 34
minutes. Cause: `DrillSession`'s discovered rank-chunk size was shared across
object types - the 2-cNode scan taught it "2", which then capped the 378-VIP
scan. The round-3 no-telemetry gate itself worked (bounded 4-create batch
probe, no per-object fan-out afterwards), but it sat *downstream* of the
population-scaled ranking.

**Remediation (this pass, mock-proven, REAL-VMS VALIDATION PENDING round 5):**

- Rank-chunk capability cached **per object_type**, and only when a larger
  size was actually refused (`was_cap`) - a small population can no longer
  poison another scope's scan. Literal regression: cnode(2) then vip(378)
  must produce <= 2 VIP rank monitors (fails on the round-4 code with 189).
- **Bounded capability pre-probe** (`DrillSession.probe_scope`, opt-in, one
  monitor over <= 8 ids) decides telemetry availability BEFORE ranking for
  scopes larger than the panel; verdict cached per session. Dead-scope
  discovery is O(1): populations 10/100/500/1000 all cost <= 3 creates
  (parametrized tests). Rank capability, telemetry availability and display
  splittability are now three explicitly separate questions (D-013).
- The no-telemetry notice actually **renders** now (drill panel and waiting
  frame both; with footer; `x` exits) - the round-3 notice was set but the
  mode-less state never reached the panel renderer.
- **Cleanup no longer depends on a writable terminal**: the round-4 leak of
  the eight headline monitors was the shutdown banner's EIO on a dead PTY
  killing `cleanup()` pre-drain. All cleanup output routes through
  best-effort `vast_common.emit_stderr`; tests prove a dead stderr still
  drains every owned monitor, end-to-end through the mock.
- **Validator**: `NO_TELEMETRY_MARKER` imported from the product (the
  round-4 NameError is structurally impossible); scenario exceptions now
  quit the session and run exact-id cleanup accounting via `finally`
  (unit-tested); nav `p` judged only with the drill confirmed open, else
  UNVERIFIED; `--round5` runs the narrow NVMe-only re-validation.
- Two **vacuous test filters** found and fixed: rank-monitor budget
  assertions matched against the calls-tuple status column and could never
  fail; the mock now records created monitor names
  (`created_monitor_names()`) and the assertions are real.

**Round-5 targets (NOT yet measured):** VIP/HOST entry in seconds with the
honest notice and <= 3 creates each; session total ~20 creates (round 4:
206); clean `q` with zero remaining ids.

## Round-5 lab findings (var203, `--nvme-only`, HEAD 4657b108)

**The monitor storm is gone - round 5 proves the round-4 architecture on the
real cluster.** Session creates fell **206 -> 20** (8 headline + cNode
rank/batch + one bounded probe create each for VIP and HOST + the nav-phase
cNode re-entry), cleanup deleted **20/20** with **NONE remaining** by per-id
GET, exit 0. Validated green: startup phases with dashboard at 78.16 s;
cNode entry (13 calls, batch layout, 2 ranked rows) and `x`; VIP and HOST
rendering the honest no-telemetry notice from **1 bounded probe create
each**; the full navigation matrix (`p` unbound, `v` not VIP); shutdown
frame before the drain.

**Two round-5 numbers were validator artifacts, not product behavior:**

1. **The "421s" / 93-98-call VIP and HOST entries were validator dead
   time.** `_drill_scenario` waited the full 420 s `--drill-budget` for a
   panel title the dead-scope path correctly never renders, and only then
   checked `NO_TELEMETRY_MARKER` - booking ~7 minutes of ordinary
   background polling against entries whose real work was one probe create
   (~3-4 calls). Fixed in round 5B: readiness completes on the first
   terminal state (`OpstatSession.wait_for_any_since`), so dead scopes
   report true elapsed time, true entry calls, true creates.
2. **The run's only red result, `nvme.cnode.manual_refresh FAIL "no
   effect", was a validator measurement defect.** The check slept a fixed
   6 s after space, but a single var203 call runs 2-38 s and
   `manual_refresh` runs the full headline pass *before* the forced drill
   query, so the first attributable log line can land 30-90 s after the
   keypress (rounds 3 and 4 passed the same check by latency luck). The
   product path - space -> `_dispatch_key` -> `manual_refresh` ->
   `fetch_drill_query(force=True)`, throttle bypassed - is correct and
   unchanged. Fixed in round 5B: the check polls the API log to a bounded
   `--refresh-deadline` and PASSes only on evidence cadence cannot forge
   (an aborted-and-restarted headline pass, or a query issued inside the
   15 s cadence/throttle window after a cleanly separated burst), judged
   from per-call issue times (completion stamp minus logged duration).
   Ambiguous activity reports UNVERIFIED, never FAIL.

## Round-5B closeout — MILESTONE CLOSED (var203, `main` @ `1aaa359`, 2026-08-16)

**Round 5B was the final real-VMS validation for this milestone. All 20
checks PASS; FAIL: none; UNVERIFIED: none.** Validator-only changes since
round 5; no production code changed. The monitor-storm remediation is
**accepted and closed**, and `main` is now the source of truth.

Observed, and independently re-verified against the raw pid-1924558 API log:

- **Monitor budget:** session creates **206 (round 4) -> 20 (round 5B)**;
  all 20 owned monitors deleted (per-id GET, 404 = gone); remaining **NONE**.
  The 20 reconcile exactly: 8 headline, cNode rank + 4 batch, 1 VIP probe,
  1 HOST rank, nav-phase cNode rank + 4 batch — every one deleted on its
  own path (drill exits, probe/rank teardown, shutdown drain).
- **cNode:** entry 13 calls / 5 monitors / batch layout / 2 ranked rows;
  no per-object fan-out anywhere in the log; `x` exits.
- **Manual refresh: PASS with API evidence.** The raw log contains the
  unforgeable signature: the in-flight cadence headline pass was aborted
  after two queries by the pending space (issued 23:35:20-21), and the
  dispatched refresh then ran the full headline pass (23:36:03-23:36:32)
  followed immediately by the forced drill queries — a truncated-then-
  restarted pass that only a dispatched key can produce.
- **VIP dead scope:** bounded — `GET /vips/` + one probe create/query/
  delete (4 calls, 1 create, 21 s to the honest notice). Zero ranking of
  the 378-VIP population.
- **HOST dead scope:** bounded — `GET /blockhosts/` + one rank create/
  query/delete, no batch attempt, no fan-out, honest notice.
- **Navigation:** canonical footer bindings; `p` unbound and does not exit
  a confirmed-open drill; `v` does not open VIP.
- **Shutdown:** cleanup frame before the drain, exit 0 in 28.34 s.
- Whole session: 140 API calls (95 queries, 20 creates, 20 deletes,
  5 inventory/cluster GETs).

**Round-5 artifacts, settled:** the round-5 "421 s" VIP/HOST figures were
validator dead time, not product latency, and the round-5 manual-refresh red
result was the validator's fixed 6-second observation window — both fixed in
5B and closed.

**Measurement footnotes (recorded so they are not mistaken for defects
later; conclusions above were verified from the raw log, not these rows):**

- The PASS detail *"query issued -0.9s after the keypress"* is a timestamp
  reconstruction artifact: issue time = second-granularity completion stamp
  minus the logged call duration, compared against a sub-second wall clock
  inside the judge's designed +/-2 s slack. Small negative values near zero
  are expected. **Not a product timing defect.** (The specific query that
  row cites was in fact part of the in-flight cadence burst — a var203
  mid-burst latency gap defeated the throttle-window proof's clean-burst
  precondition — but the verdict is independently correct: the
  abort/restart evidence above is in the same log.)
- The HOST row's "2 calls, 0 creates, 29s" under-counted its own window:
  `NO_TELEMETRY_MARKER` is scope-agnostic, and residual VIP-notice text
  during the `x`->`h` transition satisfied the wait before the host work
  ran. True host entry cost from the log: 4 calls, 1 create (rank 2898,
  deleted 1 s later), ~35 s — still inside every acceptance bound.

Both footnotes are validator display/attribution imprecision only; optional
polish, not milestone work.

## Test architecture

603 tests, green on Python 3.8 and the current interpreter, 0 skipped
(re-derive with `./scripts/validate.sh`; the gate's collection floor is 465).

| Suite | Tests | Covers |
|---|---|---|
| `test_render_navigation.py` | 150 | Footer present in every mode and width; frame never exceeds terminal; waiting/startup frames; **FR-A canonical navigation contract across all five engines**; exporter drills (self-initializing fixture — passes in isolation) |
| `test_nfs41_discovery.py` | 63 | Catalog reader, op detection, concept scan, prop probe, evidence-gated panels, all discovery report sections |
| `test_drill_semantics.py` | 38 | Partial-newest-sample defects, ranking correctness, entry call budgets, cNode batching, throttle |
| `test_nfs4_native.py` | 29 | Exporter parsing, warm-up, rate/latency derivation, counter reset, host_view filtering, cost isolation |
| `test_smb_s3_drill.py` | 28 | SMB/S3 view/tenant/bucket drill port: entry budget, ranking >32, rank cache, throttle, force bypass, batch fallback, cleanup (success + query-error), VIP topn/filtering/topn-only fallback, drill+startup loading frames |
| `test_vast_common.py` | 23 | Shared helpers |
| `test_api_efficiency.py` | 22 | Keep-alive reuse and retry, startup budget, merged-monitor budgets and fallbacks |
| `test_s3_helpers.py` | 21 | S3 helpers incl. FR-B ms/µs formatter boundaries |
| `test_drill_loading.py` | 19 | Loading interstitial ordering; rebuilt VIEW drill |
| `test_startup_loading.py` | 15 | Three-phase startup interstitial: frame before each blocking step, clear on error, host-not-cluster first message, all five engines |
| `test_wizard.py` / `test_opstat_cli.py` | 14 / 13 | Wizard and CLI |
| `test_nvme_tcp.py` | 13 | FR-C fabric percentages (literal values), drill throttle + forced refresh, FR-A footer, FR-B latency passthrough / zero-collapse |
| `test_nvme_drill.py` | 54 | NVMe drill ranking (busy cNode planted at index 10 of 12), entry/re-poll budgets, batch splitting, per-object fallback, rank cache, cleanup in both layouts, dead-scope O(1) probe at populations up to 1000, no-telemetry notice rendering, and the round-5B lab-validator measurements (wait-for-any readiness, forced-refresh judge, lifecycle) |
| `test_globals_hygiene.py` | 10 | AST check: no function assigns an ALL_CAPS module global without `global` |
| `test_tui_layout.py`, `test_openmetrics.py`, `test_cleanup_lifecycle.py`, `test_smb_helpers.py`, `test_nfs_v3_helpers.py` | 9 / 8 / 8 / 7 / 3 | Layout, export, cleanup lifecycle (signal-blocked drain, guard ordering, shutdown message), protocol helpers |

### The mock

`tests/mock_vms.py` reproduces real cluster behavior deliberately:
partially-filled newest buckets, cumulative counters, 429 views with the busy
ones planted deep (so head-slicing fails), an object-id cap, mixed-family
rejection, per-cNode hostnames differing only in a trailing digit, and
sub-microsecond latencies for `sequence`/`getfh`/`putfh`.

It also records every request, which makes it the measurement instrument for
API-call accounting.

### Mutation / regression proofs

Several fixes were proven by running the new test against the *previous*
commit in a worktree and confirming it failed. The bandwidth-scoping regression
(`test_bandwidth_survives_a_monitor_that_mixes_metric_families`) was verified
failing on `cb6e5f8` with the literal message *"read bandwidth lost to the
mixed scoring"*.

### Commands

The gate is one command and it is the one to use:

```bash
./scripts/validate.sh          # current Python + the mandatory 3.8 floor
```

It fails loudly when `openssl` is absent rather than letting 171 mock-backed
tests skip silently, and it enforces a minimum collection count. Individual
invocations, for iteration:

```bash
python3 -m pytest -q                                    # full suite
python3 -m pytest tests/test_nfs4_native.py -q          # one suite
python3 -m pytest -q -k "loading or footer"             # by keyword
uv run --python 3.8 --no-project --with pytest -- python -m pytest -q   # 3.8 floor
```

The full contract is in [../AGENTS.md](../AGENTS.md#command-and-test-contract).

---

## Known defects / unfinished work

Verified against the repository at the time of writing. **Historical
record** — the live backlog with current priorities is
[FR_BACKLOG.json](FR_BACKLOG.json) (rule:
[../.claude/rules/backlog.md](../.claude/rules/backlog.md)); open items
below are tracked there by FR number.

### Outstanding

1. ~~NVMe ranking and drill batching real-cluster confirmation~~ **CLOSED by
   rounds 4–5B** ([D-013](decisions/D-013-nvme-drill-batching-is-scope-dependent.md)):
   cNode drill fully validated (13-call entry, batch layout, ranked rows,
   API-evidence-verified forced refresh, `x`); vip/blockhost dead scopes
   bounded (probe/rank verdict, honest notice, no fan-out); session monitor
   budget 206 -> 20 with exact-id cleanup. See the Round-5B closeout.
2. **NVMe startup cost (~60-78 s real in rounds 4-5B).** Honest serial API
   time; no duplicated work to remove. The one structural lever — fewer
   startup monitors — is **closed on this build**: the round-3 merge-legality
   probes were rejected at query time (HTTP 400 "can't mix properties"), so
   headline consolidation is ruled out on var203/5.4.6 (L1 to revisit on new
   evidence from another build).
2b. ~~Queued-key drop~~ **RESOLVED in all five engines** — shared
   `vast_drill.dispatch_queued_keys` + per-engine `_dispatch_key`;
   multi-key buffers regression-tested per engine
   (`tests/test_key_dispatch.py`, 25 tests).
3. **NFSv3 VIEW drill** still uses `ViewMetrics`. The NFSv4.1 rebuild on
   `host_view` may be portable — `host_view` carries `protocol=NFS3`.
4. **Delegation diagnostic** not implemented. Needs a path-entry interaction.
5. **Synchronous exporter scrape stalls the TUI** for 1.2–2.4 s on entry. A
   loading frame makes it legible, but whether that is acceptable in practice
   is unvalidated. Background threading was deliberately deferred.
6. **NVMe monitor-family constraint unverified.** The README's claim that VMS
   cannot mix `BlockMetrics`/`VolumeMetrics`/`ProtoMetrics` has not been
   re-tested with the probe-and-fallback pattern used successfully elsewhere.
7. **Rebuilt NFSv4.1 VIEW drill shows only views with current NFS4 traffic**,
   not every configured view. Deliberate trade; the panel says so.
8. **One unreproduced test flake.** `test_smb_merged_monitor_single_query_per_refresh`
   failed once on Python 3.8 during a full run and never again across three
   consecutive full runs plus an isolated run. Suspected mock TLS startup
   transient. Unresolved.

### Resolved (do not re-open as defects)

- Navigation footer missing in NFSv4.1 drill modes — fixed, 122 tests guard it.
- Loading interstitial before blocking drill entry — fixed via shared helper.
- cNode hostnames rendering identically — fixed with an ID column and tail
  truncation.
- Sub-microsecond latencies showing `0 µs` — fixed.
- "v4 clients" terminology — renamed to "v4 hosts".
- Cluster vs cNode reconciliation — proven exact (100.00%).
- NFSv4.1 drill-down never displaying data (missing `global DRILL_MONITORS`) —
  fixed; an AST hygiene test prevents recurrence.
- SMB/S3 view/tenant/bucket ranking on the 32-object chunked serial scan —
  ported to `vast_drill.DrillSession` (entry 18 → 7 calls; rank cache on
  re-entry; throttled re-poll), real-VMS validated. `tests/test_smb_s3_drill.py`
  guards it.
- **Monitor cleanup interrupted mid-drain** (leaked an `adhoc_opstat_*` monitor
  on SIGTERM/SIGHUP during the slow drain, no warning; seen twice on var203) —
  **fixed.** Root cause: `cleanup()` set its `_CLEANED_UP` guard *before* the
  drain, and a re-entrant signal `sys.exit`ed out of the loop. Fix:
  `vast_common.drain_monitors` now blocks SIGINT/SIGTERM/SIGHUP for the drain's
  duration (deferred, not lost — clean exit still happens after every monitor is
  gone), and every engine sets `_CLEANED_UP` only after the drain completes so
  an interrupted cleanup is retried by the atexit backstop. No threads.
  `tests/test_cleanup_lifecycle.py` guards it. **Caveat:** `SIGKILL` cannot be
  blocked, so a hard kill (e.g. a harness timeout killing the process group)
  can still orphan a monitor — verify cleanup by exact session ids after
  automated runs.
- **NVMe fabric percentage (FR-C)** — Fabric/admin ops were in the workload-mix
  denominator, shrinking real read/write percentages (an 80/20 read/write mix
  rendered 16%/4% under fabric load). Fixed: read/write/reclaim are now shares
  of the actual I/O workload (fabric excluded); Fabric is shown separately as a
  share of all activity. `tests/test_nvme_tcp.py` covers it with literal values.
- Bandwidth lost to mixed-family row scoring — fixed; regression test proven
  against the prior commit.

---

## Decisions already made

These have moved to **[decisions/](decisions/)**, where each carries the
evidence that settled it and the condition that would justify reopening it.
Reopening one is an L1 decision: new evidence plus explicit approval.

| # | Decision |
|---|---|
| [D-001](decisions/D-001-monitor-api-and-exporter-are-separate-paths.md) | The monitor API and the Prometheus exporter are separate data paths |
| [D-002](decisions/D-002-nfs4metrics-counters-are-cumulative.md) | `Nfs4Metrics` counters are cumulative; rates come from differencing |
| [D-003](decisions/D-003-nfs4metrics-latency-is-microseconds.md) | `Nfs4Metrics` latency is microseconds |
| [D-004](decisions/D-004-heavy-exporter-endpoints-off-the-refresh-path.md) | `basic` never on the 5-second path; `all` never requested |
| [D-005](decisions/D-005-native-telemetry-is-an-on-demand-throttled-drill.md) | Native telemetry is an on-demand throttled drill, scraped synchronously |
| [D-006](decisions/D-006-host-view-is-the-nfs4-attribution-source.md) | `host_view` is the NFSv4 host/view attribution source |
| [D-007](decisions/D-007-topn-is-unusable-for-protocol-attribution.md) | Top-N is unusable for protocol-specific attribution |
| [D-008](decisions/D-008-delegations-are-a-file-scoped-diagnostic.md) | Delegations are a file-path-scoped diagnostic, never a panel |
| [D-009](decisions/D-009-panels-are-evidence-gated.md) | Panels are evidence-gated; derived values labelled; zero ≠ unavailable |
| [D-010](decisions/D-010-merged-monitors-are-probe-validated-with-fallback.md) | Merged headline monitors are probe-validated with fallback |
| [D-011](decisions/D-011-newest-complete-sample-scoped-per-family.md) | Newest *complete* sample, scoped per metric family |
| [D-012](decisions/D-012-terminology-v4-hosts.md) | Terminology is "v4 hosts" |
| [D-013](decisions/D-013-nvme-drill-batching-is-scope-dependent.md) | NVMe drill batching is scope-dependent and must be response-validated |

## Decisions still open

These are **not** settled and do not belong in `decisions/` until they are.

1. Whether the multi-second synchronous scrape is acceptable, or whether
   background threading is warranted. Needs real-cluster judgement.
2. Whether to port the exporter-backed view rebuild to NFSv3.
3. ~~Whether NVMe's monitor split is a genuine VMS constraint or historical.~~
   **Probed on var203 (2026-08-14):** the split is substantially *required*, but
   for a subtler reason than the code comment states. Putting all BlockMetrics
   ops in one monitor is **rejected at query time** ("can't mix pr[operties]") —
   counters and rate/avg props cannot share a monitor — so the per-op BlockMetrics
   split stands. Cross-family results were inconsistent/build-specific
   (BlockMetrics+ProtoMetrics queried fine with data; ProtoMetrics+VolumeMetrics
   rejected "metrics not …"; all-three queried fine), so cross-family
   consolidation is **not clearly safe**. Decision: preserve the split, optimize
   the drill instead. The comment claiming "BlockMetrics and ProtoMetrics cannot
   be mixed" is imprecise and should be reworded when the engine is next touched.
   Any future merge must be probe-validated with fallback (D-010 philosophy) and
   never assumed across builds.
4. Whether to implement the delegation lookup, and with what interaction.
5. ~~Whether SMB/S3 should adopt `vast_drill` wholesale or incrementally.~~
   **Settled:** view/tenant/bucket drills ported to `vast_drill.DrillSession`;
   cNode and S3 VIP left on their existing paths deliberately. See SMB/S3 status.
6. Whether the startup/loading-UX pattern (status frame before blocking startup)
   should be promoted to a general invariant and adopted by the NFS/NVMe engines.

---

## Real-VMS validation cookbook

Never put a password on the command line. Use the environment:

```bash
export VAST_TOKEN=...        # preferred
# or
export VAST_PASSWORD=...
```

### NFSv3

```bash
./opstat --nfs --version=3.0 --vms <VMS_HOST> --user admin --log-api-calls
```

Success: one `POST /monitors/` at startup and **one** `GET /monitors/<id>/query/`
per refresh in `/tmp/opstat-api-nfs-v3-*.log`. Health panel and COMBINED footer
show a real `GB/s` under load. `v` drill entry completes in seconds, not ~47 s.

### NFSv4.1

```bash
./opstat --nfs --version=4.1 --vms <VMS_HOST> --user admin --log-api-calls
```

Success:
- Cluster view: one monitor query per 5 s tick, **no `prometheusmetrics`
  request at all**.
- `c` / `t` / `v`: a *"Loading the … please stand by…"* frame appears
  immediately, before any delay.
- `4`: loading frame, then a warm-up panel; `space` completes the warm-up and
  populates all four panels. `SEQUENCE`/`GETFH` show sub-microsecond values;
  the three cNode rows are distinguishable by ID and hostname tail.
- `h`: only `protocol=NFS4` rows, ranked by IOPS.
- At most **two** `GET /api/prometheusmetrics/basic` per minute in the drill.
- On quit, no `adhoc_opstat_*` monitors remain on the VMS.

### Discovery

```bash
./opstat --nfs --version=4.1 --vms <VMS_HOST> --user admin \
  --discover-metrics --no-color > nfs41-discovery.txt 2>&1
```

Read-only; creates only temporary monitors and deletes them. Takes a few
minutes and pauses ~10 s between two exporter scrapes. Writes a full evidence
file to `/tmp/opstat-nfs41-discovery-<vms>-<pid>.txt` — **that file, not the
console output, carries the evidence.**

Run the NFSv4.1 load generator throughout, or the interrogation window will be
idle and rates cannot be measured.

### Load generators

Tracked in `scripts/` — see [../scripts/README-systemd.md](../scripts/README-systemd.md).
Per-protocol scripts and systemd units for NFS v3, NFS v4.1, SMB, NVMe-oTCP
and S3.

### Artifacts worth retaining

- `/tmp/opstat-api-*.log` from `--log-api-calls` (body cap 32 KB; override with
  `OPSTAT_API_LOG_BODY_CHARS`).
- `/tmp/opstat-nfs41-discovery-*.txt` from `--discover-metrics`.
- Terminal captures of any panel that looks wrong.

---

## Feature backlog (owner-requested FRs)

**Historical record of the refactor-era letter FRs (FR-A..FR-D).** The
live numeric backlog is [FR_BACKLOG.json](FR_BACKLOG.json); its
`legacy_frs` map records these outcomes (FR-B's remaining work is FR3).

Lightweight backlog — no external issue tracker. Status is updated in place as
each FR is folded into engine work.

### FR-A — Standardize navigation shortcut keys
Same conceptual action → same key, label, capitalization, ordering, separators
and style across all engines; protocol-unique controls appended **after** the
common set; no control lost to width truncation; narrow-terminal behavior
preserved. Common concepts: `[q]` Quit, `[o]` Ops, `[l]` Lat, `[n]` Name,
`[c]` cNode, `[v]` View, `[t]` Tenant, `[i]` VIP, `[x]` Exit drill,
`[space]` Refresh. NFSv4.1 keeps unique `[4]` native telemetry and `[h]` v4
hosts. **VIP standardizes on `[i]`, never `[v]`.** No key means two concepts on
different engines. Fold in per-engine as engines are touched; build shared
helpers where they reduce divergence safely.
- **Status: DONE locally (all five engines), including the width fix below;
  real-VMS keys/legend validated; wrapped-footer appearance re-check pending.**

  **Real-use regression (found by the owner, fixed):** the shared legend was
  rendered as ONE line and truncated to the frame width, so an ordinary
  laptop terminal showed only `[q] Quit |[o] Ops |[l] Lat` while c/v/t/x
  still *worked* but were undiscoverable — display-only, width-dependent.
  `vast_drill.nav_legend_lines()` now wraps the legend across lines (greedy
  packing, canonical order preserved, protocol extras still last); all five
  engines render every supported control at any width, and a control is only
  ever shortened by extreme narrowness, never silently omitted. Guarded by
  width-parametrized tests (200/100/80/60) plus a literal repro of the
  q/o/l-only field report at 28 columns.

  The second lab run validated the rest of the contract on real screens:
  legend text, `[i]`/`[x]`/`[space]` live, retired `v`/`p` dead
  (`nav.*` all PASS).
  The contract lives in code: `vast_drill.CANONICAL_CONTROLS` +
  `nav_controls()` (canonical order/labels, protocol extras appended, raises
  on a non-canonical key) + `nav_legend()` (one shared renderer). Every
  engine's `_NAV_CONTROLS` is built through it. NVMe was normalized first
  (`i`/`x`/`space`; stale `v`/`p` help and README text corrected); NFSv4.1's
  `[4]`/`[h]` moved after the common set; NFSv3's htop-style legend
  (`` `q`uit ``, `[spc]refresh`, `x=cluster`) was restyled to the bracket
  contract with keys unchanged, and its footer now truncates legibly at
  narrow widths. Guarded by the FR-A section of `test_render_navigation.py`
  (canonical order/labels per engine, VIP-never-v, exit-never-p, shared
  triple, legend rendering).

### Navigation deviations (deliberate, documented)
- **S3 `[b]` Bucket** is protocol-specific and appended after the common set
  (previously between `[c]` and `[t]`). It is deliberately *not* presented in
  the `[v]` View slot even though the bucket drill is ViewMetrics-backed: the
  key a user presses is `b`, and the label must match the key.
- **NFSv3 has no `[n]` Name sort** (its sorts are `r`pc/`o`ps/`l`at/`w`ork);
  the contract standardizes what exists rather than inventing controls.
  `[r]`/`[w]` are NFSv3-specific and appended after the common set.
- **NVMe `[r]` Reset stats** reuses NFSv3's `[r]` key for a different concept.
  Pre-existing collision, both protocol-specific (never co-visible), kept.

### FR-B — Latency unit correctness and display
Verify each latency's **source unit from code/API evidence** (VAST returns
s / ms / µs / ns / cumulative sums by endpoint), check every conversion
mathematically, distinguish native vs derived, avoid double conversion, never
render `0` for a meaningful sub-precision value. Prefer ms for user-facing
latency where it reads better; keep µs where values are consistently sub-ms.
Make the unit obvious in labels; one consistent policy across protocols where
practical; unit tests with literal ms/µs/ns source values.
- **Status: AUDIT COMPLETE (all five engines + exporter paths); two local
  defects fixed; unproven source units listed for the var203 package.**

  Full audit of every user-visible latency path. No ns-sourced value exists
  anywhere in the code, so no ns conversion exists (or needs tests). The two
  display defects found were fixed; no *source-unit* conversion was changed
  anywhere.

  | Engine / panel | Source (family, field) | Native unit | Evidence | Conversion → display |
  |---|---|---|---|---|
  | NFSv3 op table + combined | `NfsMetrics,nfs_{op}_latency__avg` (monitor) | µs | Monitor-API convention; real-VMS screens validated by owner | none → `N µs` (unit-labeled columns) |
  | NFSv4.1 dashboard | `ProtoMetrics NFS4Common *_latency__avg` + NfsMetrics supplement | **µs, proven** | [D-003](decisions/D-003-nfs4metrics-latency-is-microseconds.md) cross-family 0.92 agreement | `format_latency_us` auto µs/ms |
  | NFSv4.1 native drill | `Nfs4Metrics` `_sum`/`_count` deltas | **µs, proven** | [D-002](decisions/D-002-nfs4metrics-counters-are-cumulative.md)/[D-003](decisions/D-003-nfs4metrics-latency-is-microseconds.md) | derived avg = Δsum/Δcount → `format_latency_us` |
  | NFSv4.1 hosts/views drills | exporter `host_view` `latency` gauge | **UNPROVEN** — assumed µs (`latency_us` field) | none; no unit metadata in the exposition | none → `format_latency_us` |
  | SMB rows + drills | `ProtoMetrics SMBCommon *_latency__avg`, View/TenantMetrics latency | µs (assumed) | Same ProtoMetrics family as the proven NFS4Common case; not per-op proven | none → `format_latency_us` |
  | S3 rows + health | `ProtoMetrics S3Common` / `S3Metrics *_latency__avg`, ViewMetrics | µs (assumed) | As SMB | `format_latency_ms`: ms at ≥5 µs, µs below (**fixed** — was `0.00 ms` for sub-5 µs) |
  | NVMe op table + drills | `BlockMetrics`/`VolumeMetrics *_latency__avg` | µs (assumed) | Monitor-API convention (consistent with D-003); **unconfirmed on-cluster** | none → `format_latency_us` |
  | NVMe combined header | weighted read/write `avg_us` | as above | — | `format_latency_us` (**fixed** — was hand-`/1000` `"%.2f ms"`, so sub-10 µs read `0.00 ms`) |

  Zero-vs-unavailable: every formatter returns `-` for no-data, and
  counter-based ops gate latency on ops evidence (a first poll shows `-`, not
  a fake value) — covered by literal tests in `test_nvme_tcp.py`,
  `test_s3_helpers.py`, `test_tui_layout.py`.

  **Unproven source units (var203 package items):** `host_view` `latency`
  gauge; the NVMe BlockMetrics/VolumeMetrics µs assumption; SMB/S3
  per-op corroboration. Each is checkable by comparing against a known-µs
  metric on the same cluster under load. Until then those displays are
  UNVERIFIED, not wrong — behavior unchanged.

  The second lab run attempted the cross-check and it did **not** settle
  anything: the known-µs reference (`NFS4Common read_latency__avg`) read `0`
  in the probe window and `host_view` again published **zero** latency
  series, so `BlockMetrics read_latency__avg = 515.61` stands alone — a
  plausible µs magnitude, and magnitude alone is not proof. Units remain
  UNVERIFIED; the probe needs NFS4 load active during its window.

### FR-C — Block fabric percentage correction
BLOCK/NVMe: Fabric activity must **not** be in the primary workload-mix
denominator (READ/WRITE/metadata only). Fabric stays visible as its own
metric/bar. Verify current math first; tests proving the old denominator is
wrong and the new one is right.
- **Status: REAL-VMS VALIDATED (var203, second lab run).** The live BLOCK
  frame under block load showed Read 72.5% / Write 27.1% / Reclaim 0.4%
  (sums to 100.0%) with Fabric shown separately as `80.3% of all activity`,
  and the idle warm-up frame showed 0/0/0 workload with Fabric 100% — no
  fabricated workload share from fabric-only traffic. Latency presentation
  behaved as designed (`609 µs`, `1.30 ms`, `15 µs` — auto-scaled, labeled).
  `block_workload_mix` excludes fabric/admin from the read/write/reclaim
  denominator; Fabric renders as a separate `of all activity` indicator.
  Render audit confirmed the math and the panel agree end-to-end. There is no
  "metadata" workload category in the BlockMetrics op set (data / reclaim /
  fabric / admin only), so no metadata share is shown — inventing one would
  fabricate telemetry. `tests/test_nvme_tcp.py` covers read-only/write-only/
  mixed/mixed+large-fabric/reclaim/zero-workload+fabric/idle with literal
  values. Real BLOCK screen check is in the work-laptop package.

### FR-D — Testing / quality build-in
The repo has a real suite and `./scripts/validate.sh`; unit testing is **no
longer missing**. Goal is coverage expansion around legacy paths as they are
refactored: every change runs the full gate (3.8 + current, zero skips,
openssl suites actually run); new defects get regression tests; API efficiency,
TUI render/nav, and metric/unit semantics are tested dimensions; the mock does
not replace real-VMS validation for cluster-only semantic questions.
- **Status:** ongoing by design. This phase: `test_nvme_drill.py` (ranking/
  batching/fallback/cleanup, proven failing pre-change), FR-A contract tests
  across all engines, FR-B literal-unit tests, exporter-fixture isolation
  fix, collection floor raised 395 → 465 (suite at 504). The gate is the
  quality bar; coverage grows with each legacy path touched.

## How to resume

1. Read [../AGENTS.md](../AGENTS.md) — the behavioral contract.
2. Read [decisions/README.md](decisions/README.md) and skim the records. Those
   are settled; do not redesign around them.
3. Read this document for branch state and open work.
4. Inspect the repository — `git log --oneline origin/main..HEAD`,
   `git status`, `git rev-parse HEAD` vs origin. Note unpublished commits.
5. Run `./scripts/validate.sh`. Expect ~603 passing on current Python **and**
   3.8, with nothing skipped.
6. Pick the next item from [FR_BACKLOG.json](FR_BACKLOG.json) — the
   authoritative backlog and priority order.
7. Read the relevant [.claude/rules/](../.claude/rules/) file before working in
   its area.

---

## Historical discovery artifacts

The `--discover-metrics` reports and `--log-api-calls` captures that established
the NFSv4.1 findings were written to `/tmp` and downloaded on the originating
MacBook. **They do not exist in this repository and will not be present on
another machine.**

Their conclusions are preserved in [decisions/](decisions/), above, and in
module docstrings
(`nfs4_native.py`, `vast_discovery.py`) and test docstrings, which quote the
literal payload shapes observed. Regeneration is only necessary to re-validate
against a different cluster or VAST OS version — the discovery tooling is
committed and reproduces the analysis on demand.

Specifically **not** preserved: raw report files, the 70 MB OpenMetrics export
from an NFSv3 run, and the scratch benchmark harness used for PTY-driven
measurement (`drive.py` and similar lived in a session scratchpad, not the
repo). The benchmark numbers they produced are recorded above.

---

## Bootstrap prompt for a fresh Claude session

Copy and paste this into a new session on the work computer:

```text
You are resuming an in-progress refactoring effort on the opstat repository.

Before doing anything else:

1. Read AGENTS.md in the repository root. It is the behavioral contract and it
   governs. CLAUDE.md imports it and adds Claude-specific routing.
2. Read docs/decisions/README.md and the D-nnn records. Those are settled.
3. Read docs/REFACTOR_HANDOFF.md end to end for branch state and open work.
4. Read the .claude/rules/ file covering any area you expect to touch.
5. Inspect the repository yourself — do not trust the handoff over the code:
   - current branch, HEAD SHA, and whether HEAD is ahead of origin
   - git log --oneline origin/main..HEAD
   - git status
   - which engines import vast_drill / nfs4_native
   - the test files present and how many tests collect
6. Run ./scripts/validate.sh and report the exact counts it prints, for both
   interpreters. Do not substitute a bare pytest run.
7. Check whether the handoff has gone stale. Report any place where the
   document and the repository disagree.
8. Read the test suites before reading the implementation. The tests encode
   real-cluster defects and are the best statement of intended behavior.

Then report back to me with:

- a summary of what this branch has accomplished, in your own words
- the current architecture: which module owns what
- the performance invariants you must not break, and why each exists
- which decisions are settled and must not be casually reopened
- the outstanding work items, and which you would pick first, with reasoning
- anything in the handoff you could not verify against the repository

Demonstrate actual understanding rather than restating the document. If you
cannot verify something, say so explicitly.

Do not modify any code until I have approved your understanding. Do not commit,
push, merge, tag, or open a pull request unless I explicitly ask.
```
