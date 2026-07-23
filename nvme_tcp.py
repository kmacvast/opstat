#!/usr/bin/env python3
################################################################################
# Script:      nvme_tcp.py
#
# Descr:       NVMe-oTCP block performance statistics for opstat. Queries
#              VMS BlockMetrics and ProtoMetrics (BlockCommon) counters and
#              displays live I/O, reclamation, and fabric/admin telemetry.
#
# Version:     0.1.1
# Author:      KMac
################################################################################

import base64
import csv
import io
import getpass
import os
import re
import shutil
import ssl
import sys
import time
from datetime import datetime

import openmetrics
import vast_api_log
import vast_common
from tui_layout import (
    display_width, join_columns, pad_display, format_fixed_number,
    format_scaled_metric, truncate_display, c, set_color, set_unicode, glyph_set,
    as_float, raw_bw_to_mb_sec, format_throughput_mbs, format_latency_us,
    format_iops, format_block_size, format_os_release,
    _RST, _BOLD, _DIM, _GREEN, _YELLOW, _CYAN,
    _BRED, _BGREEN, _BYELLOW, _BBLUE, _BMAGENTA, _BCYAN, _BWHITE,
)

# Table column widths - headers and data rows share these exactly.
_COL_SEP = "  "
_OPS_W = {"proc": 22, "iops": 14, "throughput": 14, "size": 12, "latency": 14}
_PATH_W = {"name": 36, "iops": 12, "throughput": 14, "latency": 14}

VERSION = "0.1.2"

DEFAULT_PORT = 443
DEFAULT_USER = "admin"
DEFAULT_REFRESH_SECONDS = 5
DEFAULT_API_TIME_FRAME = "10m"

# BlockMetrics and ProtoMetrics cannot be mixed in a single VMS monitor.
BLOCK_READ_BW_FQN  = "ProtoMetrics,proto_name=BlockCommon,rd_bw"
BLOCK_WRITE_BW_FQN = "ProtoMetrics,proto_name=BlockCommon,wr_bw"
BLOCK_READ_SIZE_FQN  = "ProtoMetrics,proto_name=BlockCommon,read_size__avg"
BLOCK_WRITE_SIZE_FQN = "ProtoMetrics,proto_name=BlockCommon,write_size__avg"

# Volume object monitors expose per-op IOPS as VolumeMetrics *_latency__rate (not read_req).
VOLUME_READ_SIZE_FQN  = "VolumeMetrics,read_size__avg"
VOLUME_WRITE_SIZE_FQN = "VolumeMetrics,write_size__avg"
VOLUME_OP_METRICS = {
    "read":              ("VolumeMetrics,read_latency__rate",              "VolumeMetrics,read_latency__avg"),
    "write":             ("VolumeMetrics,write_latency__rate",             "VolumeMetrics,write_latency__avg"),
    "compare_and_write": ("VolumeMetrics,compare_and_write_latency__rate", "VolumeMetrics,compare_and_write_latency__avg"),
    "unmap":             ("VolumeMetrics,unmap_latency__rate",             "VolumeMetrics,unmap_latency__avg"),
    "write_zeros":       ("VolumeMetrics,write_zeroes_latency__rate",        "VolumeMetrics,write_zeroes_latency__avg"),
}
# Fabric/admin BlockMetrics are cluster-scoped only on current VMS builds.
_VOLUME_UNAVAILABLE_OPS = frozenset({"discovery", "handle_request", "transport_free", "get_ns_list"})
# Read/write use VolumeMetrics at volume scope; all other ops use cluster BlockMetrics.
VOLUME_PRIMARY_OPS = frozenset({"read", "write"})

# (key, label, category, ops_fqn, avg_fqn)
OPS = [
    ("read",              "READ",         "data",    "BlockMetrics,read_req",                    "BlockMetrics,read_latency__avg"),
    ("write",             "WRITE",        "data",    "BlockMetrics,write_req",                   "BlockMetrics,write_latency__avg"),
    ("compare_and_write", "CMP+WRITE",    "data",    "BlockMetrics,compare_and_write_req",       "BlockMetrics,compare_and_write_latency__avg"),
    ("unmap",             "DEALLOCATE",   "reclaim", "BlockMetrics,unmap_req",                   "BlockMetrics,unmap_latency__avg"),
    ("write_zeros",       "WRITE ZEROES", "reclaim", "BlockMetrics,write_zeros_req",             "BlockMetrics,write_zeroes_latency__avg"),
    ("discovery",         "DISCOVERY",    "fabric",  "BlockMetrics,discovery_req",                 "BlockMetrics,discovery_latency__avg"),
    ("handle_request",    "HANDLE REQ",   "fabric",  "BlockMetrics,handle_request_latency__rate", "BlockMetrics,handle_request_latency__avg"),
    ("transport_free",    "XPORT FREE",   "fabric",  "BlockMetrics,transport_free_latency__rate", "BlockMetrics,transport_free_latency__avg"),
    ("get_ns_list",       "GET NS LIST",  "admin",   "BlockMetrics,get_ns_list_latency__rate",    "BlockMetrics,get_ns_list_latency__avg"),
]

# Fixed table row order and user-facing operation names.
TABLE_ORDER = [key for key, *_rest in OPS]
DISPLAY_NAMES = {
    "read":              "READ",
    "write":             "WRITE",
    "compare_and_write": "COMPARE & WRITE",
    "unmap":             "UNMAP (TRIM)",
    "write_zeros":       "WRITE ZEROES",
    "discovery":         "FABRIC DISCOVERY",
    "handle_request":    "FABRIC REQ HANDLE",
    "transport_free":    "FABRIC XPORT FREE",
    "get_ns_list":       "ADMIN GET NS",
}

# Data I/O operations included in the header TOTAL IOPS summary.
# Fabric/admin ops share one monitor - VMS allows rate+avg pairs across these.
IO_LABELS = frozenset({"READ", "WRITE"})
_FABRIC_ADMIN_KEYS = frozenset({"handle_request", "transport_free", "get_ns_list"})
_DATA_IO_KEYS = frozenset({"read", "write", "compare_and_write"})
_READ_MIX_KEYS = frozenset({"read"})
_WRITE_MIX_KEYS = frozenset({"write", "compare_and_write"})
_RECLAIM_MIX_KEYS = frozenset({"unmap", "write_zeros"})
_FABRIC_MIX_KEYS = frozenset({"discovery", "handle_request", "transport_free", "get_ns_list"})
_DATA_IO_SIZE_KEYS = frozenset({"read", "write"})
# Fabric/admin BlockMetrics use *_latency__rate counters (already ops/sec).
_RATE_OPS_KEYS = frozenset({"handle_request", "transport_free", "get_ns_list"})
DATA_LABELS = frozenset(label for _, label, cat, _o, _a in OPS if cat == "data")
RECLAIM_LABELS = frozenset(label for _, label, cat, _o, _a in OPS if cat == "reclaim")
FABRIC_LABELS = frozenset(label for _, label, cat, _o, _a in OPS if cat in ("fabric", "admin"))

_DRILL_CFG = {
    "vip": {
        "object_type": "vip",
        "endpoint":    "/vips/",
        "name_fields": ("ip", "name", "address"),
    },
    "cnode": {
        "object_type": "cnode",
        "endpoint":    "/cnodes/",
        "name_fields": ("name", "hostname", "mgmt_ip"),
    },
    "host": {
        "object_type": "blockhost",
        "endpoint":    "/blockhosts/",
        "name_fields": ("name", "nqn"),
        "subtitle_fields": ("nqn",),
    },
}

_MAX_DRILL_OBJECTS = 8

# Metrics not exposed on current VMS builds - surfaced in discover-metrics notes.
_UNAVAILABLE_TELEMETRY = (
    "FLUSH (explicit NVMe flush command counters)",
    "CONNECT / DISCONNECT (queue-pair session counters)",
    "KEEP_ALIVE heartbeat counters",
    "Queue depth / in-flight command gauges",
    "PDU CRC error and retry counters",
)

ARGS = None
VMS = PORT = USER = PASSWORD = SAMPLE_AVERAGE = CSV_FILE = None
VOLUME_NAMES = []
VOLUME_IDS = []
VOLUME_SCOPED = False
SCOPE_LABEL = "All Volumes"
REFRESH_SECONDS = DEFAULT_REFRESH_SECONDS
API_TIME_FRAME = DEFAULT_API_TIME_FRAME
SAMPLE_AVERAGE_MODE = False
BASE_URL = AUTH = HEADERS = None
SSL_CTX = ssl._create_unverified_context()

OPS_MONITOR_IDS = []
CLUSTER_SUPPLEMENT_MONITOR_IDS = []
PROTO_MONITOR_ID = None
CLUSTER_ID = CLUSTER_NAME = None
CLUSTER_OS = None
LAST_ROWS = []
LAST_SAMPLE = "-"
PREV_ROWS = []
PREV_COUNTER_STATE = {}
LAST_POLL_MONOTONIC = None
DELTA_READY = False
DRILL_MODE = None
DRILL_OBJECTS = []
DRILL_MONITORS = []
LAST_DRILL_ROWS = []
DRILL_ERROR = None
RUN_STARTED_AT = None
RUN_STATS = {}
_COLOR = False

CSV_HEADER = [
    "local_time", "runtime", "vms", "port", "cluster", "cluster_id",
    "ops_monitor_id", "proto_monitor_id", "sample_mode", "api_time_frame",
    "selected_sample", "operation", "category", "ops_per_sec", "percent_workload",
    "avg_latency_us", "throughput_mb_sec", "avg_io_bytes",
]

_ANSI_RE = re.compile(r"\033\[[^m]*m")
_UTF8 = (sys.stdout.encoding or "ascii").lower().startswith("utf")
_G = glyph_set(_UTF8)
_H, _V = _G["H"], _G["V"]
_TL, _TR, _BL, _BR, _LT, _RT = _G["TL"], _G["TR"], _G["BL"], _G["BR"], _G["LT"], _G["RT"]
_BLK, _SHD = _G["BLK"], _G["SHD"]
_ARR_UP, _ARR_DN, _ARR_EQ, _DOT, _MUS = _G["ARR_UP"], _G["ARR_DN"], _G["ARR_EQ"], _G["DOT"], _G["MUS"]

