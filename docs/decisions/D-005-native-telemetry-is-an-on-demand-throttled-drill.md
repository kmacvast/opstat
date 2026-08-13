# D-005 — Native NFSv4 telemetry is an on-demand throttled drill, scraped synchronously

**Status:** Accepted, with one consequence still open · **Recorded:** 2026-08-13

## Context

Given that `Nfs4Metrics` costs 1.2–2.4 s to fetch
([D-004](D-004-heavy-exporter-endpoints-off-the-refresh-path.md)) and needs two
scrapes before it can express a rate
([D-002](D-002-nfs4metrics-counters-are-cumulative.md)), the question is where
in the interface it lives and how the wait is handled.

The engines are single-threaded with module-global state. That is load-bearing
for their current correctness.

## Decision

- Native NFSv4 telemetry is an **on-demand drill** (`4`), never a headline
  panel.
- It is **throttled at 30 s**. Object-scoped families publish ~1/min and the
  exporter far slower; polling faster buys nothing.
- The scrape is **synchronous**, with a **visible loading frame painted before
  the call** via `vast_drill.with_loading_status`.
- **No background threads, async, or subprocesses** were introduced in this
  pass. Adding them is an L1 decision.
- First entry shows an explicit **warm-up** state rather than zeros; `space`
  completes it.

## Evidence

- Cost and variance: see [D-004](D-004-heavy-exporter-endpoints-off-the-refresh-path.md).
- Publication cadence: nine consecutive 5-second polls of an object-scoped
  family returned byte-identical payloads with the same newest sample timestamp.
- Without a loading frame, a multi-second blocking call is indistinguishable
  from a hang. With one, the interface stays legible for the duration.
- The `h` and `v` drills share one `host_view` collector, so switching between
  them inside the 30 s window costs zero additional requests.

## Consequences

- Entering the `4` drill stalls the TUI for 1.2–2.4 s. This is visible and
  labelled, but it is a real stall.
- Every blocking drill entry must route through the shared loading helper;
  `tests/test_drill_loading.py` asserts the ordering.
- Correctness of the engines does not have to reason about concurrent access to
  module globals.

## Open consequence

**Whether the multi-second synchronous stall is acceptable in practice is
unvalidated.** A loading frame makes it legible; it does not make it fast.
Background threading was deliberately deferred rather than rejected — the
decision needs real-cluster judgement from someone using the tool, not a
measurement.

Until that is settled, do not resolve it by quietly adding a thread. Raise it.

## What would justify reopening

A real-cluster user finding the stall unacceptable, or a requirement to refresh
native telemetry without a keypress. Either would make concurrency worth its
cost — and would need an explicit decision about module-global state, not an
incidental one.
