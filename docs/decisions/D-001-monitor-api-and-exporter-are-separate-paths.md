# D-001 — The monitor API and the Prometheus exporter are separate data paths

**Status:** Accepted · **Recorded:** 2026-08-13 · **Cluster:** VAST OS 5.5.0.1

## Context

`opstat` can read telemetry two ways: the VMS monitor API (`/monitors/`, created
and queried per metric family) and the Prometheus exporter
(`/prometheusmetrics/*`, a text exposition scrape). They overlap in subject
matter and differ in almost everything else.

Treating them as interchangeable produces confident wrong answers. The NFSv4.1
view drill was implemented correctly on `ViewMetrics` — same-family rank and
display props, rank props a strict subset of display props, correct
newest-complete-row selection — and still showed nothing useful, because
`ViewMetrics` reported no meaningful NFSv4 activity while the exporter measured
~1553 SEQUENCE/s on the same cluster at the same moment.

## Decision

The two are **separate data paths** and are kept separate in code, in panels,
and in reasoning. A figure from one is never presented as equivalent to a figure
from the other.

| | Monitor API | Prometheus exporter |
|---|---|---|
| Shape | Sampled rows on a schedule, indexed by the **returned** `prop_list` | Text exposition, label-scoped series |
| Newest sample | May be partially filled — see [D-011](D-011-newest-complete-sample-scoped-per-family.md) | N/A |
| `Nfs4Metrics` | Absent entirely | Present — 29 native NFSv4 operations |
| Counter semantics | Family-dependent | `Nfs4Metrics` cumulative ([D-002](D-002-nfs4metrics-counters-are-cumulative.md)); `host_view` instantaneous |
| Cost | Small | 276 KB – 4.8 MB, 1.2–9.0 s ([D-004](D-004-heavy-exporter-endpoints-off-the-refresh-path.md)) |
| Lifecycle | Temporary monitors, always deleted | Stateless `GET` |

## Evidence

- The monitor API exposes **no** NFSv4.1 protocol-state telemetry. Across 2720
  metric names in 29 families, these concepts matched **zero** catalog entries:
  `session`, `sequence`, `exchange`, `deleg`, `layout`, `device`, `stateid`,
  `reclaim`, `compound`, `callback`, `backchannel`, `trunk`, `grace`, `replay`,
  `retry`.
- Apparent `open`/`close`/`lock` hits were false positives:
  `nfs3_open_file_handle_cnt`, `nfs3_smb_interop_handles_closed`, and 431
  `BlockMetrics` names matching "lock" through the substring in "b-**lock**".
  Both matching defects were in the discovery tool and were fixed.
- `NfsSampledMetrics` (185 names) is `socket_nfs_{op}_latency__*` over the
  **NFSv3** procedure set — `fsinfo`, `fsstat`, `pathconf`, `mknod`,
  `readdirplus` are v3-specific. Socket-layer measurement of v3, not the missing
  v4 source.
- The exporter carries `Nfs4Metrics`: **29 native NFSv4 operations** plus
  `nfs4_open_connections_cnt`, at both cluster and cNode scope, each with
  `_req_latency_count` and `_req_latency_sum`.

  | Category | Operations |
  |---|---|
  | Session / client lifecycle | `sequence`, `exchange_id`, `create_session`, `destroy_session`, `destroy_clientid`, `reclaim_complete` |
  | Stateful file | `open`, `close`, `free_stateid`, `test_stateid` |
  | Filehandle / security | `getfh`, `putfh`, `putrootfh`, `putpubfh`, `savefh`, `restorefh`, `secinfo`, `secinfo_no_name` |
  | Namespace | `access`, `getattr`, `lookup`, `lookupp`, `create`, `remove`, `readdir`, `setattr` |
  | Data | `read`, `write`, `commit` |

  Labels: cluster scope `{cluster}`; cNode scope `{cluster, cnode_id, hostname}`.

- **Both scopes arrive in the same response**, so per-cNode detail costs nothing
  beyond the single scrape — and they reconcile exactly. From a live capture
  under load:

  ```text
  SEQUENCE   cluster 1553.10   cnodes 1553.08   (100.00%)
  READ       cluster  991.90   cnodes  991.90   (100.00%)
  WRITE      cluster  482.40   cnodes  482.40   (100.00%)
  OPEN       cluster    6.14   cnodes    6.14   (100.00%)
  ```

## Consequences

- `vast_common.py` owns the monitor path; `nfs4_native.py` owns the exporter
  path. They do not share extraction code.
- Panels state which path they came from.
- A gap in one path is not evidence of a gap in the other — that asymmetry is
  precisely how `Nfs4Metrics` was found.
- Do not claim equivalence between `ViewMetrics` and `host_view`. See
  [D-006](D-006-host-view-is-the-nfs4-attribution-source.md).

## What would justify reopening

A VAST release that publishes NFSv4 protocol-state counters through the monitor
API, making the exporter path redundant for that data. Verify by catalog scan
**and** by a live query that actually returns the property — catalog presence is
not sufficient ([D-009](D-009-panels-are-evidence-gated.md)).
