# D-003 — `Nfs4Metrics` latency is expressed in microseconds

**Status:** Accepted · **Recorded:** 2026-08-13 · **Cluster:** VAST OS 5.5.0.1

## Context

The exporter publishes `nfs4_<op>_req_latency_sum` with no unit in the metric
name, no `# UNIT` metadata, and no documentation. Dividing `sum` by `count`
yields a mean whose magnitude is meaningless until the unit is known. Getting it
wrong by 1000x in either direction would be invisible in the code and obvious to
nobody reading the panel.

Units must never be inferred from a name or from what looks plausible.

## Decision

`Nfs4Metrics` latency values are **microseconds**. `opstat` renders them as µs,
and formats sub-microsecond values without rounding them to zero.

## Evidence

**Cross-family agreement.** `NFS4Common read_latency__avg` — a monitor-API
metric with a known unit — read **588.5 µs** while the `Nfs4Metrics` lifetime
mean read latency was **541.4**. A ratio of **0.92**: two independent metric
families, from two different data paths, agreeing within 8%.

**Physical ordering.** The lifetime means order exactly as the operations'
costs do, and are only coherent in microseconds:

```text
getfh      0.2      putfh      2.4      sequence   2.8
access    17.4      getattr   38.5      lookup   133
read     541        write    815        open    1204
```

In milliseconds, `getfh` would take 200 µs to return a filehandle already in
memory and `open` would take 1.2 seconds. In nanoseconds, a 541 ns read from
storage is physically impossible.

## Consequences

- Panels label latency in µs.
- Sub-microsecond values (`sequence`, `getfh`, `putfh`) must render with enough
  precision to be visible. They previously displayed as `0 µs`, which read as
  "unmeasured" rather than "very fast".
- The cross-family check is reusable: when a new exporter metric's unit is
  unknown, look for a monitor-API metric measuring the same thing.

## What would justify reopening

A VAST release that adds explicit unit metadata to the exposition, or a
cross-family comparison that no longer agrees. If the ratio against
`NFS4Common read_latency__avg` moves materially away from 1.0, re-derive rather
than assume.
