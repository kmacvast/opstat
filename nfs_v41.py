#!/usr/bin/env python3
################################################################################
# Script:      nfs_v41.py
#
# Descr:       NFS v4.1 performance statistics for opstat. Polls VMS
#              instantaneous rates (NFS4Common + NfsMetrics supplement) with
#              metadata proxy panels when native stateful/session counters are
#              unexported by the time-series engine.
#
# Version:     0.1.1
# Author:      KMac
#
# Usage:
#   ./opstat --nfs --version=4.1 --vms <VMS_IP>
#
# Controls:
#   Space  - Refresh immediately
#   c      - cNode drill-down
#   v      - View drill-down
#   t      - Tenant drill-down
#   x      - Exit drill-down
#   q      - Quit
################################################################################

import io
import os
import re
import shutil
import ssl
import sys
import time

import openmetrics
import vast_api_log
import vast_common
from tui_layout import (
    display_width, join_columns, pad_display, format_fixed_number,
    format_scaled_metric, truncate_display, c, set_color, set_unicode, glyph_set,
    as_float, raw_bw_to_mb_sec, format_throughput_mbs, format_latency_us,
    format_iops, format_block_size, format_os_release,
    _RST, _BOLD, _DIM, _GREEN, _YELLOW, _CYAN,
    _BRED, _BGREEN, _BYELLOW, _BCYAN, _BWHITE,
)

VERSION = "0.1.2"

DEFAULT_PORT = 443
DEFAULT_USER = "admin"
DEFAULT_REFRESH_SECONDS = 5
DEFAULT_API_TIME_FRAME = "10m"

_NFS4 = "ProtoMetrics,proto_name=NFS4Common"
_NFS_COMMON = "ProtoMetrics,proto_name=NFSCommon"

# NfsMetrics ops queryable on current VMS builds. OPEN/CLOSE/LOCK/LOCKU/SEQUENCE
# are not exported by the time-series engine (confirmed via privileged discovery
# against real clusters). The full namespace/metadata op set below *is* exported
# (rate + avg), so we surface it directly rather than a 4-row proxy.
_SUPPLEMENT_DATA_OPS = ("read", "write")
_SUPPLEMENT_META_OPS = (
    "access", "getattr", "lookup", "setattr", "readdir", "readdirplus",
    "create", "remove", "rename", "mkdir", "rmdir", "link", "symlink",
    "readlink", "commit",
)

STATEFUL_PANEL_TITLE = "NAMESPACE & METADATA OPS (NfsMetrics)"
SESSION_PANEL_TITLE = "SESSION WORKLOAD (NFS4Common)"

# Real NfsMetrics namespace/metadata ops exported by the VMS time-series engine.
# Shown when native v4.1 stateful counters (OPEN/CLOSE/LOCK) are absent - these
# are measured rates, not synthetic proxies.
METADATA_PROXY_OPS = [
    ("access", "ACCESS"),
    ("getattr", "GETATTR"),
    ("lookup", "LOOKUP"),
    ("setattr", "SETATTR"),
    ("readdir", "READDIR"),
    ("readdirplus", "READDIRPLUS"),
    ("create", "CREATE"),
    ("remove", "REMOVE"),
    ("rename", "RENAME"),
    ("mkdir", "MKDIR"),
    ("rmdir", "RMDIR"),
    ("link", "LINK"),
    ("symlink", "SYMLINK"),
    ("readlink", "READLINK"),
    ("commit", "COMMIT"),
]

# NFS4Common metadata workload profile (session / macro MD view).
SESSION_META_OPS = [
    ("md_iops", "MD IOPS"),
    ("rd_md_iops", "RD MD IOPS"),
    ("wr_md_iops", "WR MD IOPS"),
]

# Data-path operations - NFS4Common instantaneous rates (no delta engine).
DATA_OPS = [
    ("read", "READ"),
    ("write", "WRITE"),
]

# --- NFS v4.1 stateful / session / delegation candidate metrics ------------
# Historically OPEN/CLOSE/LOCK/LOCKU/SEQUENCE were unexported by the time-series
# engine, so opstat fell back to NfsMetrics proxies. Newer VMS builds export
# some or all of these. We probe the metric catalog at startup and render only
# what the cluster actually exposes (see probe_available_state_ops).
STATE_OPS = [
    ("open", "OPEN"),
    ("close", "CLOSE"),
    ("open_confirm", "OPEN_CONFIRM"),
    ("open_downgrade", "OPEN_DOWNGRD"),
    ("lock", "LOCK"),
    ("locku", "UNLOCK"),
    ("lockt", "LOCK_TEST"),
    ("release_lockowner", "REL_LCKOWNER"),
]
DELEGATION_OPS = [
    ("delegreturn", "DELEG_RETURN"),
    ("delegpurge", "DELEG_PURGE"),
]
SESSION_OPS_V41 = [
    ("sequence", "SEQUENCE"),
    ("exchange_id", "EXCHANGE_ID"),
    ("create_session", "CREATE_SESS"),
    ("destroy_session", "DESTROY_SESS"),
    ("bind_conn_to_session", "BIND_CONN"),
    ("reclaim_complete", "RECLAIM_CMPL"),
]
# Rendered in this order in the STATE / LOCKING / SESSION panel.
STATE_PANEL_OPS = STATE_OPS + DELEGATION_OPS + SESSION_OPS_V41
STATE_PANEL_TITLE = "STATE / LOCKING / SESSION (NfsMetrics)"