def _fresh_run_stats():
    return {
        label: {"min_us": None, "max_us": None, "weighted_sum_us": 0.0, "weight": 0.0, "seen_sample_ids": set()}
        for _k, label, _c, _o, _a in OPS
    }


def init_config(args):
    global ARGS, VMS, PORT, USER, PASSWORD, SAMPLE_AVERAGE, REFRESH_SECONDS, CSV_FILE
    global API_TIME_FRAME, SAMPLE_AVERAGE_MODE, BASE_URL, AUTH, HEADERS, _COLOR
    global RUN_STARTED_AT, RUN_STATS, OPS_MONITOR_IDS, CLUSTER_SUPPLEMENT_MONITOR_IDS, PROTO_MONITOR_ID
    global CLUSTER_ID, CLUSTER_NAME
    global LAST_ROWS, LAST_SAMPLE, PREV_ROWS, PREV_COUNTER_STATE, LAST_POLL_MONOTONIC, DELTA_READY
    global DRILL_MODE, DRILL_OBJECTS, DRILL_MONITORS, LAST_DRILL_ROWS, DRILL_ERROR
    global VOLUME_NAMES, VOLUME_IDS, VOLUME_SCOPED, SCOPE_LABEL

    ARGS = args
    password = args.password or os.environ.get("VAST_PASSWORD")
    if not password:
        try:
            password = getpass.getpass(f"Password for {args.user}@{args.vms}: ")
        except KeyboardInterrupt:
            print()
            sys.exit(1)

    VMS = args.vms
    PORT = args.port
    USER = args.user
    PASSWORD = password
    SAMPLE_AVERAGE = args.sample_average
    REFRESH_SECONDS = args.refresh
    CSV_FILE = args.csv
    API_TIME_FRAME = SAMPLE_AVERAGE or DEFAULT_API_TIME_FRAME
    SAMPLE_AVERAGE_MODE = SAMPLE_AVERAGE is not None
    BASE_URL = f"https://{VMS}/api" if PORT == 443 else f"https://{VMS}:{PORT}/api"
    AUTH = base64.b64encode(f"{USER}:{PASSWORD}".encode()).decode()
    HEADERS = {
        "Authorization": f"Basic {AUTH}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": f"opstat/nvme-tcp/{VERSION}",
    }
    vast_common.configure_connection(BASE_URL, HEADERS, SSL_CTX)
    log_path = vast_api_log.configure(
        getattr(args, "log_api_calls", False), "nvme-tcp", VMS, PORT,
    )
    if log_path:
        print(f"API call logging enabled: {log_path}", file=sys.stderr, flush=True)
    om_path = openmetrics.configure(
        getattr(args, "export_openmetrics", False),
        getattr(args, "openmetrics_file", None),
        "nvme_tcp", VMS,
    )
    if om_path:
        print(f"OpenMetrics export enabled: {om_path}", file=sys.stderr, flush=True)
    _COLOR = sys.stdout.isatty() and not args.no_color
    set_color(_COLOR)
    set_unicode(_UTF8)
    RUN_STARTED_AT = datetime.now()
    RUN_STATS = _fresh_run_stats()
    OPS_MONITOR_IDS = []
    CLUSTER_SUPPLEMENT_MONITOR_IDS = []
    PROTO_MONITOR_ID = None
    CLUSTER_ID = CLUSTER_NAME = None
    LAST_ROWS = []
    LAST_SAMPLE = "-"
    PREV_ROWS = []
    PREV_COUNTER_STATE = {}
    LAST_POLL_MONOTONIC = None
    DELTA_READY = False
    VOLUME_NAMES = []
    VOLUME_IDS = []
    VOLUME_SCOPED = False
    SCOPE_LABEL = "All Volumes"
    DRILL_MODE = DRILL_ERROR = None
    DRILL_OBJECTS = []
    DRILL_MONITORS = []
    LAST_DRILL_ROWS = []


def metric_names_for_op(op_key):
    for key, _label, _cat, ops_fqn, avg_fqn in active_ops():
        if key == op_key:
            return {"ops": ops_fqn, "avg": avg_fqn}
    raise KeyError(op_key)


def scoped_metric_fqn(fqn):
    """Legacy helper - prefer active_ops() for scope-aware metric selection."""
    return fqn


def active_ops():
    """Return OPS rows with metric FQNs for cluster or volume monitor scope."""
    rows = []
    for key, label, category, cluster_ops, cluster_avg in OPS:
        if VOLUME_SCOPED and key in VOLUME_PRIMARY_OPS:
            vol_ops, vol_avg = VOLUME_OP_METRICS.get(key, (None, None))
            rows.append((key, label, category, vol_ops, vol_avg))
        else:
            rows.append((key, label, category, cluster_ops, cluster_avg))
    return rows


def volume_primary_ops_rows():
    """Volume object monitors - read/write only."""
    rows = []
    for key, label, category, _cluster_ops, _cluster_avg in OPS:
        if key not in VOLUME_PRIMARY_OPS:
            continue
        vol_ops, vol_avg = VOLUME_OP_METRICS.get(key, (None, None))
        rows.append((key, label, category, vol_ops, vol_avg))
    return rows


def cluster_supplement_ops_rows():
    """Cluster BlockMetrics for every op except volume-scoped read/write."""
    return [
        row for row in OPS if row[0] not in VOLUME_PRIMARY_OPS
    ]


def build_proto_prop_list(cluster_scope_only=False):
    if VOLUME_SCOPED and not cluster_scope_only:
        return [VOLUME_READ_SIZE_FQN, VOLUME_WRITE_SIZE_FQN]
    return [
        BLOCK_READ_BW_FQN, BLOCK_WRITE_BW_FQN,
        BLOCK_READ_SIZE_FQN, BLOCK_WRITE_SIZE_FQN,
    ]


def ops_metric_is_rate(ops_fqn, op_key):
    """True when the VMS metric is already an instantaneous rate (ops/sec)."""
    return op_key in _RATE_OPS_KEYS or (ops_fqn or "").endswith("__rate")


def parse_volume_filter(raw):
    """Parse comma-separated volume names from CLI input."""
    if not raw:
        return []
    return [part.strip() for part in str(raw).split(",") if part.strip()]


def resolve_volume_names(names):
    """Resolve user-supplied volume names to VMS volume IDs."""
    if not names:
        return [], []
    volumes = normalize_list_response(api_request("GET", "/volumes/"))
    by_exact = {str(v.get("name")): v for v in volumes if v.get("name")}
    ids = []
    resolved = []
    for name in names:
        match = by_exact.get(name)
        if match is None:
            # No exact match: refuse rather than silently binding a substring hit,
            # which could scope metrics to an unintended volume.
            near = sorted(str(v.get("name")) for v in volumes if name in str(v.get("name", "")))
            hint = f" Did you mean: {', '.join(near[:5])}?" if near else ""
            raise RuntimeError(f"Volume not found: '{name}' (exact name required).{hint}")
        ids.append(match["id"])
        resolved.append(str(match.get("name")))
    return ids, resolved


def configure_volume_scope(args):
    """Resolve optional --volume/--volumes into global scope works scope state."""
    global VOLUME_NAMES, VOLUME_IDS, VOLUME_SCOPED, SCOPE_LABEL
    names = parse_volume_filter(getattr(args, "volumes", None))
    if not names:
        VOLUME_NAMES = []
        VOLUME_IDS = []
        VOLUME_SCOPED = False
        SCOPE_LABEL = "All Volumes"
        return
    ids, resolved = resolve_volume_names(names)
    VOLUME_NAMES = resolved
    VOLUME_IDS = ids
    VOLUME_SCOPED = True
    SCOPE_LABEL = ", ".join(resolved)


def counter_state_key(scope, op_key):
    return f"{scope}:{op_key}"


def parse_api_timestamp(value):
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


def rate_from_counter_delta(scope, op_key, raw_value, poll_time):
    """Convert cumulative counter samples into ops/sec using prior poll state."""
    global PREV_COUNTER_STATE, DELTA_READY
    state_key = counter_state_key(scope, op_key)
    raw = as_float(raw_value)
    prev = PREV_COUNTER_STATE.get(state_key)
    PREV_COUNTER_STATE[state_key] = {"raw": raw, "poll_time": poll_time}
    if raw is None or prev is None or prev.get("raw") is None:
        return None
    elapsed = poll_time - prev["poll_time"]
    if elapsed <= 0:
        return None
    delta = raw - prev["raw"]
    if delta < 0:
        delta = raw
    if delta <= 0:
        return None
    DELTA_READY = True
    return delta / elapsed


def rate_from_timeseries(data, prop_idx, ops_fqn):
    """Derive ops/sec from the two newest cumulative counter samples in a monitor."""
    idx = prop_idx.get(ops_fqn)
    if idx is None or len(data) < 2:
        return None
    row_new, row_old = data[0], data[1]
    if idx >= len(row_new) or idx >= len(row_old):
        return None
    v_new = as_float(row_new[idx])
    v_old = as_float(row_old[idx])
    if v_new is None or v_old is None:
        return None
    t_new = parse_api_timestamp(row_new[0])
    t_old = parse_api_timestamp(row_old[0])
    if t_new is None or t_old is None:
        return None
    elapsed = t_new - t_old
    if elapsed <= 0:
        return None
    delta = v_new - v_old
    if delta < 0:
        delta = v_new
    if delta <= 0:
        return None
    return delta / elapsed


