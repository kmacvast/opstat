
I'll orient myself thoroughly before reporting. Starting with repository state and the core instruction files.

State confirmed: on `refactor/tui-performance`, HEAD `02959c7`, clean tree, exactly in sync with origin, 23 commits ahead of `main`. Now the project context docs.

Handoff matches so far (it was written at `9c6d6e5`/22-ahead; the scaffold commit `02959c7` is the 23rd). Now the decision records and code verification.

Let me read the full decision records file.

Decision records fully read. Now I'll start the validation gate in the background and verify code claims against the handoff in parallel.

Confirmed: `vast_drill` imported only by `nfs_v41` and `nfs_v3`; `nfs4_native` only by `nfs_v41`; `/prometheusmetrics/all` appears only in a comment (never requested); `NFS4_ENDPOINT = basic`. This matches the handoff's "SMB/S3/NVMe not yet ported" claim. Let me verify the core mechanisms exist in code.

All core mechanisms confirmed present. The `threading` import in `vast_common.py` is only a `Lock()` guarding the shared keep-alive connection — not engine concurrency. Let me confirm no thread is actually spawned, get the exact collection total, and check on the gate.

No thread/process spawn anywhere; the `nfs4_delegs` DELETE-guard tests exist. Collection floor is 395. Let me check the gate's progress.

Current-Python leg (3.14.6) passed: **400 passed, 0 failed, 0 skipped**, openssl present so mock-backed suites ran. The 3.8 leg is still going. Let me verify the TUI footer/loading claims while I wait.

`with_loading_status` matches the claimed ordering (status → render → work → clear-in-`finally`). Let me check the 3.8 leg.

The gate is green on both interpreters. I have everything needed. Here is the orientation report.

---

# opstat Orientation Report

## CURRENT STATE

| Item | Observed |
|---|---|
| Repository root | `/Users/kevin.mcdonald/git/opstat` |
| Branch | `refactor/tui-performance` ✓ (matches expected) |
| HEAD | `02959c7f404246c68943ce9899d4cdf974f3c523` ✓ (matches expected) |
| origin relationship | `refactor/tui-performance...origin/refactor/tui-performance` = **0 ahead / 0 behind** — fully in sync, HEAD is published |
| main relationship | **23 ahead / 0 behind** `main` (`main` = `origin/main` = `77549f06`, unchanged) |
| Working tree | **clean** (`git status --short` empty) |

**Validation result** — `./scripts/validate.sh`, exit 0, `RESULT: PASS`:

- Tooling: openssl 3.6.3 present (so the 171 mock-backed tests actually ran, not skipped); uv 0.12.4 present.
- Collection: **400 collected** (floor 395).
- Current Python (**3.14.6**): `400 passed, 0 failed, 0 skipped, 0 error` in 37.10s.
- Python 3.8 (uv): `400 passed, 0 failed, 0 skipped, 0 error` in 36.95s.
- Docs: 257 relative links OK.

Two point-in-time deltas from [REFACTOR_HANDOFF.md](docs/REFACTOR_HANDOFF.md), both benign and self-consistent, not staleness:
- Handoff records HEAD `9c6d6e5` / 22-ahead; the repo is at `02959c7` / 23-ahead. `02959c7` is the *"chore: establish Claude development scaffold"* commit added after the handoff was written. Everything up to `9c6d6e5` matches.
- Handoff says "green on 3.8 and 3.12"; this workstation's "current Python" is 3.14.6. The gate runs *current Python + the 3.8 floor*, and both are green here.

## ARCHITECTURE

Single-threaded, one module per protocol, module-global state, stdlib-only. The important boundary is that **VMS access, metric interpretation, drill mechanics, and rendering are separated into distinct modules**, and the two telemetry data paths are kept apart.

**Protocol engines** (own their panels, key handling, per-protocol semantics):
- `nfs_v3.py`, `nfs_v41.py`, `smb.py`, `s3.py`, `nvme_tcp.py`

