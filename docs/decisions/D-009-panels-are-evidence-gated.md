# D-009 — Panels are evidence-gated; derived values are labelled; zero is not unavailable

**Status:** Accepted · **Recorded:** 2026-08-13 · **Cluster:** VAST OS 5.5.0.1

## Context

`opstat` reports on production storage clusters. A wrong number is worse than a
missing one, because someone acts on it. Three distinct ways of showing a number
that is not a measurement had to be closed off.

## Decision

**1. Panels are evidence-gated.** A row appears only when a **live query
actually returned that property**. Catalog presence is not sufficient.

**2. Derived figures are labelled as derived.** Operations per compound prints
`DERIVED RATIO (not a native metric)`.

**3. `0` and "unavailable" are distinct renderings.** A measured `0.00 ops/s`
means no traffic and is information. `-` means no data. They are never
collapsed.

## Evidence

**On catalog gating.** A real VAST OS 5.5.0.1 build advertised `OPEN`/`CLOSE`
counters in the metric catalog that **no monitor would ever return**. Anything
gated on the catalog would have displayed permanently blank or fabricated rows
for metrics the cluster does not measure.

**On derivation.** VMS publishes no compound counter, so operations-per-compound
cannot be measured — only inferred. A related overreach was caught and
withdrawn during discovery: a claim that SEQUENCE ≈ the sum of other operations
looked like proof of compound structure until it emerged that the probe used a
curated 14-operation list omitting `putfh`, `getfh` and `access`. The apparent
match was an artifact of the omission.

**On zero.** Zero session churn on an idle cluster is a healthy-cluster signal,
not missing data. Rendering it as `-` throws away a real observation. The
inverse is worse: rendering missing data as `0.00` asserts a measurement that
was never made.

## Consequences

- The pNFS panel exists in code and does not render, because VAST OS 5.5.0.1
  publishes no pNFS counters. It will appear automatically if a future build
  exports them. That is the gating working, not a gap.
- Attribution coverage is stated honestly rather than scaled to look complete.
- The rebuilt NFSv4 VIEW drill shows only views with current NFS4 traffic, and
  says so ([D-006](D-006-host-view-is-the-nfs4-attribution-source.md)).
- Sub-microsecond latencies must not round to `0` — that renders a real
  measurement as though it were absent ([D-003](D-003-nfs4metrics-latency-is-microseconds.md)).

## Enforcement limit

Evidence-gating is testable: a test can prove a value reached the panel from a
live result. **Truthful labelling is not testable.** Nothing can verify that the
words above a number describe what the number actually is. That judgement stays
with whoever writes the panel, which is why this is a decision record and a
rule rather than only a test.

## What would justify reopening

Nothing. This is a product property, not an implementation trade-off. A future
build exposing more counters changes what renders — it does not change the
gating.
