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

## Repository / branch baseline

| Item | Value at time of writing |
|---|---|
| Branch | `refactor/tui-performance` |
| Base | `main` @ `77549f06` (unchanged throughout) |
| Commits ahead of `main` | 22 |
| HEAD | `9c6d6e5` — *NFSv4.1: loading interstitial, exporter-backed VIEW drill, presentation fixes* |
| Published on origin | up to `9c091d0`; **`9c6d6e5` was local-only when this was written** |
| Tests | 400 collected, all passing on Python 3.8 and 3.12 |

Determine the live state rather than trusting the table:

```bash
git rev-parse --abbrev-ref HEAD
git rev-parse HEAD
git rev-parse origin/refactor/tui-performance
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

**Not done:** both still rank view/tenant candidates with the **32-object
chunked serial scan** (`_DRILL_PROBE_LIMIT` in `smb.py` and `s3.py`) — the same
path that cost 47 s on NFSv3. Porting them to `vast_drill.DrillSession` is a
direct lift; neither imports `vast_drill` yet.

Neither has been validated against the real cluster during this effort.

---

## NVMe / block status

**Least refactored engine.**

**Done:** keep-alive transport, single `/clusters/` fetch, event-driven loop,
corrected sample selection.

**Not done:**
- No candidate ranking — `nvme_tcp.py` head-slices `/volumes/`
  (`[:_MAX_DRILL_OBJECTS]`), the exact defect fixed elsewhere.
- Per-object drill monitors, not batched.
- Does not import `vast_drill`.

**Known API cost:** NVMe dominates every measurement — ~467–507 calls in a 30 s
session versus 19–27 for the NFS engines, from 8 cluster monitors queried per
refresh. `NVMe_TCP_README.md` documents that VMS cannot mix
`BlockMetrics`/`VolumeMetrics`/`ProtoMetrics` in one monitor, so the split is at
least partly a real constraint — but it has not been re-verified, and the
ranking and per-object drill issues are independent of it.

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

## Test architecture

400 tests, green on Python 3.8 and 3.12.

| Suite | Tests | Covers |
|---|---|---|
| `test_render_navigation.py` | 122 | Footer present in every mode and width; frame never exceeds terminal; other engines guarded |
| `test_nfs41_discovery.py` | 63 | Catalog reader, op detection, concept scan, prop probe, evidence-gated panels, all discovery report sections |
| `test_drill_semantics.py` | 38 | Partial-newest-sample defects, ranking correctness, entry call budgets, cNode batching, throttle |
| `test_nfs4_native.py` | 29 | Exporter parsing, warm-up, rate/latency derivation, counter reset, host_view filtering, cost isolation |
| `test_vast_common.py` | 23 | Shared helpers |
| `test_api_efficiency.py` | 22 | Keep-alive reuse and retry, startup budget, merged-monitor budgets and fallbacks |
| `test_s3_helpers.py` | 20 | S3 helpers |
| `test_drill_loading.py` | 19 | Loading interstitial ordering; rebuilt VIEW drill |
| `test_wizard.py` / `test_opstat_cli.py` | 14 / 13 | Wizard and CLI |
| `test_globals_hygiene.py` | 10 | AST check: no function assigns an ALL_CAPS module global without `global` |
| `test_tui_layout.py`, `test_openmetrics.py`, `test_smb_helpers.py`, `test_nfs_v3_helpers.py` | 9 / 8 / 7 / 3 | Layout, export, protocol helpers |

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

Verified against the repository at the time of writing.

### Outstanding

1. **SMB and S3 view/tenant ranking** still use the 32-object chunked serial
   scan. Direct port to `vast_drill.DrillSession`.
2. **NVMe ranking and drill batching.** Head-slices `/volumes/`; per-object
   drill monitors; ~467+ calls per 30 s session.
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

## Decisions still open

These are **not** settled and do not belong in `decisions/` until they are.

1. Whether the multi-second synchronous scrape is acceptable, or whether
   background threading is warranted. Needs real-cluster judgement.
2. Whether to port the exporter-backed view rebuild to NFSv3.
3. Whether NVMe's monitor split is a genuine VMS constraint or historical.
4. Whether to implement the delegation lookup, and with what interaction.
5. Whether SMB/S3 should adopt `vast_drill` wholesale or incrementally.

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

## How to resume

1. Read [../AGENTS.md](../AGENTS.md) — the behavioral contract.
2. Read [decisions/README.md](decisions/README.md) and skim the records. Those
   are settled; do not redesign around them.
3. Read this document for branch state and open work.
4. Inspect the repository — `git log --oneline origin/main..HEAD`,
   `git status`, `git rev-parse HEAD` vs origin. Note unpublished commits.
5. Run `./scripts/validate.sh`. Expect ~400 passing on current Python **and**
   3.8, with nothing skipped.
6. Pick the next item from **Known defects / unfinished work**.
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
