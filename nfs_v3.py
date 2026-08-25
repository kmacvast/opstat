#!/usr/bin/env python3
################################################################################
# Script:      nfs_v3.py
#
# Descr:       NFS v3 performance statistics for opstat. Queries VMS
#              counters and displays live NFS RPC operation statistics,
#              including a health summary, workload classification, latency
#              metrics, throughput, I/O size, workload distribution, and
#              refresh-delta tracking. Supports rolling sample averages,
#              interactive sorting, drill-down by cNode/view/tenant,
#              CSV export, and runtime historical statistics.
#
# Date:        2026-06-17
# Author:      JMo
# Revised:     KMac
# 
# Usage:
#   ./opstat --nfs --version=3.0 --vms <VMS_IP>
#
# Examples:
#   ./opstat --nfs --version=3.0 --vms <VMS_IP>
#   ./opstat --nfs --version=3.0 --vms <VMS_IP> --user vastadmin
#   ./opstat --nfs --version=3.0 --vms <VMS_IP> --sample-average 1h
#   ./opstat --nfs --version=3.0 --vms <VMS_IP> --csv nfs_stats.csv
#   ./opstat --nfs --version=3.0 --vms <VMS_IP> --discover-metrics
#   ./opstat --nfs --version=3.0 --vms <VMS_IP> --no-color
#
# Controls:
#   Space  - Refresh immediately
#   r      - Sort by RPC name (default)
#   o      - Sort by operations/sec
#   l      - Sort by avg latency
#   w      - Sort by % workload
#   c      - cNode drill-down
#   v      - View/export drill-down
#   t      - Tenant drill-down
#   x      - Exit drill-down (return to cluster view)
#   q      - Quit
#
################################################################################

import csv
import io
import os
import re
import shutil
import ssl
import sys
import time
import urllib.parse
from datetime import datetime

import openmetrics
import vast_api_log
import vast_common
import vast_drill
from opstat_version import VERSION
from tui_layout import (
    display_width, join_columns, pad_display, format_fixed_number,
    format_scaled_metric, truncate_display, c, set_color, set_unicode, glyph_set,
    as_float, raw_bw_to_gb_sec, format_os_release,
    _RST, _BOLD, _DIM, _RED, _GREEN, _YELLOW, _CYAN,
    _BRED, _BGREEN, _BYELLOW, _BCYAN, _BWHITE,
)

# NFS table column widths - headers and data rows must share these exactly.
_NFS_COL_SEP = " "
_NFS_COL_PROC = 12
_NFS_COL_OPS = 12
_NFS_COL_PCT = 7
_NFS_COL_LAT = 10
_NFS_COL_BW = 9
_NFS_COL_IO = 10

# Drill-down table column widths.
_NFS_DRILL_SEP = " "
_NFS_DRILL_NAME = 24
_NFS_DRILL_OPS = 12
_NFS_DRILL_LAT = 10
_NFS_DRILL_BW = 9
_NFS_DRILL_RPC = 12
_NFS_DRILL_TOP_PCT = 6

DEFAULT_PORT = 443
DEFAULT_USER = "admin"
DEFAULT_REFRESH_SECONDS = 5
DEFAULT_API_TIME_FRAME = "10m"

NFS_READ_BW_FQN  = "ProtoMetrics,proto_name=NFSCommon,rd_bw"
NFS_WRITE_BW_FQN = "ProtoMetrics,proto_name=NFSCommon,wr_bw"

OPS = [
    ("null",        "NULL"),
    ("getattr",     "GETATTR"),
    ("setattr",     "SETATTR"),
    ("lookup",      "LOOKUP"),
    ("access",      "ACCESS"),
    ("readlink",    "READLINK"),
    ("read",        "READ"),
    ("write",       "WRITE"),
    ("create",      "CREATE"),
    ("mkdir",       "MKDIR"),
    ("symlink",     "SYMLINK"),
    ("mknod",       "MKNOD"),
    ("remove",      "REMOVE"),
    ("rmdir",       "RMDIR"),
    ("rename",      "RENAME"),
    ("link",        "LINK"),
    ("readdir",     "READDIR"),
    ("readdirplus", "READDIRPLUS"),
    ("fsstat",      "FSSTAT"),
    ("fsinfo",      "FSINFO"),
    ("pathconf",    "PATHCONF"),
    ("commit",      "COMMIT"),
]

IO_LABELS   = frozenset({"READ", "WRITE"})
META_LABELS = frozenset(label for _, label in OPS) - IO_LABELS

# Drill-down configuration - object_type values are the VAST API monitor parameter names.
# cnode scopes use NfsMetrics; view/tenant scopes use ViewMetrics/TenantMetrics
# (NfsMetrics query returns HTTP 400 for view/tenant on current VMS builds).
_VIEW_READ_IOPS = vast_drill.VIEW_READ_IOPS
_VIEW_WRITE_IOPS = vast_drill.VIEW_WRITE_IOPS
_VIEW_READ_MD = vast_drill.VIEW_READ_MD
_VIEW_WRITE_MD = vast_drill.VIEW_WRITE_MD

_DRILL_CFG = {
    "cnode":  {
        "label": "CNODE",
        "object_type": "cnode",
        "endpoint":    "/cnodes/",
        "name_fields": ("name", "hostname", "mgmt_ip"),
        "no_aggregation": False,
    },
    "view":   {
        "label": "VIEW",
        "object_type": "view",
        "endpoint":    "/views/",
        "name_fields": ("path", "title", "name"),
        "no_aggregation": True,
    },
    "tenant": {
        "label": "TENANT",
        "object_type": "tenant",
        "endpoint":    "/tenants/",
        "name_fields": ("name",),
        "no_aggregation": False,
    },
}

_MAX_DRILL_OBJECTS = 8      # rows displayed / permanent monitors after ranking
_DRILL_PROBE_LIMIT = 32     # smallest rank batch tried before giving up
# Shared ranking / batching / throttle machinery. Constructed lazily in
# init_config once the engine's monitor helpers exist.
DRILL = None
_DRILL_MIN_QUERY_INTERVAL = 15.0   # ViewMetrics/TenantMetrics publish ~1/min

# Honest unavailable state for the VIEW drill (FR1, D-016). The marker is the
# contract the lab validator imports; the wording is deliberately
# cluster-scoped ("from this cluster"), never a universal claim about VAST,
# and names no internal metric families.
VIEW_UNAVAILABLE_MARKER = "Per-view NFSv3 attribution is not available"
VIEW_UNAVAILABLE_NOTICE = (
    "Per-view NFSv3 attribution is not available from this cluster.")
VIEW_UNAVAILABLE_DETAIL = "Cluster-level NFSv3 telemetry remains available."


# ---------------------------------------------------------------------------
# Runtime configuration (initialized by init_config)
# ---------------------------------------------------------------------------

ARGS = None
VMS = None
PORT = None
USER = None
PASSWORD = None
SAMPLE_AVERAGE = None
REFRESH_SECONDS = DEFAULT_REFRESH_SECONDS
CSV_FILE = None
API_TIME_FRAME = DEFAULT_API_TIME_FRAME
SAMPLE_AVERAGE_MODE = False
BASE_URL = None
SSL_CTX = ssl._create_unverified_context()
AUTH = None
HEADERS = None

# Mutable globals - all updated by main/fetch/drill helpers
RPC_MONITOR_ID = None
BW_MONITOR_ID  = None
CLUSTER_ID     = None
CLUSTER_NAME   = None
CLUSTER_OS     = None

SORT_MODE                 = "rpc"

LAST_ROWS   = []
LAST_SAMPLE = "-"
PREV_ROWS   = []       # rows from previous refresh cycle - used for delta display

DRILL_MODE     = None  # None | "cnode" | "view" | "tenant"
DRILL_OBJECTS  = []    # [{"id": ..., "name": ...}, ...]
DRILL_MONITORS = []    # [(monitor_id, object_name), ...]
LAST_DRILL_ROWS = []   # [{"name": ..., "total_ops": ..., "latency_us": ..., ...}]
DRILL_ERROR    = None  # set when drill-down fails; cleared on success
DRILL_STATUS   = None  # transient "Switching to …" message during drill setup
STARTUP_STATUS = None  # transient per-phase message during blocking startup

RUN_STARTED_AT = None

RUN_STATS = {}


# Dedupe window for run-stat accumulation. Only recently-returned samples can
# reappear across polls (the newest VMS sample repeats until a new one lands),
# so a small bounded window dedupes correctly without growing forever the way
# the previous unbounded sets did on long-running dashboards.
_MAX_SEEN_SAMPLE_IDS = 512


def _remember_sample(seen, sample_id):
    """Insertion-ordered bounded membership: True when *sample_id* is new."""
    if sample_id in seen:
        return False
    seen[sample_id] = None
    if len(seen) > _MAX_SEEN_SAMPLE_IDS:
        seen.pop(next(iter(seen)))
    return True


def _fresh_run_stats():
    return {
        label: {
            "min_us":           None,
            "max_us":           None,
            "weighted_sum_us":  0.0,
            "weight":           0.0,
            "seen_sample_ids":  {},
            "bw_min_gbs":       None,
            "bw_max_gbs":       None,
            "bw_seen_sample_ids": {},
        }
        for _op, label in OPS
    }


