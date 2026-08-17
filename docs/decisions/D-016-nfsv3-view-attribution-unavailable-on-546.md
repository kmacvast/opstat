# D-016 — the NFSv3 VIEW drill presents an honest unavailable state on clusters with no valid source

**Status:** Accepted · **Recorded:** 2026-08-17 · **Cluster:** var203, VAST OS 5.4.6.0

## Context

The NFSv3 VIEW drill promised "which views are carrying NFSv3 workload right
now" and answered it with ViewMetrics. Two real-VMS evidence passes (recorded
in FR1, archives `opstat-telemetry2-20260817-213038.zip` and
`opstat-fr3-fr1-validation-20260817-220404.zip`) established that on this
build no source can answer that question:

- **host_view exposes no NFS series** under any protocol label on 5.4.6 —
  only BLOCK/NDB/S3/SMB2 — reproduced across multiple exporter captures
  while heavy NFSv3 traffic was proven live.
- **ViewMetrics does not reflect a proven NFSv3 workload.** With client
  mountstats proving ~19,400 NFSv3 ops/s through the mounted namespace
  (which traverses the root view; no `/kmacs` or `/kmacs/nfstest` view
  objects exist), both path-`/` view objects measured ~0: id 1 at 0.03
  metadata-ops/s with zero data IOPS/BW, id 217 at zero, across 10 samples.
- **ViewMetrics has no protocol discriminator** anywhere in the 2,626-entry
  catalog, and TenantMetrics provides no NFS per-view substitute.
- Consequently the old drill's activity ranking surfaced **SMB, BLOCK, S3
  and NDB** views as the top "NFSv3" candidates on the real cluster, in two
  independent runs — a mislabeled screen.

## Decision

When no valid per-view NFSv3 attribution source exists, the VIEW drill
renders an honest capability notice — *"Per-view NFSv3 attribution is not
available from this cluster."* with *"Cluster-level NFSv3 telemetry remains
available."* — instead of ranking all-protocol ViewMetrics under an NFSv3
heading. The unavailable entry costs **zero API calls and zero monitors**;
the old inventory/rank/display machinery is bypassed, not hidden.

The decision point is `nfs_v3.view_attribution_source()`, a localized
capability function that is deliberately deterministic (`None`) today.

## Version boundary

This is a statement about **clusters that expose no valid source**, not
about VAST: a 5.5.0.1-class system demonstrably publishes protocol-labeled
`host_view` series (D-006), so newer builds can likely answer the question.
Future support must be **capability-driven** — replace
`view_attribution_source()` with a validated runtime check and a
host_view-backed implementation — and must be real-VMS validated on a build
that publishes NFS series before being enabled. The user-facing wording is
cluster-scoped for exactly this reason.

## Reopen when

A build that publishes NFS host_view series (or a protocol-aware
ViewMetrics) is available for validation — that work enables the capability
branch; it does not contradict this record.
