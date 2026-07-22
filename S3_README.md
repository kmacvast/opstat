# opstat - S3

Live S3 object storage performance telemetry from VAST VMS. Maps **ProtoMetrics
S3Common** instantaneous rates (with legacy `proto_name=S3` fallback) to a
two-panel TUI. When the cluster exports **S3Metrics** per-call counters and
histograms, the REST operations panel switches to native GET / PUT / DELETE /
HEAD / LIST / MULTIPART rates automatically.

**Version:** 0.1.2 · **Implementation:** [s3.py](s3.py) · **Setup:** [SETUP.md](SETUP.md)

---

## Quick Start

```bash
cd opstat

# Live dashboard
./opstat --s3 --vms <VMS_HOST> --user admin

# Metric discovery (read-only)
./opstat --s3 --vms <VMS_HOST> --discover-metrics

# Scope to buckets and/or tenants
./opstat --s3 --vms <VMS_HOST> --buckets my-bucket,logs --tenants default
```

Shared CLI flags (`--vms-port`, `--refresh`, `--sample-average`, `--csv`, `--no-color`,
`--log-api-calls`, `--export-openmetrics`, `-V`) are documented in [README.md](README.md).

---

## Telemetry Source - S3Common

Primary cluster monitor props (aligned with vast-exporter `S3Common` ProtoMetrics):

| Field | Role |
|-------|------|
| `rd_iops` / `wr_iops` | Data-path op rates (GET / PUT proxies) |
| `rd_bw` / `wr_bw` | Throughput (**bytes/s** → MB/s in TUI) |
| `md_iops`, `rd_md_iops`, `wr_md_iops` | DELETE / LIST proxies when S3Metrics is absent |
| `read_latency__avg` / `write_latency__avg` | Data-path latency (**microseconds** from VMS; TUI shows **ms**) |
| `read_latency__rate` / `write_latency__rate` | Latency fallbacks when avg is zero |
| `read_size__avg` / `write_size__avg` | Avg I/O size proxies |
| `rd_latency` / `wr_latency` | Additional latency aliases |

**Fallback:** if `S3Common` monitor creation fails, opstat retries
`ProtoMetrics,proto_name=S3,...` and labels the source `S3`.

### S3Metrics (when exported)

| Class | Examples | Role |
|-------|----------|------|
| Counters | `get_object`, `put_object`, `multi_part_upload`, `cmd_errors` | Per-call rates via `__rate` or sample deltas |
| Histograms | `head_object`, `delete_object`, `get_bucket`, `initiate_mpu`, `complete_mpu` | Per-call latency (`__rate` / `__avg`) |

On startup opstat probes `S3Metrics`. When a VMS build does not export these
props (HTTP 400 `property_error` or empty prop_list), the REST operations panel uses
S3Common proxies (GET←`rd_iops`, PUT←`wr_iops`, DELETE←`wr_md_iops`, LIST←`rd_md_iops`).

**Rate sanitization:** bare `S3Metrics,*` counters must not be treated as ops/s.
opstat prefers instantaneous `__rate` fields, falls back to counter deltas, and
rejects native totals that dwarf S3Common (~20×) so cumulative counters never
appear as millions of ops/s in the TUI.

### Workload mix bars

Health panel mix uses REST call rates (GET / PUT / DELETE / LIST+HEAD) so bars
always sum to ~100%. When S3Metrics is absent, DELETE and LIST are proxied from
`wr_md_iops` / `rd_md_iops` (or `md_iops` when only the aggregate is available).
There is no opaque `METADATA` or `DELETE+MD` bucket in the UI.

---

## Dashboard Panels (v0.1.2)

1. **S3 HEALTH & WORKLOAD** - status badge (ACTIVE / IDLE / HOT), ops / latency / BW,
   mix bars (GET / PUT / DELETE / LIST+HEAD), delta arrows
2. **S3 REST OPERATIONS** - one row per live S3 call (GET, PUT, DELETE, HEAD, LIST, …)
   with ops, latency (**ms**), auto-scaled throughput (KB/s · MB/s · GB/s), I/O size,
   and source

The former separate **LATENCY & THROUGHPUT** and **OPCODE BREAKDOWN** panels are
merged: when both would show the same GET/PUT rates, a single REST table is enough.

---

## S3 REST Operations Panel

| Call | Category | Preferred source |
|------|----------|------------------|
| `GET` | data | `S3Metrics,get_object` or `S3Common,rd_iops` |
| `PUT` | data | `S3Metrics,put_object` or `S3Common,wr_iops` |
| `DELETE` | data | `S3Metrics,delete_object` (+ `delete_objects`) or `wr_md_iops` |
| `HEAD` | metadata | `S3Metrics,head_object` / `head_bucket` |
| `LIST` | metadata | `S3Metrics,get_bucket` or `rd_md_iops` |
| `MULTIPART` | data | `S3Metrics,multi_part_upload` |
| `INIT_MPU` / `COMPLETE_MPU` | data | `initiate_mpu` / `complete_mpu` histograms |