_DRILL_CFG = {
    "cnode": {
        "object_type": "cnode",
        "endpoint": "/cnodes/",
        "name_fields": ("name", "hostname", "mgmt_ip"),
    },
    "view": {
        "object_type": "view",
        "endpoint": "/views/",
        "name_fields": ("path", "title", "name"),
    },
    "tenant": {
        "object_type": "tenant",
        "endpoint": "/tenants/",
        "name_fields": ("name",),
    },
}
_MAX_DRILL_OBJECTS = 8

_COL_SEP = "  "
_COL = {"label": 14, "iops": 12, "throughput": 12, "size": 10, "latency": 12}
_DRILL_COL = {"name": 24, "ops": 12, "lat": 10, "bw": 9, "top": 12, "pct": 6}

_ANSI_RE = re.compile(r"\033\[[^m]*m")
_UTF8 = (sys.stdout.encoding or "ascii").lower().startswith("utf")
_G = glyph_set(_UTF8)
_H, _V = _G["H"], _G["V"]
_TL, _TR, _BL, _BR, _LT, _RT = _G["TL"], _G["TR"], _G["BL"], _G["BR"], _G["LT"], _G["RT"]
_MUS = _G["MUS"]

_COLOR = False
ARGS = None
VMS = PORT = USER = PASSWORD = None
REFRESH_SECONDS = DEFAULT_REFRESH_SECONDS
API_TIME_FRAME = DEFAULT_API_TIME_FRAME
SAMPLE_AVERAGE_MODE = False
BASE_URL = AUTH = HEADERS = None
SSL_CTX = ssl._create_unverified_context()

CLUSTER_ID = CLUSTER_NAME = None
CLUSTER_OS = None
DATA_MONITOR_ID = META_MONITOR_ID = None
SUPPLEMENT_MONITOR_ID = BW_MONITOR_ID = None
STATE_MONITOR_ID = None
STATE_OPS_AVAILABLE = []   # (op, label) pairs the cluster actually exports
METRICS_SOURCE = "NFS4Common"
SORT_MODE = "default"   # default | ops | latency
LAST_ROWS = {"data": [], "stateful": [], "state": [], "session": [], "meta": {}}
LAST_SAMPLE = "-"
DRILL_MODE = DRILL_ERROR = None
DRILL_OBJECTS = []
DRILL_MONITORS = []
LAST_DRILL_ROWS = []


def init_config(args):
    global ARGS, VMS, PORT, USER, PASSWORD, REFRESH_SECONDS, API_TIME_FRAME
    global SAMPLE_AVERAGE_MODE, BASE_URL, AUTH, HEADERS, _COLOR

    ARGS = args
    VMS = args.vms
    PORT = args.port
    USER = args.user
    REFRESH_SECONDS = args.refresh
    SAMPLE_AVERAGE_MODE = bool(args.sample_average)
    API_TIME_FRAME = args.sample_average or DEFAULT_API_TIME_FRAME
    BASE_URL = f"https://{VMS}/api" if PORT == 443 else f"https://{VMS}:{PORT}/api"
    HEADERS, AUTH, PASSWORD = vast_common.resolve_auth(
        USER, VMS, args.password, f"opstat/nfs-v41/{VERSION}",
    )
    vast_common.configure_connection(BASE_URL, HEADERS, SSL_CTX)
    log_path = vast_api_log.configure(
        getattr(args, "log_api_calls", False), "nfs-v41", VMS, PORT,
    )
    if log_path:
        print(f"API call logging enabled: {log_path}", file=sys.stderr, flush=True)
    om_path = openmetrics.configure(
        getattr(args, "export_openmetrics", False),
        getattr(args, "openmetrics_file", None),
        "nfs41", VMS,
    )
    if om_path:
        print(f"OpenMetrics export enabled: {om_path}", file=sys.stderr, flush=True)
    _COLOR = sys.stdout.isatty() and not args.no_color
    set_color(_COLOR)
    set_unicode(_UTF8)


def box_top(title, width):
    raw_pre = f"{_TL}{_H} {title} "
    fill = max(0, width - display_width(raw_pre) - 1)
    if _COLOR:
        return c(f"{_TL}{_H} ", _DIM) + c(title, _BWHITE) + c(f" {_H * fill}{_TR}", _DIM)
    return f"{raw_pre}{_H * fill}{_TR}"


def box_bottom(width):
    return c(f"{_BL}{_H * (width - 2)}{_BR}", _DIM)


def box_sep(width):
    return c(f"{_LT}{_H * (width - 2)}{_RT}", _DIM)


def box_row(content, width):
    inner = max(0, width - 4)
    if display_width(content) > inner:
        content = truncate_display(content, inner) + (_RST if _COLOR else "")
    pad = max(0, inner - display_width(content))
    return f"{c(_V, _DIM)} {content}{' ' * pad} {c(_V, _DIM)}"


clear_screen = vast_common.clear_screen


def api_request(method, path, payload=None):
    return vast_common.request(method, path, payload)


def normalize_list_response(data):
    return vast_common.normalize_list_response(data)


def get_current_cluster():
    return vast_common.get_current_cluster(api_request)


def _capture_cluster_os():
    """Fetch the cluster VAST OS version once for the header (best-effort)."""
    global CLUSTER_OS
    CLUSTER_OS = vast_common.get_current_cluster_os(api_request)


def _data_fqn(suffix):
    return f"{_NFS4},{suffix}"


def _nfs_fqn(op, suffix):
    return f"NfsMetrics,nfs_{op}_latency__{suffix}"


# Server-side commit wait - how long NFS writes block for durable persistence.
# Unlike op latencies this metric has no ``nfs_`` prefix in the catalog.
_COMMIT_WAIT_FQN = "NfsMetrics,commit_wait_latency"