def apply_op_rates(rows, poll_time, scope="cluster"):
    """Convert raw counter/rate samples into true per-second ops and latency."""
    for row in rows:
        ops_fqn = row.get("ops_fqn")
        op_key = row["key"]
        raw_ops = row.pop("ops_raw", None)
        raw_lat = row.pop("lat_raw", None)
        if SAMPLE_AVERAGE_MODE and row.get("series_data") is not None:
            _prop_list, data, prop_idx = _result_parts(row["series_data"])
            if ops_metric_is_rate(ops_fqn, op_key):
                idx = prop_idx.get(ops_fqn)
                values = [
                    as_float(entry[idx])
                    for entry in data
                    if idx is not None and idx < len(entry) and as_float(entry[idx]) is not None
                ]
                row["ops_sec"] = _list_avg(values)
            else:
                row["ops_sec"] = rate_from_timeseries(data, prop_idx, ops_fqn)
            avg_idx = prop_idx.get(row.get("avg_fqn"))
            lat_values = []
            ops_idx = prop_idx.get(ops_fqn)
            for entry in data:
                ops = as_float(entry[ops_idx]) if ops_idx is not None and ops_idx < len(entry) else None
                avg = as_float(entry[avg_idx]) if avg_idx is not None and avg_idx < len(entry) else None
                if ops is not None and ops > 0 and avg is not None and avg > 0:
                    lat_values.append((ops, avg))
            row["avg_us"] = _weighted_avg(lat_values)
        elif ops_metric_is_rate(ops_fqn, op_key):
            row["ops_sec"] = raw_ops if raw_ops is not None and raw_ops > 0 else None
            row["avg_us"] = raw_lat if row["ops_sec"] and raw_lat is not None and raw_lat > 0 else None
        else:
            row["ops_sec"] = rate_from_counter_delta(scope, op_key, raw_ops, poll_time)
            row["avg_us"] = raw_lat if row.get("ops_sec") and raw_lat is not None and raw_lat > 0 else None
        row.pop("series_data", None)
        row.pop("ops_fqn", None)
        row.pop("avg_fqn", None)
    return rows


def monitor_scope():
    """Return (object_type, object_ids) for primary cluster/volume monitors."""
    if VOLUME_SCOPED and VOLUME_IDS:
        return "volume", VOLUME_IDS
    return "cluster", [CLUSTER_ID]


def build_ops_monitor_groups(cluster_scope_only=False, ops_rows=None):
    """Return metric groups compatible with the supplied or active scope."""
    if ops_rows is None:
        ops_rows = OPS if cluster_scope_only else active_ops()
    groups = []
    fabric_props = []
    for key, _label, _cat, ops_fqn, avg_fqn in ops_rows:
        if key in _FABRIC_ADMIN_KEYS:
            if ops_fqn and avg_fqn:
                fabric_props.extend([ops_fqn, avg_fqn])
            continue
        if not ops_fqn:
            continue
        props = [p for p in (ops_fqn, avg_fqn) if p]
        if props:
            groups.append(props)
    if fabric_props:
        groups.append(fabric_props)
    return groups


def build_ops_prop_list():
    """Flat prop list for discovery output."""
    props = []
    for group in build_ops_monitor_groups():
        for prop in group:
            if prop not in props:
                props.append(prop)
    return props


def merge_monitor_query_results(results):
    """Merge multiple monitor query payloads into one synthetic result dict."""
    metric_names = []
    seen = set()
    for result in results:
        if not isinstance(result, dict):
            continue
        for name in result.get("prop_list", []):
            if name in ("timestamp", "object_id") or name in seen:
                continue
            seen.add(name)
            metric_names.append(name)

    rows_by_ts = {}
    for result in results:
        if not isinstance(result, dict):
            continue
        prop_list = result.get("prop_list", [])
        prop_idx = {name: idx for idx, name in enumerate(prop_list)}
        for row in result.get("data", []):
            if not row:
                continue
            ts = row[0]
            bucket = rows_by_ts.setdefault(ts, {})
            for name in metric_names:
                idx = prop_idx.get(name)
                if idx is not None and idx < len(row):
                    bucket[name] = row[idx]

    prop_list = ["timestamp", *metric_names]
    data = [[ts, *[rows_by_ts[ts].get(name) for name in metric_names]] for ts in sorted(rows_by_ts, reverse=True)]
    return {"prop_list": prop_list, "data": data}


def query_ops_monitors(monitor_ids):
    """Query each BlockMetrics monitor separately (do not merge time series)."""
    return [api_request("GET", f"/monitors/{monitor_id}/query/") for monitor_id in monitor_ids]


def _monitor_result_for_op_key(monitor_results, ops_rows):
    """Map each operation key to the monitor query result that owns its metrics."""
    per_op = {}
    idx = 0
    fabric_result = None
    for key, _label, _cat, ops_fqn, _avg_fqn in ops_rows:
        if key in _FABRIC_ADMIN_KEYS:
            continue
        if not ops_fqn:
            per_op[key] = None
            continue
        per_op[key] = monitor_results[idx] if idx < len(monitor_results) else None
        idx += 1
    if idx < len(monitor_results):
        fabric_result = monitor_results[idx]
    for key in _FABRIC_ADMIN_KEYS:
        _ops_fqn = next((o for k, _l, _c, o, _a in ops_rows if k == key), None)
        per_op[key] = fabric_result if _ops_fqn else None
    return per_op


def extract_op_metrics_from_result(result, ops_fqn, avg_fqn):
    """Read raw counter/rate and latency samples for one operation."""
    if not isinstance(result, dict) or not result.get("data"):
        return None, None, "-", result
    _prop_list, data, prop_idx = _result_parts(result)
    ops_row, ops_sample = (
        select_latest_complete_row(data, prop_idx, [ops_fqn]) if ops_fqn else (None, "-")
    )
    lat_row, lat_sample = (
        select_latest_complete_row(data, prop_idx, [avg_fqn]) if avg_fqn else (None, "-")
    )
    selected_row = ops_row or lat_row
    if selected_row is None:
        return None, None, "-", result
    ops_raw = as_float(metric_value_from_row(ops_row, prop_idx, ops_fqn)) if ops_fqn and ops_row else None
    lat_raw = as_float(metric_value_from_row(lat_row or ops_row, prop_idx, avg_fqn)) if avg_fqn else None
    sample = ops_sample if ops_sample != "-" else lat_sample
    return ops_raw, lat_raw, sample, result


def build_ops_rows_from_monitor_results(
    monitor_results, scope="cluster", poll_time=None, ops_rows=None,
):
    """Build operation rows - one VMS monitor group per op (avoids merge skew)."""
    if not monitor_results:
        return [], "-"
    if ops_rows is None:
        ops_rows = active_ops()
    per_op = _monitor_result_for_op_key(monitor_results, ops_rows)
    rows = []
    samples = []
    poll_time = poll_time if poll_time is not None else time.monotonic()
    for key, label, category, ops_fqn, avg_fqn in ops_rows:
        result = per_op.get(key)
        if result is None:
            rows.append({
                "key": key,
                "label": label,
                "category": category,
                "ops_fqn": ops_fqn,
                "avg_fqn": avg_fqn,
                "ops_raw": None,
                "lat_raw": None,
                "sample": "-",
                "series_data": None,
                "ops_sec": None,
                "avg_us": None,
                "bw_mbs": None,
                "avg_io_bytes": None,
            })
            continue
        ops_raw, lat_raw, sample, result = extract_op_metrics_from_result(result, ops_fqn, avg_fqn)
        if sample and sample != "-":
            samples.append(str(sample))
        rows.append({
            "key": key,
            "label": label,
            "category": category,
            "ops_fqn": ops_fqn,
            "avg_fqn": avg_fqn,
            "ops_raw": ops_raw,
            "lat_raw": lat_raw,
            "sample": sample,
            "series_data": result if SAMPLE_AVERAGE_MODE else None,
            "ops_sec": None,
            "avg_us": None,
            "bw_mbs": None,
            "avg_io_bytes": None,
        })
    apply_op_rates(rows, poll_time, scope=scope)
    if VOLUME_SCOPED and any(as_float(r.get("ops_sec")) for r in rows):
        global DELTA_READY
        DELTA_READY = True
    selected_sample = samples[0] if samples else "-"
    if not DELTA_READY and not SAMPLE_AVERAGE_MODE and not VOLUME_SCOPED:
        selected_sample = f"{selected_sample} (warming up - need 2nd sample)"
    return rows, selected_sample


def build_ops_rows_from_monitor_results_average(monitor_results, scope="cluster", poll_time=None):
    """Rolling-average variant using per-monitor time series."""
    return build_ops_rows_from_monitor_results(monitor_results, scope=scope, poll_time=poll_time)


def compute_data_io_iops(rows):
    """Header TOTAL IOPS - data path operations only."""
    return sum(as_float(r["ops_sec"]) or 0 for r in rows if r["key"] in _DATA_IO_KEYS)


def _vlen(s):
    return display_width(s)


def box_top(title, width):
    raw_pre = f"{_TL}{_H} {title} "
    fill = max(0, width - display_width(raw_pre) - 1)
    if _COLOR:
        return c(f"{_TL}{_H} ", _DIM) + c(title, _BWHITE) + c(f" {_H * fill}{_TR}", _DIM)
    return f"{raw_pre}{_H * fill}{_TR}"


def _vpad(s, width, align="<"):
    return pad_display(s, width, align)


def rows_by_key(rows):
    return {r["key"]: r for r in rows}


def ordered_table_rows(rows):
    by_key = rows_by_key(rows)
    return [by_key[key] for key in TABLE_ORDER if key in by_key]


def reset_session_stats():
    """Clear session counters used for delta tracking."""
    global PREV_ROWS, RUN_STARTED_AT, RUN_STATS, PREV_COUNTER_STATE, LAST_POLL_MONOTONIC, DELTA_READY
    PREV_ROWS = []
    PREV_COUNTER_STATE = {}
    LAST_POLL_MONOTONIC = None
    DELTA_READY = False
    RUN_STARTED_AT = datetime.now()
    RUN_STATS = _fresh_run_stats()