def init_config(args):
    """Initialize module globals from parsed CLI connection arguments."""
    global ARGS, VMS, PORT, USER, PASSWORD, SAMPLE_AVERAGE, REFRESH_SECONDS, CSV_FILE
    global API_TIME_FRAME, SAMPLE_AVERAGE_MODE, BASE_URL, AUTH, HEADERS, _COLOR
    global RUN_STARTED_AT, RUN_STATS
    global RPC_MONITOR_ID, BW_MONITOR_ID, CLUSTER_ID, CLUSTER_NAME, SORT_MODE
    global LAST_ROWS, LAST_SAMPLE, PREV_ROWS
    global DRILL_MODE, DRILL_OBJECTS, DRILL_MONITORS, LAST_DRILL_ROWS, DRILL_ERROR, DRILL_STATUS
    global DRILL

    ARGS = args

    VMS = args.vms
    PORT = args.port
    USER = args.user
    SAMPLE_AVERAGE = args.sample_average
    REFRESH_SECONDS = args.refresh
    CSV_FILE = args.csv

    API_TIME_FRAME = SAMPLE_AVERAGE or DEFAULT_API_TIME_FRAME
    SAMPLE_AVERAGE_MODE = SAMPLE_AVERAGE is not None

    BASE_URL = f"https://{VMS}/api" if PORT == 443 else f"https://{VMS}:{PORT}/api"
    HEADERS, AUTH, PASSWORD = vast_common.resolve_auth(
        USER, VMS, args.password, f"opstat/nfs-v3/{VERSION}",
    )
    vast_common.configure_connection(BASE_URL, HEADERS, SSL_CTX)

    log_path = vast_api_log.configure(
        getattr(args, "log_api_calls", False), "nfs-v3", VMS, PORT,
    )
    if log_path:
        print(f"API call logging enabled: {log_path}", file=sys.stderr, flush=True)
    om_path = openmetrics.configure(
        getattr(args, "export_openmetrics", False),
        getattr(args, "openmetrics_file", None),
        "nfs3", VMS,
    )
    if om_path:
        print(f"OpenMetrics export enabled: {om_path}", file=sys.stderr, flush=True)

    _COLOR = sys.stdout.isatty() and not args.no_color
    set_color(_COLOR)
    set_unicode(_UTF8)

    RUN_STARTED_AT = datetime.now()
    RUN_STATS = _fresh_run_stats()

    RPC_MONITOR_ID = None
    BW_MONITOR_ID = None
    CLUSTER_ID = None
    CLUSTER_NAME = None
    SORT_MODE = "rpc"
    LAST_ROWS = []
    LAST_SAMPLE = "-"
    PREV_ROWS = []
    DRILL_MODE = None
    DRILL_OBJECTS = []
    DRILL_MONITORS = []
    LAST_DRILL_ROWS = []
    DRILL_ERROR = None
    DRILL_STATUS = None
    DRILL = vast_drill.DrillSession(
        request_fn=api_request,
        create_monitor_fn=_create_monitor_raw,
        delete_monitor_fn=delete_monitor,
        max_objects=_MAX_DRILL_OBJECTS,
        min_batch=_DRILL_PROBE_LIMIT,
        min_query_interval=_DRILL_MIN_QUERY_INTERVAL,
    )

CSV_HEADER = [
    "local_time", "runtime", "vms", "port", "cluster", "cluster_id",
    "rpc_monitor_id", "bw_monitor_id", "sample_mode", "api_time_frame",
    "selected_sample", "rpc", "operations_per_sec", "percent_workload",
    "avg_latency_us", "run_min_latency_us", "run_max_latency_us",
    "run_mean_latency_us", "avg_throughput_gb_sec", "min_throughput_gb_sec",
    "max_throughput_gb_sec", "avg_io_size_bytes",
]

# ---------------------------------------------------------------------------
# TUI - color, box drawing, status indicators
# ---------------------------------------------------------------------------

_ANSI_RE = re.compile(r"\033\[[^m]*m")


def _vlen(s):
    """Visual display width - delegates to shared tui_layout.display_width."""
    return display_width(s)


def _vpad(s, width, align="<"):
    """Pad a possibly-colored string to `width` visual columns."""
    return pad_display(s, width, align)


# Detect UTF-8 terminal for box / block / arrow characters.
# Fall back to plain ASCII if encoding is unset or non-UTF.
_UTF8 = (sys.stdout.encoding or "ascii").lower().startswith("utf")

_G = glyph_set(_UTF8)
_H,  _V   = _G["H"], _G["V"]
_TL, _TR  = _G["TL"], _G["TR"]
_BL, _BR  = _G["BL"], _G["BR"]
_LT, _RT  = _G["LT"], _G["RT"]
_BLK      = _G["BLK"]    # full block  (workload bar fill)
_SHD      = _G["SHD"]    # light shade (workload bar empty)
_ARR_UP   = _G["ARR_UP"]
_ARR_DN   = _G["ARR_DN"]
_ARR_EQ   = _G["ARR_EQ"]
_DOT      = _G["DOT"]    # latency severity indicator
_MUS      = _G["MUS"]

_COLOR = False


# ── Box helpers ──────────────────────────────────────────────────────────────

def box_top(title, width):
    """Print: ┌─ TITLE ─────────────────────┐"""
    # A title longer than the frame would overflow the terminal and
    # corrupt every row below it (box_row truncates its content; the
    # title never was).
    title = truncate_display(title, max(1, width - 6))
    raw_pre = f"{_TL}{_H} {title} "
    fill    = max(0, width - display_width(raw_pre) - 1)
    if _COLOR:
        return (c(f"{_TL}{_H} ", _DIM)
                + c(title, _BWHITE)
                + c(f" {_H * fill}{_TR}", _DIM))
    return f"{raw_pre}{_H * fill}{_TR}"


def box_bottom(width):
    """Print: └──────────────────────────────┘"""
    return c(f"{_BL}{_H * (width - 2)}{_BR}", _DIM)


def box_sep(width):
    """Print: ├──────────────────────────────┤"""
    return c(f"{_LT}{_H * (width - 2)}{_RT}", _DIM)


def box_row(content, width):
    """Print: │ content (padded/truncated to width)    │"""
    inner = max(0, width - 4)   # 2 border chars + 2 padding spaces
    if _vlen(content) > inner:
        content = truncate_display(content, inner) + (_RST if _COLOR else "")
    pad   = max(0, inner - _vlen(content))
    border = c(_V, _DIM)
    return f"{border} {content}{' ' * pad} {border}"


def badge(text, color_code):
    """Colored status badge: [ TEXT ]"""
    return c(f"[ {text} ]", color_code)


def lat_dot(us):
    """Colored severity dot ● for a latency value."""
    if us is None:    return c(_DOT, _DIM)
    if us > 10_000:   return c(_DOT, _BRED)
    if us > 1_000:    return c(_DOT, _YELLOW)
    return c(_DOT, _BGREEN)


def delta_arrow(value):
    """▲ green / ▼ yellow / ► dim - for metrics where higher is better."""
    if value is None or abs(value) < 0.001:
        return c(_ARR_EQ, _DIM)
    return c(_ARR_UP, _BGREEN) if value > 0 else c(_ARR_DN, _YELLOW)


def delta_arrow_lat(value):
    """▼ green / ▲ yellow - for latency where lower is better."""
    if value is None or abs(value) < 0.01:
        return c(_ARR_EQ, _DIM)
    return c(_ARR_DN, _BGREEN) if value < 0 else c(_ARR_UP, _YELLOW)


def workload_bar(pct, bar_width=22, color=_GREEN):
    """Colored fill bar: ████████████░░░░░░░░  55.3%"""
    filled = max(0, min(bar_width, round(pct / 100 * bar_width)))
    empty  = bar_width - filled
    bar    = c(_BLK * filled, color) + c(_SHD * empty, _DIM)
    return f"{bar}  {pct:4.1f}%"


# ── Per-cell semantic color helpers ──────────────────────────────────────────

def _c_latency(s, us):
    if us is None:   return c(s, _DIM)
    if us > 10_000:  return c(s, _BRED)
    if us > 1_000:   return c(s, _YELLOW)
    return c(s, _BGREEN)


def _c_pct(s, pct):
    if pct is None or pct == 0: return c(s, _DIM)
    if pct > 50:                return c(s, _RED)
    if pct > 10:                return c(s, _YELLOW)
    return c(s, _GREEN)


def _c_ops(s, ops):
    return c(s, _DIM) if ops is None or ops == 0 else c(s, _GREEN)


def _c_bw(s, bw, label):
    if bw is None: return c(s, _DIM)
    return c(s, _CYAN) if label == "READ" else c(s, _YELLOW)


def _c_delta_positive(s, value):
    if value is None or value == 0: return c(s, _DIM)
    return c(s, _BGREEN) if value > 0 else c(s, _YELLOW)


def _c_delta_latency(s, value):
    if value is None or value == 0: return c(s, _DIM)
    return c(s, _BGREEN) if value < 0 else c(s, _YELLOW)


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Monitor management
# ---------------------------------------------------------------------------

def metric_names_for_op(op):
    if op == "null":
        return {"rate": "NfsMetrics,nfs_null", "avg": None}
    return {
        "rate": f"NfsMetrics,nfs_{op}_latency__rate",
        "avg":  f"NfsMetrics,nfs_{op}_latency__avg",
    }


def build_rpc_prop_list():
    props = []
    for op, _label in OPS:
        names = metric_names_for_op(op)
        props.extend(n for n in (names["rate"], names["avg"]) if n)
    return props


def build_bw_prop_list():
    return [NFS_READ_BW_FQN, NFS_WRITE_BW_FQN]


def build_drill_prop_list(mode):
    """Scope-aware monitor props - NfsMetrics only work for cluster/cnode scopes."""
    if mode == "view":
        return vast_drill.view_display_props()
    if mode == "tenant":
        return vast_drill.tenant_display_props()
    return build_rpc_prop_list() + build_bw_prop_list()


def build_drill_rank_prop_list(mode):
    """Minimal props for one-shot batch ranking of view/tenant candidates."""
    if mode == "view":
        return vast_drill.view_rank_props()
    if mode == "tenant":
        return vast_drill.tenant_rank_props()
    return build_drill_prop_list(mode)


def _is_batch_drill_mode(mode=None):
    """True when one monitor currently covers every drill object."""
    return vast_drill.DrillSession.batch_active(None, DRILL_MONITORS)


_normalize_object_id = vast_drill.normalize_object_id
_slice_result_for_object = vast_drill.slice_result_for_object


def _create_monitor_raw(name_suffix, prop_list, object_type, object_ids, *, no_aggregation=False):
    """Core monitor creation - object_type and object_ids are caller-supplied."""
    name = f"adhoc_opstat_{name_suffix}_{int(time.time())}"
    return vast_common.create_monitor_raw(
        api_request, name, prop_list, object_type, object_ids,
        time_frame=API_TIME_FRAME, no_aggregation=no_aggregation,
    )


def create_monitor(name_suffix, prop_list):
    return _create_monitor_raw(name_suffix, prop_list, "cluster", [CLUSTER_ID])


def delete_monitor(monitor_id):
    vast_common.delete_monitor(api_request, monitor_id)


