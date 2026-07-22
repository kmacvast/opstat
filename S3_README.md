# opstat - S3

Live S3 object storage performance telemetry from VAST VMS. Maps **ProtoMetrics
S3Common** instantaneous rates (with legacy `proto_name=S3` fallback) to a
three-panel TUI. When the cluster exports **S3Metrics** per-opcode counters and
histograms, the opcode panel switches to native GET / PUT / DELETE / HEAD / LIST /
MULTIPART rates automatically.

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
| `rd_bw` / `wr_bw` | Throughput (bytes/s → MB/s in TUI) |
| `md_iops`, `rd_md_iops`, `wr_md_iops` | Metadata / LIST / HEAD style workload |
| `read_latency__avg` / `write_latency__avg` | Data-path latency |
| `read_latency__rate` / `write_latency__rate` | Latency fallbacks when avg is zero |
| `read_size__avg` / `write_size__avg` | Avg I/O size proxies |
| `rd_latency` / `wr_latency` | Additional latency aliases |

**Fallback:** if `S3Common` monitor creation fails, opstat retries
`ProtoMetrics,proto_name=S3,...` and labels the source `S3`.

### S3Metrics (when exported)

| Class | Examples | Role |
|-------|----------|------|
| Counters | `get_object`, `put_object`, `multi_part_upload`, `cmd_errors` | Opcode rates via sample deltas |
| Histograms | `head_object`, `delete_object`, `get_bucket`, `initiate_mpu`, `complete_mpu` | Per-op latency (`__rate` / `__avg`) |

On startup opstat probes `S3Metrics`. When a VMS build does not export these
props (HTTP 400 `property_error` or empty prop_list), the opcode panel uses
S3Common aggregates (GET←`rd_iops`, PUT←`wr_iops`, METADATA←`md_iops`).

### Workload mix bars

Health panel mix uses **component sum** (`rd + wr + md`) as the denominator so
metadata percentage never exceeds 100%. The aggregate `S3Common,iops` field may be
data-path only and is not used alone for mix math.

---

## Dashboard Panels (v0.1.2)

1. **S3 HEALTH & WORKLOAD** - status badge (ACTIVE / IDLE / HOT), ops/lat/BW, mix bars (GET / PUT / metadata), delta arrows
2. **LATENCY & THROUGHPUT** - GET / PUT data path with ops, latency, throughput, I/O size; metadata aggregate
3. **S3 OPCODE BREAKDOWN** - only opcodes with live data this refresh (see below)

---

## S3 Opcode Breakdown Panel

| Opcode | Category | Preferred source |
|--------|----------|------------------|
| `GET / READ` | data | `S3Metrics,get_object` or `S3Common,rd_iops` |
| `PUT / WRITE` | data | `S3Metrics,put_object` or `S3Common,wr_iops` |
| `DELETE` | data | `S3Metrics,delete_object` (+ `delete_objects`) |
| `HEAD` | metadata | `S3Metrics,head_object` / `head_bucket` |
| `LIST` | metadata | `S3Metrics,get_bucket` |
| `MULTIPART` | data | `S3Metrics,multi_part_upload` |
| `INIT MPU` / `COMPLETE MPU` | data | `initiate_mpu` / `complete_mpu` histograms |

### Source labels

| Source | Meaning |
|--------|---------|
| `MEASURED` | Direct from `S3Common` (`GET`, `PUT`) |
| `AGGREGATE` | Metadata total from `md_iops` when per-opcode split is unavailable |
| `S3METRICS` | Native per-opcode export |

Empty opcode rows are omitted rather than displayed with dashes.

---

## Drill-Down

| Key | Scope | Metrics |
|-----|-------|---------|
| `c` | cNode | `ProtoMetrics,proto_name=S3Common,*` |
| `b` | Bucket / **view** | `ViewMetrics,*` (BucketViewMetrics probed at discovery) |
| `t` | Tenant | `TenantMetrics,*` with cumulative delta engine |
| `i` | VIP | `ProtoMetrics,proto_name=S3Common,*` on `/vips/` |
| `x` | Exit drill | Return to cluster dashboard |
| `Space` | Refresh | Immediate poll |
| `q` | Quit | |

Bucket/tenant ranking scans objects in batches, ranks by ops/s, and displays the
top targets. Bucket monitors use `no_aggregation=True` (seconds resolution).
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

Each refresh appends one row per LATENCY & THROUGHPUT and OPCODE panel line with
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
| `vast.s3.latency` | microseconds |
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