def badge(text, color_code):
    """Colored status badge: [ TEXT ]"""
    return c(f"[ {text} ]", color_code)


def box_bottom(width):
    return c(f"{_BL}{_H * (width - 2)}{_BR}", _DIM)


def box_sep(width):
    return c(f"{_LT}{_H * (width - 2)}{_RT}", _DIM)


def box_row(content, width):
    inner = max(0, width - 4)
    if _vlen(content) > inner:
        content = truncate_display(content, inner) + (_RST if _COLOR else "")
    pad = max(0, inner - _vlen(content))
    border = c(_V, _DIM)
    return f"{border} {content}{' ' * pad} {border}"


def lat_dot(us):
    if us is None:
        return c(_DOT, _DIM)
    if us > 10_000:
        return c(_DOT, _BRED)
    if us > 1_000:
        return c(_DOT, _YELLOW)
    return c(_DOT, _BGREEN)


def delta_arrow(value):
    if value is None or abs(value) < 0.001:
        return c(_ARR_EQ, _DIM)
    return c(_ARR_UP, _BGREEN) if value > 0 else c(_ARR_DN, _YELLOW)


def delta_arrow_lat(value):
    if value is None or abs(value) < 0.01:
        return c(_ARR_EQ, _DIM)
    return c(_ARR_DN, _BGREEN) if value < 0 else c(_ARR_UP, _YELLOW)


def workload_bar(pct, bar_width=22, color=_GREEN):
    filled = max(0, min(bar_width, round(pct / 100 * bar_width)))
    empty = bar_width - filled
    return c(_BLK * filled, color) + c(_SHD * empty, _DIM) + f"  {pct:4.1f}%"


def fmt(value, width=12, precision=2):
    return format_fixed_number(value, width, precision)


def fmt_size(value, width=12):
    value = as_float(value)
    if value is None:
        return pad_display("", width, ">")
    if value < 1024:
        text = f"{value:.0f} B"
    elif value < 1024 ** 2:
        text = f"{value / 1024:.1f} KiB"
    elif value < 1024 ** 3:
        text = f"{value / (1024 ** 2):.1f} MiB"
    else:
        text = f"{value / (1024 ** 3):.2f} GiB"
    return format_scaled_metric(text, width)


def fmt_delta(value, precision=2):
    if value is None:
        return ""
    sign = "+" if value >= 0 else ""
    return f"{sign}{value:.{precision}f}"


def avg_io_size_bytes(ops_sec, bw_mbs):
    ops = as_float(ops_sec)
    bw = as_float(bw_mbs)
    if ops is None or bw is None or ops <= 0:
        return None
    return (bw * 1_000_000.0) / ops


def _weighted_avg(pairs):
    valid = [(w, v) for w, v in pairs if w is not None and v is not None and w > 0]
    if not valid:
        return None
    weight_sum = sum(w for w, _ in valid)
    if weight_sum <= 0:
        return None
    return sum(w * v for w, v in valid) / weight_sum


def _list_avg(values):
    return sum(values) / len(values) if values else None


def api_request(method, path, payload=None):
    return vast_common.request(method, path, payload)


def normalize_list_response(obj):
    return vast_common.normalize_list_response(obj)


def get_current_cluster():
    return vast_common.get_current_cluster(api_request)


def _capture_cluster_os():
    """Fetch the cluster VAST OS version once for the header (best-effort)."""
    global CLUSTER_OS
    CLUSTER_OS = vast_common.get_current_cluster_os(api_request)


def _create_monitor_raw(name_suffix, prop_list, object_type, object_ids):
    name = f"adhoc_opstat_{name_suffix}_{int(time.time())}"
    return vast_common.create_monitor_raw(
        api_request, name, prop_list, object_type, object_ids,
        time_frame=API_TIME_FRAME,
    )


def create_ops_monitors(name_prefix, object_type, object_ids, ops_rows=None):
    """Create one VMS monitor per compatible metric group.

    On a mid-loop failure, roll back this call's already-created monitors so a
    partially-warmed group never orphans monitors on the VMS.
    """
    monitor_ids = []
    try:
        for idx, prop_list in enumerate(build_ops_monitor_groups(ops_rows=ops_rows)):
            monitor_ids.append(
                _create_monitor_raw(f"{name_prefix}_ops_{idx}", prop_list, object_type, object_ids)
            )
    except Exception:
        for monitor_id in monitor_ids:
            delete_monitor(monitor_id)
        raise
    return monitor_ids


def create_cluster_monitors():
    """Create scope-aware BlockMetrics/VolumeMetrics + optional size/bw monitors."""
    global OPS_MONITOR_IDS, CLUSTER_SUPPLEMENT_MONITOR_IDS, PROTO_MONITOR_ID
    if VOLUME_SCOPED and VOLUME_IDS:
        OPS_MONITOR_IDS = create_ops_monitors(
            "nvme_vol", "volume", VOLUME_IDS, ops_rows=volume_primary_ops_rows(),
        )
        CLUSTER_SUPPLEMENT_MONITOR_IDS = create_ops_monitors(
            "nvme_cl", "cluster", [CLUSTER_ID], ops_rows=cluster_supplement_ops_rows(),
        )
    else:
        object_type, object_ids = monitor_scope()
        OPS_MONITOR_IDS = create_ops_monitors("nvme", object_type, object_ids)
        CLUSTER_SUPPLEMENT_MONITOR_IDS = []
    PROTO_MONITOR_ID = _create_monitor_raw(
        "nvme_proto", build_proto_prop_list(), *(
            ("volume", VOLUME_IDS) if VOLUME_SCOPED and VOLUME_IDS else ("cluster", [CLUSTER_ID])
        ),
    )


def create_monitor(name_suffix, prop_list):
    return _create_monitor_raw(name_suffix, prop_list, "cluster", [CLUSTER_ID])


def delete_monitor(monitor_id):
    vast_common.delete_monitor(api_request, monitor_id)


setup_keyboard = vast_common.setup_keyboard
restore_terminal = vast_common.restore_terminal


_CLEANED_UP = False


def cleanup():
    global _CLEANED_UP
    if _CLEANED_UP:
        return
    _CLEANED_UP = True
    restore_terminal()
    vast_common.drain_monitors(delete_monitor)
    vast_api_log.close()
    openmetrics.close()
    for monitor_id, detail in vast_common.failed_deletes():
        print(f"WARNING: monitor {monitor_id} not deleted: {detail}", file=sys.stderr)


def signal_handler(_signum, _frame):
    cleanup()
    print()
    sys.exit(0)


check_keypress = vast_common.check_keypress
clear_screen = vast_common.clear_screen


def _result_parts(result):
    prop_list = result.get("prop_list", [])
    data = result.get("data", [])
    prop_idx = {name: idx for idx, name in enumerate(prop_list)}
    return prop_list, data, prop_idx


def metric_value_from_row(row, prop_idx, metric_name):
    if not metric_name or row is None:
        return None
    idx = prop_idx.get(metric_name)
    if idx is None or idx >= len(row):
        return None
    return row[idx]


def select_latest_complete_row(data, prop_idx, prop_list):
    if not data:
        return None, "-"
    metric_indexes = [prop_idx[n] for n in prop_list if n in prop_idx]
    if not metric_indexes:
        return data[0], data[0][0] if data[0] else "-"
    best_row = data[0]
    best_score = -1
    for row in data:
        score = sum(1 for idx in metric_indexes if idx < len(row) and row[idx] is not None)
        if score > best_score:
            best_score = score
            best_row = row
        if score == len(metric_indexes):
            break
    return best_row, best_row[0] if best_row else "-"


def build_ops_rows_from_single_sample(result):
    _prop_list, data, prop_idx = _result_parts(result)
    if not data:
        return [], "-"
    selected_row, selected_sample = select_latest_complete_row(data, prop_idx, _prop_list)
    rows = []
    for key, label, category, ops_fqn, avg_fqn in OPS:
        rows.append({
            "key": key,
            "label": label,
            "category": category,
            "ops_sec": metric_value_from_row(selected_row, prop_idx, ops_fqn),
            "avg_us": metric_value_from_row(selected_row, prop_idx, avg_fqn),
            "sample": selected_sample,
            "bw_mbs": None,
            "avg_io_bytes": None,
        })
    return rows, selected_sample


def build_ops_rows_from_sample_average(result):
    _prop_list, data, prop_idx = _result_parts(result)
    if not data:
        return [], "-"
    newest_sample = data[0][0] if data and data[0] else "-"
    rows = []
    for key, label, category, ops_fqn, avg_fqn in OPS:
        ops_idx = prop_idx.get(ops_fqn)
        avg_idx = prop_idx.get(avg_fqn)
        ops_values = []
        latency_components = []
        for row in data:
            sample_time = row[0] if row else newest_sample
            ops = as_float(row[ops_idx]) if ops_idx is not None and ops_idx < len(row) else None
            avg = as_float(row[avg_idx]) if avg_idx is not None and avg_idx < len(row) else None
            if ops is not None:
                ops_values.append(ops)
            if ops is not None and ops > 0 and avg is not None:
                latency_components.append((ops, avg, sample_time))
        rows.append({
            "key": key,
            "label": label,
            "category": category,
            "ops_sec": _list_avg(ops_values),
            "avg_us": _weighted_avg([(r, a) for r, a, _ in latency_components]),
            "sample": latency_components[0][2] if latency_components else newest_sample,
            "bw_mbs": None,
            "avg_io_bytes": None,
        })
    return rows, f"rolling average over {API_TIME_FRAME}"


def extract_proto_from_single_sample(result):
    _prop_list, data, prop_idx = _result_parts(result)
    if not data:
        return None, None, None, None
    proto_props = build_proto_prop_list()
    selected_row, _sample = select_latest_complete_row(data, prop_idx, proto_props)
    return (
        raw_bw_to_mb_sec(metric_value_from_row(selected_row, prop_idx, BLOCK_READ_BW_FQN)),
        raw_bw_to_mb_sec(metric_value_from_row(selected_row, prop_idx, BLOCK_WRITE_BW_FQN)),
        as_float(metric_value_from_row(selected_row, prop_idx, BLOCK_READ_SIZE_FQN)),
        as_float(metric_value_from_row(selected_row, prop_idx, BLOCK_WRITE_SIZE_FQN)),
    )