def create_headline_monitors():
    """Create the RPC + BW headline monitors, merged into one when possible.

    A single cluster monitor can carry NfsMetrics and ProtoMetrics props
    together (the SMB engine's production headline monitor mixes exactly these
    families), which halves the per-refresh query count. The merged monitor is
    validated with one probe query; if the create fails or either family is
    missing from the returned prop_list, fall back to the historical split
    monitors so behavior on older/stricter VMS builds is unchanged.
    """
    global RPC_MONITOR_ID, BW_MONITOR_ID
    rpc_props = build_rpc_prop_list()
    bw_props = build_bw_prop_list()
    merged_id = None
    try:
        merged_id = create_monitor("rpcbw", rpc_props + bw_props)
        result = api_request("GET", f"/monitors/{merged_id}/query/")
        returned = set(result.get("prop_list", []) or []) if isinstance(result, dict) else set()
        if (any(p in returned for p in rpc_props)
                and any(p in returned for p in bw_props)):
            RPC_MONITOR_ID = merged_id
            BW_MONITOR_ID = merged_id
            return
        delete_monitor(merged_id)
    except RuntimeError:
        delete_monitor(merged_id)
    RPC_MONITOR_ID = create_monitor("rpc", rpc_props)
    BW_MONITOR_ID = create_monitor("bw", bw_props)


# ---------------------------------------------------------------------------
# Terminal / keyboard
# ---------------------------------------------------------------------------

setup_keyboard = vast_common.setup_keyboard
restore_terminal = vast_common.restore_terminal


_CLEANED_UP = False


def cleanup():
    global _CLEANED_UP
    if _CLEANED_UP:
        return
    restore_terminal()
    # Shutdown feedback: the drain is a slow synchronous DELETE loop (and blocks
    # signals), so announce it truthfully before blocking rather than leaving a
    # silent pause. Count is known up front; no fake progress.
    _pending = vast_common.pending_monitor_count()
    if _pending:
        vast_common.emit_stderr(vast_common.cleanup_message(_pending))
    vast_common.drain_monitors(delete_monitor)
    # Set the guard only after the drain actually completes, so an interrupted
    # or failed cleanup is retried by the atexit/finally backstop instead of
    # being skipped. drain_monitors blocks signals internally, so it is not
    # itself interruptible mid-loop.
    _CLEANED_UP = True
    vast_api_log.close()
    openmetrics.close()
    for monitor_id, detail in vast_common.failed_deletes():
        vast_common.emit_stderr(
            f"WARNING: monitor {monitor_id} not deleted: {detail}")


def signal_handler(_signum, _frame):
    cleanup()
    print()
    sys.exit(0)


check_keypress = vast_common.check_keypress


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

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
    """Format a numeric delta with explicit +/- sign."""
    if value is None:
        return ""
    sign = "+" if value >= 0 else ""
    return f"{sign}{value:.{precision}f}"


def csv_value(value):
    if value is None:
        return ""
    try:
        return float(value)
    except Exception:
        return value


