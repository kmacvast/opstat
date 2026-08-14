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

Whether the per-object fallback's *cost* on `vip`/`blockhost` is acceptable in
practice has not been measured on a real cluster; the wall-clock evidence from
the tethered work-laptop run was discarded as network-distorted.

## What would justify reopening

A VAST build returning per-object rows at `vip`/`blockhost` scope — which the
existing validation would pick up automatically, so the record would be
updated rather than the code. Re-probe with
`scripts/var203_validation/probe_var203.py` and compare the table above.
