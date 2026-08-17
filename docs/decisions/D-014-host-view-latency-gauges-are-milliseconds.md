# D-014 — `host_view` latency gauges are milliseconds

**Status:** Accepted · **Recorded:** 2026-08-17 · **Cluster:** var203, VAST OS 5.4.6.0

## Context

`vast_host_view_latency` (and its `read_latency`/`write_latency` siblings)
carry no unit anywhere: the exporter `# HELP` text says only "VMS host-view
latency", the `/metrics/` catalog does not cover exporter gauges, and the
19.6 MB OpenAPI schema contains no latency-metric semantics. opstat had
assumed microseconds (`latency_us`) since the NFSv4.1 host/view drills were
built, consistent with every monitor-API latency this project had proven.

## Decision

**`vast_host_view_*latency*` gauges are native milliseconds.** opstat
converts them to microseconds at ingestion (`parse_host_view`:
`latency_us = gauge × 1000`), keeping the µs-based display pipeline and
formatters unchanged. Every consumer of `parse_host_view` inherits the fix.

## Evidence

Same-op-class, same-traffic pairing on var203 (2026-08-17, probe HEAD
`0bced30`, evidence archive `opstat-telemetry2-20260817-213038.zip`):
`BlockMetrics,read_latency__avg` (cluster monitor) vs the raw
`vast_host_view_read_latency{protocol="BLOCK"}` gauge, six paired nonzero
samples under live fio block load:

| BlockMetrics (µs-scale raw) | host_view read gauge | ratio |
|---|---|---|
| 620.13 / 606.36 / 581.33 / 620.28 / 727.01 / 637.30 | 0.64 (constant across two exporter refreshes) | 908.3–1136.0, **median 969.1**, mean 987.6 |

No unit relationship other than 1000× fits that distribution; the earlier
combined-gauge pairing (median 739×) matched once its op-mix difference was
accounted for (the combined gauge folds in 1.43–1.46 ms writes). The
alternative reading — BlockMetrics in ms — would mean 0.6 **second** block
reads on an idle-ish flash cluster, and contradicts D-003's proven-µs
ProtoMetrics anchor. Confidence recorded as VERY STRONG EVIDENCE (an
independent client-side fio latency anchor would make it airtight; the
block-loadgen journal was not readable from the lab account).

The pre-fix code displayed the raw gauge through `format_latency_us` — a
~1000× understatement (0.64 rendered as "0.64 µs"; truth 640 µs) in the
NFSv4.1 `h`/`v` drills. Latent on 5.4.6 only because that build publishes no
NFS `host_view` series (see D-006's cluster, 5.5.0.1, where the drills carry
data).

## Reopen when

A VAST release documents these gauges, or a same-op-class pairing on another
build lands outside the ms band.