clear_screen = vast_common.clear_screen


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------

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
    runtime     = str(datetime.now() - RUN_STARTED_AT).split(".")[0]
    local_time  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(CSV_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        for r in rows:
            writer.writerow([
                local_time, runtime, VMS, PORT, CLUSTER_NAME, CLUSTER_ID,
                RPC_MONITOR_ID, BW_MONITOR_ID, sample_mode, API_TIME_FRAME,
                selected_sample,
                r["label"],
                csv_value(r["ops_sec"]),
                csv_value(r["pct"]),
                csv_value(r["avg_us"]),
                csv_value(r["run_min_us"]),
                csv_value(r["run_max_us"]),
                csv_value(r["run_mean_us"]),
                csv_value(r.get("bw_gbs")),
                csv_value(r.get("bw_min_gbs")),
                csv_value(r.get("bw_max_gbs")),
                csv_value(r.get("avg_io_bytes")),
            ])


# ---------------------------------------------------------------------------
# Math helpers
# ---------------------------------------------------------------------------

def avg_io_size_bytes(ops_sec, bw_gbs):
    ops = as_float(ops_sec)
    bw  = as_float(bw_gbs)
    if ops is None or bw is None or ops <= 0:
        return None
    return (bw * 1_000_000_000.0) / ops


def _weighted_avg(pairs):
    """Weighted average of (weight, value) pairs. Returns None if weight sums to zero."""
    weight_sum = sum(w for w, _ in pairs)
    if weight_sum <= 0:
        return None
    return sum(w * v for w, v in pairs) / weight_sum


def _list_avg(values):
    return sum(values) / len(values) if values else None


def _update_bound(stat, min_key, max_key, value):
    if stat[min_key] is None or value < stat[min_key]:
        stat[min_key] = value
    if stat[max_key] is None or value > stat[max_key]:
        stat[max_key] = value


# ---------------------------------------------------------------------------
# Data extraction
# ---------------------------------------------------------------------------

def _result_parts(result):
    """Returns (prop_list, data, prop_idx) from a monitor query result dict."""
    prop_list = result.get("prop_list", [])
    data      = result.get("data", [])
    prop_idx  = {name: idx for idx, name in enumerate(prop_list)}
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
    best_row   = data[0]
    best_score = -1
    for row in data:
        score = sum(1 for idx in metric_indexes if idx < len(row) and row[idx] is not None)
        if score > best_score:
            best_score = score
            best_row   = row
        if score == len(metric_indexes):
            break
    return best_row, best_row[0] if best_row else "-"


def compute_combined_avg_latency(rows):
    pairs = [(as_float(r["ops_sec"]), as_float(r["avg_us"])) for r in rows]
    return _weighted_avg([
        (ops, avg) for ops, avg in pairs
        if ops is not None and avg is not None and ops > 0
    ])


def compute_total_throughput_gbs(rows):
    valid = [as_float(r.get("bw_gbs")) for r in rows if r["label"] in IO_LABELS]
    valid = [v for v in valid if v is not None]
    return sum(valid) if valid else None


def _desc_sort_key(r, field):
    v = as_float(r[field])
    return (v is None, -(v or 0.0), r["label"])


def sorted_rows(rows):
    if SORT_MODE == "latency":
        return sorted(rows, key=lambda r: _desc_sort_key(r, "avg_us"))
    if SORT_MODE == "workload":
        return sorted(rows, key=lambda r: _desc_sort_key(r, "pct"))
    if SORT_MODE == "ops":
        return sorted(rows, key=lambda r: _desc_sort_key(r, "ops_sec"))
    return sorted(rows, key=lambda r: r["label"])


def sort_label():
    return {
        "rpc":     "RPC name A-Z",
        "latency": "avg latency high-low",
        "workload":"% workload high-low",
        "ops":     "operations/sec high-low",
    }.get(SORT_MODE, SORT_MODE)


# ---------------------------------------------------------------------------
# Row builders
# ---------------------------------------------------------------------------

def build_rpc_rows_from_single_sample(result):
    _prop_list, data, prop_idx = _result_parts(result)
    if not data:
        return [], "-"
    # Score only the RPC props: a merged headline monitor also carries the
    # ProtoMetrics bandwidth columns, which publish on a different cadence.
    selected_row, selected_sample = select_latest_complete_row(
        data, prop_idx, build_rpc_prop_list())
    rows = []
    for op, label in OPS:
        names = metric_names_for_op(op)
        rows.append({
            "label":   label,
            "ops_sec": metric_value_from_row(selected_row, prop_idx, names["rate"]),
            "avg_us":  metric_value_from_row(selected_row, prop_idx, names["avg"]),
            "sample":  selected_sample,
            "bw_gbs":  None,
        })
    return rows, selected_sample


def build_rpc_rows_from_sample_average(result):
    _prop_list, data, prop_idx = _result_parts(result)
    if not data:
        return [], "-"
    newest_sample = data[0][0] if data and data[0] else "-"
    rows = []
    for op, label in OPS:
        names     = metric_names_for_op(op)
        rate_idx  = prop_idx.get(names["rate"]) if names["rate"] else None
        avg_idx   = prop_idx.get(names["avg"])  if names["avg"]  else None
        rate_values       = []
        latency_components = []
        for row in data:
            sample_time = row[0] if row else newest_sample
            rate = as_float(row[rate_idx]) if rate_idx is not None and rate_idx < len(row) else None
            avg  = as_float(row[avg_idx])  if avg_idx  is not None and avg_idx  < len(row) else None
            if rate is not None:
                rate_values.append(rate)
            if rate is not None and rate > 0 and avg is not None:
                latency_components.append((rate, avg, sample_time))
        rows.append({
            "label":   label,
            "ops_sec": _list_avg(rate_values),
            "avg_us":  _weighted_avg([(r, a) for r, a, _ in latency_components]),
            "sample":  latency_components[0][2] if latency_components else newest_sample,
            "bw_gbs":  None,
        })
    return rows, f"rolling average over {API_TIME_FRAME}"


def extract_bw_from_single_sample(result):
    _prop_list, data, prop_idx = _result_parts(result)
    if not data:
        return None, None
    # Score only the bandwidth props - see build_rpc_rows_from_single_sample.
    selected_row, _sample = select_latest_complete_row(
        data, prop_idx, build_bw_prop_list())
    return (
        raw_bw_to_gb_sec(metric_value_from_row(selected_row, prop_idx, NFS_READ_BW_FQN)),
        raw_bw_to_gb_sec(metric_value_from_row(selected_row, prop_idx, NFS_WRITE_BW_FQN)),
    )


def extract_bw_from_sample_average(result):
    _prop_list, data, prop_idx = _result_parts(result)
    if not data:
        return None, None
    read_idx  = prop_idx.get(NFS_READ_BW_FQN)
    write_idx = prop_idx.get(NFS_WRITE_BW_FQN)
    read_values, write_values = [], []
    for row in data:
        if read_idx is not None and read_idx < len(row):
            v = raw_bw_to_gb_sec(row[read_idx])
            if v is not None:
                read_values.append(v)
        if write_idx is not None and write_idx < len(row):
            v = raw_bw_to_gb_sec(row[write_idx])
            if v is not None:
                write_values.append(v)
    return _list_avg(read_values), _list_avg(write_values)


def build_rows_from_results(rpc_result, bw_result):
    if not isinstance(rpc_result, dict):
        return [], "-"
    if SAMPLE_AVERAGE_MODE:
        rows, selected_sample = build_rpc_rows_from_sample_average(rpc_result)
        read_gbs, write_gbs = (
            extract_bw_from_sample_average(bw_result)
            if isinstance(bw_result, dict) else (None, None)
        )
    else:
        rows, selected_sample = build_rpc_rows_from_single_sample(rpc_result)
        read_gbs, write_gbs = (
            extract_bw_from_single_sample(bw_result)
            if isinstance(bw_result, dict) else (None, None)
        )
    for r in rows:
        if r["label"] == "READ":
            r["bw_gbs"] = read_gbs
        if r["label"] == "WRITE":
            r["bw_gbs"] = write_gbs
    total_ops = sum(as_float(r["ops_sec"]) for r in rows if as_float(r["ops_sec"]) is not None)
    for r in rows:
        ops = as_float(r["ops_sec"])
        r["pct"] = ops / total_ops * 100.0 if total_ops > 0 and ops is not None else None
    return rows, selected_sample


# ---------------------------------------------------------------------------
# Run stats
# ---------------------------------------------------------------------------

def update_run_stats(rows):
    for r in rows:
        label   = r["label"]
        avg_us  = as_float(r["avg_us"])
        ops_sec = as_float(r["ops_sec"])
        sample  = r.get("sample", "-")
        stat    = RUN_STATS[label]
        if avg_us is not None and ops_sec is not None and ops_sec > 0:
            sample_id = f"lat:{sample}:{avg_us}:{ops_sec}"
            if _remember_sample(stat["seen_sample_ids"], sample_id):
                _update_bound(stat, "min_us", "max_us", avg_us)
                stat["weighted_sum_us"] += avg_us * ops_sec
                stat["weight"]          += ops_sec
        bw_gbs = as_float(r.get("bw_gbs"))
        if label in IO_LABELS and bw_gbs is not None:
            sample_id = f"bw:{sample}:{bw_gbs}"
            if _remember_sample(stat["bw_seen_sample_ids"], sample_id):
                _update_bound(stat, "bw_min_gbs", "bw_max_gbs", bw_gbs)


def run_mean_us(label):
    stat = RUN_STATS[label]
    if stat["weight"] <= 0:
        return None
    return stat["weighted_sum_us"] / stat["weight"]


def attach_run_stats(row):
    label = row["label"]
    row["run_min_us"]  = RUN_STATS[label]["min_us"]
    row["run_max_us"]  = RUN_STATS[label]["max_us"]
    row["run_mean_us"] = run_mean_us(label)
    if label in IO_LABELS:
        row["bw_min_gbs"]   = RUN_STATS[label]["bw_min_gbs"]
        row["bw_max_gbs"]   = RUN_STATS[label]["bw_max_gbs"]
        row["avg_io_bytes"] = avg_io_size_bytes(row.get("ops_sec"), row.get("bw_gbs"))
    else:
        row["bw_min_gbs"]   = None
        row["bw_max_gbs"]   = None
        row["avg_io_bytes"] = None
    return row


# ---------------------------------------------------------------------------
# Workload analysis
# ---------------------------------------------------------------------------

def workload_mix(rows):
    """Returns (meta_pct, read_pct, write_pct) as percentages of total ops."""
    total = sum(as_float(r["ops_sec"]) or 0 for r in rows)
    if total <= 0:
        return 0.0, 0.0, 0.0
    read_ops  = next((as_float(r["ops_sec"]) or 0 for r in rows if r["label"] == "READ"),  0)
    write_ops = next((as_float(r["ops_sec"]) or 0 for r in rows if r["label"] == "WRITE"), 0)
    meta_ops  = total - read_ops - write_ops
    return meta_ops / total * 100, read_ops / total * 100, write_ops / total * 100


def classify_workload(rows):
    """Return a human-readable workload description string."""
    total = sum(as_float(r["ops_sec"]) or 0 for r in rows)
    if total < 0.5:
        return "Idle / no load"

    meta_pct, read_pct, write_pct = workload_mix(rows)
    io_pct = read_pct + write_pct

    rddir_ops  = sum(as_float(r["ops_sec"]) or 0 for r in rows if r["label"] in ("READDIR", "READDIRPLUS"))
    create_ops = sum(as_float(r["ops_sec"]) or 0 for r in rows if r["label"] in ("CREATE", "MKDIR", "SYMLINK", "MKNOD"))
    remove_ops = sum(as_float(r["ops_sec"]) or 0 for r in rows if r["label"] in ("REMOVE", "RMDIR"))

    # I/O size tag
    read_io  = next((as_float(r.get("avg_io_bytes")) for r in rows if r["label"] == "READ"),  None)
    write_io = next((as_float(r.get("avg_io_bytes")) for r in rows if r["label"] == "WRITE"), None)
    io_bytes = read_io if read_io else write_io
    size_tag = ""
    if io_bytes:
        if io_bytes < 8_192:
            size_tag = "small-file "
        elif io_bytes >= 65_536:
            size_tag = "large-block "

    if rddir_ops / total * 100 > 25:
        return f"{size_tag}directory traversal workload"
    if (create_ops + remove_ops) / total * 100 > 30:
        return f"{size_tag}namespace churn workload"
    if meta_pct >= 80:
        return f"{size_tag}metadata-heavy workload"
    if meta_pct >= 60:
        return f"{size_tag}metadata-heavy {'write' if write_pct > read_pct else 'read'} workload"
    if io_pct >= 80:
        if read_pct > write_pct * 3:
            return f"{size_tag}read-heavy workload"
        if write_pct > read_pct * 3:
            return f"{size_tag}write-heavy workload"
        return f"{size_tag}balanced read/write workload"
    if read_pct > 50:
        return f"{size_tag}read-dominated mixed workload"
    if write_pct > 50:
        return f"{size_tag}write-dominated mixed workload"
    if io_pct > 30 and meta_pct > 30:
        dom = "read" if read_pct > write_pct else "write"
        return f"{size_tag}mixed {dom} + metadata workload"
    return f"{size_tag}mixed workload"


def nfs_health_label(total_ops, combined_latency_us):
    """Return (label_string, ansi_color) for the NFS health status."""
    if total_ops is None or total_ops < 0.5:
        return "IDLE", _DIM
    if combined_latency_us is None:
        return "LOW LOAD", _BGREEN
    if combined_latency_us > 50_000:
        return "CRITICAL", _BRED
    if combined_latency_us > 10_000:
        return "DEGRADED", _BRED
    if combined_latency_us > 5_000:
        return "ELEVATED LATENCY", _YELLOW
    if combined_latency_us > 1_000:
        return "MODERATE LATENCY", _YELLOW
    if total_ops < 10:
        return "LOW LOAD", _BGREEN
    return "HEALTHY", _BGREEN


# ---------------------------------------------------------------------------
# Delta tracking
# ---------------------------------------------------------------------------

def compute_deltas(current_rows, prev_rows):
    """Return dict[label] = {ops, lat, bw} for fields that changed."""
    if not prev_rows or not current_rows:
        return {}
    prev_by_label = {r["label"]: r for r in prev_rows}
    deltas = {}
    for r in current_rows:
        label = r["label"]
        p = prev_by_label.get(label)
        if not p:
            continue
        d = {}
        cur_ops  = as_float(r["ops_sec"]);  prev_ops  = as_float(p["ops_sec"])
        cur_lat  = as_float(r["avg_us"]);   prev_lat  = as_float(p["avg_us"])
        cur_bw   = as_float(r.get("bw_gbs")); prev_bw = as_float(p.get("bw_gbs"))
        if cur_ops  is not None and prev_ops  is not None:
            d["ops"] = cur_ops  - prev_ops
        if cur_lat  is not None and prev_lat  is not None:
            d["lat"] = cur_lat  - prev_lat
        if cur_bw   is not None and prev_bw   is not None:
            d["bw"]  = cur_bw   - prev_bw
        if d:
            deltas[label] = d
    return deltas


def cluster_delta_summary(deltas):
    """Aggregate per-label deltas to cluster-wide totals."""
    ops_deltas = [d["ops"] for d in deltas.values() if "ops" in d]
    bw_deltas  = [d["bw"]  for label, d in deltas.items() if label in IO_LABELS and "bw" in d]
    # Weighted latency delta is complex to aggregate; show the label with largest abs change
    lat_deltas = [(label, d["lat"]) for label, d in deltas.items() if "lat" in d]
    return (
        sum(ops_deltas) if ops_deltas else None,
        sum(bw_deltas)  if bw_deltas  else None,
        lat_deltas,
    )


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------

def fetch_monitor_query():
    global LAST_ROWS, LAST_SAMPLE, PREV_ROWS

    PREV_ROWS = LAST_ROWS  # snapshot before updating

    rpc_result = api_request("GET", f"/monitors/{RPC_MONITOR_ID}/query/")
    # Merged headline monitor: one query carries both RPC and BW props.
    bw_result = (
        rpc_result if BW_MONITOR_ID == RPC_MONITOR_ID
        else api_request("GET", f"/monitors/{BW_MONITOR_ID}/query/")
    )

    rows, sample = build_rows_from_results(rpc_result, bw_result)
    update_run_stats(rows)
    for r in rows:
        attach_run_stats(r)

    LAST_ROWS   = rows
    LAST_SAMPLE = sample

    write_csv_rows(rows, sample)
    _export_openmetrics()


def _openmetrics_series():
    series = []
    for r in LAST_ROWS:
        label = r["label"]
        is_io = label in IO_LABELS
        bw_gbs = as_float(r.get("bw_gbs"))
        io_bytes = as_float(r.get("avg_io_bytes"))
        if io_bytes is None and is_io:
            io_bytes = avg_io_size_bytes(r.get("ops_sec"), bw_gbs)
        series.append({
            "operation": label,
            "category": "data" if is_io else "metadata",
            "ops_sec": as_float(r.get("ops_sec")),
            "avg_us": as_float(r.get("avg_us")),
            "bw_bytes_sec": openmetrics.gbps_to_bytes_sec(bw_gbs),
            "io_bytes": io_bytes,
        })
    return series


def _export_openmetrics():
    if not openmetrics.is_enabled():
        return
    openmetrics.export_snapshot(
        CLUSTER_NAME, None, CLUSTER_NAME, _openmetrics_series(), sample=LAST_SAMPLE,
    )


# ---------------------------------------------------------------------------
# Drill-down
# ---------------------------------------------------------------------------

def _obj_name(obj, name_fields):
    return vast_common.resolve_object_name(obj, name_fields)


def _cleanup_drill_monitors():
    global DRILL_MONITORS
    for monitor_id, _name in DRILL_MONITORS:
        delete_monitor(monitor_id)
    DRILL_MONITORS = []


_parse_sample_ts = vast_drill.parse_sample_ts
_latest_complete_values = vast_drill.latest_complete_values
_bounding_samples = vast_common.bounding_samples
_delta_rate_from_samples = vast_drill.delta_rate_from_samples
_avg_from_sum_count_deltas = vast_drill.avg_from_sum_count_deltas
_weighted_us = vast_drill.weighted_us
_drill_top_op = vast_drill.top_op


def _build_cnode_drill_row(result, obj_name):
    rows, _sample = build_rows_from_results(result, result)
    total_ops = sum(as_float(r["ops_sec"]) or 0 for r in rows)
    latency = compute_combined_avg_latency(rows)
    bw = compute_total_throughput_gbs(rows)
    active = [r for r in rows if (as_float(r["ops_sec"]) or 0) > 0]
    top = max(active, key=lambda r: as_float(r["ops_sec"]) or 0, default=None)
    return {
        "name": obj_name,
        "total_ops": total_ops if total_ops > 0 else None,
        "latency_us": latency,
        "bw_gbs": bw if bw else None,
        "top_rpc": top["label"] if top else "-",
        "top_rpc_pct": as_float(top["pct"]) if top else None,
    }


_build_view_drill_row = vast_drill.build_view_row
_build_tenant_drill_row = vast_drill.build_tenant_row


def _build_drill_row(mode, result, obj_name):
    if mode == "view":
        return _build_view_drill_row(result, obj_name)
    if mode == "tenant":
        return _build_tenant_drill_row(result, obj_name)
    return _build_cnode_drill_row(result, obj_name)


def _drill_score(mode, sliced_result):
    """Activity score for one object during ranking."""
    return as_float(_build_drill_row(mode, sliced_result, "")["total_ops"]) or 0.0


def _rank_drill_candidates(mode, objects, cfg):
    """Return the most active ``_MAX_DRILL_OBJECTS`` view/tenant candidates.

    Ranking a cluster's full object list used to mean one create/query/delete
    round trip per 32 objects, serially: 429 views cost 42 requests and ~47 s
    of "stand by" on a cluster answering in ~1 s per call. vast_drill tries a
    single server-side topn request, then the fewest batched rank monitors the
    cluster accepts, and caches the result so re-entering a drill is free.
    """
    return DRILL.rank(
        mode, objects,
        object_type=cfg["object_type"],
        rank_props=build_drill_rank_prop_list(mode),
        score_fn=lambda sliced: _drill_score(mode, sliced),
        time_frame=API_TIME_FRAME,
        name_of=lambda obj: _obj_name(obj, cfg["name_fields"]),
        no_aggregation=cfg.get("no_aggregation", False),
    )


def view_attribution_source():
    """Decide the per-view NFSv3 attribution source for this cluster.

    A capability result, not an eternal declaration (D-016). On the validated
    VAST 5.4.6 environment there is none: host_view publishes no NFS series
    under any protocol label, and ViewMetrics attributed ~0 of a proven
    ~19,400 ops/s NFSv3 workload to the root view while carrying no protocol
    dimension at all - so ranking it under an NFSv3 heading surfaced other
    protocols' busiest views. Newer builds (5.5.0.1-class) are known to
    publish protocol-labeled host_view series; when a validated capability
    check for those exists, it replaces this deterministic result and returns
    a usable source. Until then: None means "this cluster cannot answer the
    per-view NFSv3 question honestly".
    """
    return None


def enter_drill_mode(mode):
    global DRILL_MODE, DRILL_OBJECTS, DRILL_MONITORS, DRILL_ERROR, LAST_DRILL_ROWS

    cfg = _DRILL_CFG.get(mode)
    if not cfg:
        DRILL_ERROR = f"Unknown drill mode: {mode}"
        return

    if mode == "view" and view_attribution_source() is None:
        # Honest unavailable state (FR1/D-016). Zero API cost on purpose:
        # no view inventory fetch, no rank monitors, no display monitors
        # for a question this cluster cannot answer.
        DRILL_ERROR = VIEW_UNAVAILABLE_NOTICE
        return

    try:
        data    = api_request("GET", cfg["endpoint"])
        objects = normalize_list_response(data)
    except RuntimeError as e:
        DRILL_ERROR = f"Cannot fetch {mode} objects: {e}"
        return

    if not objects:
        DRILL_ERROR = f"No {mode} objects returned from {cfg['endpoint']}"
        return

    all_valid = [o for o in objects if "id" in o]
    if mode in ("view", "tenant"):
        DRILL_OBJECTS = _rank_drill_candidates(mode, all_valid, cfg)
    else:
        # cnodes are few and all equally interesting, so no ranking pass.
        selected = all_valid[:_MAX_DRILL_OBJECTS]
        DRILL_OBJECTS = [
            {"id": o["id"], "name": _obj_name(o, cfg["name_fields"])}
            for o in selected
        ]

    if not DRILL_OBJECTS:
        DRILL_ERROR = f"No valid {mode} objects available for drill-down"
        return

    _cleanup_drill_monitors()
    new_monitors, last_error = DRILL.create_monitors(
        mode, DRILL_OBJECTS,
        object_type=cfg["object_type"],
        props=build_drill_prop_list(mode),
        no_aggregation=cfg.get("no_aggregation", False),
        # cnode per-object rows are inferred (VMS returns an object_id column
        # even for a single-object cnode monitor), so confirm before trusting.
        validate_batch=(mode == "cnode"),
    )

    if not new_monitors:
        hint = ""
        if mode == "view":
            hint = " (view monitors require seconds resolution without aggregation)"
        elif mode == "tenant":
            hint = " (tenant scope requires TenantMetrics counters)"
        detail = f": {last_error}" if last_error else ""
        DRILL_ERROR = (
            f"Could not create any {mode} monitors (object_type="
            f"'{cfg['object_type']}' may not be supported){hint}{detail}"
        )
        DRILL_OBJECTS = []
        return

    DRILL_MONITORS  = new_monitors
    DRILL_MODE      = mode
    DRILL_ERROR     = None
    LAST_DRILL_ROWS = []


def exit_drill_mode():
    global DRILL_MODE, DRILL_OBJECTS, LAST_DRILL_ROWS, DRILL_ERROR, DRILL_STATUS
    _cleanup_drill_monitors()
    DRILL_MODE      = None
    DRILL_OBJECTS   = []
    LAST_DRILL_ROWS = []
    DRILL_ERROR     = None
    DRILL_STATUS    = None


def fetch_drill_query(force=False):
    """Re-query the active drill monitors, throttled unless *force*.

    Object-scoped metric families publish a new sample roughly once a minute,
    so a 5s dashboard refresh re-fetched identical payloads: on VAST OS
    5.5.0.1 nine consecutive polls all returned the same newest sample
    timestamp. Skipping those wasted queries keeps the panel exactly as fresh
    while cutting drill request volume. The space bar passes force=True.
    """
    global LAST_DRILL_ROWS, DRILL_ERROR
    if not DRILL_MODE:
        return
    if not DRILL.should_query(force=force, have_data=bool(LAST_DRILL_ROWS)):
        return
    drill_rows = []
    query_errors = 0

    if _is_batch_drill_mode() and DRILL_MONITORS:
        monitor_id, _name = DRILL_MONITORS[0]
        try:
            result = api_request("GET", f"/monitors/{monitor_id}/query/")
            for obj in DRILL_OBJECTS:
                slice_result = _slice_result_for_object(result, obj["id"])
                drill_rows.append(_build_drill_row(DRILL_MODE, slice_result, obj["name"]))
        except RuntimeError:
            query_errors = len(DRILL_OBJECTS)
    else:
        for monitor_id, obj_name in DRILL_MONITORS:
            try:
                result = api_request("GET", f"/monitors/{monitor_id}/query/")
                drill_rows.append(_build_drill_row(DRILL_MODE, result, obj_name))
            except RuntimeError:
                query_errors += 1

    LAST_DRILL_ROWS = sorted(
        drill_rows,
        key=lambda r: r["total_ops"] or 0,
        reverse=True,
    )
    if openmetrics.is_enabled() and DRILL_MODE:
        openmetrics.export_drill(CLUSTER_NAME, DRILL_MODE, LAST_DRILL_ROWS, sample=LAST_SAMPLE)
    if not LAST_DRILL_ROWS and query_errors:
        DRILL_ERROR = (
            f"{DRILL_MODE} drill monitors returned no data "
            f"({query_errors}/{len(DRILL_OBJECTS)} queries failed)"
        )


def switch_drill_mode(mode):
    """Enter drill mode with an immediate standby message for slow monitor setup."""
    global SORT_MODE
    _cfg = _DRILL_CFG.get(mode, {})
    exit_drill_mode()
    if mode in ("view", "tenant"):
        SORT_MODE = "ops"
    def _work():
        enter_drill_mode(mode)
        if DRILL_MODE:
            fetch_drill_query(force=True)

    # The VIEW capability notice (D-016) returns instantly at zero API cost,
    # so that entry must never claim a 30-second wait even on its first use.
    instant = mode == "view" and view_attribution_source() is None
    vast_drill.with_loading_status(
        _set_drill_status, render_screen, mode, _work,
        first_time=bool(DRILL) and not instant and DRILL.begin_load(mode))


def _set_drill_status(text):
    global DRILL_STATUS
    DRILL_STATUS = text


# ---------------------------------------------------------------------------
# Table column layout (adaptive to terminal width)
# ---------------------------------------------------------------------------
#
# Always visible  : Procedure(12)  Ops/s(12)  %Work(7)  Avg µs(10)  = 44 + 3 sep = 47
# +run stats      : Min µs(10)  Max µs(10)  Mean µs(10)             = +33 → 80
# +BW (data only) : Avg GB/s(9)  Min GB/s(9)  Max GB/s(9)           = +30 → 110
# +I/O size       : I/O Size(10)                                     = +11 → 121
# Box overhead    : 4 chars (│ border + spaces on each side)

def _col_levels(inner_width):
    """Return (show_run_stats, show_bw, show_io) based on available inner width."""
    show_run = inner_width >= 80
    show_bw  = inner_width >= 110
    show_io  = inner_width >= 121 and show_bw
    return show_run, show_bw, show_io


def _nfs_pct_cell(pct):
    """Format %Work column to fixed width (unit suffix before padding)."""
    if pct is not None:
        return pad_display(f"{pct:.1f}%", _NFS_COL_PCT, ">")
    return pad_display("-", _NFS_COL_PCT, ">")


def _table_header_cells(show_run, show_bw, show_io):
    """Return header cell strings (unjoined) for the DATA I/O table."""
    parts = [
        c(pad_display("Procedure", _NFS_COL_PROC, "<"), _BOLD),
        c(pad_display("Ops/s", _NFS_COL_OPS, ">"), _BOLD),
        c(pad_display("%Work", _NFS_COL_PCT, ">"), _BOLD),
        c(pad_display(f"Avg {_MUS}", _NFS_COL_LAT, ">"), _BOLD),
    ]
    if show_run:
        parts += [
            c(pad_display(f"Min {_MUS}", _NFS_COL_LAT, ">"), _BOLD),
            c(pad_display(f"Max {_MUS}", _NFS_COL_LAT, ">"), _BOLD),
            c(pad_display(f"Mean {_MUS}", _NFS_COL_LAT, ">"), _BOLD),
        ]
    if show_bw:
        parts += [
            c(pad_display("Avg GB/s", _NFS_COL_BW, ">"), _BOLD),
            c(pad_display("Min GB/s", _NFS_COL_BW, ">"), _BOLD),
            c(pad_display("Max GB/s", _NFS_COL_BW, ">"), _BOLD),
        ]
    if show_io:
        parts.append(c(pad_display("I/O Size", _NFS_COL_IO, ">"), _BOLD))
    return parts


def _table_header(show_run, show_bw, show_io):
    """Column header row for the DATA I/O table."""
    return join_columns(_table_header_cells(show_run, show_bw, show_io), _NFS_COL_SEP)


def _meta_table_header(show_run):
    """Column header row for the METADATA table (no BW/IO columns)."""
    return join_columns(_meta_table_header_cells(show_run), _NFS_COL_SEP)


def _meta_table_header_cells(show_run):
    parts = [
        c(pad_display("Procedure", _NFS_COL_PROC, "<"), _BOLD),
        c(pad_display("Ops/s", _NFS_COL_OPS, ">"), _BOLD),
        c(pad_display("%Work", _NFS_COL_PCT, ">"), _BOLD),
        c(pad_display(f"Avg {_MUS}", _NFS_COL_LAT, ">"), _BOLD),
    ]
    if show_run:
        parts += [
            c(pad_display(f"Min {_MUS}", _NFS_COL_LAT, ">"), _BOLD),
            c(pad_display(f"Max {_MUS}", _NFS_COL_LAT, ">"), _BOLD),
            c(pad_display(f"Mean {_MUS}", _NFS_COL_LAT, ">"), _BOLD),
        ]
    return parts


def _rpc_row_cells(r, show_run=True, show_bw=True, show_io=True):
    """Return data cell strings (unjoined) for one RPC row."""
    label  = r["label"]
    ops    = as_float(r["ops_sec"])
    pct    = as_float(r["pct"])
    avg_us = as_float(r["avg_us"])
    bw     = as_float(r.get("bw_gbs"))

    if label == "READ":
        label_s = c(pad_display(label, _NFS_COL_PROC, "<"), _BCYAN)
    elif label == "WRITE":
        label_s = c(pad_display(label, _NFS_COL_PROC, "<"), _BYELLOW)
    elif ops:
        label_s = c(pad_display(label, _NFS_COL_PROC, "<"), _BWHITE)
    else:
        label_s = c(pad_display(label, _NFS_COL_PROC, "<"), _DIM)

    parts = [
        label_s,
        _c_ops(fmt(r["ops_sec"], _NFS_COL_OPS, 2), ops),
        _c_pct(_nfs_pct_cell(pct), pct),
        _c_latency(fmt(r["avg_us"], _NFS_COL_LAT, 2), avg_us),
    ]
    if show_run:
        parts += [
            c(fmt(r["run_min_us"],  _NFS_COL_LAT, 2), _DIM),
            c(fmt(r["run_max_us"],  _NFS_COL_LAT, 2), _DIM),
            c(fmt(r["run_mean_us"], _NFS_COL_LAT, 2), _DIM),
        ]
    if show_bw:
        parts += [
            _c_bw(fmt(r.get("bw_gbs"),     _NFS_COL_BW, 3), bw, label),
            c(fmt(r.get("bw_min_gbs"),      _NFS_COL_BW, 3), _DIM),
            c(fmt(r.get("bw_max_gbs"),      _NFS_COL_BW, 3), _DIM),
        ]
    if show_io:
        io_color = _CYAN if label == "READ" else _YELLOW if label == "WRITE" else _DIM
        parts.append(c(fmt_size(r.get("avg_io_bytes"), _NFS_COL_IO), io_color))
    return parts


def _rpc_row_content(r, show_run=True, show_bw=True, show_io=True):
    """Build the inner content string for one RPC row (no border)."""
    return join_columns(_rpc_row_cells(r, show_run, show_bw, show_io), _NFS_COL_SEP)


def _subtotal_row_content(label_text, ops, pct, lat, bw, show_run, show_bw, show_io):
    """Build a subtotal row (DATA TOTAL / META TOTAL)."""
    parts   = [
        c(pad_display(label_text, _NFS_COL_PROC, "<"), _BOLD),
        c(fmt(ops, _NFS_COL_OPS, 2), _BOLD),
        c(_nfs_pct_cell(pct), _BOLD),
        _c_latency(fmt(lat, _NFS_COL_LAT, 2), lat),
    ]
    if show_run:
        parts += [c(fmt(None, _NFS_COL_LAT), _DIM)] * 3
    if show_bw:
        bw_s = c(fmt(bw, _NFS_COL_BW, 3), _CYAN) if bw is not None else c(fmt(None, _NFS_COL_BW), _DIM)
        parts += [bw_s, c(fmt(None, _NFS_COL_BW), _DIM), c(fmt(None, _NFS_COL_BW), _DIM)]
    if show_io:
        parts.append(c(fmt(None, _NFS_COL_IO), _DIM))
    return join_columns(parts, _NFS_COL_SEP)


# ---------------------------------------------------------------------------
# Panel renderers
# ---------------------------------------------------------------------------

def _render_health_panel(rows, total_ops, combined_latency, total_bw, deltas, width):
    meta_pct, read_pct, write_pct = workload_mix(rows)
    health_lbl, health_color      = nfs_health_label(total_ops, combined_latency)
    workload_type                  = classify_workload(rows)
    ops_delta, bw_delta, lat_deltas = cluster_delta_summary(deltas)

    print(box_top("NFS HEALTH", width))

    # Status line - badge + key metrics in one scannable row
    ops_s  = c(f"{total_ops:,.2f} ops/s" if total_ops else "- ops/s", _BWHITE)
    lat_s  = _c_latency(f"{combined_latency:.0f} {_MUS}" if combined_latency else f"- {_MUS}",
                         combined_latency)
    bw_s   = c(f"{total_bw:.3f} GB/s" if total_bw is not None else "- GB/s", _CYAN)
    status = (badge(health_lbl, health_color)
              + "   " + ops_s
              + "   " + lat_dot(combined_latency) + " " + lat_s
              + "   " + delta_arrow(total_bw) + " " + bw_s)
    print(box_row(status, width))

    # Workload classification
    print(box_row(c("Workload  ", _DIM) + c(workload_type, _YELLOW), width))

    # Workload mix bars
    print(box_row(c(f"{'Metadata':<10}", _DIM) + workload_bar(meta_pct,  22, _CYAN),    width))
    print(box_row(c(f"{'Read':<10}",     _DIM) + workload_bar(read_pct,  22, _BGREEN),  width))
    print(box_row(c(f"{'Write':<10}",    _DIM) + workload_bar(write_pct, 22, _BYELLOW), width))

    # Refresh delta (only when prev data exists)
    if deltas:
        parts = []
        if ops_delta is not None:
            parts.append(delta_arrow(ops_delta)
                         + " " + _c_delta_positive(f"{fmt_delta(ops_delta, 2)} ops/s", ops_delta))
        if bw_delta is not None:
            parts.append(delta_arrow(bw_delta)
                         + " " + _c_delta_positive(f"BW {fmt_delta(bw_delta, 3)} GB/s", bw_delta))
        if lat_deltas:
            worst = max(lat_deltas, key=lambda x: abs(x[1]))
            parts.append(delta_arrow_lat(worst[1])
                         + " " + _c_delta_latency(
                             f"Lat {fmt_delta(worst[1], 1)} {_MUS} [{worst[0]}]", worst[1]))
        if parts:
            print(box_row(c("Δ  ", _DIM) + "   ".join(parts), width))

    print(box_bottom(width))


def _render_insights_panel(rows, deltas, width):
    active_rows = [r for r in rows if (as_float(r["ops_sec"]) or 0) > 0]

    print(box_top("PERFORMANCE INSIGHTS", width))

    # Top contributor
    top_op = max(active_rows, key=lambda r: as_float(r["ops_sec"]) or 0, default=None)
    if top_op:
        pct_v = as_float(top_op["pct"]) or 0
        row   = (c("Top Contributor  ", _DIM)
                 + c(top_op["label"], _BWHITE)
                 + c(f"  {pct_v:.1f}% of ops", _GREEN))
        print(box_row(row, width))

    # Highest active latency
    active_with_lat = [r for r in active_rows if as_float(r["avg_us"]) is not None]
    if active_with_lat:
        hi  = max(active_with_lat, key=lambda r: as_float(r["avg_us"]) or 0)
        us  = as_float(hi["avg_us"])
        row = (c("Highest Latency  ", _DIM)
               + _c_latency(hi["label"], us)
               + "   " + lat_dot(us) + " " + _c_latency(f"{us:.0f} {_MUS}", us))
        print(box_row(row, width))

    # Largest data consumer
    io_rows = [r for r in rows if r["label"] in IO_LABELS
               and as_float(r.get("bw_gbs")) is not None]
    if io_rows:
        top_bw = max(io_rows, key=lambda r: as_float(r.get("bw_gbs")) or 0)
        bw_v   = as_float(top_bw["bw_gbs"])
        io_sz  = fmt_size(top_bw.get("avg_io_bytes")).strip()
        row    = (c("Data Consumer    ", _DIM)
                  + _c_bw(top_bw["label"], bw_v, top_bw["label"])
                  + c(f"  {bw_v:.3f} GB/s", _CYAN)
                  + (c(f"  avg I/O {io_sz}", _DIM) if io_sz else ""))
        print(box_row(row, width))

    # Top delta mover
    if deltas:
        top_d = max(deltas.items(), key=lambda kv: abs(kv[1].get("ops", 0)), default=None)
        if top_d and "ops" in top_d[1] and abs(top_d[1]["ops"]) > 0.1:
            lbl_d, d = top_d
            d_s = fmt_delta(d["ops"], 2)
            row = (c("Top Δ            ", _DIM)
                   + c(lbl_d, _BWHITE)
                   + "   " + delta_arrow(d["ops"])
                   + " " + _c_delta_positive(f"{d_s}/s", d["ops"]))
            if "lat" in d:
                row += ("   " + delta_arrow_lat(d["lat"])
                        + " " + _c_delta_latency(f"Lat {fmt_delta(d['lat'], 1)} {_MUS}", d["lat"]))
            print(box_row(row, width))

    # Observation
    wt  = classify_workload(rows)
    print(box_row(c("Observation      ", _DIM) + c(wt, _YELLOW), width))
    print(box_bottom(width))


def _render_data_panel(rows, deltas, width):
    data_rows = sorted([r for r in rows if r["label"] in IO_LABELS], key=lambda r: r["label"])
    inner     = width - 4
    show_run, show_bw, show_io = _col_levels(inner)

    print(box_top("DATA I/O", width))
    print(box_row(_table_header(show_run, show_bw, show_io), width))
    print(box_sep(width))

    for r in data_rows:
        print(box_row(_rpc_row_content(r, show_run, show_bw, show_io), width))

    print(box_sep(width))

    # Subtotals
    data_ops  = sum(as_float(r["ops_sec"]) or 0 for r in data_rows)
    data_lat  = compute_combined_avg_latency(data_rows)
    data_bw   = compute_total_throughput_gbs(data_rows)
    total_ops = sum(as_float(r["ops_sec"]) or 0 for r in rows)
    data_pct  = (data_ops / total_ops * 100) if total_ops > 0 else None

    print(box_row(
        _subtotal_row_content("DATA TOTAL", data_ops, data_pct, data_lat,
                               data_bw, show_run, show_bw, show_io),
        width,
    ))

    # Delta
    if deltas:
        io_ops_d = sum(deltas.get(lbl, {}).get("ops", 0) or 0 for lbl in IO_LABELS)
        io_bw_d  = sum(
            deltas.get(lbl, {}).get("bw", 0) or 0
            for lbl in IO_LABELS if "bw" in deltas.get(lbl, {})
        )
        parts = []
        if abs(io_ops_d) > 0.01:
            parts.append(delta_arrow(io_ops_d)
                         + " " + _c_delta_positive(f"{fmt_delta(io_ops_d, 2)} ops/s", io_ops_d))
        if abs(io_bw_d) > 0.0001:
            parts.append(delta_arrow(io_bw_d)
                         + " " + _c_delta_positive(f"BW {fmt_delta(io_bw_d, 3)} GB/s", io_bw_d))
        if parts:
            print(box_row(c("Δ  ", _DIM) + "   ".join(parts), width))

    print(box_bottom(width))


def _render_metadata_panel(rows, deltas, width):
    all_meta = sorted_rows([r for r in rows if r["label"] in META_LABELS])
    active   = [r for r in all_meta if (as_float(r["ops_sec"]) or 0) > 0]
    idle_n   = len(all_meta) - len(active)
    inner    = width - 4
    show_run = inner >= 80   # metadata never shows BW/IO

    print(box_top("METADATA", width))
    print(box_row(_meta_table_header(show_run), width))
    print(box_sep(width))

    if active:
        for r in active:
            print(box_row(_rpc_row_content(r, show_run, False, False), width))
    else:
        print(box_row(c("No active metadata operations", _DIM), width))

    if idle_n > 0:
        noun = f"{idle_n} idle RPC{'s' if idle_n > 1 else ''} hidden"
        print(box_row(c(noun, _DIM), width))

    print(box_sep(width))

    # Subtotals
    meta_ops  = sum(as_float(r["ops_sec"]) or 0 for r in all_meta)
    meta_lat  = compute_combined_avg_latency(all_meta)
    total_ops = sum(as_float(r["ops_sec"]) or 0 for r in rows)
    meta_pct  = (meta_ops / total_ops * 100) if total_ops > 0 else None

    print(box_row(
        _subtotal_row_content("META TOTAL", meta_ops, meta_pct, meta_lat,
                               None, show_run, False, False),
        width,
    ))

    if deltas:
        meta_ops_d = sum(deltas.get(lbl, {}).get("ops", 0) or 0 for lbl in META_LABELS)
        if abs(meta_ops_d) > 0.01:
            print(box_row(
                c("Δ  ", _DIM)
                + delta_arrow(meta_ops_d)
                + " " + _c_delta_positive(f"{fmt_delta(meta_ops_d, 2)} ops/s", meta_ops_d),
                width,
            ))

    print(box_bottom(width))


def _render_drill_panel(width):
    if DRILL_STATUS:
        print(box_top("DRILL-DOWN", width))
        print(box_row(c(DRILL_STATUS, _YELLOW), width))
        print(box_bottom(width))
        return

    if DRILL_ERROR and VIEW_UNAVAILABLE_MARKER in DRILL_ERROR:
        # Capability notice, not an error: NFSv3 telemetry is healthy; this
        # cluster simply has no valid per-view attribution source (D-016).
        print(box_top("VIEW DRILL-DOWN", width))
        print(box_row(c(DRILL_ERROR, _YELLOW), width))
        print(box_row(c(VIEW_UNAVAILABLE_DETAIL, _DIM), width))
        print(box_row(c("Press x to return to cluster view", _DIM), width))
        print(box_bottom(width))
        return

    if DRILL_ERROR:
        mode_t = DRILL_MODE.upper() + " DRILL-DOWN" if DRILL_MODE else "DRILL-DOWN"
        print(box_top(mode_t, width))
        print(box_row(c(f"Error: {DRILL_ERROR}", _BRED), width))
        print(box_row(c("Press x to return to cluster view", _DIM), width))
        print(box_bottom(width))
        return

    mode_label = DRILL_MODE.upper() if DRILL_MODE else "?"
    print(box_top(f"{mode_label} DRILL-DOWN", width))

    if not LAST_DRILL_ROWS:
        print(box_row(c("Waiting for data…", _DIM), width))
        print(box_bottom(width))
        return

    hdr = join_columns([
        c(pad_display("Name", _NFS_DRILL_NAME, "<"), _BOLD),
        c(pad_display("Ops/s", _NFS_DRILL_OPS, ">"), _BOLD),
        c(pad_display(f"Avg {_MUS}", _NFS_DRILL_LAT, ">"), _BOLD),
        c(pad_display("GB/s", _NFS_DRILL_BW, ">"), _BOLD),
        c(pad_display("Top RPC", _NFS_DRILL_RPC, ">"), _BOLD),
        c(pad_display("Top%", _NFS_DRILL_TOP_PCT, ">"), _BOLD),
    ], _NFS_DRILL_SEP)
    print(box_row(hdr, width))
    print(box_sep(width))

    for dr in LAST_DRILL_ROWS:
        pct_val = dr.get("top_rpc_pct")
        pct_str = pad_display(f"{(pct_val or 0):.1f}%", _NFS_DRILL_TOP_PCT, ">")
        row = join_columns([
            pad_display(dr["name"], _NFS_DRILL_NAME, "<"),
            _c_ops(fmt(dr["total_ops"], _NFS_DRILL_OPS, 2), dr["total_ops"]),
            _c_latency(fmt(dr["latency_us"], _NFS_DRILL_LAT, 2), dr["latency_us"]),
            c(fmt(dr["bw_gbs"], _NFS_DRILL_BW, 3), _CYAN) if dr["bw_gbs"]
            else c(fmt(None, _NFS_DRILL_BW), _DIM),
            c(pad_display(dr["top_rpc"], _NFS_DRILL_RPC, ">"), _BWHITE),
            _c_pct(pct_str, pct_val),
        ], _NFS_DRILL_SEP)
        print(box_row(row, width))

    print(box_sep(width))
    coverage = _drill_coverage_note()
    if coverage:
        print(box_row(coverage, width))
    print(box_row(c("Press x to return to cluster view", _DIM), width))
    print(box_bottom(width))


def _drill_coverage_note():
    """State how much cluster activity these rows actually account for.

    ViewMetrics/TenantMetrics attribute only the operations VMS can tie to a
    view or tenant, so the drill rows legitimately sum to less than the
    cluster total (they are also only the top ``_MAX_DRILL_OBJECTS``). Saying
    so beats letting the gap read as a bug or, worse, scaling the numbers up
    to match.
    """
    if not LAST_DRILL_ROWS or DRILL_MODE not in ("view", "tenant"):
        return ""
    shown = sum(as_float(r.get("total_ops")) or 0.0 for r in LAST_DRILL_ROWS)
    cluster = sum(as_float(r["ops_sec"]) or 0.0 for r in LAST_ROWS)
    if cluster <= 0:
        return ""
    fraction = vast_drill.coverage_fraction(shown, cluster)
    if fraction is None:
        return (
            c(f"Top {len(LAST_DRILL_ROWS)} {DRILL_MODE}s shown; ", _DIM)
            + c(f"{DRILL_MODE} counters are not directly comparable to the "
                f"cluster totals above", _DIM)
        )
    return (
        c(f"Top {len(LAST_DRILL_ROWS)} {DRILL_MODE}s account for ", _DIM)
        + c(f"{shown:,.2f} ops/s ({fraction * 100.0:.1f}%)", _BWHITE)
        + c(f" of {cluster:,.2f} cluster ops/s - VMS attributes only "
            f"{DRILL_MODE}-scoped operations to {DRILL_MODE}s", _DIM)
    )


# ---------------------------------------------------------------------------
# render_screen
# ---------------------------------------------------------------------------

def poll_tick():
    """One refresh poll: headline monitors plus the active drill, if any."""
    fetch_monitor_query()
    if DRILL_MODE:
        fetch_drill_query()


def manual_refresh():
    """Space-bar refresh: also bypass the drill query throttle."""
    fetch_monitor_query()
    if DRILL_MODE:
        fetch_drill_query(force=True)


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


# Canonical common controls first (FR-A contract in vast_drill), then the
# NFSv3-specific sort keys: [r] sort by RPC count, [w] sort by workload share.
# This replaced the original htop-style legend (`q`uit / [spc]refresh /
# x=cluster) so every engine reads the same way; the keys themselves are
# unchanged.
_NAV_CONTROLS = vast_drill.nav_controls(
    ("q", "o", "l", "c", "v", "t", "x", "space"),
    extra=(("r", "RPC"), ("w", "Work")),
)


def _footer_keys(width=None):
    """The navigation key legend (owned by the common render path).

    Wrapped to the terminal width rather than truncated: right-truncation
    silently dropped controls (a real laptop terminal showed only q/o/l while
    c/v/t/x still worked), and a control that works must stay discoverable.
    """
    inner = None if width is None else max(width - 2, 12)
    return "\n".join(
        c("  ", _DIM) + line
        for line in vast_drill.nav_legend_lines(_NAV_CONTROLS, inner)
    )


def _render_frame():
    rows            = LAST_ROWS
    selected_sample = LAST_SAMPLE
    width            = min(shutil.get_terminal_size((184, 40)).columns, 184)
    mode_tag         = "avg " + API_TIME_FRAME if SAMPLE_AVERAGE_MODE else "latest"

    # ── Title bar (plain - intentionally outside any box, htop style) ────────
    drill_tag = c(f"  {_V} {DRILL_MODE.upper()} DRILL {_V}", _BYELLOW) if DRILL_MODE else ""
    csv_tag   = c(f"  csv:{CSV_FILE}", _DIM) if CSV_FILE else ""

    title_line = (
        c("  VAST NFSv3", _BCYAN) + c(" opstat", _BWHITE) + c(f" v{VERSION}", _DIM)
        + "   VMS " + c(f"{VMS}:{PORT}", _BWHITE)
        + "   cluster " + c(CLUSTER_NAME or "?", _BWHITE)
        + c(f"   refresh {REFRESH_SECONDS}s", _DIM)
        + drill_tag + csv_tag
    )
    os_label = format_os_release(CLUSTER_OS)
    info_line = c(
        f"  mode:{mode_tag}  frame:{API_TIME_FRAME}"
        f"  sort:{sort_label()}  sample:{selected_sample}"
        + (f"  {os_label}" if os_label else ""),
        _DIM,
    )
    # Header lines sit outside the box borders, so they must be truncated
    # explicitly: an untruncated line wraps on a narrow terminal, shifting
    # every row below it and corrupting the frame. A realistic cluster name
    # plus FQDN and OS build ran these to 99 columns - past a standard
    # 80-column terminal, not just an unusually narrow one.
    title_line = truncate_display(title_line, width)
    info_line = truncate_display(info_line, width)

    # ── Startup / waiting frame: keep the header and footer (the common path
    #    owns the footer - never a bare early return). ─────────────────────────
    if STARTUP_STATUS or not rows:
        print(title_line)
        print(info_line)
        print(c(_H * width, _DIM))
        if STARTUP_STATUS:
            print(truncate_display(c("  " + STARTUP_STATUS, _BYELLOW), width))
        else:
            print(truncate_display(
                c(f"  Waiting for data…  VMS={VMS}:{PORT}"
                  f"  cluster={CLUSTER_NAME or '?'}", _DIM), width))
        print(c(_H * width, _DIM))
        print(_footer_keys(width), flush=True)
        return

    total_ops        = sum(as_float(r["ops_sec"]) or 0 for r in rows)
    combined_latency = compute_combined_avg_latency(rows)
    total_bw         = compute_total_throughput_gbs(rows)
    deltas           = compute_deltas(rows, PREV_ROWS)
    print(title_line)
    print(info_line)
    print(c(_H * width, _DIM))

    # ── Panels ───────────────────────────────────────────────────────────────
    if DRILL_MODE or DRILL_ERROR or DRILL_STATUS:
        _render_drill_panel(width)
    else:
        _render_health_panel(rows, total_ops, combined_latency, total_bw, deltas, width)
        _render_insights_panel(rows, deltas, width)
        _render_data_panel(rows, deltas, width)
        _render_metadata_panel(rows, deltas, width)

    # ── Grand total footer ───────────────────────────────────────────────────
    lat_s   = _c_latency(
        f"{combined_latency:.0f} {_MUS}" if combined_latency else f"- {_MUS}",
        combined_latency,
    )
    bw_s    = c(f"{total_bw:.3f} GB/s" if total_bw is not None else "- GB/s", _CYAN)
    foot    = (
        c("  COMBINED  ", _BOLD)
        + c(f"{total_ops:,.2f} ops/s", _BWHITE)
        + c("   100%   ", _BOLD if total_ops else _DIM)
        + lat_dot(combined_latency) + " " + lat_s
        + "   " + delta_arrow(total_bw) + " " + bw_s
    )
    print(c(_H * width, _DIM))
    # The grand-total line is drawn outside the box borders too, so it needs
    # the same explicit truncation as the header lines.
    print(truncate_display(foot, width))
    print(_footer_keys(width), flush=True)


# ---------------------------------------------------------------------------
# Metric discovery
# ---------------------------------------------------------------------------

def discover_metrics():
    """Query the VMS for available NFS metrics and drill-down objects, then exit."""
    print(f"\n=== VAST NFSv3 Metrics Discovery ===")
    print(f"VMS: {VMS}:{PORT}  (connecting...)\n")

    global CLUSTER_ID, CLUSTER_NAME
    try:
        CLUSTER_ID, CLUSTER_NAME = get_current_cluster()
        print(f"Cluster: {CLUSTER_NAME}  (id={CLUSTER_ID})\n")
    except RuntimeError as e:
        print(f"ERROR: Could not connect to VMS: {e}")
        sys.exit(1)

    # ── Object types ─────────────────────────────────────────────────────────
    print("[ Object Types ]")
    object_endpoints = {
        "clusters": "/clusters/",
        "cnodes":   "/cnodes/",
        "views":    "/views/",
        "vips":     "/vips/",
        "tenants":  "/tenants/",
    }
    drill_available = {}
    for name, endpoint in object_endpoints.items():
        try:
            data    = api_request("GET", endpoint)
            objects = normalize_list_response(data)
            if objects:
                samples = []
                for o in objects[:3]:
                    n = (_obj_name(o, ("name", "path", "hostname", "mgmt_ip"))
                         or str(o.get("id", "?")))
                    samples.append(f"{n} (id={o.get('id','?')})")
                print(f"  {name:<12}: {len(objects)} object(s)  [{', '.join(samples)}{'...' if len(objects)>3 else ''}]")
                drill_available[name] = len(objects)
            else:
                print(f"  {name:<12}: 0 objects")
        except RuntimeError as e:
            print(f"  {name:<12}: not available ({e})")

    # ── NFS metrics (via a temporary monitor) ────────────────────────────────
    print("\n[ NFS RPC Metrics in Use ]")
    rpc_props = build_rpc_prop_list()
    bw_props  = build_bw_prop_list()
    try:
        tmp_id = create_monitor("discover_tmp", rpc_props)
        try:
            result    = api_request("GET", f"/monitors/{tmp_id}/query/")
            prop_list = result.get("prop_list", []) if isinstance(result, dict) else []
            reported  = [p for p in prop_list if p and not p.startswith("timestamp")]
            for prop in sorted(reported):
                print(f"  {prop}")
            if not reported:
                print("  (monitor returned no prop_list - API may require different query parameters)")
        finally:
            delete_monitor(tmp_id)
    except RuntimeError as e:
        print(f"  Could not create temporary monitor: {e}")
        print(f"  Metrics configured in script ({len(rpc_props)} RPC + {len(bw_props)} bandwidth):")
        for prop in rpc_props[:10]:
            print(f"    {prop}")
        if len(rpc_props) > 10:
            print(f"    ... ({len(rpc_props) - 10} more)")

    print("\n[ Bandwidth Metrics ]")
    for fqn in bw_props:
        print(f"  {fqn}")

    # ── Drill-down summary ───────────────────────────────────────────────────
    print("\n[ Drill-Down Availability ]")
    key_map = {"cnodes": "c (cNode)", "views": "v (View)", "tenants": "t (Tenant)"}
    for name, key_label in key_map.items():
        count = drill_available.get(name)
        if count:
            print(f"  {key_label:<16}: {count} object(s) available -> drill-down supported")
        else:
            print(f"  {key_label:<16}: not available (no objects or API 404)")

    print("\nUse c/v/t keys during normal operation to enter drill-down mode.")
    print("Use --no-color to suppress ANSI output when piping.\n")


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def _set_startup_status(text):
    global STARTUP_STATUS
    STARTUP_STATUS = text


def initialize():
    """Blocking startup behind per-phase status frames.

    Auth, cluster resolution, monitor creation and the first sample fetch take
    tens of seconds against a real VMS. Each phase paints its status frame
    before it blocks (via vast_drill.with_startup_status), so the terminal shows
    what it is waiting on; the status clears when the first real frame renders,
    and on error.
    """
    def _connect():
        global CLUSTER_ID, CLUSTER_NAME
        CLUSTER_ID, CLUSTER_NAME = get_current_cluster()
        _capture_cluster_os()

    def _prepare():
        create_headline_monitors()

    def _gather():
        fetch_monitor_query()

    vast_drill.with_startup_status(_set_startup_status, render_screen, [
        (f"Connecting to {VMS}:{PORT}, please stand by...", _connect),
        (lambda: f"Preparing metrics on {CLUSTER_NAME or VMS}, please stand by...", _prepare),
        ("Gathering initial metrics, please stand by...", _gather),
    ])


def _dispatch_key(key):
    """Handle one navigation key (see vast_drill.dispatch_queued_keys).

    Returns "rendered" when the action painted already, "refresh" when a
    repaint is owed after the queued batch drains, None for an unbound key.
    """
    # Keys arrive case-preserved from dispatch_queued_keys (text prompts
    # need raw characters); commands are case-insensitive, so fold here.
    key = key.lower()
    global SORT_MODE
    if key == " ":
        vast_common.guarded_poll(manual_refresh, render_screen)
        return "rendered"
    if key in ("r", "o", "l", "w"):
        SORT_MODE = {"r": "rpc", "o": "ops", "l": "latency", "w": "workload"}[key]
        return "refresh"
    if key in ("c", "v", "t"):
        switch_drill_mode({"c": "cnode", "v": "view", "t": "tenant"}[key])
        return "refresh"
    if key == "x":
        exit_drill_mode()
        return "refresh"
    return None


def main():
    global RPC_MONITOR_ID, BW_MONITOR_ID, CLUSTER_ID, CLUSTER_NAME, SORT_MODE, DRILL_ERROR

    vast_common.install_signal_handlers(signal_handler)
    vast_common.register_atexit(cleanup)

    if ARGS.discover_metrics:
        discover_metrics()
        return 0

    ensure_csv_file()
    setup_keyboard()

    initialize()
    render_screen()

    next_refresh_time = time.time() + REFRESH_SECONDS

    while True:
        now   = time.time()
        chars = check_keypress()

        if chars:
            if "\x03" in chars or "q" in chars.lower():
                break
            # Every queued key, in arrival order - see vast_drill.
            if vast_drill.dispatch_queued_keys(chars, _dispatch_key, render_screen):
                next_refresh_time = time.time() + REFRESH_SECONDS
            continue

        if now >= next_refresh_time:
            vast_common.guarded_poll(poll_tick, render_screen)
            next_refresh_time = time.time() + REFRESH_SECONDS
            continue

        vast_common.wait_for_input(next_refresh_time - now)

    return 0


def run(args):
    """Entry point for NFS v3 statistics collection."""
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