def _commit_wait_avg(values):
    return as_float(values.get(f"{_COMMIT_WAIT_FQN}__avg"))


def _first_positive(*values):
    """Return the first value > 0; zero is treated as missing for coalesce."""
    for value in values:
        parsed = as_float(value)
        if parsed is not None and parsed > 0:
            return parsed
    return None


def _avg_io_from_bw_ops(ops, bw_mbs):
    if not ops or not bw_mbs or ops <= 0:
        return None
    return (bw_mbs * 1_000_000.0) / ops


def build_data_monitor_props():
    """NFS4Common data-path rates - poll values map directly to display (no deltas)."""
    return [
        _data_fqn("rd_iops"), _data_fqn("wr_iops"),
        _data_fqn("rd_bw"), _data_fqn("wr_bw"),
        _data_fqn("read_latency__avg"), _data_fqn("write_latency__avg"),
    ]


def build_supplement_monitor_props():
    """NfsMetrics fallback - active on builds where NFS4Common stays at zero."""
    props = []
    for op in _SUPPLEMENT_DATA_OPS + _SUPPLEMENT_META_OPS:
        props.extend([_nfs_fqn(op, "rate"), _nfs_fqn(op, "avg")])
    props.append(f"{_COMMIT_WAIT_FQN}__avg")
    return props


def build_bw_monitor_props():
    """Bandwidth from NFSCommon (NFS4Common bw is often zero on mixed NFS clusters)."""
    return [f"{_NFS_COMMON},rd_bw", f"{_NFS_COMMON},wr_bw"]


def build_meta_monitor_props():
    return [
        _data_fqn("md_iops"), _data_fqn("rd_md_iops"), _data_fqn("wr_md_iops"),
        _data_fqn("iops"), _data_fqn("latency"),
    ]


def build_state_monitor_props(ops):
    """NfsMetrics rate/avg props for the given stateful/session/delegation ops."""
    props = []
    for op, _label in ops:
        props.extend([_nfs_fqn(op, "rate"), _nfs_fqn(op, "avg")])
    return props


def _collect_metric_names(obj):
    """Recursively gather every string in a catalog response (schema-agnostic)."""
    names = set()

    def walk(node):
        if isinstance(node, str):
            names.add(node)
        elif isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, (list, tuple)):
            for value in node:
                walk(value)

    walk(obj)
    return names


def probe_available_state_ops():
    """Return the subset of STATE_PANEL_OPS the cluster's metric catalog exports.

    Best-effort and read-only. Returns:
      - a (possibly empty) list of (op, label) when the catalog is readable, or
      - None when the catalog cannot be read, so the caller can fall back to a
        trial monitor-creation attempt.
    """
    try:
        raw = api_request("GET", "/metrics/")
    except RuntimeError:
        return None
    names = _collect_metric_names(raw)
    if not names:
        return None
    available = []
    for op, label in STATE_PANEL_OPS:
        needle = f"nfs_{op}_latency"
        if any(needle in name for name in names):
            available.append((op, label))
    return available


def build_drill_prop_list():
    return (
        build_data_monitor_props()
        + build_supplement_monitor_props()
        + build_bw_monitor_props()
        + build_meta_monitor_props()
    )


def _create_monitor_raw(name_suffix, prop_list, object_type, object_ids):
    name = f"adhoc_opstat_nfs41_{name_suffix}_{int(time.time())}"
    return vast_common.create_monitor_raw(
        api_request, name, prop_list, object_type, object_ids,
        time_frame=API_TIME_FRAME,
    )


def create_monitor(name_suffix, prop_list):
    return _create_monitor_raw(name_suffix, prop_list, "cluster", [CLUSTER_ID])


def delete_monitor(monitor_id):
    vast_common.delete_monitor(api_request, monitor_id)


def _result_parts(result):
    prop_list = result.get("prop_list", [])
    data = result.get("data", [])
    prop_idx = {name: idx for idx, name in enumerate(prop_list)}
    return prop_list, data, prop_idx


def _latest_row(result):
    _prop_list, data, prop_idx = _result_parts(result)
    if not data:
        return {}, "-"
    row = data[0]
    sample = row[0] if row else "-"
    values = {}
    for name, idx in prop_idx.items():
        if idx < len(row):
            values[name] = row[idx]
    return values, sample


def _metric(values, suffix):
    return as_float(values.get(_data_fqn(suffix)))


def _supplement_metric(values, op, suffix):
    return as_float(values.get(_nfs_fqn(op, suffix)))


def _op_metrics(nfs4_values, supplement_values, bw_values, op_key):
    """Resolve one op's metrics, choosing a single tier so ops and latency stay consistent.

    The tier (NFS4Common vs NfsMetrics) is selected once from the IOPS signal;
    latency is then read from the *same* tier to avoid flapping/mismatched blends
    at low load.
    """
    if op_key == "read":
        iops_suffix, lat_suffix, supp_op = "rd_iops", "read_latency__avg", "read"
        bw_native, bw_common = "rd_bw", f"{_NFS_COMMON},rd_bw"
    else:
        iops_suffix, lat_suffix, supp_op = "wr_iops", "write_latency__avg", "write"
        bw_native, bw_common = "wr_bw", f"{_NFS_COMMON},wr_bw"

    nfs4_iops = as_float(_metric(nfs4_values, iops_suffix))
    if nfs4_iops is not None and nfs4_iops > 0:
        ops = nfs4_iops
        avg_us = _first_positive(_metric(nfs4_values, lat_suffix))
    else:
        ops = _first_positive(_supplement_metric(supplement_values, supp_op, "rate"))
        avg_us = _first_positive(_supplement_metric(supplement_values, supp_op, "avg"))

    bw_mbs = _first_positive(
        raw_bw_to_mb_sec(_metric(nfs4_values, bw_native)),
        raw_bw_to_mb_sec(as_float(bw_values.get(bw_common))),
    )
    avg_io = _avg_io_from_bw_ops(ops, bw_mbs)
    return {"ops_sec": ops, "avg_us": avg_us, "bw_mbs": bw_mbs, "avg_io_bytes": avg_io}


