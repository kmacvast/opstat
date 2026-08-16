# D-013 — NVMe drill batching is scope-dependent and must be response-validated

**Status:** Accepted · **Recorded:** 2026-08-14 · **Cluster:** var203, VAST OS 5.4.6
*(originally mis-recorded as 5.5.0.1; the validation frames report
`vast-os-release-5.4.6.0`, build `release-5.4.6-2628322`)*

## Context

The NVMe drill originally created one monitor per operation group *per object*,
so entering a drill on eight objects cost 65 API calls and re-polled 64 queries
a tick. Batching across objects — one monitor per op group carrying every
selected `object_id` — is the fix used everywhere else in the codebase
([D-010](D-010-merged-monitors-are-probe-validated-with-fallback.md)).

The open question was whether a real VMS supports that shape at the NVMe drill
scopes (`cnode`, `vip`, `blockhost`). The mock could model the documented shape
but could not answer it; `/blockhosts/` is not modelled at all.

## Decision

**Batching is committed to only after the response is proven splittable, per
scope, at run time.** A successful `POST /monitors/` is *not* evidence that the
batch layout works.

The engine creates the batch, queries the first group once, and requires both:

1. an `object_id` column in `prop_list`, and
2. at least one data row that slices to a requested `object_id`.

Anything short of that deletes the monitors it just created and falls back to
the per-object layout. The per-op *group* split is preserved in both layouts —
that is a separate real constraint (counter and rate/avg properties cannot
share one BlockMetrics monitor), so batching happens across objects, never
across groups.

## Evidence

A read-only probe on var203 (`scripts/var203_validation/probe_var203.py`),
same property set and same monitor shape at each scope:

| Scope | create | query | splittable | observed |
|---|---|---|---|---|
| `cnode` | PASS | PASS | **PASS** | ids `[4, 3]`, **120 rows each** |
| `vip` | PASS | PASS | **FAIL** | ids `[755, 55, 56, 57]`, **0 rows per object** |
| `blockhost` | PASS | PASS | **FAIL** | ids `[1, 2, 3, 4]`, **0 rows per object** |

The decisive part is that `vip` and `blockhost` **succeed at both create and
query** and still yield nothing usable. An engine that treated a successful
create — or even a successful query — as proof of a working batch would have
rendered an empty or fabricated panel on two of the three NVMe drill modes,
with no error to point at.

Rank monitors are a separate shape and are accepted at `cnode` scope: a
two-counter multi-object rank monitor returned usable per-object deltas
(`read_req` object 4 = 1062.353/s, object 3 = 0.0/s), confirming activity
ranking is viable there and that a real zero is distinguishable from no data.

## Consequences

- `cnode` gets the batch layout on this cluster; `vip` and `blockhost` fall
  back to per-object monitors and keep their previous API cost.
- The fallback costs one wasted create+query+delete per mode per session
  (`DrillSession` remembers the rejection), which is the price of not
  hard-coding a cluster-specific assumption.
- **Nothing is hard-coded per scope.** A future VMS build that starts returning
  per-object rows at `vip`/`blockhost` will engage the batch automatically, and
  a build that stops doing so at `cnode` will fall back automatically.
- The mock gained a `batch_unsplittable` knob reproducing both possible
  response shapes, and `tests/test_nvme_drill.py` asserts the fallback, that
  the rejected batch leaks no monitors, and that the fallback still renders
  real per-object rows. Removing the validation fails four of those tests.

## The exact unsplittable shape (settled by the second run)

