# D-015 — the `/metrics/` catalog `units` field is a display convention, not the wire unit

**Status:** Accepted · **Recorded:** 2026-08-17 · **Cluster:** var203, VAST OS 5.4.6.0

## Context

The `/metrics/` catalog (2,626 entries on this build) carries a per-metric
`units` field — the first machine-readable unit metadata this project found.
It was the obvious candidate for settling latency source units outright
(proof mechanism A of the FR3 investigation).

## Decision

**Do not read the catalog `units` field as the unit of the raw API value.**
It records what the VMS's own UI displays after conversion. Raw monitor-API
latency values remain microseconds where empirically proven (D-003) or
family-consistently inferred; unit questions are settled by same-traffic
pairing against a proven source, not by this field.

## Evidence

From the raw catalog capture (2026-08-17, `catalog-page-00.json`, 490 KB,
single unpaginated page):

- `ProtoMetrics,proto_name=NFS4Common,read_latency__avg` is labeled
  `units: "ms"` — the very metric D-003 **proved** returns raw microseconds
  (0.92 cross-family agreement with Nfs4Metrics µs sums).
- `BlockMetrics,read_latency__avg` is labeled `"ms"` while returning raw
  values (576–727) that read as ~0.6 ms only if µs — as ms they would claim
  0.6-second flash reads, and the same-traffic host_view pairing (D-014)
  confirms the µs reading.
- The field is internally inconsistent: `KafkaViewMetrics,*latency__avg`
  and `ViewMetrics,qos_wait_for_budget_time__avg` say `"us"` while every
  neighboring `*_latency__avg` says `"ms"`.
- `SmbMetrics`, `S3Metrics` and `Nfs4Metrics` are absent from the catalog
  entirely (queryable families the catalog does not list — the inverse of
  the known catalog-presence-is-not-queryability rule).

Consistent story: VMS UI shows latency in ms (raw µs ÷ 1000); the catalog
describes the UI.

## Reopen when

A VAST release ships a catalog whose `units` labels match empirically proven
raw units, or adds explicit raw-unit metadata elsewhere.
