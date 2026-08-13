# D-006 — `host_view` is the NFSv4 host and view attribution source

**Status:** Accepted · **Recorded:** 2026-08-13 · **Cluster:** VAST OS 5.5.0.1

## Context

The NFSv4.1 VIEW drill was built on the monitor API's `ViewMetrics` family, and
it did not show meaningful activity on a cluster that was demonstrably busy with
NFSv4 traffic. The natural conclusion — that the implementation was buggy — was
wrong.

## Decision

`/prometheusmetrics/host_view` is the attribution source for NFSv4 host and view
traffic, filtered to `protocol=NFS4`.

- `h` (**v4 hosts**) — client IP × view path, ranked by IOPS.
- `v` (NFSv4 views) — the same scrape aggregated by path.

Both share one collector, so switching between them inside the throttle window
costs nothing.

| Key | Mode | Source |
|---|---|---|
| `4` | Native NFSv4 telemetry | `/prometheusmetrics/basic` |
| `h` | **v4 hosts** — client IP × view | `/prometheusmetrics/host_view`, `protocol=NFS4` |
| `v` | NFSv4 views — aggregated by path | `/prometheusmetrics/host_view`, shares the `h` scrape |

`ViewMetrics` remains the correct monitor-API family for view scope in other
contexts. It is **not** equivalent to `host_view` and must never be presented as
such.

## Evidence

**The `ViewMetrics` implementation was internally correct.** Rank and display
props came from the same family, rank props were a strict subset of display
props, newest-complete-row selection was correct, and the topn path correctly
no-opped because the real endpoint exposes no `view` dimension. The defect was
the data source, not the code.

**The two sources disagree because they measure different things.**
`ViewMetrics` reported no meaningful NFSv4 activity while `Nfs4Metrics` measured
~1553 SEQUENCE/s and `host_view` attributed that same traffic to specific paths,
all on the same cluster at the same time.

**`host_view` shape.** ~5 KB, ~1–3 s. Twelve gauges per client IP × view path ×
protocol: `iops`, `read_iops`, `write_iops`, `md_iops`, `bw`, `read_bw`,
`write_bw`, `latency`, plus read/write latency variants. The gauges are
**instantaneous** — one scrape, no warm-up, no differencing. Unlike
`Nfs4Metrics` ([D-002](D-002-nfs4metrics-counters-are-cumulative.md)).

**Label discipline.** `protocol` is a clean scalar label taking `NFS4`, `NFS3`,
`NDB`. Do not confuse it with the distinct **list-valued** `protocols` label on
*view* metrics (e.g. `"['NFS4', 'SMB']"`) — that is view **configuration**, not
traffic.

## Consequences

- The rebuilt VIEW drill shows only views with **current NFS4 traffic**, not
  every configured view. This is a deliberate trade and the panel says so.
- `host_view` carries `protocol=NFS3` series too, so the same rebuild is
  directly portable to the NFSv3 VIEW drill. Not yet investigated — tracked in
  [REFACTOR_HANDOFF.md](../REFACTOR_HANDOFF.md).
- A drill being "internally correct" is not evidence it is showing the right
  thing. Check the source before rewriting the logic.

## What would justify reopening

`ViewMetrics` beginning to carry meaningful NFSv4 activity in a future VAST
release, which would make a monitor-API path viable and cheaper. Prove it with a
live query under load — not from the catalog.
