HERE IS CLAUDES RESPONSE, but this is a multi-part response, see below. 

Starting read-only. First the API-efficiency workflow skill and the applicable rules, then deep inspection of the code.

Rules `vast-api-safety.md` and `testing-and-evidence.md` are already in my context. Now mapping the drill code in SMB, S3, and the reference NFSv3 usage.

Good map. Now the detailed reads: the `DrillSession` interface, the SMB drill region, and the NFSv3 adapter pattern that shows how a protocol wires into it.

Now the SMB drill region in full, plus the NFSv3 adapter that shows how a protocol wires `.rank()`/`create_monitors` together.

SMB's ranking is a plain chunked scan — no topn, no batch-size adaptation, no cache. Let me read the config/prop builders, the S3 drill region (which has extra `vip`/`bucket` modes and a topn fallback), and check throttling in the refresh loops.

Critical difference found: **SMB/S3 `fetch_drill_query` have no throttle** — NFSv3 gates re-polls through `DRILL.should_query()`. Let me confirm the SMB main-loop call sites and read the S3 drill region (which has protocol-specific `vip`/`bucket` modes and a topn fallback).

SMB `poll_tick` re-queries the drill every tick unthrottled. Now the S3 drill config and its entry/rank/fetch/vip-fallback region.

Now the S3 `enter_drill_mode` and `fetch_drill_query` — the VIP topn fallback is the S3-specific complexity.

Now the NFSv3 target pattern (how it wires `rank` + `create_monitors` + `with_loading_status`) and the existing test coverage.

This confirms the port is structurally a direct lift — NFSv3's `enter`/`fetch`/`switch` are near-identical to SMB/S3's, differing only in the ranking call, monitor creation, throttle, and loading helper. Now let me check a subtle slicing risk, run the accounting suites for real numbers, and map existing test coverage.

Let me locate SMB's `_slice_result_for_object`, run the accounting suites for real numbers, and map SMB/S3 drill test coverage.

SMB/S3 `_slice_result_for_object` are byte-identical to `vast_drill.slice_result_for_object` (same `_result_parts`/`_normalize_object_id`) — so slicing is equivalent, downgrading that risk. Existing API-efficiency tests cover only the *headline* merged monitor, not drill ranking/throttle. Let me run the accounting suites and inspect the drill-test parametrization to see what template exists.

Confirmed: the drill entry-budget, ranking-correctness, and throttle tests exist **only** for NFSv3 (`engine`) and NFSv41 (`engine41`) — SMB/S3 have none. Both accounting suites pass (60 tests). Now let me actually measure the current SMB/S3 drill API cost against the mock. Let me see the fixture/mock setup so I can drive it.

I understand the fixture pattern. Let me write a standalone measurement harness in the scratchpad that drives SMB and S3 drill entry + refresh ticks against the mock and counts real calls.

