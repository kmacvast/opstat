# D-004 — `/prometheusmetrics/basic` stays off the refresh path; `/all` is never requested

**Status:** Accepted · **Recorded:** 2026-08-13 · **Cluster:** VAST OS 5.5.0.1

## Context

`Nfs4Metrics` is the only source of native NFSv4 protocol telemetry
([D-001](D-001-monitor-api-and-exporter-are-separate-paths.md)), and it is only
reachable by scraping a Prometheus exposition endpoint. The obvious
implementation — fetch it on the normal refresh tick alongside everything else —
is not viable at the observed cost.

## Decision

1. **The 5-second refresh path must never scrape `/prometheusmetrics/*`.**
2. **`/prometheusmetrics/all` is never requested at all**, on any path.
3. `/prometheusmetrics/basic` is the endpoint used, on demand only, throttled.

Moving `basic` onto the NFSv4.1 refresh path is an **L1 decision**: it requires
new cost evidence from a real cluster and explicit approval.

## Evidence

Measured against the real cluster:

| Endpoint | Bytes | Latency (observed range) | `Nfs4Metrics` series |
|---|---|---|---|
| `/prometheusmetrics/basic` | ~276 KB | 1.2 – 2.4 s | 118 |
| `/prometheusmetrics/` | ~455 KB | 1.5 – 5.4 s | 118 |
| `/latest/prometheusmetrics/` | ~455 KB | 1.3 – 3.6 s | 118 |
| `/prometheusmetrics/all` | **4.8 MB** | 3.7 – 9.0 s | 118 |

Two things follow directly:

- **`basic` is the narrowest known carrier of the full family.** There is no
  `Nfs4Metrics`-only endpoint. `/all` costs 17x the bytes for *identical*
  coverage — the series count is the same 118.
- **Latency varied roughly 2x run to run in both directions.** Sub-second cannot
  be assumed even when a given run is fast.

A ~276 KB, multi-second synchronous fetch on a 5-second tick would consume half
the refresh budget in the good case and exceed it in the bad one, while freezing
the interface for the duration.

## Consequences

- Native telemetry is an on-demand drill, throttled at 30 s
  ([D-005](D-005-native-telemetry-is-an-on-demand-throttled-drill.md)).
- `host_view` (~5 KB, ~1–3 s) is a separate, much cheaper scrape and is shared
  between the `h` and `v` drills, so switching between them inside the throttle
  window costs nothing.
- Real-cluster success criterion: while in the drill, at most **two**
  `GET /api/prometheusmetrics/basic` per minute; in the cluster view, **none at
  all**.
- `tests/test_nfs4_native.py` asserts the cost isolation.

## What would justify reopening

A VAST release exposing a narrow `Nfs4Metrics`-only endpoint, or measured
evidence from a real cluster that `basic` has become cheap enough to sit on a
refresh path. Measure it; do not infer it from a fast single run, given the
2x variance.