def _nfs_op_metrics(values, op_key):
    rate = _supplement_metric(values, op_key, "rate")
    avg = _supplement_metric(values, op_key, "avg")
    return {"ops_sec": rate, "avg_us": avg, "bw_mbs": None, "avg_io_bytes": None}


def _metadata_iops_supplement(supplement_values):
    total = 0.0
    found = False
    for op in _SUPPLEMENT_META_OPS:
        rate = _supplement_metric(supplement_values, op, "rate")
        if rate is not None and rate > 0:
            total += rate
            found = True
    return total if found else None


def _build_stateful_rows(supplement_values):
    """NfsMetrics metadata proxy rows - native OPEN/CLOSE/LOCK/LOCKU are unexported."""
    return _rows_with_pct(
        METADATA_PROXY_OPS,
        lambda k: _nfs_op_metrics(supplement_values, k),
    )


def _build_state_rows(state_values):
    """Real OPEN/CLOSE/LOCK/UNLOCK/session/delegation rows (NfsMetrics rate+avg)."""
    if not STATE_OPS_AVAILABLE:
        return []
    return _rows_with_pct(
        STATE_OPS_AVAILABLE,
        lambda k: _nfs_op_metrics(state_values, k),
    )


def _build_session_rows(meta):
    """NFS4Common md_iops workload profile (instantaneous rates, no deltas)."""
    def _meta_metric(key):
        val = as_float(meta.get(key))
        return {
            "ops_sec": val if val is not None and val > 0 else None,
            "avg_us": None,
            "bw_mbs": None,
            "avg_io_bytes": None,
        }

    return _rows_with_pct(SESSION_META_OPS, _meta_metric)


def _rows_with_pct(row_defs, metrics_fn):
    rows = []
    for key, label in row_defs:
        m = metrics_fn(key)
        rows.append({"key": key, "label": label, **m})
    total = sum(as_float(r["ops_sec"]) or 0 for r in rows)
    for r in rows:
        ops = as_float(r["ops_sec"]) or 0
        r["pct"] = (ops / total * 100) if total > 0 else None
    return rows


def build_rows_from_results(
    data_result,
    supplement_result=None,
    bw_result=None,
    meta_result=None,
    state_result=None,
):
    global METRICS_SOURCE
    nfs4_values, sample = _latest_row(data_result)
    supplement_values, _ = _latest_row(supplement_result) if supplement_result else ({}, sample)
    bw_values, _ = _latest_row(bw_result) if bw_result else ({}, sample)
    meta_values, _ = _latest_row(meta_result) if meta_result else ({}, sample)
    state_values, _ = _latest_row(state_result) if state_result else ({}, sample)

    data_rows = _rows_with_pct(
        DATA_OPS,
        lambda k: _op_metrics(nfs4_values, supplement_values, bw_values, k),
    )
    nfs4_active = any(
        (as_float(_metric(nfs4_values, s)) or 0) > 0
        for s in ("rd_iops", "wr_iops", "rd_bw", "wr_bw")
    )
    supplement_active = any(
        (as_float(_supplement_metric(supplement_values, op, "rate")) or 0) > 0
        for op in _SUPPLEMENT_DATA_OPS
    )
    if nfs4_active and supplement_active:
        METRICS_SOURCE = "NFS4Common + NfsMetrics"
    elif nfs4_active:
        METRICS_SOURCE = "NFS4Common"
    elif supplement_active:
        METRICS_SOURCE = "NfsMetrics supplement"
    else:
        METRICS_SOURCE = "idle"

    md_iops = _first_positive(
        _metric(meta_values, "md_iops"),
        _metadata_iops_supplement(supplement_values),
    )
    meta = {
        "md_iops": md_iops,
        "rd_md_iops": _metric(meta_values, "rd_md_iops"),
        "wr_md_iops": _metric(meta_values, "wr_md_iops"),
        "total_iops": _first_positive(_metric(meta_values, "iops"), md_iops),
        "latency_us": _first_positive(
            _metric(meta_values, "latency"),
            weighted_latency(data_rows),
        ),
        "commit_wait_us": _commit_wait_avg(supplement_values),
    }
    stateful_rows = _build_stateful_rows(supplement_values)
    state_rows = _build_state_rows(state_values)
    session_rows = _build_session_rows(meta)

    return {
        "data": data_rows,
        "stateful": stateful_rows,
        "state": state_rows,
        "session": session_rows,
        "meta": meta,
    }, sample


def _sort_rows(rows):
    """Apply the active SORT_MODE. Inactive rows (ops 0/None) always sink to the bottom."""
    if SORT_MODE == "ops":
        return sorted(rows, key=lambda r: as_float(r.get("ops_sec")) or 0.0, reverse=True)
    if SORT_MODE == "latency":
        return sorted(rows, key=lambda r: as_float(r.get("avg_us")) or -1.0, reverse=True)
    return list(rows)


def _sort_label():
    return {
        "ops": "ops/s high-low",
        "latency": "latency high-low",
    }.get(SORT_MODE, "default")