S3 bucket entry costs **45 calls** — the exact chunked-scan defect. Let me capture the SMB numbers cleanly (they scrolled past the engine's own frame output).

Clean numbers captured. SMB `view` and S3 `bucket` entry both cost **45 calls** — identical to the pre-refactor NFSv3 defect. Let me confirm the exact `DrillSession` construction NFSv3 uses (so I can specify the port config) and verify the mock plants busy objects deep.

I now have complete evidence. Working tree is clean (only scratchpad touched). The mock confirms 429 views with busy ones planted deep (indexes 317, 401, 355…), so ranking is mandatory — head-slicing picks idle views.

---

# SMB/S3 VAST_DRILL PORT ASSESSMENT

*Read-only. No repo files modified (verified `git status` clean; measurement harness lives in scratchpad). Mock call-counts are loopback measurements — the counts transfer to the real cluster, the wall-clock does not (real mean 1.048 s/call).*

### Current SMB drill architecture
Module-global drill state (`DRILL_MODE/OBJECTS/MONITORS/ERROR/STATUS`, `LAST_DRILL_ROWS`). Three modes via `_DRILL_CFG`: `cnode`, `view`, `tenant` ([smb.py:151](smb.py:151)).
- `_rank_drill_candidates` ([smb.py:1889](smb.py:1889)) — serial chunked scan: one create/query/delete monitor per 32 objects, scoring each by `_build_drill_row(...)["total_ops"]`. No topn, no batch-size adaptation, no cache.
- `enter_drill_mode` ([smb.py:1933](smb.py:1933)) — fetch objects; rank view/tenant, head-slice cnode; then inline batch monitor for view/tenant (`_is_batch_drill_mode`) **with no per-object fallback**, or per-object loop for cnode.
- `fetch_drill_query` ([smb.py:2027](smb.py:2027)) — batch-slice or per-object query; **no throttle**. `poll_tick` ([smb.py:2182](smb.py:2182)) re-queries every 5 s tick.
- `switch_drill_mode` ([smb.py:2065](smb.py:2065)) — open-coded loading frame (`DRILL_STATUS` + `render_screen()`), correct ordering, but not the shared helper.

### Current S3 drill architecture
Same shape, four modes: `cnode`, `bucket`(object_type `view`), `tenant`, `vip` ([s3.py:131](s3.py:131)).
- `_rank_drill_candidates` ([s3.py:1738](s3.py:1738)) — identical chunked scan (bucket/tenant only).
- `enter_drill_mode` ([s3.py:1782](s3.py:1782)) — adds `--buckets`/`--tenants` scope filtering before ranking, and a **VIP-specific path**: rank by `/monitors/topn/` activity (`_fetch_vip_topn`, `_vip_topn_activity_rows`), filter internal `192.168.*` addresses (`_vip_objects_for_drill`), and a **topn-only fallback** when no `vip` ProtoMetrics monitor can be created (sets `DRILL_MONITORS=[]`, `LAST_DRILL_ROWS=topn_rows`).
- `fetch_drill_query` ([s3.py:1922](s3.py:1922)) — has a dedicated `vip && not DRILL_MONITORS` topn-refresh branch; **no throttle** elsewhere.

### Current ranking behavior
Both engines scan **every** candidate serially in 32-object chunks and sort by `total_ops`. Correct results (unlike a head-slice), but cost scales with object count. No `topn` shortcut, no adaptive batch-size discovery, no 5-minute rank cache — so re-entering a drill re-scans from scratch.

### Current API cost (measured against the mock: 429 views, 6 tenants, busy objects planted deep)
| Dimension | SMB | S3 |
|---|---|---|
| `view`/`bucket` drill ENTRY | **45 calls** | **45 calls** |
| `tenant` drill ENTRY (mock has only 6 tenants) | 6 | 6 |
| `vip` drill ENTRY (S3 only) | — | 10 |
| drill re-poll, 6 ticks @ 5 s refresh | 6 (1/tick) | 6 (1/tick); vip 24 (4/tick) |
| leftover live monitors after exit | 0 ✓ | 0 ✓ |

The 45-call view/bucket entry is the *exact* pre-refactor NFSv3 defect (handoff: "VIEW drill entry, 429 views → 47.5 s / 45 calls"). At the real cluster's 1.048 s/call that is ≈47 s of "stand by". NFSv3 post-port is ~4 calls / ~4.2 s. **Caveat:** the mock has only 6 tenants (real cluster has 38–39), so the measured tenant entry (6 calls) understates real-cluster tenant cost (~2 chunks today → ~4 ported).

### Shared behavior (already common between all four engines)
Byte-for-byte identical, differing only by variable name:
- `_slice_result_for_object` / `_normalize_object_id` / `_result_parts` — **identical to `vast_drill.slice_result_for_object` et al.** (verified). NFSv3 already aliases them.
- `enter_drill_mode`, `exit_drill_mode`, `fetch_drill_query`, `switch_drill_mode` control flow.
- The `{id,name}` DRILL_OBJECTS contract, the `(monitor_id, name_or_None)` DRILL_MONITORS contract, and `total_ops`-keyed sorting.
- `_cleanup_drill_monitors`, per-mode error hints, `openmetrics.export_drill` hook.

This is why NFSv3's port was "a direct lift" — its `enter/fetch/switch` are structurally the SMB/S3 functions with four substitutions.

### Protocol-specific behavior (must be preserved, not shared)
- **S3 `vip` mode** — topn-driven ranking, `192.168.*` filtering, and the topn-only fallback (`DRILL_MONITORS=[]`) with its dedicated `fetch_drill_query` branch. `DrillSession` has no notion of this; it must be left entirely alone.
- **S3 `--buckets`/`--tenants` scope filters** — applied to `all_valid` before ranking; preserved as-is since filtering precedes the rank call.
- **Row builders & prop lists** — `build_drill_prop_list`, `build_drill_rank_prop_list`, `_build_drill_row`, `_build_*_drill_row` stay protocol-specific and untouched; they feed the `score_fn`/display exactly as NFSv3's do.
- **cnode mode** — SMB/S3 use a per-object loop today; NFSv3 routes cnode through `create_monitors(validate_batch=True)`. Whether to change SMB/S3 cnode is optional and a separate behavior change.

### Safe direct reuse
- `vast_drill.DrillSession` for view/tenant(/bucket) ranking via `.rank(...)` — the `score_fn`/`name_of` adapters are ~3 lines each, mirroring NFSv3's `_drill_score`.
- `DrillSession.create_monitors(...)` — replaces inline batch creation and **adds the missing batch→per-object fallback**.
- `DrillSession.should_query(force, have_data)` — adds the currently-absent re-poll throttle.
- `vast_drill.with_loading_status(...)` — replaces the open-coded loading frame.
- `slice_result_for_object` — can alias the shared function (already equivalent).

### Required adaptations
1. Construct a module-level `DRILL = vast_drill.DrillSession(request_fn=api_request, create_monitor_fn=_create_monitor_raw, delete_monitor_fn=delete_monitor, max_objects=_MAX_DRILL_OBJECTS, min_batch=_DRILL_PROBE_LIMIT, min_query_interval=…)` per [nfs_v3.py:301](nfs_v3.py:301). Needs a `_DRILL_MIN_QUERY_INTERVAL` constant (both engines lack one).
2. Replace `_rank_drill_candidates` body with `DRILL.rank(...)` (add `_drill_score`).
3. Replace inline monitor creation in `enter_drill_mode` with `DRILL.create_monitors(...)` — for S3, **only** in the `elif mode in ("bucket","tenant")`/cnode branches, never the vip branch.
4. Add `force=False` + `DRILL.should_query(...)` guard to `fetch_drill_query`; thread `force=True` from `manual_refresh`/space and the post-entry fetch. S3's vip-topn branch must keep its own behavior (or be explicitly exempt from the throttle).
5. Optionally adopt `with_loading_status` in `switch_drill_mode`.
6. `DrillSession.reset()` must be called wherever the engines reset global state between runs (test fixtures reset globals — see `reset_smb_globals`/`reset_s3_globals`).

### Risks
- **S3 vip entanglement (highest).** The vip path shares `enter_drill_mode`/`fetch_drill_query` with bucket/tenant. A careless port that routes vip through `DrillSession` or throttles the topn branch would break VIP ranking and the topn-only fallback. Mitigation: scope the change to bucket/tenant/cnode; add a vip regression test first.
- **Throttle changes observable freshness semantics.** Moving from 1-query-per-tick to a 15 s throttle is an API-efficiency win but a behavior change; `space`/`manual_refresh` must still force. Low risk (NFSv3/v41 proven), but must be tested per engine.
- **`create_monitors` batch-validation.** NFSv3 uses `validate_batch=True` only for cnode. For view/tenant `no_aggregation=True`, the batch is already trusted; keep `validate_batch=False` there to avoid an extra query. Mis-setting it changes entry cost.
- **Tenant `__sum` scoring.** SMB/S3 tenant rank props use `__sum`/`__num_samples`, not `__rate`. `score_fn` must derive `total_ops` the same way `_build_drill_row` does; reusing `_drill_score → _build_drill_row` (as NFSv3 does) preserves this. Verify against the real tenant payload shape.
- **No real-cluster validation available in this session.** SMB/S3 were *not* validated against the real cluster during the original effort (handoff). This port needs the owner's real-cluster pass before it is "done".
- Not a risk: slicing divergence (functions are identical); monitor cleanup (measured 0 leftover on the happy path — but the *error* path needs a test).

### Tests already covering this
- `test_api_efficiency.py` — SMB/S3 **headline** merged-monitor budget + fallback only (`test_smb_merged_monitor_single_query_per_refresh` and 4 siblings). Nothing about drills.
- `test_drill_semantics.py` — the entry-budget/ranking/throttle/cleanup tests bind to the **`engine`(nfs_v3)** and **`engine41`** fixtures only. `test_engines_do_not_read_the_filling_bucket_verbatim` is parametrized `["smb","s3","nfs_v41"]` but covers **sample selection**, not ranking.
- Net: **no** SMB/S3 test exercises drill ranking, entry cost, throttle, batch/fallback, or ranking-monitor cleanup.

### Tests missing (must be added with the port; a defect-fix needs a test proven to fail on the prior commit)
1. SMB `view` / S3 `bucket` entry budget: ≤ ~5 calls (proven to fail at 45 on HEAD).
2. Ranking correctness: the busy-planted-deep views/buckets are the ones selected (mock already plants them at indexes >32).
3. Rank result cached — re-entering the same drill issues no new rank monitors.
4. Re-poll throttle: N ticks inside the window → 1 query; `space`/force overrides.
5. `create_monitors` batch → per-object fallback on batch rejection (mock can reject).
6. No ranking/drill monitor left behind, **including the error path** (`vms.live_monitors() == {}`).
7. **S3 vip regression**: topn ranking, `192.168.*` filtering, and topn-only fallback all unchanged after the port.
8. Extend the `test_drill_semantics` fixtures to add `engine_smb`/`engine_s3` (mirror `engine41`).

### Expected before/after API cost (mock counts; ×1.048 s ≈ real wall-clock)
| Dimension | Before | After (target) |
|---|---|---|
| SMB `view` entry | 45 calls (~47 s) | ~4 calls (~4 s) |
| S3 `bucket` entry | 45 calls (~47 s) | ~4 calls (~4 s) |
| tenant entry (real ~38 tenants) | ~8–9 calls | ~4 calls |
| drill re-poll per 6 ticks @5 s | 6 | ~2 |
| re-enter same drill | full re-scan | 0 rank calls (cached) |
| leftover monitors after exit | 0 | 0 (unchanged) |
| S3 `vip` (out of scope) | 10 / 24-per-6-ticks | unchanged |

### Recommended implementation plan
1. **SMB first** (simpler — no vip). Add `_DRILL_MIN_QUERY_INTERVAL` + module `DRILL`; port `_rank_drill_candidates`→`DRILL.rank`; `enter_drill_mode`→`DRILL.create_monitors`; throttle `fetch_drill_query`; adopt `with_loading_status`. Add SMB drill tests (items 1–6, 8). Run `audit-api-efficiency` before/after; `./scripts/validate.sh`.
2. **S3 second**, reusing the SMB pattern, but **fence off vip**: port only bucket/tenant/cnode; leave the vip ranking, filtering, and topn-only fallback and its `fetch_drill_query` branch untouched. Add the S3 drill tests **including the vip regression** (item 7).
3. **Prove the defect** for items 1–2: create a worktree at HEAD, run the new entry-budget test, confirm it fails at 45 calls before the fix.
4. **Decide cnode explicitly** (L2): either leave SMB/S3 cnode per-object as-is (smaller diff) or route it through `create_monitors(validate_batch=True)` like NFSv3 (consistency, needs its own test). Recommend leaving it for this change to keep scope tight, and noting it.
5. Do **not** touch prop lists, row builders, headline monitors, or the exporter path. Update `docs/REFACTOR_HANDOFF.md` outstanding-work item #1 when done. Flag for the owner's real-cluster validation pass (SMB/S3 drills were never validated live).

**Scope guard:** view/tenant(/bucket) ranking, entry, and re-poll throttle only. VIP, NVMe, and cnode-batching are separate items. No `/prometheusmetrics/*` is anywhere near these paths (verified), so no L1 exposure. Two invariants materially improve (entry-cost ranking, drill throttle) and none regress.

---

Assessment complete — no files modified, nothing committed or pushed. Ready to implement SMB (step 1) on your go, or to answer questions on any part first.



THEN I SENT HIM THIS: 

One thing to note is that you now have direct access to query VMS endpoints.  Here is what you can use instead of having to run a mock vms service.  This will help you develop faster and more accurately.  You can also run opstat itself from the repo as needed: 

nfs3 = '~/git/opstat/opstat --nfs --version=3.0 --vms var204.selab.vastdata.com --user admin --password 123456 --export-openmetrics'

smb   = '~/git/opstat/opstat --smb --vms var203.selab.vastdata.com --user admin --password 123456'

nfs41 ='~/git/opstat/opstat --nfs --version=4.1 --vms var204.selab.vastdata.com --user admin --password 123456'

block='~/git/opstat/opstat --block --nvme-over-tcp --vms var203.selab.vastdata.com --user admin --password 123456 --volumes kmacs-block-vol1,kmacs-block-vol2'

s3='~/git/opstat/opstat --smb --vms var203.selab.vastdata.com --user admin --password 123456'

AND HE REPLIED WITH: 


This meaningfully changes how I can work — real-cluster measurement and validation are the exact gaps I flagged as unavailable in the assessment (real tenant count, real payload shape for `__sum` scoring, the 45-call/47 s claim, and the post-port validation pass the handoff reserves for you). A few safety/setup points before I touch the clusters, and one blocker to flag.

Let me first check opstat's non-interactive options and confirm the password env-var path, so I drive the real (read-only) tool cleanly rather than fighting its TUI.

opstat has no one-shot mode — the only self-terminating non-interactive path is `--discover-metrics`. Real *drill* measurement therefore needs a small PTY key-driver (send `v`/`t`, then `q`) plus `--log-api-calls`. Before I drive a real cluster and risk leaving an `adhoc_opstat_*` monitor behind, let me confirm how opstat cleans up on quit/signal.

Good — engines wire `install_signal_handlers` (SIGINT/SIGTERM/SIGHUP) plus `register_atexit(cleanup)`, so both a clean `q` and a SIGTERM tear monitors down; only `SIGKILL` would leak one. Let me confirm `cleanup()` deletes the monitors, then I'll set the safe pattern.

`cleanup()` drains all tracked monitors on quit/signal and even warns to stderr on any failed delete — so driving via `q`/SIGTERM (never `SIGKILL`) is safe. Here's my safety posture and one blocker before I use the access:

**Safety posture I'll hold to**
- Only opstat's own read-only behavior (GET + temporary monitors always deleted) and `--discover-metrics`. No hand-rolled non-GET, never the `nfs4_delegs` DELETE, no config changes — consistent with [vast-api-safety.md](.claude/rules/vast-api-safety.md).
- Password via `VAST_PASSWORD` env (not `--password`) to keep it out of `ps`/argv. **Credentials and cluster identifiers never get written to a tracked file, commit, or doc.** `--log-api-calls`/openmetrics output stays in `/tmp`, uncommitted, and I'll read only paths/methods from it, not auth headers.
- Quit via `q` or SIGTERM only (both trigger `cleanup()`→monitor teardown); never `SIGKILL`. I'll check for the "monitor not deleted" warning after each run.
- var203/var204.selab are the documented lab clusters (var204 is the handoff's validation cluster), not customer production.

**Blocker:** your `s3=` line is `--smb ... var203` (a copy-paste of the SMB command) — it drives the SMB engine, not S3. I can't exercise the real S3 drill without the true invocation (likely `--s3 --vms var203 …`). SMB, NVMe (`--block`), and NFS look right.

Now a minimal, clean connectivity+auth proof against the real SMB cluster, using a small PTY driver (which I'll reuse for drill measurement). Headline only, ~7 s, then graceful `q`.

macOS has no `timeout`; my driver has its own hard cap. Running directly:

Connectivity and auth succeeded (it connected and enabled API logging). It didn't catch the `q` within 7 s — startup on the real cluster spent the window on connect calls — so the SIGTERM fallback fired, which still runs `cleanup()`. Let me verify from the real API log: that calls were GET-only, and that every monitor POST has a matching DELETE (no leak). I'll extract only method/path/status, never auth headers.

The log format differs from my guess. Let me inspect its structure (stripping any auth lines) to parse it correctly.

Access is proven: opstat authenticated, connected, enabled logging, and — critically — wrote a `session end` line via `cleanup()`, confirming clean teardown (no monitor-leak warning). My 7 s window was just too short for the real cluster's slow startup to reach the input loop, so `q` wasn't consumed and the SIGTERM fallback (also clean) fired; the log shows only session start/end, no monitors created, nothing to leak.

Since the whole point is real, accurate numbers, let me do one properly-tuned run that captures the real **SMB view-drill "before" cost** — the 45-call/47 s claim my assessment could only get from the mock. Longer window, enter the view drill, then graceful quit.

It entered the view drill (the "Ranking VIEW… stand by" interstitial and full footer both rendered). Now the real numbers from the API log — methods, monitor lifecycle balance, and timing:

Real data — and it's informative in two ways. Let me see the full ordered sequence (redacted) with timestamps to reconstruct the monitor lifecycle and check whether the headline monitor `2245` was ever deleted.

The sequence reconstructs cleanly, and it's revealing. Let me map it:

- `2245` = headline monitor (POST 17:50:46, queried 2×).
- `2246/2247/2248` = **three sequential rank-chunk monitors** (create→query→delete each) — the chunked serial scan, confirmed on real hardware, ~7 s per chunk.
- `2249` = the view display (batch) monitor, created at quit, deleted by cleanup.

Every **drill** monitor got a matching DELETE — good. **But the headline monitor `2245` has no DELETE before `session end`.** `cleanup()` drains monitors *before* closing the log, so a successful delete would have been logged. That points at a possible leaked `adhoc_opstat_*` monitor on var203. This is a real safety item — let me verify directly with a read-only `GET /monitors/` and clean up my own leak if present.

This is a real finding I need to surface immediately. Let me get the complete leftover inventory grouped by type before reporting.

I need to surface something immediately, before anything else.

## Safety disclosure: I leaked one temporary monitor on var203

My tuned SMB drill run left a temporary monitor behind:

- **`id=2245  adhoc_opstat_smb_headline_cmds_1786661443`** — my SMB headline monitor, still live on var203.

**Cause:** my PTY driver's fault, not opstat's normal path. The real cluster's startup outlasted my 7 s→ later 60 s `q` window, so the driver's **SIGTERM fallback** fired; the driver then timed out and closed the PTY after 2 s, which **truncated opstat's shutdown `cleanup()` mid-drain**. The API log confirms it: cleanup reached `DELETE 2249` (the drill monitor) and wrote `session end` in the same second, but never got to deleting the headline `2245`. All four *drill* monitors (2246–2249) were deleted correctly; only the headline was cut off. A real user pressing Ctrl-C during the multi-second synchronous drain could in principle hit the same truncation — worth a closer look later, but here it was my driver killing cleanup early.

I have **stopped** driving the cluster. Deleting a monitor is a non-GET write outside opstat's own lifecycle (an L1 action), so I will **not** delete anything without your say-so.

**Also present (not mine):** 38 other leaked `adhoc_opstat_*` monitors from earlier sessions — 22 nvme (`nvme_cl`/`nvme_proto`/`nvme_vol`) and 16 `cnode_*` — with older timestamps. 455 monitors total on the cluster; I won't touch the ~416 non-opstat ones.

**Two asks:**
1. May I delete **my** leaked monitor `2245`? (undoing my own side effect; the invariant is "no `adhoc_opstat_*` may remain")
2. Do you also want me to sweep the **38 pre-existing** `adhoc_opstat_*` leftovers? They're not mine, so I'm not touching them without approval.

I'll also fix the driver to send `q` and then **wait for opstat's cleanup to finish** (never close the PTY or SIGKILL mid-drain) before any further runs.

---

## What the run did establish (real var203, read-only)

Genuinely useful for the port, and more accurate than the mock:

- **The chunked serial scan is confirmed on real hardware.** The view drill created three rank monitors (`2246`, `2247`, `2248`) in strict create→query→delete sequence — the exact pattern `vast_drill.DrillSession` replaces — at **~7 s per chunk** at this cluster's latency. var203 has far fewer views than var204's 429, so the absolute call count was smaller than the mock's 45; the **linear-in-object-count pattern** (the thing the port removes) is what's confirmed.
- **TUI invariants hold in the real drill:** the "Ranking VIEW drill-down… stand by" loading interstitial rendered, and the full navigation footer was present during ranking.
- **Real SMB headline aux behavior:** the headline path issues `/monitors/topn/?object_type=view…` and `/openfilehandles/?protocol=SMB` as throttled aux context — separate from drill ranking, and untouched by this port.

## Still blocked
Your `s3=` line is the SMB command (`--smb … var203`). I need the real S3 invocation (likely `--s3 --vms var203 …`) to measure/validate the S3 engine — it's the half of this task I currently can't exercise.

---

I'd like your call on the two cleanup asks before I do anything else against the clusters. Once `2245` is handled and I have the S3 command, my plan is: fix the driver for clean shutdown, capture real "before" numbers for SMB view/tenant and S3 bucket/tenant drills, then implement the SMB port (step 1) with regression tests proven against HEAD — pending your go for implementation.
