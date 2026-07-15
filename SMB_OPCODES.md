# SMB2 Opcode Reference - opstat

How **opstat** maps SMB2 opcodes to VMS telemetry, what each opcode means in a
real workload, and exactly how rates are calculated in [smb.py](smb.py).

**Parent doc:** [SMB_README.md](SMB_README.md) · **VMS catalog:** [SMB_PHASE0_RESULTS.md](../../docs/dev/smb/SMB_PHASE0_RESULTS.md)

---

## Overview

The **SMB2 OPCODE WORKFLOW** panel answers: *which SMB2 commands are active right now,
and where did the numbers come from?*

VMS does not export every opcode as a separate counter on current builds (validated on
var203). opstat therefore uses a **tiered mapping**:

1. **Direct measurement** - `SMB2_READ` / `SMB2_WRITE` from `ProtoMetrics,SMBCommon`
2. **Aggregate metadata** - all namespace ops rolled into `md_iops`
3. **REST snapshots** - locks and sessions from operational APIs
4. **Counter proxies** - `notify_counter`, NFS/SMB interop counters
5. **Native per-command** - `SmbMetrics` when a future VMS build enables export

Only opcodes with **live data** for the current refresh are shown. Zero-rate rows are
omitted rather than displayed with dashes.

| `INTEROP` | `NfsMetrics,nfs3_smb_interop_*` counter mapped to interop label |
| `INFERRED` | Workload classifier guess - no per-opcode rate (text hint line) |

Footer: `Authoritative: SMBCommon counters · Derived: REST snapshots, proxies, classifier`

---

## Panel Sections

The **SMB2 OPCODE WORKFLOW** panel renders two tiers:

### Based on Authoritative Metrics

Rows backed by direct VMS monitor counters. The ops/s values are ground truth from
`ProtoMetrics,SMBCommon` (or `SmbMetrics` when per-command export is enabled).

| Source | Rows |
|--------|------|
| `MEASURED` | `SMB2_READ`, `SMB2_WRITE` |
| `AGGREGATE` | `METADATA (total)` - `md_iops` is real; opcode split is not |
| `SMBMETRICS` | All opcodes when native per-command export is active |

### Inferred from System Context

Rows derived from REST API snapshots, counter-to-opcode mapping, or workload heuristics.
These expand visibility beyond the two data-path opcodes VMS splits today.

| Source | Rows / content |
|--------|----------------|
| `PROXY` | `SMB2_CHANGE_NOTIFY` ← `notify_counter` delta rate |
| `HANDLES` | `SMB2_LOCK` ← open-handle snapshot (`has_locks` count) |
| `SESSIONS` | `SMB2_NEGOTIATE`, `SMB2_SESSION_SETUP`, `SMB2_TREE_CONNECT` ← connection count |
| `INTEROP` | NFS/SMB interop lease-break counters (`NfsMetrics,nfs3_smb_interop_*`) |
| Classifier hints | Comma-separated likely metadata opcodes (text line, not table rows) |