**Shared infrastructure:**
- `vast_common.py` — VMS access layer: one keep-alive `HTTPSConnection` reused across calls (`request`/`request_text`, with a single stale-socket retry), monitor lifecycle (`create_monitor_raw`/`delete_monitor`), and the per-family sample selectors (`latest_complete_row`, `latest_complete_values`, `bounding_samples`). A `threading.Lock` guards the shared connection object — this is *not* engine concurrency; nothing spawns a thread anywhere (`grep` for `Thread(`/`.start()`/`Process(`/`create_task` → none).
- `vast_drill.py` — drill mechanics: `DrillSession.rank` (three-tier topn → batched rank monitors → chunked scan), `slice_result_for_object` for object_id slicing, throttle, view/tenant row builders, `coverage_fraction`, and `with_loading_status` (the loading-frame helper).
- `nfs4_native.py` — Prometheus exporter interpretation: `parse_nfs4_exposition`, `Nfs4Collector` (cumulative differencing + warm-up + reset re-baseline), `parse_host_view`/`HostViewCollector` (instantaneous), `aggregate_by_path`.
- `vast_discovery.py` — read-only `--discover-metrics` survey (discovery-time only).
- `tui_layout.py` — column layout, display width, value formatting.
- `openmetrics.py`, `vast_api_log.py`, `wizard.py` — export, REST logging, interactive setup.

**Separation of concerns, verified in code:** VMS *access* (monitor path) lives in `vast_common`; the *exporter* path lives in `nfs4_native`; they share no extraction code (D-001). *Interpretation* of cumulative-vs-instantaneous, units, and rate derivation is isolated in `nfs4_native`. *Drill* logic is in `vast_drill`. *Rendering* is per-engine `_render_frame` plus `tui_layout`. Only `nfs_v3` and `nfs_v41` import `vast_drill`; only `nfs_v41` imports `nfs4_native` — confirming SMB/S3/NVMe have not yet adopted the shared machinery.

## REFACTOR STATUS

**Already refactored:**
- **NFSv3** — fully refactored, real-VMS validated: merged headline monitor (probe-validated with fallback), `vast_drill`-based ranking/batching/throttle, honest attribution coverage.
- **NFSv4.1** — most heavily worked: merged headline monitor with no exporter call on the refresh path; `c`/`t`/`v` drills ranked/batched/throttled with loading interstitial; native `4` drill and `h`/`v` host/view drills off the exporter.
- **All five engines** share: keep-alive transport, single startup `/clusters/` fetch, `select()`-driven event loop (no 50 ms poll spin), corrected newest-complete-per-family sample selection.

**Still on the old implementation (verified):**
- **SMB / S3** — still rank view/tenant candidates via the 32-object chunked serial scan (`_DRILL_PROBE_LIMIT = 32` at `smb.py:205`, `s3.py:164`); neither imports `vast_drill`. Not validated against the real cluster this effort.
- **NVMe-oTCP** — least refactored: head-slices `/volumes/` (`_MAX_DRILL_OBJECTS = 8`, `nvme_tcp.py:1248`), per-object (unbatched) drill monitors, no ranking, doesn't import `vast_drill`. Dominates API cost (~467+ calls / 30 s).

**Outstanding work / deferred (from the handoff, verified against code):**
1. Port SMB/S3 view/tenant ranking to `vast_drill.DrillSession`.
2. NVMe ranking + drill batching; re-verify the `BlockMetrics`/`VolumeMetrics`/`ProtoMetrics` monitor-split constraint with probe-and-fallback.
3. NFSv3 VIEW drill still on `ViewMetrics`; the `host_view` rebuild may port (`host_view` carries `protocol=NFS3`).
4. Delegation diagnostic not implemented (needs path-entry interaction; D-008).
5. Synchronous exporter scrape stalls the TUI 1.2–2.4 s on `4`-drill entry — legible via loading frame but unvalidated as acceptable (D-005 open consequence).
6. One unreproduced flake: `test_smb_merged_monitor_single_query_per_refresh` (once, on 3.8; suspected mock TLS startup transient). It did **not** recur in this run.

## NFSv4.1 STATUS