def weighted_latency(rows):
    pairs = [
        (as_float(r["ops_sec"]), as_float(r["avg_us"]))
        for r in rows if (as_float(r["ops_sec"]) or 0) > 0 and as_float(r["avg_us"]) is not None
    ]
    weight = sum(w for w, _ in pairs)
    if weight <= 0:
        return None
    return sum(w * v for w, v in pairs) / weight


def _dash(w):
    return c(pad_display("-", w, ">"), _DIM)


def _metric_cell(text, w, color):
    return c(format_scaled_metric(text, w), color)


def _label_cell(text, w, color):
    return c(pad_display(text, w, "<"), color)


def _table_header_titles(titles):
    cells = []
    for title, key, align in titles:
        cells.append(c(pad_display(title, _COL[key], align), _BOLD))
    return join_columns(cells, _COL_SEP)


def _data_row_cells(row):
    w = _COL
    ops = as_float(row.get("ops_sec"))
    active = ops is not None and ops > 0
    if not active:
        color = _DIM
        return join_columns([
            _label_cell(row["label"], w["label"], color),
            _dash(w["iops"]), _dash(w["throughput"]), _dash(w["size"]), _dash(w["latency"]),
        ], _COL_SEP)
    bw_text, _ = format_throughput_mbs(row.get("bw_mbs"))
    size_text, _ = format_block_size(row.get("avg_io_bytes"))
    lat_text, lat_us = format_latency_us(row.get("avg_us"))
    label_color = _BCYAN if row["key"] == "read" else _BYELLOW if row["key"] == "write" else _BWHITE
    lat_color = _BRED if (lat_us or 0) > 10_000 else _YELLOW if (lat_us or 0) > 1_000 else _BGREEN
    return join_columns([
        _label_cell(row["label"], w["label"], label_color),
        _metric_cell(format_iops(ops), w["iops"], _GREEN),
        _metric_cell(bw_text, w["throughput"], _CYAN),
        _metric_cell(size_text, w["size"], _CYAN if row["key"] == "read" else _YELLOW),
        _metric_cell(lat_text, w["latency"], lat_color),
    ], _COL_SEP)


def _simple_row_cells(row):
    w = _COL
    ops = as_float(row.get("ops_sec"))
    active = ops is not None and ops > 0
    if not active:
        return join_columns([
            _label_cell(row["label"], w["label"], _DIM),
            _dash(w["iops"]), _dash(w["throughput"]), _dash(w["size"]), _dash(w["latency"]),
        ], _COL_SEP)
    lat_text, lat_us = format_latency_us(row.get("avg_us"))
    lat_color = _BRED if (lat_us or 0) > 10_000 else _YELLOW if (lat_us or 0) > 1_000 else _BGREEN
    return join_columns([
        _label_cell(row["label"], w["label"], _BWHITE),
        _metric_cell(format_iops(ops), w["iops"], _GREEN),
        _dash(w["throughput"]), _dash(w["size"]),
        _metric_cell(lat_text, w["latency"], lat_color),
    ], _COL_SEP)


def _render_data_panel(rows, width):
    titles = [
        ("Operation", "label", "<"), ("IOPS", "iops", ">"), ("Throughput", "throughput", ">"),
        ("Avg Size", "size", ">"), ("Latency", "latency", ">"),
    ]
    print(box_top("DATA OPERATIONS", width))
    print(box_row(_table_header_titles(titles), width))
    print(box_sep(width))
    for row in _sort_rows(rows):
        print(box_row(_data_row_cells(row), width))
    print(box_bottom(width))


def _render_stateful_panel(rows, meta, width):
    titles = [
        ("Operation", "label", "<"), ("Ops/s", "iops", ">"), ("", "throughput", ">"),
        ("", "size", ">"), ("Latency", "latency", ">"),
    ]
    print(box_top(STATEFUL_PANEL_TITLE, width))
    print(box_row(_table_header_titles(titles), width))
    print(box_sep(width))
    active = [r for r in rows if (as_float(r.get("ops_sec")) or 0) > 0]
    shown = active or rows
    for row in _sort_rows(shown):
        print(box_row(_simple_row_cells(row), width))
    cw_text, _ = format_latency_us(meta.get("commit_wait_us"))
    note = (
        "Real NfsMetrics ops (OPEN/CLOSE/LOCK unexported on this build) - "
        f"md_iops {format_iops(meta.get('md_iops'))}   commit-wait {cw_text}"
    )
    print(box_row(c(note, _DIM), width))
    print(box_bottom(width))


def _render_state_panel(rows, width):
    """Real NFS4.1 state/locking/session ops (shown when the cluster exports them)."""
    titles = [
        ("Operation", "label", "<"), ("Ops/s", "iops", ">"), ("", "throughput", ">"),
        ("", "size", ">"), ("Latency", "latency", ">"),
    ]
    print(box_top(STATE_PANEL_TITLE, width))
    print(box_row(_table_header_titles(titles), width))
    print(box_sep(width))
    active = [r for r in rows if (as_float(r.get("ops_sec")) or 0) > 0]
    shown = active or rows
    for row in _sort_rows(shown):
        print(box_row(_simple_row_cells(row), width))
    if not active:
        print(box_row(c("No active OPEN/CLOSE/LOCK/session ops this sample.", _DIM), width))
    print(box_bottom(width))


def _session_summary_line(meta):
    md = format_iops(meta.get("md_iops"))
    rd = format_iops(meta.get("rd_md_iops"))
    wr = format_iops(meta.get("wr_md_iops"))
    return (
        c("MD IOPS ", _DIM) + c(md, _YELLOW)
        + c("   RD MD ", _DIM) + c(rd, _BCYAN)
        + c("   WR MD ", _DIM) + c(wr, _BYELLOW)
    )