def extract_proto_from_sample_average(result):
    _prop_list, data, prop_idx = _result_parts(result)
    if not data:
        return None, None, None, None
    read_bw, write_bw, read_sz, write_sz = [], [], [], []
    for row in data:
        for idx_list, fqn, dest in (
            (read_bw, BLOCK_READ_BW_FQN, raw_bw_to_mb_sec),
            (write_bw, BLOCK_WRITE_BW_FQN, raw_bw_to_mb_sec),
            (read_sz, BLOCK_READ_SIZE_FQN, as_float),
            (write_sz, BLOCK_WRITE_SIZE_FQN, as_float),
        ):
            idx = prop_idx.get(fqn)
            if idx is not None and idx < len(row):
                val = dest(row[idx])
                if val is not None:
                    idx_list.append(val)
    return _list_avg(read_bw), _list_avg(write_bw), _list_avg(read_sz), _list_avg(write_sz)


def extract_volume_sizes_from_result(result):
    """Read per-volume average I/O sizes from a VolumeMetrics monitor."""
    _prop_list, data, prop_idx = _result_parts(result)
    if not data:
        return None, None
    props = [VOLUME_READ_SIZE_FQN, VOLUME_WRITE_SIZE_FQN]
    selected_row, _sample = select_latest_complete_row(data, prop_idx, props)
    return (
        as_float(metric_value_from_row(selected_row, prop_idx, VOLUME_READ_SIZE_FQN)),
        as_float(metric_value_from_row(selected_row, prop_idx, VOLUME_WRITE_SIZE_FQN)),
    )


def throughput_mbs_from_iops_size(ops_sec, size_bytes):
    """Compute MB/s from instantaneous IOPS and average I/O size (bytes)."""
    ops = as_float(ops_sec)
    size = as_float(size_bytes)
    if ops is None or size is None or ops <= 0 or size <= 0:
        return None
    return ops * size / 1_000_000.0


def build_rows_from_results(ops_monitor_results, proto_result, scope="cluster", poll_time=None, ops_rows=None):
    if not ops_monitor_results:
        return [], "-"
    poll_time = poll_time if poll_time is not None else time.monotonic()
    rows, selected_sample = build_ops_rows_from_monitor_results(
        ops_monitor_results, scope=scope, poll_time=poll_time, ops_rows=ops_rows,
    )
    read_bw = write_bw = read_sz = write_sz = None
    if isinstance(proto_result, dict):
        if VOLUME_SCOPED:
            read_sz, write_sz = extract_volume_sizes_from_result(proto_result)
        elif SAMPLE_AVERAGE_MODE:
            read_bw, write_bw, _read_sz, _write_sz = extract_proto_from_sample_average(proto_result)
        else:
            read_bw, write_bw, _read_sz, _write_sz = extract_proto_from_single_sample(proto_result)
    for r in rows:
        if r["key"] == "read":
            if VOLUME_SCOPED:
                r["avg_io_bytes"] = read_sz if read_sz and read_sz > 0 else None
                r["bw_mbs"] = throughput_mbs_from_iops_size(r.get("ops_sec"), read_sz)
            else:
                r["bw_mbs"] = read_bw
                r["avg_io_bytes"] = avg_io_size_bytes(r.get("ops_sec"), read_bw)
        elif r["key"] == "write":
            if VOLUME_SCOPED:
                r["avg_io_bytes"] = write_sz if write_sz and write_sz > 0 else None
                r["bw_mbs"] = throughput_mbs_from_iops_size(r.get("ops_sec"), write_sz)
            else:
                r["bw_mbs"] = write_bw
                r["avg_io_bytes"] = avg_io_size_bytes(r.get("ops_sec"), write_bw)
        elif r.get("ops_sec") and r.get("bw_mbs"):
            r["avg_io_bytes"] = avg_io_size_bytes(r.get("ops_sec"), r.get("bw_mbs"))
    total_ops = sum(as_float(r["ops_sec"]) or 0 for r in rows)
    for r in rows:
        ops = as_float(r["ops_sec"])
        r["pct"] = ops / total_ops * 100.0 if total_ops > 0 and ops is not None else None
    return rows, selected_sample


def merge_volume_and_cluster_rows(vol_rows, cluster_rows):
    """Combine volume read/write rows with cluster-scoped supplement ops."""
    by_key = rows_by_key(cluster_rows)
    for row in vol_rows:
        by_key[row["key"]] = row
    rows = [by_key[key] for key in TABLE_ORDER if key in by_key]
    total_ops = sum(as_float(r["ops_sec"]) or 0 for r in rows)
    for r in rows:
        ops = as_float(r["ops_sec"])
        r["pct"] = ops / total_ops * 100.0 if total_ops > 0 and ops is not None else None
    return rows


def compute_combined_avg_latency(rows, labels=None):
    if labels:
        rows = [r for r in rows if r["label"] in labels]
    pairs = [(as_float(r["ops_sec"]), as_float(r["avg_us"])) for r in rows]
    return _weighted_avg([(ops, avg) for ops, avg in pairs if ops is not None and avg is not None and ops > 0])


def compute_data_io_weighted_latency(rows):
    """Weighted average latency across read, write, and compare-and-write ops."""
    pairs = []
    for r in rows:
        if r["key"] not in _DATA_IO_KEYS:
            continue
        ops = as_float(r["ops_sec"])
        avg = as_float(r["avg_us"])
        if ops is not None and ops > 0 and avg is not None:
            pairs.append((ops, avg))
    return _weighted_avg(pairs)


def compute_data_io_throughput_mbs(rows):
    """Read + write throughput only (data path bandwidth)."""
    total = 0.0
    found = False
    for r in rows:
        if r["key"] not in _DATA_IO_SIZE_KEYS:
            continue
        bw = as_float(r.get("bw_mbs"))
        if bw is not None and bw > 0:
            total += bw
            found = True
    return total if found else None


def compute_combined_data_io_size(rows):
    """IOPS-weighted average I/O size across read and write data ops."""
    weighted_sum = 0.0
    total_ops = 0.0
    for r in rows:
        if r["key"] not in _DATA_IO_SIZE_KEYS:
            continue
        ops = as_float(r["ops_sec"]) or 0
        size = as_float(r.get("avg_io_bytes"))
        if ops > 0 and size is not None and size > 0:
            weighted_sum += ops * size
            total_ops += ops
    return weighted_sum / total_ops if total_ops > 0 else None


def _ops_for_keys(rows, keys):
    return sum(as_float(r["ops_sec"]) or 0 for r in rows if r["key"] in keys)


def block_workload_mix(rows):
    """Return (read_pct, write_pct, reclaim_pct, fabric_pct) of total ops."""
    total = sum(as_float(r["ops_sec"]) or 0 for r in rows)
    if total <= 0:
        return 0.0, 0.0, 0.0, 0.0
    return (
        _ops_for_keys(rows, _READ_MIX_KEYS) / total * 100,
        _ops_for_keys(rows, _WRITE_MIX_KEYS) / total * 100,
        _ops_for_keys(rows, _RECLAIM_MIX_KEYS) / total * 100,
        _ops_for_keys(rows, _FABRIC_MIX_KEYS) / total * 100,
    )


def cluster_delta_summary(deltas):
    """Aggregate per-label deltas to cluster-wide totals."""
    ops_deltas = [d["ops"] for d in deltas.values() if "ops" in d]
    bw_deltas = [d["bw"] for label, d in deltas.items() if label in IO_LABELS and "bw" in d]
    lat_deltas = [(label, d["lat"]) for label, d in deltas.items() if "lat" in d]
    return (
        sum(ops_deltas) if ops_deltas else None,
        sum(bw_deltas) / 1024 if bw_deltas else None,
        lat_deltas,
    )


def compute_total_throughput_mbs(rows):
    valid = [as_float(r.get("bw_mbs")) for r in rows if r["label"] in IO_LABELS]
    valid = [v for v in valid if v is not None]
    return sum(valid) if valid else None


def compute_deltas(current_rows, prev_rows):
    if not prev_rows or not current_rows:
        return {}
    prev_by_label = {r["label"]: r for r in prev_rows}
    deltas = {}
    for r in current_rows:
        p = prev_by_label.get(r["label"])
        if not p:
            continue
        d = {}
        cur_ops = as_float(r["ops_sec"])
        prev_ops = as_float(p["ops_sec"])
        cur_lat = as_float(r["avg_us"])
        prev_lat = as_float(p["avg_us"])
        cur_bw = as_float(r.get("bw_mbs"))
        prev_bw = as_float(p.get("bw_mbs"))
        if cur_ops is not None and prev_ops is not None:
            d["ops"] = cur_ops - prev_ops
        if cur_lat is not None and prev_lat is not None:
            d["lat"] = cur_lat - prev_lat
        if cur_bw is not None and prev_bw is not None:
            d["bw"] = cur_bw - prev_bw
        if d:
            deltas[r["label"]] = d
    return deltas


def block_health_label(total_data_iops, read_lat_us, write_lat_us):
    """Return (label_string, ansi_color) for the block health status badge."""
    if total_data_iops is None or total_data_iops < 0.5:
        return "IDLE", _DIM
    if (read_lat_us is not None and read_lat_us > 2_000) or (write_lat_us is not None and write_lat_us > 5_000):
        return "HIGH LATENCY", _BRED
    return "HEALTHY", _BGREEN