- **Telemetry sources:** two separate paths that must never be conflated (D-001). The VMS **monitor API** exposes no NFSv4 protocol-state counters; the **Prometheus exporter** does.
- **Monitor API responsibility:** the 5-second headline — one merged monitor (`NfsMetrics`+`ProtoMetrics`-style families), probe-validated with fallback to the split layout (D-010). **No `/prometheusmetrics/*` on the refresh path at all** (verified: `NFS4_ENDPOINT` is `basic`, only reached from the drill).
- **Prometheus exporter responsibility:** native protocol telemetry (`4`) via `/prometheusmetrics/basic`; host/view attribution (`h`/`v`) via `/prometheusmetrics/host_view`. `/prometheusmetrics/all` is **never** requested (only a comment in `nfs4_native.py:32`).
- **Native NFSv4 telemetry architecture:** `Nfs4Collector` — `_count`/`_sum` are cumulative lifetime totals published as gauges (D-002); rates come from differencing two scrapes; first entry is a warm-up frame (`space` completes it); negative delta / non-positive interval drops the series and re-baselines. Latency is microseconds (D-003), rendered without rounding sub-µs to zero.
- **Host/view attribution architecture:** `host_view` gauges are **instantaneous** — one scrape, no warm-up, no differencing (D-006). `h` = client IP × view path ranked by IOPS; `v` = same scrape aggregated by path. One shared collector, so switching within the throttle window costs nothing. Filtered to the scalar `protocol=NFS4` label (not the list-valued `protocols` view-config label).
- **Known telemetry limitations:** VIEW drill shows only views with *current* NFS4 traffic, not all configured views (deliberate; panel says so). `LOCK*`, `OPEN_DOWNGRADE`, `DELEGRETURN`, all pNFS ops have **no counters** on VAST OS 5.5.0.1 — the pNFS panel is evidence-gated and stays hidden until a build exports them (D-008/D-009). Top-N cannot attribute by protocol (D-007). Delegations are file-scoped, not enumerable (D-008).
- **API-cost constraints and why:** `/prometheusmetrics/basic` is ~276 KB / 1.2–2.4 s with ~2× run-to-run variance; `/all` is 4.8 MB for identical coverage. A multi-second scrape on a 5 s tick would consume/exceed the refresh budget and freeze the UI — hence off the refresh path (D-004, L1 to change) and throttled at 30 s on demand (D-005). Object-scoped families publish ~1/min, so faster polling buys nothing.
- **Current drill behavior:** `c`/`t`/`v` ranked/batched/throttled with a loading interstitial; `4` warm-up then four panels; `h`/`v` from the shared `host_view` collector; navigation footer renders in every mode (122 render tests); at most two `basic` scrapes/minute in the drill, none in cluster view.

## ENGINEERING INVARIANTS

Rules I regard as inviolable while continuing:

- **Python 3.8 is the floor and mandatory**; current Python must also stay green. No syntax/stdlib newer than 3.8 (no `dict |`, `list[str]` runtime annotations, `functools.cache`, `str.removeprefix`). A change isn't done until run on both.
- **API-call budget:** 5 s refresh never scrapes `/prometheusmetrics/*`; `/all` never requested (L1 to change, D-004); one keep-alive connection per session; drills batch into one monitor and slice by `object_id`; rank by activity not API order; throttle drill/exporter refreshes; newest-*complete* sample scoped per family (D-011). Request volume is a regression dimension.
- **Monitor cleanup:** every path that creates an `adhoc_opstat_*` monitor deletes it, including error/interrupt paths; tests assert `live_monitors() == {}`. The `nfs4_delegs` `DELETE` sibling is never invoked (guarded by tests).
- **Real-VMS outranks mock:** where they disagree the cluster wins; counter semantics/units/scope need a real cluster — otherwise say "unproven." Don't present mock-only behavior (≈1 ms loopback, synthetic values, planted busy views) as cluster behavior.
- **TUI:** footer renders in every mode and width (no early `return` bypassing it — the common path owns the footer); paint a loading frame before blocking work via `with_loading_status`; zero ≠ unavailable; no silent control truncation; `select()`-driven loop, no poll spin; background threading is an L1 decision.
- **Git/approval:** feature branch only; **no push/merge/tag/PR/commit without explicit per-action approval**; rebase-onto-upstream is the correct response to a rejected push, never force-push published history; a successful outcome never authorizes an unapproved action retroactively.
- **Evidence/testing:** never fabricate a metric or present derived as native; defect fixes need a regression test reproducing the literal payload shape and proven to fail before the fix; never weaken/skip/delete a test to go green; never accept a silent skip (openssl-gated suites must run).