def _render_session_panel(rows, meta, width):
    titles = [
        ("Metric", "label", "<"), ("Ops/s", "iops", ">"), ("", "throughput", ">"),
        ("", "size", ">"), ("Latency", "latency", ">"),
    ]
    print(box_top(SESSION_PANEL_TITLE, width))
    print(box_row(_session_summary_line(meta), width))
    print(box_sep(width))
    print(box_row(_table_header_titles(titles), width))
    print(box_sep(width))
    for row in rows:
        print(box_row(_simple_row_cells(row), width))
    lat_text, _ = format_latency_us(meta.get("latency_us"))
    note = (
        "SEQUENCE unexported on this VMS - NFS4Common metadata workload profile "
        f"(cluster latency {lat_text})"
    )
    print(box_row(c(note, _DIM), width))
    print(box_bottom(width))


def _render_health_panel(snapshot, width):
    data = snapshot["data"]
    meta = snapshot["meta"]
    total_data_iops = sum(as_float(r["ops_sec"]) or 0 for r in data)
    total_bw = sum(as_float(r["bw_mbs"]) or 0 for r in data)
    combined_lat = weighted_latency(data)
    print(box_top("NFS v4.1 HEALTH", width))
    ops_s = c(f"{total_data_iops:,.2f} ops/s" if total_data_iops else "- ops/s", _BWHITE)
    lat_text, _ = format_latency_us(combined_lat)
    lat_s = c(lat_text if combined_lat else "-", _BGREEN if combined_lat else _DIM)
    bw_text, _ = format_throughput_mbs(total_bw)
    bw_s = c(bw_text if total_bw else "-", _CYAN)
    md_s = c(
        f"md {format_iops(meta.get('md_iops'))} ops/s"
        if as_float(meta.get("md_iops")) else "md -",
        _YELLOW,
    )
    print(box_row(f"{ops_s}   Lat {lat_s}   BW {bw_s}   {md_s}", width))
    print(box_bottom(width))


def _obj_name(obj, fields):
    return vast_common.resolve_object_name(obj, fields)


def _cleanup_drill_monitors():
    global DRILL_MONITORS
    for monitor_id, _name in DRILL_MONITORS:
        delete_monitor(monitor_id)
    DRILL_MONITORS = []


def enter_drill_mode(mode):
    global DRILL_MODE, DRILL_OBJECTS, DRILL_ERROR, LAST_DRILL_ROWS

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
    DRILL_OBJECTS = [
        {"id": o["id"], "name": _obj_name(o, cfg["name_fields"])} for o in valid
    ]
    _cleanup_drill_monitors()
    new_monitors = []
    for obj in DRILL_OBJECTS:
        try:
            monitor_id = _create_monitor_raw(
                f"{mode}_{obj['id']}", build_drill_prop_list(),
                cfg["object_type"], [obj["id"]],
            )
            new_monitors.append((monitor_id, obj["name"]))
        except RuntimeError:
            pass
    if not new_monitors:
        DRILL_ERROR = (
            f"Could not create any {mode} monitors "
            f"(object_type='{cfg['object_type']}' may not be supported)"
        )
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
    drill_rows = []
    for monitor_id, obj_name in DRILL_MONITORS:
        try:
            result = api_request("GET", f"/monitors/{monitor_id}/query/")
            snapshot, _ = build_rows_from_results(result, result, result, result)
            data = snapshot["data"]
            total_ops = sum(as_float(r["ops_sec"]) or 0 for r in data)
            latency = weighted_latency(data)
            total_bw = sum(as_float(r["bw_mbs"]) or 0 for r in data) / 1024.0
            active = [r for r in data if (as_float(r["ops_sec"]) or 0) > 0]
            top = max(active, key=lambda r: as_float(r["ops_sec"]) or 0, default=None)
            drill_rows.append({
                "name": obj_name,
                "total_ops": total_ops,
                "latency_us": latency,
                "bw_gbs": total_bw if total_bw else None,
                "top_rpc": top["label"] if top else "-",
                "top_rpc_pct": as_float(top["pct"]) if top else None,
            })
        except RuntimeError:
            pass
    LAST_DRILL_ROWS = sorted(drill_rows, key=lambda r: r["total_ops"] or 0, reverse=True)
    if openmetrics.is_enabled() and DRILL_MODE:
        openmetrics.export_drill(CLUSTER_NAME, DRILL_MODE, LAST_DRILL_ROWS, sample=LAST_SAMPLE)


def _render_drill_panel(width):
    dc = _DRILL_COL
    print(box_top(f"{(DRILL_MODE or '?').upper()} DRILL-DOWN", width))
    if DRILL_ERROR:
        print(box_row(c(f"Error: {DRILL_ERROR}", _BRED), width))
        print(box_bottom(width))
        return
    if not LAST_DRILL_ROWS:
        print(box_row(c("Waiting for data…", _DIM), width))
        print(box_bottom(width))
        return
    header = join_columns([
        c(pad_display("Name", dc["name"], "<"), _BOLD),
        c(pad_display("Ops/s", dc["ops"], ">"), _BOLD),
        c(pad_display(f"Avg {_MUS}", dc["lat"], ">"), _BOLD),
        c(pad_display("GB/s", dc["bw"], ">"), _BOLD),
        c(pad_display("Top Op", dc["top"], ">"), _BOLD),
        c(pad_display("Top%", dc["pct"], ">"), _BOLD),
    ], " ")
    print(box_row(header, width))
    print(box_sep(width))
    for dr in LAST_DRILL_ROWS:
        pct = pad_display(f"{(dr.get('top_rpc_pct') or 0):.1f}%", dc["pct"], ">")
        line = join_columns([
            pad_display(dr["name"], dc["name"], "<"),
            c(format_fixed_number(dr["total_ops"], dc["ops"], 2), _BWHITE),
            c(format_fixed_number(dr["latency_us"], dc["lat"], 2), _BGREEN),
            c(format_fixed_number(dr["bw_gbs"], dc["bw"], 3), _CYAN),
            c(pad_display(dr["top_rpc"], dc["top"], ">"), _BWHITE),
            c(pct, _DIM),
        ], " ")
        print(box_row(line, width))
    print(box_sep(width))
    print(box_row(c("Press x to return to cluster view", _DIM), width))
    print(box_bottom(width))


