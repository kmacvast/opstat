# D-019 — `host_view` `protocol=SMB2` is the SMB view and tenant attribution source

**Status:** Accepted · **Recorded:** 2026-08-26 · **Clusters:** var203 (VAST
OS 5.4.6.0) for the SMB evidence; var204 (5.5.0.1) for the NFSv4 contrast

## Context

The SMB VIEW drill was built on the monitor API's `ViewMetrics` family, and
the SMB TENANT drill on `TenantMetrics`. Neither family carries a protocol
discriminator anywhere in the 2,626-entry catalog
([D-016](D-016-nfsv3-view-attribution-unavailable-on-546.md)), so on a mixed
cluster both drills ranked and displayed whatever was busiest *cluster-wide*.

This was not theoretical. Manual lab testing on 2026-08-25 found the SMB VIEW
drill listing `/kmacs/block` — an NVMe view — and `/bgolliher/nfs-source` — an
NFS view — inside a panel titled `VAST SMB … | VIEW DRILL`. The drill was
internally correct and pointed at data that cannot answer the question it
asked.

## Decision

`/prometheusmetrics/host_view`, filtered to `protocol=SMB2`, is the
attribution source for SMB view and tenant traffic.

| Key | Mode | Source |
|---|---|---|
| `v` | SMB views — per view path | `host_view`, `protocol=SMB2`, aggregated by path |
| `t` | SMB tenants — per tenant | the same scrape, aggregated by the `tenant` label |
| `c` | cNode | unchanged: monitor API, `proto_name=SMBCommon` |

Both share one throttled collector, so switching between them inside the
throttle window costs nothing. Entry is one scrape and **zero monitors** —
there is no monitor lifecycle and no cleanup burden. Nothing runs on the
5-second refresh path
([D-004](D-004-heavy-exporter-endpoints-off-the-refresh-path.md),
[D-005](D-005-native-telemetry-is-an-on-demand-throttled-drill.md)).

`ViewMetrics` and `TenantMetrics` are **removed from this engine**, not kept
as a fallback. A fallback would reintroduce exactly the defect this record
exists to close, silently, on the day the exporter is unavailable.

This is the same shape [D-006](D-006-host-view-is-the-nfs4-attribution-source.md)
established for NFSv4, applied to the protocol whose label was already
observed on the older build.

## The `share` label

`host_view` SMB2 rows carry a `share` label alongside `path`. It is displayed
as its own column: `path` remains the operator's namespace identity, and
`share` is the SMB-native identity that `ViewMetrics` could never provide. On
var203 the validated rows read `path=/kmacs/smb/opstat`, `share=opstattest` —
matching the client's CIFS mount exactly.

Parsing note: the per-row key is `(ip, path, tenant, share)`. The previous
3-tuple key silently overwrote one row with another when a single path was
exported under two shares; the 4-tuple both carries the label and fixes that
latent loss.

## Evidence

**First-party, under live SMB load (var203/5.4.6, 2026-08-25 probe):**
`protocol=SMB2` attributed ~747 IOPS to `path=/kmacs/smb/opstat`,
`share=opstattest`, split across two client IPs including the probe host, with
the full field set (read/write/metadata IOPS, read/write bandwidth, latency),
stable across all six scrapes.

**Production validation (var203/5.4.6, 2026-08-26)** — the committed FR14
validator driving the production engine, with 3,142 CIFS client operations
proven through `/mnt/smbtest` across the window:

| Check | Result |
|---|---|
| SMB2 attributed during the window | **799.20 IOPS** |
| Foreign paths excluded | **4**, derived from the cluster's own exposition |
| Own paths shown | `/kmacs/smb/opstat` |
| `share` column | `opstattest` |
| Tenant drill | `default`, with foreign tenants excluded |
| Provenance line | `source host_view/SMB2` |
| Monitors created | **0**; none leaked |

The contamination check is not a fixed list: the validator scrapes
`host_view` unfiltered, computes the set of paths and tenants carrying **no**
SMB2 traffic, and requires that none of them appear in the drill. It therefore
works on any cluster and cannot rot into a stale allowlist.

## Consequences

- The SMB drills show only views and tenants with **current SMB2 traffic**,
  not every configured share. That is the same deliberate trade D-006 made
  for NFSv4, and the panel says so.
- When the exporter reports nothing for SMB2, the drill says so plainly
  rather than falling back to another family.
- The frame's `source` token reads `host_view/SMB2` while these drills are
  open. It previously read `SMBCommon` above exporter-derived rows, which was
  a provenance lie.

## What would justify reopening

`ViewMetrics`/`TenantMetrics` gaining a protocol discriminator in a future
VAST release, which would make a cheaper monitor-API path viable. Prove it
with a live query under load on a mixed cluster — not from the catalog, which
is what produced the original defect.