### Source labels

| Source | Meaning |
|--------|---------|
| `MEASURED` | Direct from `S3Common` (`GET`, `PUT`) |
| `AGGREGATE` | DELETE / LIST proxied from metadata iops when per-call S3Metrics is unavailable |
| `S3METRICS` | Native per-call export |

Empty call rows are omitted rather than displayed with dashes.
Latency is always rendered in **milliseconds** (never µs) in the S3 TUI.

---

## Drill-Down

| Key | Scope | Metrics |
|-----|-------|---------|
| `c` | cNode | `ProtoMetrics,proto_name=S3Common,*` |
| `b` | Bucket / **view** | `ViewMetrics,*` (BucketViewMetrics probed at discovery) |
| `t` | Tenant | `TenantMetrics,*` with cumulative delta engine |
| `i` | VIP | Prefer VIP topn activity; ProtoMetrics when present. Internal `192.168.*` addresses are hidden. |
| `x` | Exit drill | Return to cluster dashboard |
| `Space` | Refresh | Immediate poll |
| `q` | Quit | |

### Drill columns

All S3 drills show:

| Column | Meaning |
|--------|---------|
| **GET/s · PUT/s · DEL/s · LIST/s** | Per-REST-call rates (not a single Ops/s total) |
| **BW** | Auto-scaled KB/s · MB/s · GB/s |
| **Avg ms** | Weighted latency in milliseconds |
| **Top Op** | Dominant S3 REST name (`GET` / `PUT` / `DELETE` / `LIST`), never `RD MD` / `WR MD` |

### Unit notes (important)

| Source | Bandwidth | Latency |
|--------|-----------|---------|
| ProtoMetrics `S3Common` | **bytes/s** (converted to MB/s) | **microseconds** |
| ViewMetrics / BucketViewMetrics | already **MB/s** (do not divide by 1e6 again) | **nanoseconds** (converted to µs, then displayed as ms) |
| TenantMetrics `*_bw__sum` | cumulative **bytes** → delta rate is bytes/s | latency sums are **nanoseconds** |
| VIP topn `bw` | already **MB/s** | topn `latency` is **microseconds** (never treated as ops) |

VIP topn (`GET /monitors/topn/?key=vip`) returns several metric buckets that share
`{title, total, read, write}`. Only `iops` / `md_iops` / `bw` / `latency` are used
for their real meanings. Treating `latency.read` / `latency.write` as ops/s was a
bug (µs values looked like tens of thousands of PUT/s).

Bucket monitors use `no_aggregation=True` (seconds resolution). Bucket/tenant
ranking scans objects in batches and keeps the top targets by activity.
`--buckets` / `--tenants` filter which objects enter the ranking set.

---

## Bucket and Tenant Scoping

```bash
./opstat --s3 --buckets app-data,logs --vms <HOST>
./opstat --s3 --tenants default,prod --vms <HOST>
./opstat --s3 --bucket app-data --tenant default --vms <HOST>
```

Filters **bucket** and **tenant** drill candidate lists. Cluster-wide headline
S3Common telemetry remains visible unless you drill into a scoped target.

---

## CSV Export

```bash
./opstat --s3 --vms <HOST> --csv s3_stats.csv
```

Each refresh appends one row per HEALTH / REST OPERATIONS panel line with
ops, latency, throughput, and I/O size columns.

---

## OpenMetrics

```bash
./opstat --s3 --vms <HOST> --export-openmetrics
```

Streams JSON Lines under the `vast.s3.*` namespace:

| Metric | Unit |
|--------|------|
| `vast.s3.operations` | ops/s |
| `vast.s3.latency` | microseconds (export schema; TUI uses ms) |
| `vast.s3.throughput` | bytes/s |
| `vast.s3.io_size` | bytes |

---

## Examples

```bash
# Rolling average window
./opstat --s3 --vms var203.selab.vastdata.com --sample-average 10m

# Bucket-scoped drill candidates
./opstat --s3 --buckets ml-training --vms var203.selab.vastdata.com

# API debug log + CSV
./opstat --s3 --vms <HOST> --csv s3.csv --log-api-calls

# SSH tunnel
ssh -L 8443:var203.selab.vastdata.com:443 user@jump-host
./opstat --s3 --vms localhost --vms-port 8443 --user admin
```

---

## Related docs

- [README.md](README.md) - shared CLI and protocol matrix
- [SETUP.md](SETUP.md) - install and first-run
- [SMB_README.md](SMB_README.md) - sibling ProtoMetrics engine (SMBCommon)
