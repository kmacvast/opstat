# D-002 — `Nfs4Metrics` counters are cumulative lifetime totals

**Status:** Accepted · **Recorded:** 2026-08-13 · **Cluster:** VAST OS 5.5.0.1

## Context

The Prometheus exporter publishes `nfs4_<op>_req_latency_count` and
`nfs4_<op>_req_latency_sum` as Prometheus **gauges**. A gauge conventionally
holds an instantaneous value. If these were read that way, every rate `opstat`
displays would be wrong by an unbounded factor.

## Decision

`_count` and `_sum` are **cumulative lifetime totals published as gauges**.
Rates and averages are derived by differencing two scrapes:

```text
ops/sec = delta_count / elapsed_seconds
avg     = delta_sum / delta_count          (microseconds — see D-003)
```

A negative delta (counter reset) or a non-positive interval **drops that series
and re-baselines**. `opstat` never emits a negative rate. The first scrape is a
warm-up and the panel says so explicitly rather than showing zeros.

## Evidence

Proven two independent ways.

1. **Under load**, 63 of 236 series grew between consecutive scrapes — an
   instantaneous gauge would fluctuate in both directions, not monotonically
   accumulate.
2. **On an idle cluster** the deltas were zero, but
   `nfs4_sequence_req_latency_count` held **12,941,555**. An instantaneous rate
   reads ~0 when idle; a lifetime total does not.

The idle-cluster observation is the decisive one: it distinguishes the two
readings in a way the loaded observation alone cannot.

## Consequences

- The native NFSv4 drill requires **two** scrapes before it can show a rate,
  hence the warm-up frame. `space` completes the warm-up.
- Scrape interval must be recorded alongside the values; the elapsed time is
  part of the measurement, not an assumption.
- A single scrape can still show absolute counts honestly — it cannot show a
  rate, and must not pretend to.
- This is the reason the native telemetry is a throttled drill rather than a
  headline panel ([D-005](D-005-native-telemetry-is-an-on-demand-throttled-drill.md)).
- Contrast `host_view`, whose gauges **are** instantaneous — one scrape, no
  warm-up, no differencing ([D-006](D-006-host-view-is-the-nfs4-attribution-source.md)).

## What would justify reopening

A VAST release that changes the exporter's counter semantics, or exposes the
same data as a proper Prometheus counter type. Re-prove with the idle-cluster
test: a large non-zero value with zero delta on a quiet cluster means
cumulative.