## DECISIONS

**Durable and settled (D-001…D-012, reopening each is L1 = new evidence + approval):**
- **D-001** monitor API and exporter are separate paths. **D-002** `Nfs4Metrics` counters are cumulative. **D-003** their latency is microseconds. **D-004** `basic` off the refresh path, `all` never fetched. **D-005** native telemetry is an on-demand, 30 s-throttled, synchronous drill. **D-006** `host_view` is the NFSv4 host/view attribution source. **D-007** top-N is unusable for protocol attribution. **D-008** delegations are a file-scoped diagnostic, never a panel; `DELETE` never called. **D-009** panels evidence-gated, derived labelled, zero ≠ unavailable. **D-010** merged monitors are probe-validated with fallback. **D-011** newest-complete sample scoped per family. **D-012** terminology is "v4 hosts".

Most materially constraining for future work: **D-004** (any move of `basic` onto a refresh path is L1), **D-005** (no incidental threading; the stall is deliberate), **D-010/D-011** (merged-monitor + per-family scoping must be preserved together — merging without per-family scoring lost columns and shipped once), and **D-001/D-006** (never present a monitor-API figure as equivalent to an exporter one).

**Genuinely still open (do NOT treat settled ones as open):**
1. Whether the multi-second synchronous scrape is acceptable, or background threading is warranted (needs real-cluster judgement).
2. Whether to port the `host_view` view-rebuild to NFSv3.
3. Whether NVMe's monitor split is a real VMS constraint or historical.
4. Whether/how to implement the delegation lookup.
5. Whether SMB/S3 adopt `vast_drill` wholesale or incrementally.

## NEXT WORK (recommended, priority order — not started)

1. **Port SMB and S3 view/tenant ranking to `vast_drill.DrillSession`.** Highest value/lowest risk: the handoff calls it a "direct lift," the shared machinery is proven on NFSv3/v4.1, the 32-object chunked scan is the exact defect (47 s → 4 s) already fixed elsewhere, and `test_api_efficiency`/`test_drill_semantics` plus the mock give an immediate call-count regression signal. Well within L2/L3 scope.
2. **NVMe ranking + drill batching.** Largest absolute API-cost offender (~467+ calls/30 s, head-slices `/volumes/`, per-object monitors). Ranking and per-object batching are *independent* of the unverified family-split constraint, so real gains are available without reopening D-010. Re-verify the family constraint with probe-and-fallback as a separate step.
3. **Resolve the D-005 open consequence — real-cluster judgement on the synchronous exporter stall.** It gates whether concurrency (an L1 architecture decision) is ever needed. This needs the repository owner using the tool against a real cluster; it can't be settled from a mock. Surfacing it early prevents someone "fixing" it by quietly adding a thread.
4. **Investigate porting the `host_view` view-rebuild to the NFSv3 VIEW drill.** `host_view` carries `protocol=NFS3`, so the NFSv4.1 rebuild may port directly; likely closes the same `ViewMetrics` attribution weakness for v3. Investigation first (open decision #2), then implement if it holds.
5. **Chase the one flake** (`test_smb_merged_monitor_single_query_per_refresh`) only if it recurs — capture output rather than re-running to green, per the testing rule. Lowest priority; it did not recur this run.

Each requires reading the matching `.claude/rules/` file and (for 1/2) an `audit-api-efficiency` before/after; 3/4 begin read-only.

---

**Do I have enough repository context to continue safely without the previous workstation's Claude history?** **Yes.** Branch/HEAD/origin/tree state, the full behavioral contract (AGENTS.md + all four rules), all twelve decision records with their evidence, and the handoff are read and cross-checked against the code — imports, exporter usage, absence of threading, drill machinery, sample selectors, monitor lifecycle, footer/loading helper, and the outstanding-work markers all agree with the documentation. The gate passes on both interpreters with nothing skipped. The only things genuinely *not* reconstructable here are the raw `/tmp` discovery reports and API logs and the PTY benchmark harness — all deliberately not transferred, with their conclusions preserved in `docs/decisions/` and regenerable via the committed tooling. None of those is needed to continue; real-cluster validation (the owner's step) is the one input this session cannot supply on its own.

No files were changed. Stopping here as requested — awaiting your direction before any work begins.