Classifier hints use `infer_likely_active_opcodes()` - same rules documented in
[Workload Classifier Cross-Reference](#workload-classifier-cross-reference).

Partitioning is implemented by `_split_opcode_rows()` / `_opcode_tier()` in [smb.py](smb.py).

---

## Calculation Pipeline

Each refresh cycle follows this flow:

```
┌─────────────────────────────────────────────────────────────────────────┐
│ 1. Cluster headline monitor (SMBCommon + interop props)                 │
│    GET /monitors/{id}/query/  →  build_rows_from_results()              │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ 2. Session context (optional REST snapshot)                             │
│    GET /openfilehandles/?protocol=SMB                                   │
│    GET /clusters/list_smb_client_connections/?client_ip=  (--clients)   │
│    →  fetch_session_context()  →  LAST_SESSION_CONTEXT                  │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ 3. Opcode row builder                                                   │
│    build_opcode_workflow_rows(data, metadata, session, meta, smb_cmd)   │
│    →  _visible_opcode_rows()  (drop rows with ops/lat/bw all zero)       │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ 4. Render SMB2 OPCODE WORKFLOW panel                                    │
│    Category headers + table columns + metadata split sub-line           │
└─────────────────────────────────────────────────────────────────────────┘
```

### Startup probe - native `SmbMetrics`

On launch, `try_create_smb_command_monitor()` creates a temporary monitor with all
`SmbMetrics,smb_{cmd}_latency__{rate|avg}` properties from `SMB_CMD_CANDIDATES`. If VMS
returns any `SmbMetrics,*` property, `SMB_PER_COMMAND_EXPORTED` is set and the opcode
builder switches to `_build_opcode_rows_from_smbmetrics()` for every refresh.

On var203 this probe fails (HTTP 400 `property_error`) - aggregate mode is used.

### Headline monitor properties

`build_headline_monitor_props()` requests:

| Property group | Examples |
|----------------|----------|
| Data-path rates | `rd_iops`, `wr_iops`, `rd_bw`, `wr_bw` |
| Metadata rates | `md_iops`, `rd_md_iops`, `wr_md_iops` |
| Latency | `read_latency__avg`, `write_latency__avg`, `*_latency__rate`, `rd_latency`, `wr_latency` |
| I/O size | `read_size__avg`, `write_size__avg` |
| Notify | `notify_counter` (counter - rate via delta) |
| Interop | `NfsMetrics,nfs3_smb_interop_*` (8 counters) |

All SMBCommon props use the FQN prefix: `ProtoMetrics,proto_name=SMBCommon,{suffix}`.

### `build_rows_from_results()` - field extraction

The latest monitor sample row is parsed into panel data:

| Output field | Source property | Transform |
|--------------|-----------------|-----------|
| `read_ops` | `rd_iops` | instantaneous rate |
| `write_ops` | `wr_iops` | instantaneous rate |
| `read_lat` | `read_latency__avg` → `read_latency__rate` → `rd_latency` | first positive |
| `write_lat` | `write_latency__avg` → `write_latency__rate` → `wr_latency` | first positive |
| `read_bw` | `rd_bw` | bytes/s → MB/s (`÷ 1_000_000`) |
| `write_bw` | `wr_bw` | bytes/s → MB/s |
| `read_size` | `read_size__avg` | bytes |
| `write_size` | `write_size__avg` | bytes |
| `md_iops` | `md_iops` | instantaneous rate |
| `rd_md_iops` | `rd_md_iops` | instantaneous rate |
| `wr_md_iops` | `wr_md_iops` | instantaneous rate |
| `notify_rate` | `notify_counter` | counter delta rate across samples |
| `total_iops` | `rd + wr + md` component sum | fallback to `iops` if all zero |

**Workload mix denominator:** `rd_iops + wr_iops + md_iops` (not `SMBCommon,iops` alone,
which is often data-path only).

### `build_opcode_workflow_rows()` - row construction

**Mode A - `SmbMetrics` native** (`SMB_PER_COMMAND_EXPORTED = True`):

For each entry in `SMB2_OPCODES`:

| Column | Calculation |
|--------|-------------|
| Ops/s | `SmbMetrics,smb_{cmd}_latency__rate` - delta rate, else latest sample value |
| Latency | `SmbMetrics,smb_{cmd}_latency__avg` - sum/count delta, else latest sample |
| Source | `SMBMETRICS` |
| % workload | `ops / sum(all opcode ops) × 100` |

Rows with zero ops are filtered by `_visible_opcode_rows()`.

**Mode B - SMBCommon aggregate** (current var203 default):

| Category | Opcode | Shown when | Ops/s | Throughput | Latency | Avg size | Source |
|----------|--------|------------|-------|------------|---------|----------|--------|
| data | `SMB2_READ` | `rd_iops > 0` | `rd_iops` | `rd_bw` | read latency chain | `read_size__avg` | `MEASURED` |
| data | `SMB2_WRITE` | `wr_iops > 0` | `wr_iops` | `wr_bw` | write latency chain | `write_size__avg` | `MEASURED` |
| metadata | `METADATA (total)` | `md_iops > 0` | `md_iops` | - | - | - | `AGGREGATE` |
| notify | `SMB2_CHANGE_NOTIFY` | `notify_rate > 0` | counter delta on `notify_counter` | - | - | - | `PROXY` |
| lock | `SMB2_LOCK` | open handles with `has_locks` | count of locked handles | - | - | - | `HANDLES` |
| session | `SMB2_NEGOTIATE` | connections exist | connection count | - | - | - | `SESSIONS` |
| session | `SMB2_SESSION_SETUP` | connections exist | connection count | - | - | - | `SESSIONS` |
| session | `SMB2_TREE_CONNECT` | connections exist | connection count | - | - | - | `SESSIONS` |

**Metadata sub-line** (under `METADATA (total)` only):

```
read-md {rd_md_iops}/s  ·  write-md {wr_md_iops}/s
```

Only non-zero split values are included.

**Workload % column:** each visible row's ops/s divided by the sum of all visible row
ops/s for that refresh.

**Visibility filter** (`_opcode_has_data`): a row is kept when any of `ops_sec`, `avg_us`,
or `bw_mbs` is greater than zero.

### Interop rows (derived section)

When `NfsMetrics,nfs3_smb_interop_*` counter deltas are positive, interop labels
(e.g. `LEASE BREAKS`, `LEASE RETRIES`) appear in **Inferred from System Context**
with source `INTEROP`. These are not SMB2 opcodes - they indicate NFSv3/SMB
cross-protocol activity.

---

## Source Labels

| Source | Trust level | Meaning |
|--------|-------------|---------|
| `MEASURED` | High | Direct `SMBCommon` counter for `SMB2_READ` or `SMB2_WRITE` |
| `AGGREGATE` | High (total only) | `md_iops` sum; per-opcode breakdown not exported by VMS |
| `SMBMETRICS` | High | Native per-command `SmbMetrics` export (future VMS builds) |
| `PROXY` | Medium | Opcode approximated from a related counter (`notify_counter`) |
| `HANDLES` | Snapshot | Point-in-time count from open-file-handle API, not a rate |
| `SESSIONS` | Snapshot | Point-in-time connection count, not a per-opcode rate |
| `MD_BUCKET` | Legacy | Per-opcode placeholder when metadata shared one bucket (pre-v0.1.2 UI) |
| `MD_HINT` | Legacy | `MD_BUCKET` plus classifier guess of likely active opcode (pre-v0.1.2 UI) |

`MD_BUCKET` and `MD_HINT` are no longer emitted in v0.1.2. Metadata opcodes without
individual VMS counters are folded into `METADATA (total)` / `AGGREGATE`.

---

## Opcode Catalog

Opcodes are listed in **troubleshooting priority order** (same as `SMB2_OPCODES` in
[smb.py](smb.py)).

### Data path

#### `SMB2_READ`

| | |
|---|---|
| **SMB2 role** | Read file/stream data from an open handle |
| **Typical symptoms** | High latency here → slow sequential reads, large-file streaming issues |
| **VMS metric** | `ProtoMetrics,proto_name=SMBCommon,rd_iops` |
| **Throughput** | `rd_bw` (bytes/s → displayed as KB/MB/GB/s) |
| **Latency** | `read_latency__avg`, fallback `read_latency__rate`, fallback `rd_latency` |
| **Avg I/O size** | `read_size__avg` |
| **Source** | `MEASURED` |
| **Shown when** | `rd_iops > 0` |

#### `SMB2_WRITE`

| | |
|---|---|
| **SMB2 role** | Write file/stream data to an open handle |
| **Typical symptoms** | Elevated write latency → slow saves, copy-in, database checkpoints |
| **VMS metric** | `ProtoMetrics,proto_name=SMBCommon,wr_iops` |
| **Throughput** | `wr_bw` |
| **Latency** | `write_latency__avg` → `write_latency__rate` → `wr_latency` |
| **Avg I/O size** | `write_size__avg` |
| **Source** | `MEASURED` |
| **Shown when** | `wr_iops > 0` |

---

### Metadata / namespace

These opcodes are **not shown individually** on var203 because VMS does not export
per-command rates. They are included here for interpretation when `METADATA (total)` is
elevated.

#### `METADATA (total)` - aggregate row

| | |
|---|---|
| **SMB2 role** | All namespace/metadata operations combined |
| **VMS metric** | `ProtoMetrics,proto_name=SMBCommon,md_iops` |
| **Sub-line split** | `rd_md_iops` (read-side metadata), `wr_md_iops` (write-side metadata) |
| **Source** | `AGGREGATE` |
| **Shown when** | `md_iops > 0` |

#### `SMB2_CREATE`

| | |
|---|---|
| **SMB2 role** | Open or create a file/directory/pipe |
| **Typical symptoms** | High rate → application open/close churn, temp-file storms |
| **VMS today** | Contributes to `md_iops` - no separate counter |
| **Future** | `SmbMetrics,smb_create_latency__rate` when exported |

#### `SMB2_CLOSE`

| | |
|---|---|
| **SMB2 role** | Close an open handle |
| **Typical symptoms** | Spikes with CREATE - Explorer navigation, short-lived app patterns |
| **VMS today** | Contributes to `md_iops` |
| **Future** | `SmbMetrics,smb_close_latency__rate` |

#### `SMB2_FLUSH`

| | |
|---|---|
| **SMB2 role** | Flush buffered data/metadata to stable storage |
| **Typical symptoms** | Bursts during save operations or strict durability apps |
| **VMS today** | Contributes to `md_iops` |
| **Future** | `SmbMetrics,smb_flush_latency__rate` |

#### `SMB2_QUERY_INFO`

| | |
|---|---|
| **SMB2 role** | Query file/object attributes (size, timestamps, security descriptor) |
| **Typical symptoms** | Elevated on stat-heavy apps, backup scanners, metadata crawlers |
| **VMS today** | Contributes to `md_iops` / often `rd_md_iops` |
| **Future** | `SmbMetrics,smb_query_info_latency__rate` |

#### `SMB2_QUERY_DIRECTORY`

| | |
|---|---|
| **SMB2 role** | List directory contents |
| **Typical symptoms** | Dominant in Explorer/Finder browsing, backup directory walks |
| **VMS today** | Contributes to `md_iops` / often `rd_md_iops` |
| **Classifier hint** | `infer_likely_active_opcodes()` flags this when metadata ≥ 50% and avg read size < 32 KiB |
| **Future** | `SmbMetrics,smb_query_directory_latency__rate` |

#### `SMB2_SET_INFO`

| | |
|---|---|
| **SMB2 role** | Set file/object attributes (rename, chmod, truncate, timestamps) |
| **Typical symptoms** | Elevated on write-heavy metadata (save-as, renames, ACL changes) |
| **VMS today** | Contributes to `md_iops` / often `wr_md_iops` |
| **Future** | `SmbMetrics,smb_set_info_latency__rate` |

---

### Locking

#### `SMB2_LOCK`

| | |
|---|---|
| **SMB2 role** | Acquire or release byte-range locks on an open file |
| **Typical symptoms** | Contention → app stalls; often paired with database or Office files |
| **VMS today** | No per-opcode rate; proxy from open-handle snapshot |
| **Calculation** | Count of handles where `has_locks == true` in `GET /openfilehandles/?protocol=SMB` |
| **Source** | `HANDLES` (snapshot count, not ops/s) |
| **Shown when** | `lock_count > 0` |
| **Classifier hint** | Flagged when `nfs3_smb_interop_triggered_lease_breaks` rate is elevated |

---

### Session / tree

These opcodes establish and tear down SMB sessions and share connections. VMS does not
export per-opcode session rates on var203; opstat shows a **connection-count proxy** when
`--clients` scoping returns live connections.

#### `SMB2_NEGOTIATE`

| | |
|---|---|
| **SMB2 role** | Protocol version/capability negotiation at connection start |
| **VMS today** | Proxy: active connection count |
| **Source** | `SESSIONS` |
| **Shown when** | `conn_count > 0` and opcode is in proxy set |

#### `SMB2_SESSION_SETUP`

| | |
|---|---|
| **SMB2 role** | Authenticate and establish an SMB session |
| **Typical symptoms** | Storms → mass reconnects, credential issues, load-balancer failovers |
| **VMS today** | Proxy: active connection count |
| **API** | `GET /clusters/list_smb_client_connections/?client_ip=` |
| **Source** | `SESSIONS` |

#### `SMB2_LOGOFF`

| | |
|---|---|
| **SMB2 role** | Tear down an authenticated session |
| **VMS today** | Not shown - no rate or snapshot proxy implemented |
| **Future** | `SmbMetrics,smb_logoff_latency__rate` |

#### `SMB2_TREE_CONNECT`

| | |
|---|---|
| **SMB2 role** | Connect to a share (tree) within a session |
| **Typical symptoms** | Elevated during mount storms or multi-share clients |
| **VMS today** | Proxy: active connection count |
| **Source** | `SESSIONS` |

#### `SMB2_TREE_DISCONNECT`

| | |
|---|---|
| **SMB2 role** | Disconnect from a share |
| **VMS today** | Not shown - no rate or snapshot proxy implemented |
| **Future** | `SmbMetrics,smb_tree_disconnect_latency__rate` |

---

### Notify

#### `SMB2_CHANGE_NOTIFY`

| | |
|---|---|
| **SMB2 role** | Register for directory change notifications (watch for file changes) |
| **Typical symptoms** | Sustained rate → apps watching directories (sync tools, IDEs) |
| **VMS metric** | `ProtoMetrics,proto_name=SMBCommon,notify_counter` |
| **Calculation** | Counter delta rate across monitor samples (`_delta_rate_from_samples`) |
| **Source** | `PROXY` |
| **Shown when** | `notify_rate > 0` |

---

## Opcodes Probed but Not in the Workflow Panel

`SMB_CMD_CANDIDATES` includes additional commands probed at startup for future
`SmbMetrics` export. They are not rendered in the workflow table today:

| Command key | SMB2 equivalent | Notes |
|-------------|-----------------|-------|
| `ioctl` | `SMB2_IOCTL` | FSCTL / offload / lease operations |
| `echo` | `SMB2_ECHO` | Keep-alive |
| `cancel` | `SMB2_CANCEL` | Cancel in-flight request |
| `oplock_break` | oplock break path | Lease/oplock break handling |

When VMS enables `SmbMetrics` for these commands, they will appear automatically with
source `SMBMETRICS` if their rate is greater than zero.

---

## Workload Classifier Cross-Reference

`classify_smb_workload()` and `infer_likely_active_opcodes()` use opcode *patterns* to
describe the workload in the Health and Insights panels - even when individual metadata
opcodes are not shown:

| Condition | Likely active opcodes / label |
|-----------|-------------------------------|
| Metadata ≥ 35% of component ops | `QUERY_DIRECTORY`, `QUERY_INFO`, `CREATE`, `CLOSE` |
| Metadata ≥ 20% and write > read | `SET_INFO`, `CREATE`, `CLOSE` |
| Metadata ≥ 50% and avg read < 32 KiB | `QUERY_DIRECTORY` (directory enumeration) |
| `notify_rate > 0` | `CHANGE_NOTIFY` |
| Interop lease-break rate > 0 | `LOCK` (locking/lease activity) |
| Metadata ≥ 60% | "metadata-heavy {read\|write} workload" |

These heuristics inform the **Observation** line in Performance Insights; they do not
create separate opcode rows.

---

## Column Reference (opcode table)

| Column | Description |
|--------|-------------|
| **SMB2 Opcode** | Protocol command name or `METADATA (total)` aggregate |
| **Ops/s** | Operations per second (or snapshot count for HANDLES/SESSIONS) |
| **Throughput** | Data-path bandwidth (`rd_bw` / `wr_bw` only) |
| **Avg Size** | `read_size__avg` or `write_size__avg` for data path |
| **Latency** | Microseconds or milliseconds; `-` when not exported |
| **Source** | Measurement tier (see table above) |
| **% (internal)** | Used by Insights "Top Opcode" line; not always displayed in table |

---

## Related

- [SMB_README.md](SMB_README.md) - dashboard panels, drill-down, `--clients`
- [SMB_PHASE0_RESULTS.md](../../docs/dev/smb/SMB_PHASE0_RESULTS.md) - var203 metric catalog and API probes
- [SMB_IMPLEMENTATION_PLAN.md](../../docs/dev/smb/SMB_IMPLEMENTATION_PLAN.md) - phased design record
- [smb.py](smb.py) - `SMB2_OPCODES`, `build_opcode_workflow_rows()`, `build_rows_from_results()`