def classify_block_workload(rows):
    """Return a human-readable NVMe block workload description."""
    total = sum(as_float(r["ops_sec"]) or 0 for r in rows)
    if total < 0.5:
        return "Idle / no block load"

    read_ops = _ops_for_keys(rows, _READ_MIX_KEYS)
    write_ops = _ops_for_keys(rows, _WRITE_MIX_KEYS)
    reclaim_ops = _ops_for_keys(rows, _RECLAIM_MIX_KEYS)
    fabric_ops = _ops_for_keys(rows, _FABRIC_MIX_KEYS)
    data_ops = read_ops + write_ops

    if fabric_ops / total > 0.50:
        return "fabric-overhead dominant / idle data workload"
    if reclaim_ops / total > 0.30:
        return "space-reclamation heavy (TRIM/UNMAP) workload"

    avg_size = compute_combined_data_io_size(rows)
    if avg_size is None:
        block_profile = "mixed-block"
    elif avg_size < 32 * 1024:
        block_profile = "small-block random"
    elif avg_size > 256 * 1024:
        block_profile = "large-block sequential"
    else:
        block_profile = "mixed-block"

    if data_ops <= 0:
        return f"{block_profile} idle data workload"

    if read_ops / data_ops > 0.70:
        direction = "read-heavy"
    elif write_ops / data_ops > 0.70:
        direction = "write-heavy"
    else:
        direction = "mixed read/write"

    return f"{block_profile} {direction} workload"


def sorted_rows(rows):
    return sorted(rows, key=lambda r: r["label"])


def sort_label():
    return "fixed operation order"


def ensure_csv_file():
    if not CSV_FILE:
        return
    try:
        needs_header = os.path.getsize(CSV_FILE) == 0
    except OSError:
        needs_header = True
    if needs_header:
        with open(CSV_FILE, "w", newline="") as f:
            csv.writer(f).writerow(CSV_HEADER)


