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

## 2026-08-25 corrective evidence (appended; the historical record above is
## preserved as written)

The reopen clause was exercised: FR14's GET-only host_view probe
(`scripts/opstat-lab-fr14-hostview-attribution-probe.sh`) ran four times on
2026-08-25 across var203 (5.4.6.0) and var204 (5.5.0.1), including one
**corrective** run after the first NFS3 run was found to have its workload
pointed at a different cluster entirely (the nfs3-loadgen's mount targeted
172.200.202.x while var204 was probed — the probe now hard-fails on that
mismatch).

**Corrective var204 / 5.5.0.1 result — decisive negative.** With
`/mnt/var204-nfs3` mounted vers=3 from 172.200.204.6 (the probed cluster) and
1,112,397 NFSv3 client operations proven through it during the window
(~5–6k ops/s, client 172.200.14.198, fio under `/kmacs/nfstest`), host_view
attributed **none of it**: across six scrapes the `protocol="NFS3"` rows
never included the loaded path, while the **same client's NFSv4.1 traffic to
the same `/kmacs/nfstest` subtree was attributed continuously** (~304 IOPS)
in the same scrapes. `protocol="NFS3"` rows existed only for unrelated idle
`/jpalumbo/*` paths from another client.

**Refinement of the historical observation, not a rewrite.** The 2026-08-17
capture recorded *no NFS series under any protocol label* on var203/5.4.6.
On 2026-08-25, var203 (still 5.4.6.0) **did** publish `protocol="NFS3"` rows
correctly attributing an unrelated k8s tenant's background NFSv3 writes
(~719 KB/s to a csi path, `tenant="mars-k8s-tenant"`). host_view therefore
demonstrably *can* carry the NFS3 label with real values. The supported
conclusion is narrower than "host_view never carries NFS3":

> **Neither validated lab cluster provides reliable per-view host_view
> attribution for a controlled first-party NFSv3 workload.** The label can
> appear, and some background NFS3 traffic has been attributed elsewhere,
> but a proven ~5–6k ops/s NFSv3 load produced no attribution on the
> 5.5.0.1 build.

## 2026-08-26 production validation (appended; both records above stand)

The corrective probe used a standalone scrape. This run drove the PRODUCTION
engines against var204/5.5.0.1 through the committed FR14 validator, under a
first-party NFSv3 workload proven through the exact target mount
(`/mnt/var204-nfs3`, 105,548 client operations measured across the window),
and it is the strongest form of this finding yet:

| Check | Result |
|---|---|
| Cluster NFSv3 headline under load | **44,531 ops/s** - the cluster is unambiguously busy with NFSv3 |
| `host_view` per-view NFSv3 attribution of that load | **none** |
| `host_view` NFSv4 attribution, same cluster, same window | **294.95 IOPS**, attributed to `/`, `/kmacs/nfstest`, `/tx-tenant-csi` |
| NFSv3 VIEW drill | capability notice, **0 API calls** |
| NFSv3 TENANT drill | capability notice, **0 API calls** |

So the exporter was working, and attributing, for another protocol on the same
cluster in the same window - it simply did not attribute the NFSv3 workload.
That removes the last benign explanation (an idle window, a broken scrape, a
wrong cluster) for the negative result.

**The conclusion is unchanged and still deliberately narrow.** `host_view`
*can* carry `protocol=NFS3` rows - var203/5.4.6 was observed attributing a
third-party tenant's background NFSv3 writes. What neither validated cluster
provides is *usable per-view attribution of a controlled NFSv3 workload*.
That distinction is the whole finding: presence of a label is not a
capability.

**Capability must therefore be decided from demonstrated telemetry behaviour,
never from a version string.** var204 runs the 5.5.0.1-class build this record
once expected to answer the question, and it does not. `view_attribution_source()`
and its FR14 sibling `tenant_attribution_source()` stay deterministic `None`
until a build demonstrably attributes a controlled workload under live
validation.

**Decision unchanged.** The honest unavailable notice remains the correct
production behavior on both validated capability shapes, and
`view_attribution_source()` remains deterministic `None`. Enablement still
requires a build (or configuration) that demonstrably attributes a
controlled NFSv3 workload, validated live. Evidence archives:
`opstat-fr14-hostview-probe-20260825-{204140,204320,204447,210134}.zip`
(returned to the repository owner; identifiers per lab policy stay out of
the tree beyond the cluster names already recorded here).