The second lab run captured the full `prop_list`, settling what the first run
left ambiguous: the response **does** carry an `object_id` column at every
scope — the rows simply never match the requested ids ("column present, no
matching rows"). Both candidate shapes remain modelled in the mock, but the
real one is now known.

The `vip` result carries a second finding: the requested
`BlockMetrics,read_req` / `read_latency__avg` came back in the `prop_list` as
**`TopNMetrics,read_req` / `TopNMetrics,read_latency__avg`** — at vip scope
the cluster silently rewrites the metric family. A monitor that echoes a
*different family* than requested is one more reason response validation, not
request success, is the only trustworthy signal. `blockhost` echoed
BlockMetrics unchanged and still returned no matching rows.

## The fallback cost, measured — and the redesign it forced (round 3)

The third lab run measured the per-object fallback on a cluster-adjacent host:
**vip 43 monitors / 137 calls / 464 s; blockhost 41 monitors / 122 calls /
720 s — and zero usable rows from any of them.** Every per-object monitor at
those scopes returned as empty as the batch had, one 2–38 s call at a time,
and the accumulated fan-out (206 monitors in one session) made clean shutdown
slower than the observer's patience.

The fallback is therefore redesigned around the telemetry evidence itself:

- The rank scan's slices decide whether a scope publishes per-object telemetry
  at all. **No rows for any requested id → the drill renders an explicit
  no-telemetry notice and creates no display monitors** — a bounded probe
  (endpoint list + rank attempt + batch attempt) instead of a monitor storm
  proving each object empty individually. On var203's 5.4.6 build this is the
  real vip/blockhost outcome.
- When telemetry exists but the batch will not split, the per-object fallback
  is **bounded**: the top `_MAX_FALLBACK_OBJECTS` (4) ranked candidates, and
  only the data-I/O op groups the drill panel renders (read/write/compare +
  proto) — the fabric/admin/reclaim groups fed no drill field yet cost a
  monitor each per object.
- A scan whose rank-monitor creates were all refused proves nothing about
  telemetry; the verdict is only recorded from slices that were actually
  examined, and the drill errs toward the bounded fallback.

## Startup consolidation is ruled out (round-3 merge probes)

Two cluster-scope merge-legality probes settled whether the NVMe headline's
per-op monitor split could be consolidated: `merge.data_pairs` (read+write
pairs in one monitor) and `merge.data_plus_fabric` were both **rejected at
query time with HTTP 400 "can't mix properties"**. The per-op split is a real
constraint of this build, not history — headline consolidation must not be
attempted around these combinations. Startup cost work has to come from
elsewhere (the first-paint no longer blocks on the initial query cycle).

## Round 4: the storm the redesign missed, and the verdict moved first

The fourth lab run validated the bounded batch probe (4 creates, 1 query,
teardown - exactly as designed) and cNode end-to-end (13 calls, batch layout,
real rows, 28 s), and then exposed a defect UPSTREAM of all of it: the VIP
rank scan itself created **189 monitors** (`rank_vip_0,2,4,...,376` - chunk
size 2, all 378 VIPs in pairs, 34 minutes). The DrillSession's discovered
rank-chunk size was a single value shared across object types: the 2-cNode
scan "learned" that chunks of 2 work, and that size then capped the VIP scan.
A size learned because a scope IS small is not capability evidence.

Two structural changes follow, and they pin down a distinction this record
now states explicitly - these are **three separate questions that must never
share a cache or a boolean**:

1. **Rank-batching capability** (what `object_ids` count will the cluster
   accept in one rank monitor?) - cached per `object_type`, and only when a
   larger size was actually refused first (`was_cap`), never merely because a
   small population fit in one chunk.
2. **Telemetry availability** (does this scope return per-object rows at
   all?) - decided by a bounded O(1) capability probe (`DrillSession.
   probe_scope`, one monitor over <= 8 sampled ids) BEFORE any
   population-scaled ranking, for scopes larger than the panel. Small scopes
   reach the same verdict through their single rank chunk. The verdict is
   cached per session; a dead scope re-renders its notice for free. The probe
   is opt-in: the proven NFS/SMB/S3 callers rank scopes whose telemetry is
   already established and are untouched.
3. **Display-batch splittability** (can one display monitor serve all
   selected objects?) - unchanged: response-validated at entry per this
   record's original decision.

Dead-scope discovery cost is now constant - mock-proven at populations 10,
100, 500 and 1000 with a fixed create budget of <= 3 - and the honest
no-telemetry notice renders (with footer) whether or not the dashboard has
data yet, with `x` returning to the dashboard.

Round 4 also proved the cleanup invariant the hard way: the abandoned session
leaked its eight headline monitors because the shutdown banner's write to a
dead PTY raised EIO and killed `cleanup()` before the drain. Cleanup output is
now best-effort by construction (`vast_common.emit_stderr`); monitor deletion
never depends on a writable terminal.

## What would justify reopening

A VAST build returning per-object rows at `vip`/`blockhost` scope — which the
existing validation would pick up automatically, so the record would be
updated rather than the code. Re-probe with
`scripts/var203_validation/probe_var203.py` and compare the table above.