def write_csv_rows(rows, selected_sample):
    if not CSV_FILE or not rows:
        return
    sample_mode = "sample average " + API_TIME_FRAME if SAMPLE_AVERAGE_MODE else "latest complete sample"
    runtime = str(datetime.now() - RUN_STARTED_AT).split(".")[0]
    local_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(CSV_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        for r in rows:
            writer.writerow([
                local_time, runtime, VMS, PORT, CLUSTER_NAME, CLUSTER_ID,
                ",".join(str(i) for i in OPS_MONITOR_IDS), PROTO_MONITOR_ID, sample_mode, API_TIME_FRAME,
                selected_sample, r["label"], r["category"],
                r.get("ops_sec"), r.get("pct"), r.get("avg_us"),
                r.get("bw_mbs"), r.get("avg_io_bytes"),
            ])


def fetch_monitor_query():
    global LAST_ROWS, LAST_SAMPLE, PREV_ROWS, LAST_POLL_MONOTONIC
    PREV_ROWS = LAST_ROWS
    poll_time = time.monotonic()
    LAST_POLL_MONOTONIC = poll_time
    proto_result = api_request("GET", f"/monitors/{PROTO_MONITOR_ID}/query/") if PROTO_MONITOR_ID else None

    if VOLUME_SCOPED and CLUSTER_SUPPLEMENT_MONITOR_IDS:
        vol_results = query_ops_monitors(OPS_MONITOR_IDS)
        cluster_results = query_ops_monitors(CLUSTER_SUPPLEMENT_MONITOR_IDS)
        vol_rows, sample = build_rows_from_results(
            vol_results, proto_result, scope="volume", poll_time=poll_time,
            ops_rows=volume_primary_ops_rows(),
        )
        cluster_rows, cluster_sample = build_rows_from_results(
            cluster_results, None, scope="cluster", poll_time=poll_time,
            ops_rows=cluster_supplement_ops_rows(),
        )
        rows = merge_volume_and_cluster_rows(vol_rows, cluster_rows)
        if sample == "-" and cluster_sample != "-":
            sample = cluster_sample
    else:
        ops_results = query_ops_monitors(OPS_MONITOR_IDS)
        scope = "volume" if VOLUME_SCOPED else "cluster"
        rows, sample = build_rows_from_results(
            ops_results, proto_result, scope=scope, poll_time=poll_time,
        )

    LAST_ROWS = rows
    LAST_SAMPLE = sample
    write_csv_rows(rows, sample)
    _export_openmetrics()


def _openmetrics_series():
    series = []
    for r in LAST_ROWS:
        series.append({
            "operation": r.get("label", ""),
            "category": r.get("category", "data"),
            "ops_sec": as_float(r.get("ops_sec")),
            "avg_us": as_float(r.get("avg_us")),
            "bw_bytes_sec": openmetrics.mbps_to_bytes_sec(as_float(r.get("bw_mbs"))),
            "io_bytes": as_float(r.get("avg_io_bytes")),
        })
    return series


def _export_openmetrics():
    if not openmetrics.is_enabled():
        return
    openmetrics.export_snapshot(
        CLUSTER_NAME, None, CLUSTER_NAME, _openmetrics_series(), sample=LAST_SAMPLE,
    )


def _obj_name(obj, name_fields):
    return vast_common.resolve_object_name(obj, name_fields)


def _cleanup_drill_monitors():
    global DRILL_MONITORS
    for ops_ids, proto_id, _name in DRILL_MONITORS:
        for ops_id in ops_ids:
            delete_monitor(ops_id)
        if proto_id is not None:
            delete_monitor(proto_id)
    DRILL_MONITORS = []


def enter_drill_mode(mode):
    global DRILL_MODE, DRILL_OBJECTS, DRILL_MONITORS, DRILL_ERROR, LAST_DRILL_ROWS
    cfg = _DRILL_CFG.get(mode)
    if not cfg:
        DRILL_ERROR = f"Unknown drill mode: {mode}"
        return
    try:
        data = api_request("GET", cfg["endpoint"])
        objects = normalize_list_response(data)
    except RuntimeError as e:
        DRILL_ERROR = f"Cannot fetch {mode} objects: {e}"
        return
    if not objects:
        DRILL_ERROR = f"No {mode} objects returned from {cfg['endpoint']}"
        return
    valid = [o for o in objects if "id" in o][:_MAX_DRILL_OBJECTS]
    subtitle_fields = cfg.get("subtitle_fields", ())
    DRILL_OBJECTS = []
    for o in valid:
        entry = {"id": o["id"], "name": _obj_name(o, cfg["name_fields"])}
        for field in subtitle_fields:
            val = o.get(field)
            if val:
                entry[field] = str(val)
        DRILL_OBJECTS.append(entry)
    _cleanup_drill_monitors()
    new_monitors = []
    for obj in DRILL_OBJECTS:
        try:
            ops_ids = create_ops_monitors(
                f"{mode}_{obj['id']}", cfg["object_type"], [obj["id"]], ops_rows=OPS,
            )
            proto_id = None
            if cfg["object_type"] != "blockhost":
                proto_id = _create_monitor_raw(
                    f"{mode}_{obj['id']}_proto", build_proto_prop_list(cluster_scope_only=True),
                    cfg["object_type"], [obj["id"]],
                )
            new_monitors.append((ops_ids, proto_id, obj["name"]))
        except RuntimeError:
            pass
    if not new_monitors:
        DRILL_ERROR = f"Could not create any {mode} monitors"
        DRILL_OBJECTS = []
        return
    DRILL_MONITORS = new_monitors
    DRILL_MODE = mode
    DRILL_ERROR = None
    LAST_DRILL_ROWS = []


def exit_drill_mode():
    global DRILL_MODE, DRILL_OBJECTS, LAST_DRILL_ROWS, DRILL_ERROR
    _cleanup_drill_monitors()
    DRILL_MODE = None
    DRILL_OBJECTS = []
    LAST_DRILL_ROWS = []
    DRILL_ERROR = None


def fetch_drill_query():
    global LAST_DRILL_ROWS
    poll_time = time.monotonic()
    drill_rows = []
    for ops_ids, proto_id, obj_name in DRILL_MONITORS:
        try:
            scope = f"drill:{obj_name}"
            ops_results = query_ops_monitors(ops_ids)
            proto_result = api_request("GET", f"/monitors/{proto_id}/query/") if proto_id else None
            rows, _ = build_rows_from_results(
                ops_results, proto_result, scope=scope, poll_time=poll_time, ops_rows=OPS,
            )
            if not rows:
                continue
            total_iops = compute_data_io_iops(rows)
            read_row = next((r for r in rows if r["key"] == "read"), None)
            write_row = next((r for r in rows if r["key"] == "write"), None)
            lat_pairs = []
            if read_row:
                rops = as_float(read_row.get("ops_sec"))
                rlat = as_float(read_row.get("avg_us"))
                if rops is not None and rlat is not None:
                    lat_pairs.append((rops, rlat))
            if write_row:
                wops = as_float(write_row.get("ops_sec"))
                wlat = as_float(write_row.get("avg_us"))
                if wops is not None and wlat is not None:
                    lat_pairs.append((wops, wlat))
            avg_lat = _weighted_avg(lat_pairs) if lat_pairs else None
            bw = compute_data_io_throughput_mbs(rows)
            subtitle = next(
                (obj.get("nqn") for obj in DRILL_OBJECTS if obj["name"] == obj_name and obj.get("nqn")),
                None,
            )
            drill_rows.append({
                "name": obj_name,
                "subtitle": subtitle,
                "total_iops": total_iops,
                "latency_us": avg_lat,
                "bw_mbs": bw,
            })
        except RuntimeError:
            pass
    LAST_DRILL_ROWS = sorted(
        drill_rows,
        key=lambda r: (r["total_iops"] or 0, r["bw_mbs"] or 0),
        reverse=True,
    )
    if openmetrics.is_enabled() and DRILL_MODE:
        openmetrics.export_drill(CLUSTER_NAME, DRILL_MODE, LAST_DRILL_ROWS, sample=LAST_SAMPLE)


def _c_latency_text(text, us):
    if us is None:
        return c(text, _DIM)
    if us > 10_000:
        return c(text, _BRED)
    if us > 1_000:
        return c(text, _YELLOW)
    return c(text, _BGREEN)


def _c_ops_text(text, ops):
    return c(text, _DIM) if ops is None or ops == 0 else c(text, _GREEN if ops < 10000 else _BWHITE)


def _operation_cell(row):
    name = DISPLAY_NAMES.get(row["key"], row["label"])
    ops = as_float(row["ops_sec"])
    w = _OPS_W["proc"]
    if row["key"] == "read":
        return _label_cell(name, w, _BCYAN if ops else _DIM)
    if row["key"] == "write":
        return _label_cell(name, w, _BYELLOW if ops else _DIM)
    return _label_cell(name, w, _BWHITE if ops else _DIM)


def _host_display_name(dr):
    name = dr.get("name", "?")
    subtitle = dr.get("subtitle")
    if subtitle:
        short = subtitle if len(subtitle) <= 36 else f"{subtitle[:18]}…{subtitle[-14:]}"
        return f"{name} ({short})"
    return name


def _dash_cell(width):
    return c(pad_display("-", width, ">"), _DIM)


def _metric_cell(text, width, color_code):
    """Right-align a value+unit string, then apply color."""
    return c(format_scaled_metric(text, width), color_code)


def _label_cell(text, width, color_code):
    """Left-align a label, then apply color."""
    return c(pad_display(text, width, "<"), color_code)


def _ops_table_header():
    w = _OPS_W
    return join_columns([
        c(pad_display("Operation", w["proc"], "<"), _BOLD),
        c(pad_display("IOPS", w["iops"], ">"), _BOLD),
        c(pad_display("Throughput", w["throughput"], ">"), _BOLD),
        c(pad_display("Avg Size", w["size"], ">"), _BOLD),
        c(pad_display("Latency", w["latency"], ">"), _BOLD),
    ], _COL_SEP)


def _path_table_header(col_name):
    w = _PATH_W
    return join_columns([
        c(pad_display(col_name, w["name"], "<"), _BOLD),
        c(pad_display("IOPS", w["iops"], ">"), _BOLD),
        c(pad_display("Throughput", w["throughput"], ">"), _BOLD),
        c(pad_display("Latency", w["latency"], ">"), _BOLD),
    ], _COL_SEP)


def _row_is_active(row):
    ops = as_float(row.get("ops_sec"))
    bw = as_float(row.get("bw_mbs"))
    has_ops = ops is not None and ops > 0
    has_bw = bw is not None and bw > 0
    return has_ops or has_bw


def _table_row_cells(row):
    w = _OPS_W
    active = _row_is_active(row)
    ops = as_float(row.get("ops_sec"))
    bw_val = as_float(row.get("bw_mbs"))
    has_ops = ops is not None and ops > 0
    has_bw = bw_val is not None and bw_val > 0
    if not active:
        return join_columns([
            _operation_cell(row),
            _dash_cell(w["iops"]),
            _dash_cell(w["throughput"]),
            _dash_cell(w["size"]),
            _dash_cell(w["latency"]),
        ], _COL_SEP)

    iops_s = (
        _c_ops_text(format_scaled_metric(format_iops(ops), w["iops"]), ops)
        if has_ops else _dash_cell(w["iops"])
    )
    bw_text, _ = format_throughput_mbs(row.get("bw_mbs"))
    bw_s = (
        _metric_cell(bw_text, w["throughput"], _CYAN if has_bw else _DIM)
        if has_bw else _dash_cell(w["throughput"])
    )
    size_text, size_val = format_block_size(row.get("avg_io_bytes"))
    if size_val and (has_ops or has_bw):
        if size_val and row["key"] == "read":
            size_color = _CYAN
        elif size_val and row["key"] == "write":
            size_color = _YELLOW
        else:
            size_color = _DIM
        size_s = _metric_cell(size_text, w["size"], size_color)
    else:
        size_s = _dash_cell(w["size"])
    lat_text, lat_us = format_latency_us(row.get("avg_us"), active=has_ops)
    lat_s = (
        _c_latency_text(format_scaled_metric(lat_text, w["latency"]), lat_us)
        if has_ops else _dash_cell(w["latency"])
    )
    return join_columns([_operation_cell(row), iops_s, bw_s, size_s, lat_s], _COL_SEP)


def _display_name(row):
    return DISPLAY_NAMES.get(row["key"], row["label"])


def _c_delta_positive(s, value):
    if value is None or value == 0:
        return c(s, _DIM)
    return c(s, _BGREEN) if value > 0 else c(s, _YELLOW)


def _c_delta_latency(s, value):
    if value is None or value == 0:
        return c(s, _DIM)
    return c(s, _BGREEN) if value < 0 else c(s, _YELLOW)


def _render_health_panel(rows, width):
    total_data_iops = compute_data_io_iops(rows)
    read_row = next((r for r in rows if r["key"] == "read"), None)
    write_row = next((r for r in rows if r["key"] == "write"), None)
    read_lat = as_float(read_row["avg_us"]) if read_row else None
    write_lat = as_float(write_row["avg_us"]) if write_row else None
    combined_lat = compute_data_io_weighted_latency(rows)
    bw_mbs = compute_data_io_throughput_mbs(rows)
    total_bw_gbs = bw_mbs / 1024 if bw_mbs is not None else None

    read_pct, write_pct, reclaim_pct, fabric_pct = block_workload_mix(rows)
    health_lbl, health_color = block_health_label(total_data_iops, read_lat, write_lat)
    workload_type = classify_block_workload(rows)
    deltas = compute_deltas(rows, PREV_ROWS)
    ops_delta, bw_delta, lat_deltas = cluster_delta_summary(deltas)

    print(box_top("BLOCK HEALTH & WORKLOAD", width))

    scope_text = SCOPE_LABEL if VOLUME_SCOPED else "All Volumes"
    print(box_row(
        c("Scope  ", _DIM) + c(scope_text, _BCYAN if VOLUME_SCOPED else _BWHITE),
        width,
    ))

    ops_s = c(f"{total_data_iops:,.2f} ops/s" if total_data_iops else "- ops/s", _BWHITE)
    if combined_lat is not None:
        lat_ms = combined_lat / 1000.0
        lat_s = _c_latency_text(f"{lat_ms:.2f} ms", combined_lat)
    else:
        lat_s = c("- ms", _DIM)
    bw_s = c(f"{total_bw_gbs:.3f} GB/s" if total_bw_gbs is not None else "- GB/s", _CYAN)
    status = (
        badge(health_lbl, health_color)
        + "   " + ops_s
        + "   " + c("•", _DIM) + "  " + lat_dot(combined_lat) + " " + lat_s
        + "   " + delta_arrow(bw_delta if deltas else None) + " " + bw_s
    )
    print(box_row(status, width))

    print(box_row(c("Workload  ", _DIM) + c(workload_type, _YELLOW), width))

    print(box_row(c(f"{'Read':<10}", _DIM) + workload_bar(read_pct, 22, _BCYAN), width))
    print(box_row(c(f"{'Write':<10}", _DIM) + workload_bar(write_pct, 22, _BYELLOW), width))
    print(box_row(c(f"{'Reclaim':<10}", _DIM) + workload_bar(reclaim_pct, 22, _BMAGENTA), width))
    print(box_row(c(f"{'Fabric':<10}", _DIM) + workload_bar(fabric_pct, 22, _BBLUE), width))

    if deltas:
        parts = []
        if ops_delta is not None:
            parts.append(
                delta_arrow(ops_delta)
                + " " + _c_delta_positive(f"{fmt_delta(ops_delta, 2)} ops/s", ops_delta)
            )
        if bw_delta is not None:
            parts.append(
                delta_arrow(bw_delta)
                + " " + _c_delta_positive(f"BW {fmt_delta(bw_delta, 3)} GB/s", bw_delta)
            )
        if lat_deltas:
            worst = max(lat_deltas, key=lambda x: abs(x[1]))
            parts.append(
                delta_arrow_lat(worst[1])
                + " " + _c_delta_latency(
                    f"Lat {fmt_delta(worst[1], 1)} {_MUS} [{worst[0]}]", worst[1]
                )
            )
        if parts:
            print(box_row(c("Δ  ", _DIM) + "   ".join(parts), width))

    mode_tag = "avg " + API_TIME_FRAME if SAMPLE_AVERAGE_MODE else "latest"
    print(box_row(
        c(f"Sample: {LAST_SAMPLE}   Mode: {mode_tag}   Frame: {API_TIME_FRAME}", _DIM),
        width,
    ))
    print(box_bottom(width))


def _render_insights_panel(rows, width):
    active_rows = [r for r in rows if (as_float(r["ops_sec"]) or 0) > 0]

    print(box_top("PERFORMANCE INSIGHTS", width))

    top_op = max(active_rows, key=lambda r: as_float(r["ops_sec"]) or 0, default=None)
    if top_op:
        pct_v = as_float(top_op.get("pct")) or 0
        name = _display_name(top_op)
        print(box_row(
            c("Top Contributor  ", _DIM)
            + c(name, _BWHITE)
            + c(f"  {pct_v:.1f}% of ops", _GREEN),
            width,
        ))
    else:
        print(box_row(c("Top Contributor  ", _DIM) + c("-", _DIM), width))

    active_with_lat = [
        r for r in active_rows
        if as_float(r["avg_us"]) is not None and as_float(r["avg_us"]) > 0
    ]
    if active_with_lat:
        hi = max(active_with_lat, key=lambda r: as_float(r["avg_us"]) or 0)
        us = as_float(hi["avg_us"])
        name = _display_name(hi)
        lat_text, _ = format_latency_us(us, active=True)
        print(box_row(
            c("Highest Latency  ", _DIM)
            + _c_latency_text(name, us)
            + "   " + lat_dot(us) + " " + _c_latency_text(lat_text, us),
            width,
        ))
    else:
        print(box_row(c("Highest Latency  ", _DIM) + c("-", _DIM), width))

    combined_size = compute_combined_data_io_size(rows)
    size_text, _ = format_block_size(combined_size)
    if combined_size is not None:
        print(box_row(
            c("Data Consumer    ", _DIM)
            + c(f"avg I/O {size_text}", _BCYAN),
            width,
        ))
    else:
        print(box_row(c("Data Consumer    ", _DIM) + c("-", _DIM), width))

    print(box_bottom(width))


def _render_header_block(rows, width):
    """Legacy alias - health panel replaces the old header block."""
    _render_health_panel(rows, width)


def _render_operations_table(rows, width):
    table_rows = ordered_table_rows(rows)
    print(box_top("OPERATIONS", width))
    print(box_row(_ops_table_header(), width))
    print(box_sep(width))
    for row in table_rows:
        print(box_row(_table_row_cells(row), width))
    print(box_bottom(width))


def _render_path_table(width):
    if DRILL_MODE == "host":
        mode_title = "HOST INITIATORS"
        col_name = "Host Initiator (IP/NQN)"
    elif DRILL_MODE == "vip":
        mode_title = "VIP PATHS"
        col_name = "VIP"
    else:
        mode_title = "CNODE PATHS"
        col_name = "cNode"
    print(box_top(mode_title, width))
    if DRILL_ERROR:
        print(box_row(c(f"Error: {DRILL_ERROR}", _BRED), width))
        print(box_bottom(width))
        return
    if not LAST_DRILL_ROWS:
        print(box_row(c("Collecting initiator metrics…", _DIM), width))
        print(box_bottom(width))
        return
    header = _path_table_header(col_name)
    print(box_row(header, width))
    print(box_sep(width))
    for dr in LAST_DRILL_ROWS:
        iops = as_float(dr["total_iops"])
        has_iops = iops is not None and iops > 0
        bw_text, bw_val = format_throughput_mbs(dr.get("bw_mbs"))
        has_bw = bw_val is not None and bw_val > 0
        lat_text, lat_us = format_latency_us(dr.get("latency_us"), active=has_iops)
        display = _host_display_name(dr) if DRILL_MODE == "host" else dr["name"]
        pw = _PATH_W
        line = join_columns([
            _label_cell(display, pw["name"], _BWHITE if has_iops or has_bw else _DIM),
            _c_ops_text(format_scaled_metric(format_iops(iops), pw["iops"]), iops)
            if has_iops else _dash_cell(pw["iops"]),
            _metric_cell(bw_text, pw["throughput"], _CYAN) if has_bw else _dash_cell(pw["throughput"]),
            _c_latency_text(format_scaled_metric(lat_text, pw["latency"]), lat_us)
            if has_iops else _dash_cell(pw["latency"]),
        ], _COL_SEP)
        print(box_row(line, width))
    print(box_bottom(width))


def _render_help_bar(width):
    legend = (
        c("[q]", _BWHITE) + c(" Quit ", _DIM)
        + c(" | ", _DIM)
        + c("[r]", _BWHITE) + c(" Reset Stats ", _DIM)
        + c(" | ", _DIM)
        + c("[v]", _BWHITE) + c(" Toggle VIP View ", _DIM)
        + c(" | ", _DIM)
        + c("[c]", _BWHITE) + c(" Toggle cNode View ", _DIM)
        + c(" | ", _DIM)
        + c("[h]", _BWHITE) + c(" Toggle Host View ", _DIM)
        + c(" | ", _DIM)
        + c("[p]", _BWHITE) + c(" Return to Main", _DIM)
    )
    print(c(_H * width, _DIM))
    print(c("  ", _DIM) + legend, flush=True)


def poll_tick():
    """One refresh poll: cluster monitors plus the active drill, if any."""
    fetch_monitor_query()
    if DRILL_MODE:
        fetch_drill_query()


def render_screen():
    """Compose the whole frame into a buffer, then flush it in one write."""
    buf = io.StringIO()
    real_stdout = sys.stdout
    sys.stdout = buf
    try:
        _render_frame()
    finally:
        sys.stdout = real_stdout
    vast_common.flush_frame(buf.getvalue())


def _render_frame():
    rows = LAST_ROWS
    if not rows:
        print(f"Waiting for data…  VMS={VMS}:{PORT}  cluster={CLUSTER_NAME}")
        return

    width = min(shutil.get_terminal_size((120, 40)).columns, 120)

    title = (
        c("  VAST NVMe-oTCP", _BCYAN) + c(" opstat", _BWHITE) + c(f" v{VERSION}", _DIM)
    )
    if DRILL_MODE == "vip":
        title += c("  - VIP PATH VIEW", _BYELLOW)
    elif DRILL_MODE == "cnode":
        title += c("  - CNODE PATH VIEW", _BYELLOW)
    elif DRILL_MODE == "host":
        title += c("  - HOST INITIATOR VIEW", _BYELLOW)
    if CSV_FILE:
        title += c(f"  csv:{CSV_FILE}", _DIM)
    print(title)
    meta = (
        c("Cluster ", _DIM) + c(CLUSTER_NAME or "-", _BWHITE)
        + c("   VMS ", _DIM) + c(f"{VMS}:{PORT}", _BWHITE)
        + c("   Refresh ", _DIM) + c(f"{REFRESH_SECONDS}s", _BWHITE)
    )
    os_label = format_os_release(CLUSTER_OS)
    if os_label:
        meta += c(f"   {os_label}", _DIM)
    print(c("  ", _DIM) + meta)
    print(c(_H * width, _DIM))

    if DRILL_MODE in ("vip", "cnode", "host"):
        _render_health_panel(rows, width)
        _render_path_table(width)
    else:
        _render_health_panel(rows, width)
        _render_insights_panel(rows, width)
        _render_operations_table(rows, width)

    _render_help_bar(width)


def discover_metrics():
    print("\n=== VAST NVMe-oTCP Metrics Discovery ===")
    print(f"VMS: {VMS}:{PORT}\n")
    global CLUSTER_ID, CLUSTER_NAME
    try:
        CLUSTER_ID, CLUSTER_NAME = get_current_cluster()
        print(f"Cluster: {CLUSTER_NAME}  (id={CLUSTER_ID})\n")
    except RuntimeError as e:
        print(f"ERROR: Could not connect to VMS: {e}")
        sys.exit(1)

    print("[ Object Types ]")
    for name, endpoint in {
        "cnodes": "/cnodes/", "vips": "/vips/", "volumes": "/volumes/", "blockhosts": "/blockhosts/",
    }.items():
        try:
            objects = normalize_list_response(api_request("GET", endpoint))
            print(f"  {name:<10}: {len(objects)} object(s)")
        except RuntimeError as e:
            print(f"  {name:<10}: not available ({e})")

    print("\n[ BlockMetrics Operations Configured ]")
    for _k, label, category, ops_fqn, avg_fqn in OPS:
        print(f"  {label:<14} [{category:<7}] ops={ops_fqn}")
        if avg_fqn:
            print(f"  {'':14} {'':<9} lat={avg_fqn}")

    print("\n[ ProtoMetrics BlockCommon ]")
    for fqn in build_proto_prop_list():
        print(f"  {fqn}")

    print("\n[ Telemetry Not Yet Exposed on VMS ]")
    for item in _UNAVAILABLE_TELEMETRY:
        print(f"  - {item}")

    print("\n[ Path Drill-Down ]")
    print("  v key -> toggle VIP path view")
    print("  c key -> toggle cNode path view")
    print("  h key -> toggle block host initiator view")
    print("  p key -> return to main operations table")
    print("\n[ Volume Scoping ]")
    print("  --volume NAME / --volumes a,b  -> object_type=volume VolumeMetrics monitors")
    print("  Volume scope uses VolumeMetrics,*_latency__rate for IOPS (not BlockMetrics read_req)")
    print("\nUse --no-color when piping output.\n")


def main():
    global OPS_MONITOR_IDS, PROTO_MONITOR_ID, CLUSTER_ID, CLUSTER_NAME, DRILL_ERROR

    vast_common.install_signal_handlers(signal_handler)
    vast_common.register_atexit(cleanup)

    if ARGS.discover_metrics:
        discover_metrics()
        return 0

    ensure_csv_file()
    setup_keyboard()
    CLUSTER_ID, CLUSTER_NAME = get_current_cluster()
    _capture_cluster_os()
    configure_volume_scope(ARGS)
    create_cluster_monitors()
    fetch_monitor_query()
    render_screen()

    next_refresh_time = time.time() + REFRESH_SECONDS
    while True:
        now = time.time()
        chars = check_keypress()
        if chars:
            ch = chars.lower()
            if "\x03" in chars or "q" in ch:
                break
            refresh_needed = True
            if "r" in ch:
                reset_session_stats()
            elif "p" in ch:
                exit_drill_mode()
            elif "v" in ch:
                if DRILL_MODE == "vip":
                    exit_drill_mode()
                else:
                    exit_drill_mode()
                    enter_drill_mode("vip")
                    if DRILL_MODE:
                        fetch_drill_query()
            elif "c" in ch:
                if DRILL_MODE == "cnode":
                    exit_drill_mode()
                else:
                    exit_drill_mode()
                    enter_drill_mode("cnode")
                    if DRILL_MODE:
                        fetch_drill_query()
            elif "h" in ch:
                if DRILL_MODE == "host":
                    exit_drill_mode()
                else:
                    exit_drill_mode()
                    enter_drill_mode("host")
                    if DRILL_MODE:
                        fetch_drill_query()
            else:
                refresh_needed = False
            if refresh_needed:
                render_screen()
            continue
        if now >= next_refresh_time:
            vast_common.guarded_poll(poll_tick, render_screen)
            next_refresh_time = time.time() + REFRESH_SECONDS
            continue
        time.sleep(0.05)
    return 0


def run(args):
    init_config(args)
    exit_code = 0
    try:
        exit_code = main() or 0
    except KeyboardInterrupt:
        pass
    except Exception as e:
        restore_terminal()
        print()
        print(f"ERROR: {e}", file=sys.stderr)
        exit_code = 1
    finally:
        cleanup()
    return exit_code