def fetch_monitor_query():
    global LAST_ROWS, LAST_SAMPLE
    data_result = api_request("GET", f"/monitors/{DATA_MONITOR_ID}/query/")
    supplement_result = api_request("GET", f"/monitors/{SUPPLEMENT_MONITOR_ID}/query/")
    bw_result = api_request("GET", f"/monitors/{BW_MONITOR_ID}/query/")
    meta_result = api_request("GET", f"/monitors/{META_MONITOR_ID}/query/")
    state_result = (
        api_request("GET", f"/monitors/{STATE_MONITOR_ID}/query/")
        if STATE_MONITOR_ID else None
    )
    LAST_ROWS, LAST_SAMPLE = build_rows_from_results(
        data_result, supplement_result, bw_result, meta_result, state_result,
    )
    _export_openmetrics()


def _openmetrics_series():
    series = []

    def add(rows, category):
        for r in rows:
            series.append({
                "operation": r.get("label", ""),
                "category": category,
                "ops_sec": as_float(r.get("ops_sec")),
                "avg_us": as_float(r.get("avg_us")),
                "bw_bytes_sec": openmetrics.mbps_to_bytes_sec(as_float(r.get("bw_mbs"))),
                "io_bytes": as_float(r.get("avg_io_bytes")),
            })

    add(LAST_ROWS.get("data", []), "data")
    if STATE_OPS_AVAILABLE:
        add(LAST_ROWS.get("state", []), "state")
    else:
        add(LAST_ROWS.get("stateful", []), "metadata")
    add(LAST_ROWS.get("session", []), "session")
    return series


def _export_openmetrics():
    if not openmetrics.is_enabled():
        return
    openmetrics.export_snapshot(
        CLUSTER_NAME, None, CLUSTER_NAME, _openmetrics_series(), sample=LAST_SAMPLE,
    )


def poll_tick():
    """One refresh poll: headline monitors plus the active drill, if any."""
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
    width = min(shutil.get_terminal_size((120, 40)).columns, 120)
    title = (
        c("  VAST NFSv41", _BCYAN) + c(" opstat", _BWHITE) + c(f" v{VERSION}", _DIM)
        + f"   VMS {c(f'{VMS}:{PORT}', _BWHITE)}   cluster {c(CLUSTER_NAME, _BWHITE)}"
        + c(f"   refresh {REFRESH_SECONDS}s", _DIM)
    )
    if DRILL_MODE:
        title += c(f"   | {DRILL_MODE.upper()} DRILL", _BYELLOW)
    print(title)
    os_label = format_os_release(CLUSTER_OS)
    print(c(
        f"  sample {LAST_SAMPLE}   frame {API_TIME_FRAME}   source {METRICS_SOURCE}"
        + f"   sort {_sort_label()}"
        + (f"   {os_label}" if os_label else ""),
        _DIM,
    ))
    print()
    if DRILL_MODE:
        _render_drill_panel(width)
        return
    _render_health_panel(LAST_ROWS, width)
    print()
    _render_data_panel(LAST_ROWS["data"], width)
    print()
    if STATE_OPS_AVAILABLE:
        _render_state_panel(LAST_ROWS["state"], width)
    else:
        _render_stateful_panel(LAST_ROWS["stateful"], LAST_ROWS["meta"], width)
    print()
    _render_session_panel(LAST_ROWS["session"], LAST_ROWS["meta"], width)
    print()
    print(box_row(
        c("[q]", _BWHITE) + c(" Quit ", _DIM)
        + c("|", _DIM) + c("[o]", _BWHITE) + c(" Ops ", _DIM)
        + c("|", _DIM) + c("[l]", _BWHITE) + c(" Lat ", _DIM)
        + c("|", _DIM) + c("[n]", _BWHITE) + c(" Name ", _DIM)
        + c("|", _DIM) + c("[c]", _BWHITE) + c(" cNode ", _DIM)
        + c("|", _DIM) + c("[v]", _BWHITE) + c(" View ", _DIM)
        + c("|", _DIM) + c("[t]", _BWHITE) + c(" Tenant ", _DIM)
        + c("|", _DIM) + c("[x]", _BWHITE) + c(" Exit drill ", _DIM)
        + c("|", _DIM) + c("[space]", _BWHITE) + c(" Refresh", _DIM),
        width,
    ), flush=True)


