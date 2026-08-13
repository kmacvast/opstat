# D-011 — Select the newest *complete* sample, scoped per metric family

**Status:** Accepted · **Recorded:** 2026-08-13 · **Cluster:** VAST OS 5.5.0.1

## Context

VMS monitor queries return a series of sampled rows. The newest row is the
obvious one to display, and it is the wrong one: VMS publishes a **still-filling
newest bucket**, so the most recent row is frequently mostly null.

Selecting "the newest row with the most populated metrics" fixes that — until
monitors are merged ([D-010](D-010-merged-monitors-are-probe-validated-with-fallback.md))
and a single row carries props from several families that fill at different
times.

## Decision

Select the newest **complete** row, and score completeness **per metric
family**, not across the whole row.

`vast_common` provides the shared selectors:

- `latest_complete_row(data, metric_indexes)` — newest row carrying the most
  populated metrics
- `latest_complete_values(data, prop_idx, prop_names=None)` — `prop_names`
  restricts which columns decide "most populated"
- `bounding_samples(data, *indexes)` — newest and oldest rows where every column
  in `*indexes` is populated

Every engine routes through these. Always index by the **returned** `prop_list`,
never by the requested order.

## Evidence

**Partial newest buckets are real.** On the live cluster, a cluster monitor row
had **2 of 46** metrics populated. A `ViewMetrics` row had exactly **one**
(`read_md_iops__rate`), everything else null.

**Cross-family scoring loses whole columns, and shipped.** After the monitor
merge, both extractors scored row completeness across all props. On the real
cluster the newest cNode row had only bandwidth populated, so the row chosen for
one family was the wrong row for the other — and `► - GB/s` appeared in the
user's real output where a bandwidth figure belonged.

The fix is guarded by
`test_bandwidth_survives_a_monitor_that_mixes_metric_families`, which was
**proven to fail on the prior commit** `cb6e5f8` with the message *"read
bandwidth lost to the mixed scoring"*.

**`prop_list` ordering differs from the request.** Observed on the real cluster.
Indexing by requested order silently reads the wrong column.

## Consequences

- Displayed values may lag the true newest sample by one bucket. That is
  correct: a complete older sample is better than an incomplete newer one.
- Object-scoped families publish ~1/min — nine consecutive 5-second polls
  returned byte-identical payloads with the same newest sample timestamp — so
  the lag is not the limiting factor on freshness anyway.
- Any new extraction path must use the shared selectors. Hand-rolled "take the
  last row" logic reintroduces the defect.
- This applies to every engine; the defect was found in the view/tenant drills
  and then audited across NFSv4.1, SMB and S3.

## What would justify reopening

VMS no longer publishing partially-filled newest buckets — verifiable by
querying a monitor under load and checking the newest row's null pattern. Do not
infer it from a single clean sample.