def discover_metrics():
    global CLUSTER_ID, CLUSTER_NAME
    print(f"NFS v4.1 metric discovery - VMS {VMS}:{PORT}\n")
    try:
        CLUSTER_ID, CLUSTER_NAME = get_current_cluster()
        print(f"Cluster: {CLUSTER_NAME} (id={CLUSTER_ID})\n")
    except RuntimeError as e:
        print(f"ERROR: Could not connect to VMS: {e}")
        sys.exit(1)

    print("[ NFS4Common ProtoMetrics (data path - instantaneous rates) ]")
    for suffix in (
        "rd_iops", "wr_iops", "rd_bw", "wr_bw",
        "read_latency__avg", "write_latency__avg",
        "md_iops", "rd_md_iops", "wr_md_iops", "iops", "latency",
    ):
        print(f"  {_data_fqn(suffix)}")

    print("\n[ NfsMetrics namespace/metadata ops (real, exported - rate + avg) ]")
    for op in _SUPPLEMENT_DATA_OPS + _SUPPLEMENT_META_OPS:
        print(f"  {_nfs_fqn(op, 'rate')} / __avg")
    print(f"  {_COMMIT_WAIT_FQN}__avg  (server-side commit/durability wait)")
    print("  Data fallback: nfs_{read,write}_latency__rate when NFS4Common IOPS are zero.")

    print("\n[ Bandwidth fallback ]")
    for prop in build_bw_monitor_props():
        print(f"  {prop}")

    print("\n[ State / locking / session ops (probed live from metric catalog) ]")
    probed = probe_available_state_ops()
    if probed is None:
        print("  metric catalog unreadable - availability decided by monitor creation")
    else:
        available_keys = {op for op, _ in probed}
        for op, label in STATE_PANEL_OPS:
            status = "exported" if op in available_keys else "not exported"
            print(f"  {label:<14} NfsMetrics,nfs_{op}_latency__rate / __avg - {status}")
        if not probed:
            print("  none exported → STATE panel falls back to NfsMetrics proxies")
    print("  Fallback stateful panel: NfsMetrics proxies (GETATTR, LOOKUP, CREATE, REMOVE)")
    print("  Session panel: NFS4Common md_iops / rd_md_iops / wr_md_iops")

    print("\n[ Drill-down endpoints ]")
    for mode, cfg in _DRILL_CFG.items():
        try:
            objects = normalize_list_response(api_request("GET", cfg["endpoint"]))
            print(f"  {mode:<8} {cfg['endpoint']:<12} {len(objects)} object(s)")
        except RuntimeError as e:
            print(f"  {mode:<8} {cfg['endpoint']:<12} error: {e}")

    print("\nPoll semantics: VMS delivers instantaneous rates (__rate, rd_iops) and")
    print("pre-averaged fields (__avg). No counter-delta engine is used in nfs_v41.")


setup_keyboard = vast_common.setup_keyboard
restore_terminal = vast_common.restore_terminal
check_keypress = vast_common.check_keypress


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
    sys.exit(0)


def _init_state_monitor():
    """Create the state/locking/session monitor from whatever the cluster exports.

    Uses the metric catalog to trim candidates, then verifies by creating the
    monitor. On any failure the feature is disabled and the classic NfsMetrics
    proxy panel is shown instead - never breaking the dashboard.
    """
    global STATE_MONITOR_ID, STATE_OPS_AVAILABLE
    candidates = probe_available_state_ops()
    if candidates is None:            # catalog unreadable - try the full set
        candidates = STATE_PANEL_OPS
    if not candidates:
        STATE_OPS_AVAILABLE = []
        return
    try:
        STATE_MONITOR_ID = create_monitor("state", build_state_monitor_props(candidates))
        STATE_OPS_AVAILABLE = candidates
    except RuntimeError:
        STATE_MONITOR_ID = None
        STATE_OPS_AVAILABLE = []


def main():
    global DATA_MONITOR_ID, META_MONITOR_ID, SUPPLEMENT_MONITOR_ID, BW_MONITOR_ID
    global CLUSTER_ID, CLUSTER_NAME, SORT_MODE

    vast_common.install_signal_handlers(signal_handler)
    vast_common.register_atexit(cleanup)

    if ARGS.discover_metrics:
        discover_metrics()
        return 0

    setup_keyboard()
    CLUSTER_ID, CLUSTER_NAME = get_current_cluster()
    _capture_cluster_os()
    DATA_MONITOR_ID = create_monitor("data", build_data_monitor_props())
    SUPPLEMENT_MONITOR_ID = create_monitor("supplement", build_supplement_monitor_props())
    BW_MONITOR_ID = create_monitor("bw", build_bw_monitor_props())
    META_MONITOR_ID = create_monitor("meta", build_meta_monitor_props())
    _init_state_monitor()

    fetch_monitor_query()
    render_screen()
    next_refresh = time.time() + REFRESH_SECONDS

    while True:
        chars = check_keypress()
        if chars:
            if "\x03" in chars or "q" in chars.lower():
                break
            if "o" in chars.lower():
                SORT_MODE = "ops"
            elif "l" in chars.lower():
                SORT_MODE = "latency"
            elif "n" in chars.lower():
                SORT_MODE = "default"
            elif "c" in chars.lower():
                exit_drill_mode()
                enter_drill_mode("cnode")
                if DRILL_MODE:
                    fetch_drill_query()
            elif "v" in chars.lower():
                exit_drill_mode()
                enter_drill_mode("view")
                if DRILL_MODE:
                    fetch_drill_query()
            elif "t" in chars.lower():
                exit_drill_mode()
                enter_drill_mode("tenant")
                if DRILL_MODE:
                    fetch_drill_query()
            elif "x" in chars.lower():
                exit_drill_mode()
            elif " " in chars:
                vast_common.guarded_poll(poll_tick, render_screen)
                next_refresh = time.time() + REFRESH_SECONDS
                continue
            render_screen()
            continue

        if time.time() >= next_refresh:
            vast_common.guarded_poll(poll_tick, render_screen)
            next_refresh = time.time() + REFRESH_SECONDS
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
        print(f"ERROR: {e}", file=sys.stderr)
        exit_code = 1
    finally:
        cleanup()
    return exit_code
