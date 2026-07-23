#!/usr/bin/env python3
################################################################################
# Script:      smb.py
#
# Descr:       SMB performance statistics for opstat. SMBCommon aggregate
#              panels (Phase 0 var203). Drill-down in Phase 4.
#
# Version:     0.1.2
# Author:      KMac
#
# Usage:
#   ./opstat --smb --vms <VMS_IP>
#   ./opstat --smb --discover-metrics --vms <VMS_IP>
#
# Controls (planned):
#   Space  - Refresh immediately
#   c      - cNode drill-down
#   v      - View / share drill-down
#   t      - Tenant drill-down
#   x      - Exit drill-down
#   q      - Quit
################################################################################

import base64
import csv
import getpass
import io
import json
import os
import re
import shutil
import ssl
import sys
import time
import urllib.parse
from datetime import datetime

import ipaddress

import openmetrics
import vast_api_log
import vast_common
from tui_layout import (
    display_width, format_fixed_number, format_scaled_metric, join_columns,
    pad_display, truncate_display, c, set_color, set_unicode, glyph_set,
    as_float, raw_bw_to_mb_sec, raw_bw_to_gb_sec, format_throughput_mbs,
    format_latency_us, format_iops, format_block_size, format_os_release,
    _RST, _BOLD, _DIM, _GREEN, _YELLOW, _CYAN,
    _BRED, _BGREEN, _BYELLOW, _BCYAN, _BWHITE,
)

VERSION = "0.1.2"

DEFAULT_PORT = 443
DEFAULT_USER = "admin"
DEFAULT_REFRESH_SECONDS = 5
DEFAULT_API_TIME_FRAME = "10m"

_PROTO_SMB = "ProtoMetrics,proto_name=SMB"
_PROTO_SMB_COMMON = "ProtoMetrics,proto_name=SMBCommon"

# Phase 0 var203: SMBCommon is the only live telemetry class; SmbMetrics per-command
# returns HTTP 400 property_error. METRICS_SOURCE labels the active binding.
METRICS_SOURCE = "SMBCommon"
SMB_PER_COMMAND_EXPORTED = False

# NFSv3+SMB interop counters - SESSION panel + workload classifier (var203 exportable).
_INTEROP_METRICS = (
    "NfsMetrics,nfs3_smb_interop_ops",
    "NfsMetrics,nfs3_smb_interop_io_ops",
    "NfsMetrics,nfs3_smb_interop_triggered_lease_breaks",
    "NfsMetrics,nfs3_smb_interop_lease_break_retries",
    "NfsMetrics,nfs3_smb_interop_handles_closed",
    "NfsMetrics,nfs3_smb_interop_nvhash_updates",
    "NfsMetrics,nfs3_smb_interop_nvhash_add_or_updates",
    "NfsMetrics,nfs3_smb_interop_ram_cache_scrubbed_entries",
)
_INTEROP_LABELS = {
    "nfs3_smb_interop_triggered_lease_breaks": "LEASE BREAKS",
    "nfs3_smb_interop_lease_break_retries": "LEASE RETRIES",
    "nfs3_smb_interop_handles_closed": "HANDLES CLOSED",
    "nfs3_smb_interop_ops": "INTEROP OPS",
    "nfs3_smb_interop_io_ops": "INTEROP IO",
    "nfs3_smb_interop_nvhash_updates": "NVHASH UPD",
    "nfs3_smb_interop_nvhash_add_or_updates": "NVHASH ADD/UPD",
    "nfs3_smb_interop_ram_cache_scrubbed_entries": "CACHE SCRUB",
}

SMB_CMD_CANDIDATES = (
    "read", "write", "create", "close", "query_directory", "query_info",
    "set_info", "ioctl", "lock", "change_notify", "session_setup",
    "tree_connect", "tree_disconnect", "negotiate", "logoff", "echo",
    "cancel", "oplock_break", "flush",
)

# Primary SMB2 opcodes for workflow panel (order = troubleshooting priority).
SMB2_OPCODES = (
    ("SMB2_READ", "data", "read"),
    ("SMB2_WRITE", "data", "write"),
    ("SMB2_CREATE", "metadata", "create"),
    ("SMB2_CLOSE", "metadata", "close"),
    ("SMB2_FLUSH", "metadata", "flush"),
    ("SMB2_QUERY_INFO", "metadata", "query_info"),
    ("SMB2_QUERY_DIRECTORY", "metadata", "query_directory"),
    ("SMB2_SET_INFO", "metadata", "set_info"),
    ("SMB2_LOCK", "lock", "lock"),
    ("SMB2_NEGOTIATE", "session", "negotiate"),
    ("SMB2_SESSION_SETUP", "session", "session_setup"),
    ("SMB2_LOGOFF", "session", "logoff"),
    ("SMB2_TREE_CONNECT", "session", "tree_connect"),
    ("SMB2_TREE_DISCONNECT", "session", "tree_disconnect"),
    ("SMB2_CHANGE_NOTIFY", "notify", "change_notify"),
)

_OPCODE_COL = {"label": 22, "iops": 11, "throughput": 11, "size": 9, "latency": 11, "source": 10}

# Opcode workflow panel tiers - authoritative VMS counters vs system-inferred context.
_OPCODE_SECTION_AUTHORITATIVE = "Based on Authoritative Metrics"
_OPCODE_SECTION_DERIVED = "Inferred from System Context"
_AUTHORITATIVE_SOURCES = frozenset({"MEASURED", "SMBMETRICS", "AGGREGATE"})
_DERIVED_SOURCES = frozenset({"PROXY", "HANDLES", "SESSIONS", "INFERRED", "INTEROP"})

OBJECT_ENDPOINTS = (
    "/cnodes/", "/views/", "/tenants/", "/vips/", "/hosts/",
    "/monitoredhosts/", "/monitoredusers/", "/monitoredviews/",
)

# Swagger-documented SMB/session probes (Phase 0 revised).
SWAGGER_PROBE_CALLS = (
    ("/clusters/list_smb_client_connections/", {"client_ip": "0.0.0.0"}),
    ("/openfilehandles/", {"protocol": "SMB", "page_size": "1"}),
    ("/monitors/topn/", {
        "object_type": "view",
        "prop_list": "ViewMetrics,read_iops__rate",
        "time_frame": "10m",
        "limit": "3",
    }),
)

PROTO_PROBE_PROPS = [
    f"{_PROTO_SMB},iops",
    f"{_PROTO_SMB},bw",
    f"{_PROTO_SMB},latency",
    f"{_PROTO_SMB_COMMON},rd_iops",
    f"{_PROTO_SMB_COMMON},wr_iops",
    f"{_PROTO_SMB_COMMON},rd_bw",
    f"{_PROTO_SMB_COMMON},wr_bw",
    f"{_PROTO_SMB_COMMON},md_iops",
    f"{_PROTO_SMB_COMMON},rd_md_iops",
    f"{_PROTO_SMB_COMMON},wr_md_iops",
]

_DRILL_CFG = {
    "cnode": {
        "label": "CNODE",
        "object_type": "cnode",
        "endpoint": "/cnodes/",
        "name_fields": ("name", "hostname", "mgmt_ip"),
        "no_aggregation": False,
    },
    "view": {
        "label": "VIEW",
        "object_type": "view",
        "endpoint": "/views/",
        "name_fields": ("path", "title", "name"),
        "no_aggregation": True,
    },
    "tenant": {
        "label": "TENANT",
        "object_type": "tenant",
        "endpoint": "/tenants/",
        "name_fields": ("name",),
        "no_aggregation": False,
    },
}

# View/tenant drill scopes use ViewMetrics/TenantMetrics (SMBCommon is cluster/cnode).
_VIEW_READ_IOPS = "ViewMetrics,read_iops__rate"
_VIEW_WRITE_IOPS = "ViewMetrics,write_iops__rate"
_VIEW_READ_MD = "ViewMetrics,read_md_iops__rate"
_VIEW_WRITE_MD = "ViewMetrics,write_md_iops__rate"
_VIEW_READ_LAT = "ViewMetrics,read_latency__avg"
_VIEW_WRITE_LAT = "ViewMetrics,write_latency__avg"
_VIEW_READ_BW = "ViewMetrics,read_bw__rate"
_VIEW_WRITE_BW = "ViewMetrics,write_bw__rate"
_VIEW_READ_MD_LAT = "ViewMetrics,read_md_latency__avg"
_VIEW_WRITE_MD_LAT = "ViewMetrics,write_md_latency__avg"
_VIEW_QOS_FAILURES = "ViewMetrics,qos_failures"
_VIEW_QOS_WAIT = "ViewMetrics,qos_wait_for_budget_time__rate"

_TENANT_READ_IOPS = "TenantMetrics,read_iops__sum"
_TENANT_WRITE_IOPS = "TenantMetrics,write_iops__sum"
_TENANT_READ_MD = "TenantMetrics,read_md_iops__sum"
_TENANT_WRITE_MD = "TenantMetrics,write_md_iops__sum"
_TENANT_READ_BW = "TenantMetrics,read_bw__sum"
_TENANT_WRITE_BW = "TenantMetrics,write_bw__sum"
_TENANT_READ_LAT = "TenantMetrics,read_latency__sum"
_TENANT_WRITE_LAT = "TenantMetrics,write_latency__sum"
_TENANT_READ_CNT = "TenantMetrics,read_iops__num_samples"
_TENANT_WRITE_CNT = "TenantMetrics,write_iops__num_samples"
_TENANT_READ_MD_LAT_SUM = "TenantMetrics,read_md_latency__sum"
_TENANT_WRITE_MD_LAT_SUM = "TenantMetrics,write_md_latency__sum"
_TENANT_READ_MD_LAT_CNT = "TenantMetrics,read_md_latency__num_samples"
_TENANT_WRITE_MD_LAT_CNT = "TenantMetrics,write_md_latency__num_samples"

_MAX_DRILL_OBJECTS = 8
_DRILL_PROBE_LIMIT = 32
_DRILL_COL = {"name": 24, "ops": 12, "lat": 10, "bw": 9, "top": 12, "pct": 6}

HEALTH_PANEL_TITLE = "SMB HEALTH & WORKLOAD"
INSIGHTS_PANEL_TITLE = "PERFORMANCE INSIGHTS"
DATA_PANEL_TITLE = "DATA PATH"
METADATA_PANEL_TITLE = "METADATA & NAMESPACE"
SESSION_PANEL_TITLE = "SESSION & LOCKING"
OPCODE_PANEL_TITLE = "SMB2 OPCODE WORKFLOW"

DATA_OPS = [("read", "READ"), ("write", "WRITE")]
METADATA_OPS = [
    ("md_total", "METADATA"),
    ("rd_md", "RD METADATA"),
    ("wr_md", "WR METADATA"),
]

_COL_SEP = "  "
_COL = {"label": 14, "iops": 12, "throughput": 12, "size": 10, "latency": 12}

_ANSI_RE = re.compile(r"\033\[[^m]*m")
_UTF8 = (sys.stdout.encoding or "ascii").lower().startswith("utf")
_G = glyph_set(_UTF8)
_H, _V = _G["H"], _G["V"]
_TL, _TR, _BL, _BR, _LT, _RT = _G["TL"], _G["TR"], _G["BL"], _G["BR"], _G["LT"], _G["RT"]
_MUS = _G["MUS"]
_DOT, _BLK, _SHD = _G["DOT"], _G["BLK"], _G["SHD"]
_ARR_UP, _ARR_DN, _ARR_EQ = _G["ARR_UP"], _G["ARR_DN"], _G["ARR_EQ"]

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
HEADLINE_MONITOR_ID = None
SMB_COMMAND_MONITOR_ID = None
CLIENT_SCOPED = False
CLIENT_IPS = []
LAST_ROWS = {}
PREV_ROWS = {}
LAST_SAMPLE = "-"
DRILL_MODE = None
DRILL_OBJECTS = []
DRILL_MONITORS = []
LAST_DRILL_ROWS = []
DRILL_ERROR = None
DRILL_STATUS = None
LAST_TOPN = None
LAST_SESSION_CONTEXT = None
_LAST_AUX_FETCH_AT = 0.0
CSV_FILE = None
RUN_STARTED_AT = None

CSV_HEADER = [
    "local_time", "runtime", "vms", "port", "cluster", "cluster_id",
    "headline_monitor_id", "sample_mode", "api_time_frame", "selected_sample",
    "metrics_source", "panel", "label", "ops_per_sec", "pct_workload",
    "avg_latency_us", "throughput_mb_sec", "avg_io_bytes",
]

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def init_config(args):
    """Initialize module globals from parsed CLI arguments."""
    global ARGS, VMS, PORT, USER, PASSWORD, REFRESH_SECONDS, API_TIME_FRAME
    global SAMPLE_AVERAGE_MODE, BASE_URL, AUTH, HEADERS, CSV_FILE, RUN_STARTED_AT

    ARGS = args
    VMS = args.vms
    PORT = args.port
    USER = args.user
    password = args.password or os.environ.get("VAST_PASSWORD")
    if not password:
        password = getpass.getpass(f"Password for {USER}@{VMS}: ")
    PASSWORD = password
    REFRESH_SECONDS = args.refresh
    SAMPLE_AVERAGE_MODE = bool(args.sample_average)
    API_TIME_FRAME = args.sample_average or DEFAULT_API_TIME_FRAME
    BASE_URL = f"https://{VMS}/api" if PORT == 443 else f"https://{VMS}:{PORT}/api"
    token = os.environ.get("VAST_TOKEN")
    if token:
        AUTH = None
        HEADERS = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    else:
        AUTH = base64.b64encode(f"{USER}:{PASSWORD}".encode()).decode()
        HEADERS = {"Authorization": f"Basic {AUTH}", "Content-Type": "application/json"}
    HEADERS["User-Agent"] = f"opstat/smb/{VERSION}"
    vast_common.configure_connection(BASE_URL, HEADERS, SSL_CTX)
    log_path = vast_api_log.configure(
        getattr(args, "log_api_calls", False), "smb", VMS, PORT,
    )
    if log_path:
        print(f"API call logging enabled: {log_path}", file=sys.stderr, flush=True)
    om_path = openmetrics.configure(
        getattr(args, "export_openmetrics", False),
        getattr(args, "openmetrics_file", None),
        "smb", VMS,
    )
    if om_path:
        print(f"OpenMetrics export enabled: {om_path}", file=sys.stderr, flush=True)
    global _COLOR
    _COLOR = sys.stdout.isatty() and not args.no_color
    set_color(_COLOR)
    set_unicode(_UTF8)
    CSV_FILE = getattr(args, "csv", None)
    RUN_STARTED_AT = datetime.now()
    configure_client_scope(args)


def configure_client_scope(args):
    """Parse --client/--clients; filters topn insights and session connection probes."""
    global CLIENT_SCOPED, CLIENT_IPS
    raw = getattr(args, "clients", None)
    if not raw:
        CLIENT_SCOPED = False
        CLIENT_IPS = []
        return
    cleaned, rejected = [], []
    for item in (s.strip() for s in raw.split(",") if s.strip()):
        try:
            ipaddress.ip_address(item)
            cleaned.append(item)
        except ValueError:
            if item and all(ch.isalnum() or ch in ".-_" for ch in item):
                cleaned.append(item)  # permit hostnames
            else:
                rejected.append(item)
    if rejected:
        print(
            f"WARNING: ignoring malformed --clients entries: {', '.join(rejected)}",
            file=sys.stderr,
        )
    CLIENT_IPS = cleaned
    CLIENT_SCOPED = bool(CLIENT_IPS)


def api_request(method, path, payload=None):
    """Issue an authenticated VMS REST request (see vast_common.request)."""
    return vast_common.request(method, path, payload)


def normalize_list_response(data):
    """Normalize VMS list endpoints to a plain list."""
    return vast_common.normalize_list_response(data)


def get_current_cluster():
    """Return (cluster_id, cluster_name) for the active cluster."""
    return vast_common.get_current_cluster(api_request)


def _capture_cluster_os():
    """Fetch the cluster VAST OS version once for the header (best-effort)."""
    global CLUSTER_OS
    CLUSTER_OS = vast_common.get_current_cluster_os(api_request)


def smb_metric_fqn(cmd, suffix):
    """Return SmbMetrics FQN for per-command latency export probes."""
    return f"SmbMetrics,smb_{cmd}_latency__{suffix}"


def smb_command_props():
    """Build candidate SmbMetrics property names for discovery probes."""
    props = []
    for cmd in SMB_CMD_CANDIDATES:
        props.extend([smb_metric_fqn(cmd, "rate"), smb_metric_fqn(cmd, "avg")])
    return props


def filter_smb_metrics(metrics):
    """Return metrics catalog entries that mention SMB."""
    hits = []
    for entry in metrics if isinstance(metrics, list) else []:
        text = json.dumps(entry) if isinstance(entry, dict) else str(entry)
        if re.search(r"smb|SMB", text):
            hits.append(entry)
    return hits


def probe_monitor(cluster_id, prop_list, label):
    """Create a temporary monitor, query it, delete it; return (status, detail)."""
    payload = {
        "name": f"adhoc_opstat_smb_discover_{label}_{int(time.time())}",
        "object_type": "cluster",
        "object_ids": [cluster_id],
        "time_frame": "10m",
        "prop_list": prop_list,
        "aggregation": "avg",
        "query_aggregation": "avg",
    }
    try:
        created = api_request("POST", "/monitors/", payload)
        monitor_id = created.get("id") if isinstance(created, dict) else None
        if not monitor_id:
            return "create_failed", str(created)[:200]
        result = api_request("GET", f"/monitors/{monitor_id}/query/")
        api_request("DELETE", f"/monitors/{monitor_id}/")
        rows = len(result.get("data", [])) if isinstance(result, dict) else 0
        props_preview = result.get("prop_list", [])[:6] if isinstance(result, dict) else []
        return "ok", f"{rows} rows, props={props_preview}..."
    except RuntimeError as e:
        return "error", str(e)[:200]


def _common_fqn(suffix):
    return f"{_PROTO_SMB_COMMON},{suffix}"


def _first_positive(*values):
    for value in values:
        parsed = as_float(value)
        if parsed is not None and parsed > 0:
            return parsed
    return None


def fmt_delta(value, precision=2):
    if value is None:
        return "-"
    sign = "+" if value > 0 else ""
    return f"{sign}{value:,.{precision}f}"


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


def badge(text, color_code):
    return c(f"[ {text} ]", color_code)


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
    filled = max(0, min(bar_width, round((pct or 0) / 100 * bar_width)))
    empty = bar_width - filled
    bar = c(_BLK * filled, color) + c(_SHD * empty, _DIM)
    return f"{bar}  {(pct or 0):4.1f}%"


def build_headline_monitor_props():
    """SMBCommon cluster monitor + interop/session counters (var203 confirmed)."""
    return [
        _common_fqn("iops"), _common_fqn("bw"),
        _common_fqn("rd_iops"), _common_fqn("wr_iops"),
        _common_fqn("rd_bw"), _common_fqn("wr_bw"),
        _common_fqn("md_iops"), _common_fqn("rd_md_iops"), _common_fqn("wr_md_iops"),
        _common_fqn("read_latency__avg"), _common_fqn("write_latency__avg"),
        _common_fqn("read_latency__rate"), _common_fqn("write_latency__rate"),
        _common_fqn("read_size__avg"), _common_fqn("write_size__avg"),
        _common_fqn("rd_latency"), _common_fqn("wr_latency"),
        _common_fqn("notify_counter"),
        *_INTEROP_METRICS,
    ]


def build_drill_prop_list(mode):
    """Scope-aware monitor props for SMB drill-down."""
    if mode == "view":
        return [
            _VIEW_READ_IOPS, _VIEW_WRITE_IOPS,
            _VIEW_READ_MD, _VIEW_WRITE_MD,
            _VIEW_READ_LAT, _VIEW_WRITE_LAT,
            _VIEW_READ_BW, _VIEW_WRITE_BW,
            _VIEW_READ_MD_LAT, _VIEW_WRITE_MD_LAT,
            _VIEW_QOS_FAILURES, _VIEW_QOS_WAIT,
        ]
    if mode == "tenant":
        return [
            _TENANT_READ_IOPS, _TENANT_WRITE_IOPS,
            _TENANT_READ_MD, _TENANT_WRITE_MD,
            _TENANT_READ_BW, _TENANT_WRITE_BW,
            _TENANT_READ_LAT, _TENANT_WRITE_LAT,
            _TENANT_READ_CNT, _TENANT_WRITE_CNT,
            _TENANT_READ_MD_LAT_SUM, _TENANT_WRITE_MD_LAT_SUM,
            _TENANT_READ_MD_LAT_CNT, _TENANT_WRITE_MD_LAT_CNT,
        ]
    return build_headline_monitor_props()


def build_drill_rank_prop_list(mode):
    """Minimal props for one-shot batch ranking of view/tenant candidates."""
    if mode == "view":
        return [_VIEW_READ_IOPS, _VIEW_WRITE_IOPS, _VIEW_READ_MD, _VIEW_WRITE_MD]
    if mode == "tenant":
        return [
            _TENANT_READ_IOPS, _TENANT_WRITE_IOPS,
            _TENANT_READ_MD, _TENANT_WRITE_MD,
        ]
    return build_drill_prop_list(mode)


def _is_batch_drill_mode(mode=None):
    mode = mode or DRILL_MODE
    return mode in ("view", "tenant")


def _normalize_object_id(value):
    """Coerce VMS object_id values for reliable batch-monitor slicing."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


def _interop_rates_from_result(result):
    """Derive interop counter rates from multi-sample monitor query."""
    rates = {}
    for fqn in _INTEROP_METRICS:
        short = fqn.split(",", 1)[-1]
        rate = _delta_rate_from_samples(result, fqn)
        if rate is not None and rate > 0:
            rates[short] = rate
    return rates


def _parse_topn_ip(title):
    """Extract client IP from topn title like '172.200.14.253 [default]'."""
    if not title:
        return ""
    return str(title).split()[0]


def _client_matches_scope(title, client_ips=None):
    """Return True when title IP is in scoped client list (or scope is off)."""
    ips = client_ips if client_ips is not None else CLIENT_IPS
    if not CLIENT_SCOPED or not ips:
        return True
    return _parse_topn_ip(title) in ips


def _topn_dimension_rows(dimension, metric="md_iops", client_ips=None):
    """Return topn rows for a dimension/metric, optionally filtered by --clients."""
    if not LAST_TOPN or not isinstance(LAST_TOPN, dict):
        return []
    dim = (LAST_TOPN.get("data") or {}).get(dimension) or {}
    rows = dim.get(metric) or []
    if CLIENT_SCOPED and client_ips is not None:
        rows = [row for row in rows if _client_matches_scope(row.get("title"), client_ips)]
    elif CLIENT_SCOPED:
        rows = [row for row in rows if _client_matches_scope(row.get("title"))]
    return rows


def _aux_refresh_interval():
    """Minimum seconds between topn/session REST probes (decoupled from headline poll)."""
    return max(30, REFRESH_SECONDS * 6)


def _maybe_fetch_aux_context(*, force=False):
    """Refresh topn + session snapshots; throttled to avoid REST on every tick."""
    global _LAST_AUX_FETCH_AT
    now = time.time()
    stale = (
        _LAST_AUX_FETCH_AT == 0.0
        or now - _LAST_AUX_FETCH_AT >= _aux_refresh_interval()
    )
    if not force and not stale:
        return
    fetch_topn_data()
    fetch_session_context()
    _LAST_AUX_FETCH_AT = now


def fetch_topn_data():
    """Load /monitors/topn/ ranking (Swagger) for insights and client scoping."""
    global LAST_TOPN
    prop = urllib.parse.quote("ViewMetrics,read_iops__rate", safe=",")
    frame = urllib.parse.quote(API_TIME_FRAME, safe="")
    path = (
        f"/monitors/topn/?object_type=view&prop_list={prop}"
        f"&time_frame={frame}&limit=10"
    )
    try:
        LAST_TOPN = api_request("GET", path)
    except RuntimeError:
        LAST_TOPN = None


def fetch_session_context():
    """Snapshot SMB sessions and open handles (Swagger operational APIs)."""
    global LAST_SESSION_CONTEXT
    ctx = {"connections": [], "open_handles": [], "errors": []}
    try:
        data = api_request("GET", "/openfilehandles/?protocol=SMB&page_size=8")
        ctx["open_handles"] = normalize_list_response(data)[:8]
    except RuntimeError as e:
        ctx["errors"].append(f"open handles: {str(e)[:80]}")
    for ip in (CLIENT_IPS if CLIENT_SCOPED else [])[:4]:
        try:
            qip = urllib.parse.quote(ip, safe="")
            data = api_request("GET", f"/clusters/list_smb_client_connections/?client_ip={qip}")
            if isinstance(data, dict):
                ctx["connections"].extend(data.get("connections") or [])
        except RuntimeError as e:
            ctx["errors"].append(f"{ip}: {str(e)[:60]}")
    LAST_SESSION_CONTEXT = ctx


def _component_ops_total(meta, data_rows):
    """Sum rd + wr + md components - authoritative mix denominator for SMBCommon."""
    read_ops = next((as_float(r["ops_sec"]) or 0 for r in data_rows if r["key"] == "read"), 0)
    write_ops = next((as_float(r["ops_sec"]) or 0 for r in data_rows if r["key"] == "write"), 0)
    md_ops = as_float(meta.get("md_iops")) or 0
    return read_ops + write_ops + md_ops


def _slice_result_for_object(result, object_id):
    if not isinstance(result, dict):
        return result
    prop_list, data, prop_idx = _result_parts(result)
    oid_idx = prop_idx.get("object_id")
    if oid_idx is None:
        return result
    want = _normalize_object_id(object_id)
    filtered = [
        row for row in data
        if len(row) > oid_idx and _normalize_object_id(row[oid_idx]) == want
    ]
    return {"prop_list": prop_list, "data": filtered}


def _create_monitor_raw(name_suffix, prop_list, object_type, object_ids, *, no_aggregation=False):
    name = f"adhoc_opstat_smb_{name_suffix}_{int(time.time())}"
    return vast_common.create_monitor_raw(
        api_request, name, prop_list, object_type, object_ids,
        time_frame=API_TIME_FRAME, no_aggregation=no_aggregation,
    )


def create_monitor(name_suffix, prop_list):
    return _create_monitor_raw(name_suffix, prop_list, "cluster", [CLUSTER_ID])


def try_create_smb_command_monitor():
    """Probe SmbMetrics per-opcode export; enables native opcode rows when available."""
    global SMB_PER_COMMAND_EXPORTED, SMB_COMMAND_MONITOR_ID
    SMB_PER_COMMAND_EXPORTED = False
    SMB_COMMAND_MONITOR_ID = None
    monitor_id = None
    props = smb_command_props()
    try:
        monitor_id = _create_monitor_raw(
            "smb_commands", props, "cluster", [CLUSTER_ID], no_aggregation=False,
        )
        result = api_request("GET", f"/monitors/{monitor_id}/query/")
        returned = set(result.get("prop_list", []))
        if any(p.startswith("SmbMetrics,") for p in returned):
            SMB_COMMAND_MONITOR_ID = monitor_id
            SMB_PER_COMMAND_EXPORTED = True
            return
        delete_monitor(monitor_id)
    except RuntimeError:
        delete_monitor(monitor_id)


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
    return as_float(values.get(_common_fqn(suffix)))


def weighted_latency(rows):
    pairs = [
        (as_float(r["ops_sec"]), as_float(r["avg_us"]))
        for r in rows
        if (as_float(r["ops_sec"]) or 0) > 0 and as_float(r["avg_us"]) is not None
    ]
    weight = sum(w for w, _ in pairs)
    if weight <= 0:
        return None
    return sum(w * v for w, v in pairs) / weight


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


def build_rows_from_results(headline_result):
    """Map SMBCommon monitor sample to panel rows."""
    global METRICS_SOURCE
    values, sample = _latest_row(headline_result)

    read_ops = _metric(values, "rd_iops")
    write_ops = _metric(values, "wr_iops")
    read_lat = _first_positive(
        _metric(values, "read_latency__avg"),
        _metric(values, "read_latency__rate"),
        _metric(values, "rd_latency"),
    )
    write_lat = _first_positive(
        _metric(values, "write_latency__avg"),
        _metric(values, "write_latency__rate"),
        _metric(values, "wr_latency"),
    )
    read_bw = raw_bw_to_mb_sec(_metric(values, "rd_bw"))
    write_bw = raw_bw_to_mb_sec(_metric(values, "wr_bw"))
    read_size = _metric(values, "read_size__avg")
    write_size = _metric(values, "write_size__avg")
    notify_rate = _delta_rate_from_samples(headline_result, _common_fqn("notify_counter"))

    md_iops = _metric(values, "md_iops")
    rd_md = _metric(values, "rd_md_iops")
    wr_md = _metric(values, "wr_md_iops")
    read_val = as_float(read_ops) or 0
    write_val = as_float(write_ops) or 0
    md_val = as_float(md_iops) or 0
    component_total = read_val + write_val + md_val
    # SMBCommon,iops is often data-path only; component sum includes metadata.
    total_iops = component_total if component_total > 0 else _metric(values, "iops")
    total_bw_mbs = _first_positive(
        raw_bw_to_mb_sec(_metric(values, "bw")),
        ((read_bw or 0) + (write_bw or 0)) or None,
    )

    interop_rates = _interop_rates_from_result(headline_result)
    session_rows = []
    for short, rate in sorted(interop_rates.items(), key=lambda kv: -kv[1]):
        session_rows.append({
            "key": short,
            "label": _INTEROP_LABELS.get(short, short.upper()),
            "ops_sec": rate,
            "pct": None,
            "avg_us": None,
            "bw_mbs": None,
            "avg_io_bytes": None,
        })
    if notify_rate is not None and notify_rate > 0:
        session_rows.append({
            "key": "notify_counter",
            "label": "CHANGE NOTIFY",
            "ops_sec": notify_rate,
            "pct": None,
            "avg_us": None,
            "bw_mbs": None,
            "avg_io_bytes": None,
        })

    active = any(
        (as_float(v) or 0) > 0
        for v in (read_ops, write_ops, md_iops, total_iops)
    ) or bool(session_rows)
    METRICS_SOURCE = "SMBCommon" if active else "idle"

    def _data_metric(key):
        if key == "read":
            return {
                "ops_sec": read_ops, "avg_us": read_lat,
                "bw_mbs": read_bw, "avg_io_bytes": read_size,
            }
        return {
            "ops_sec": write_ops, "avg_us": write_lat,
            "bw_mbs": write_bw, "avg_io_bytes": write_size,
        }

    def _meta_metric(key):
        mapping = {
            "md_total": md_iops,
            "rd_md": rd_md,
            "wr_md": wr_md,
        }
        val = mapping.get(key)
        return {
            "ops_sec": val if val is not None and val > 0 else None,
            "avg_us": None, "bw_mbs": None, "avg_io_bytes": None,
        }

    data_rows = _rows_with_pct(DATA_OPS, _data_metric)
    metadata_rows = _rows_with_pct(METADATA_OPS, _meta_metric)
    meta = {
        "md_iops": md_iops,
        "rd_md_iops": rd_md,
        "wr_md_iops": wr_md,
        "total_iops": total_iops,
        "total_bw_mbs": total_bw_mbs,
        "latency_us": weighted_latency(data_rows),
        "interop_lease_break_rate": interop_rates.get("nfs3_smb_interop_triggered_lease_breaks"),
        "notify_rate": notify_rate,
    }
    return {
        "data": data_rows,
        "metadata": metadata_rows,
        "session": session_rows,
        "opcodes": [],
        "meta": meta,
    }, sample


def _opcode_cmd_map():
    return {cmd: label for label, _cat, cmd in SMB2_OPCODES}


def _build_opcode_rows_from_smbmetrics(result):
    """Native per-command rows when VMS exports SmbMetrics."""
    if not result:
        return []
    rows = []
    for label, category, cmd in SMB2_OPCODES:
        rate_fqn = smb_metric_fqn(cmd, "rate")
        avg_fqn = smb_metric_fqn(cmd, "avg")
        ops = _delta_rate_from_samples(result, rate_fqn)
        if ops is None:
            values, _s = _latest_row(result)
            ops = as_float(values.get(rate_fqn))
        lat = None
        prop_list, data, prop_idx = _result_parts(result)
        if avg_fqn in prop_idx and len(data) >= 2:
            lat = _avg_from_sum_count_deltas(result, avg_fqn, rate_fqn)
        if lat is None:
            values, _s = _latest_row(result)
            lat = as_float(values.get(avg_fqn))
        rows.append({
            "label": label,
            "category": category,
            "cmd": cmd,
            "ops_sec": ops if ops and ops > 0 else None,
            "avg_us": lat,
            "bw_mbs": None,
            "avg_io_bytes": None,
            "source": "SMBMETRICS",
            "hint": False,
        })
    return rows


def infer_likely_active_opcodes(meta, data_rows):
    """Heuristic opcode hints when metadata opcodes share one VMS bucket."""
    hints = set()
    md_pct, read_pct, write_pct = smb_workload_mix(meta, data_rows)
    if md_pct >= 35:
        hints.update({
            "SMB2_QUERY_DIRECTORY", "SMB2_QUERY_INFO", "SMB2_CREATE", "SMB2_CLOSE",
        })
    if md_pct >= 20 and write_pct > read_pct:
        hints.update({"SMB2_SET_INFO", "SMB2_CREATE", "SMB2_CLOSE"})
    if as_float(meta.get("notify_rate")):
        hints.add("SMB2_CHANGE_NOTIFY")
    if as_float(meta.get("interop_lease_break_rate")):
        hints.add("SMB2_LOCK")
    read_io = next((as_float(r.get("avg_io_bytes")) for r in data_rows if r["key"] == "read"), None)
    if md_pct >= 50 and read_io and read_io < 32_768:
        hints.add("SMB2_QUERY_DIRECTORY")
    return hints


def _opcode_has_data(row):
    """Return True when an opcode row has measurable activity this refresh."""
    if (as_float(row.get("ops_sec")) or 0) > 0:
        return True
    if (as_float(row.get("avg_us")) or 0) > 0:
        return True
    if (as_float(row.get("bw_mbs")) or 0) > 0:
        return True
    return False


def _visible_opcode_rows(rows):
    """Drop opcodes with no data for the current refresh cycle."""
    return [row for row in rows if _opcode_has_data(row)]


def _opcode_tier(source):
    """Return workflow section tier for an opcode row source label."""
    if source in _AUTHORITATIVE_SOURCES:
        return "authoritative"
    return "derived"


def _split_opcode_rows(rows):
    """Partition opcode rows into authoritative vs system-inferred sections."""
    auth, derived = [], []
    for row in rows:
        (auth if _opcode_tier(row.get("source")) == "authoritative" else derived).append(row)
    return auth, derived


def _interop_rows_from_session(session_rows):
    """Map interop monitor rows into derived-section opcode-shaped rows."""
    out = []
    for row in session_rows:
        if (as_float(row.get("ops_sec")) or 0) <= 0:
            continue
        out.append({
            "label": row.get("label", "?"),
            "category": "interop",
            "cmd": row.get("key", "interop"),
            "ops_sec": row.get("ops_sec"),
            "avg_us": None,
            "bw_mbs": None,
            "avg_io_bytes": None,
            "source": "INTEROP",
            "hint": False,
        })
    return out


def _opcode_category_banner(category):
    return {
        "data": "Data path",
        "metadata": "Metadata",
        "lock": "Locking",
        "session": "Session / tree",
        "notify": "Notify",
        "interop": "NFS/SMB interop",
    }.get(category, category.replace("_", " ").title())


def build_opcode_workflow_rows(data_rows, metadata_rows, session_rows, meta, smb_cmd_result):
    """Build SMB2 opcode table - only rows with live data are returned."""
    if smb_cmd_result and SMB_PER_COMMAND_EXPORTED:
        native = _build_opcode_rows_from_smbmetrics(smb_cmd_result)
        total = sum(as_float(r["ops_sec"]) or 0 for r in native)
        if total > 0:
            for row in native:
                ops = as_float(row["ops_sec"]) or 0
                row["pct"] = (ops / total * 100) if total > 0 else None
            return _visible_opcode_rows(native)
        return []

    data_by_key = {r["key"]: r for r in data_rows}
    md_total = as_float(meta.get("md_iops"))
    rd_md = as_float(meta.get("rd_md_iops"))
    wr_md = as_float(meta.get("wr_md_iops"))
    ctx = LAST_SESSION_CONTEXT or {}
    handles = ctx.get("open_handles") or []
    lock_count = sum(1 for h in handles if h.get("has_locks"))
    conn_count = len(ctx.get("connections") or [])

    rows = []
    for label, category, cmd in SMB2_OPCODES:
        if category == "data" and cmd in data_by_key:
            src = data_by_key[cmd]
            if (as_float(src.get("ops_sec")) or 0) <= 0:
                continue
            rows.append({
                "label": label,
                "category": category,
                "cmd": cmd,
                "ops_sec": src.get("ops_sec"),
                "avg_us": src.get("avg_us"),
                "bw_mbs": src.get("bw_mbs"),
                "avg_io_bytes": src.get("avg_io_bytes"),
                "source": "MEASURED",
                "hint": False,
            })
        elif category == "notify":
            notify_rate = as_float(meta.get("notify_rate"))
            if notify_rate and notify_rate > 0:
                rows.append({
                    "label": label,
                    "category": category,
                    "cmd": cmd,
                    "ops_sec": notify_rate,
                    "avg_us": None,
                    "bw_mbs": None,
                    "avg_io_bytes": None,
                    "source": "PROXY",
                    "hint": False,
                })
        elif category == "lock" and lock_count > 0:
            rows.append({
                "label": label,
                "category": category,
                "cmd": cmd,
                "ops_sec": float(lock_count),
                "avg_us": None,
                "bw_mbs": None,
                "avg_io_bytes": None,
                "source": "HANDLES",
                "hint": False,
            })
        elif category == "session" and conn_count > 0 and cmd in (
            "session_setup", "tree_connect", "negotiate",
        ):
            rows.append({
                "label": label,
                "category": category,
                "cmd": cmd,
                "ops_sec": float(conn_count),
                "avg_us": None,
                "bw_mbs": None,
                "avg_io_bytes": None,
                "source": "SESSIONS",
                "hint": False,
            })

    if md_total and md_total > 0:
        rows.append({
            "label": "METADATA (total)",
            "category": "metadata",
            "cmd": "metadata_total",
            "ops_sec": md_total,
            "avg_us": None,
            "bw_mbs": None,
            "avg_io_bytes": None,
            "source": "AGGREGATE",
            "hint": False,
            "_md_rd": rd_md,
            "_md_wr": wr_md,
        })

    active_ops = sum(as_float(r["ops_sec"]) or 0 for r in rows)
    for row in rows:
        ops = as_float(row.get("ops_sec")) or 0
        row["pct"] = (ops / active_ops * 100) if active_ops > 0 else None
    return _visible_opcode_rows(rows)


def smb_workload_mix(meta, data_rows):
    """Return (md_pct, read_pct, write_pct) - always sum to ~100%."""
    total = _component_ops_total(meta, data_rows)
    if total <= 0:
        return 0.0, 0.0, 0.0
    md = as_float(meta.get("md_iops")) or 0
    read_ops = next((as_float(r["ops_sec"]) or 0 for r in data_rows if r["key"] == "read"), 0)
    write_ops = next((as_float(r["ops_sec"]) or 0 for r in data_rows if r["key"] == "write"), 0)
    return md / total * 100, read_ops / total * 100, write_ops / total * 100


def classify_smb_workload(meta, data_rows):
    """Return a human-readable SMB workload description."""
    total = _component_ops_total(meta, data_rows)
    if total < 0.5:
        return "Idle / no SMB load"

    md_pct, read_pct, write_pct = smb_workload_mix(meta, data_rows)
    read_io = next((as_float(r.get("avg_io_bytes")) for r in data_rows if r["key"] == "read"), None)
    size_tag = ""
    if read_io:
        if read_io < 8_192:
            size_tag = "small-file "
        elif read_io >= 65_536:
            size_tag = "large-block "

    if md_pct >= 60:
        dom = "write" if write_pct > read_pct else "read"
        return f"{size_tag}metadata-heavy {dom} workload"
    if as_float(meta.get("interop_lease_break_rate")) and meta["interop_lease_break_rate"] > 0.1:
        return "interop lease-break activity"
    if md_pct >= 40:
        return f"{size_tag}metadata-elevated mixed workload"
    if read_pct > write_pct * 2:
        return f"{size_tag}read-biased SMB workload"
    if write_pct > read_pct * 2:
        return f"{size_tag}write-biased SMB workload"
    if md_pct > 25:
        return f"{size_tag}mixed data + metadata workload"
    return f"{size_tag}balanced SMB workload"


def smb_health_label(total_ops, combined_latency_us):
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


def _all_panel_rows(snapshot):
    return snapshot["data"] + snapshot["metadata"]


def compute_deltas(current_rows, prev_rows):
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
            deltas[label] = d
    return deltas


def cluster_delta_summary(deltas):
    ops_delta = bw_delta = None
    lat_deltas = []
    for label, d in deltas.items():
        if "ops" in d:
            ops_delta = (ops_delta or 0) + d["ops"]
        if "bw" in d:
            bw_delta = (bw_delta or 0) + d["bw"]
        if "lat" in d:
            lat_deltas.append((label, d["lat"]))
    return ops_delta, bw_delta, lat_deltas


def csv_value(value):
    return "" if value is None else value


def ensure_csv_file():
    if not CSV_FILE:
        return
    try:
        needs_header = os.path.getsize(CSV_FILE) == 0
    except OSError:
        needs_header = True
    if needs_header:
        with open(CSV_FILE, "w", newline="") as handle:
            csv.writer(handle).writerow(CSV_HEADER)


def write_csv_snapshot(snapshot, selected_sample):
    """Append one row per panel line for the current refresh cycle."""
    if not CSV_FILE or not snapshot:
        return
    sample_mode = f"sample average {API_TIME_FRAME}" if SAMPLE_AVERAGE_MODE else "latest"
    runtime = str(datetime.now() - RUN_STARTED_AT).split(".")[0]
    local_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    base = [
        local_time, runtime, VMS, PORT, CLUSTER_NAME, CLUSTER_ID,
        HEADLINE_MONITOR_ID, sample_mode, API_TIME_FRAME, selected_sample,
        METRICS_SOURCE,
    ]
    with open(CSV_FILE, "a", newline="") as handle:
        writer = csv.writer(handle)
        for panel, rows in (
            ("data", snapshot["data"]),
            ("metadata", snapshot["metadata"]),
            ("opcode", snapshot.get("opcodes") or []),
        ):
            for row in rows:
                writer.writerow(base + [
                    panel,
                    row["label"],
                    csv_value(row.get("ops_sec")),
                    csv_value(row.get("pct")),
                    csv_value(row.get("avg_us")),
                    csv_value(row.get("bw_mbs")),
                    csv_value(row.get("avg_io_bytes")),
                ])


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
        return join_columns([
            _label_cell(row["label"], w["label"], _DIM),
            _dash(w["iops"]), _dash(w["throughput"]), _dash(w["size"]), _dash(w["latency"]),
        ], _COL_SEP)
    bw_text, _ = format_throughput_mbs(row.get("bw_mbs"))
    size_text, _ = format_block_size(row.get("avg_io_bytes"))
    lat_text, lat_us = format_latency_us(row.get("avg_us"))
    label_color = _BCYAN if row["key"] == "read" else _BYELLOW
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
        _metric_cell(lat_text, w["latency"], lat_color) if lat_us else _dash(w["latency"]),
    ], _COL_SEP)


def _render_health_panel(snapshot, deltas, width):
    meta = snapshot["meta"]
    data_rows = snapshot["data"]
    total_ops = as_float(meta.get("total_iops")) or 0
    combined_lat = as_float(meta.get("latency_us"))
    total_bw_mbs = as_float(meta.get("total_bw_mbs"))
    md_pct, read_pct, write_pct = smb_workload_mix(meta, data_rows)
    health_lbl, health_color = smb_health_label(total_ops, combined_lat)
    workload_type = classify_smb_workload(meta, data_rows)
    ops_delta, bw_delta, lat_deltas = cluster_delta_summary(deltas)

    print(box_top(HEALTH_PANEL_TITLE, width))
    ops_s = c(f"{total_ops:,.2f} ops/s" if total_ops else "- ops/s", _BWHITE)
    lat_text, _ = format_latency_us(combined_lat)
    lat_s = c(lat_text if combined_lat else "-", _BGREEN if combined_lat else _DIM)
    bw_text, _ = format_throughput_mbs(total_bw_mbs)
    bw_s = c(bw_text if total_bw_mbs else "-", _CYAN)
    status = (
        badge(health_lbl, health_color)
        + "   " + ops_s
        + "   " + lat_dot(combined_lat) + " Lat " + lat_s
        + "   BW " + bw_s
    )
    print(box_row(status, width))
    print(box_row(c("Workload  ", _DIM) + c(workload_type, _YELLOW), width))
    print(box_row(c(f"{'Metadata':<10}", _DIM) + workload_bar(md_pct, 22, _CYAN), width))
    print(box_row(c(f"{'Read':<10}", _DIM) + workload_bar(read_pct, 22, _BGREEN), width))
    print(box_row(c(f"{'Write':<10}", _DIM) + workload_bar(write_pct, 22, _BYELLOW), width))
    if deltas:
        parts = []
        if ops_delta is not None and abs(ops_delta) >= 0.001:
            parts.append(delta_arrow(ops_delta) + " " + c(fmt_delta(ops_delta, 2) + " ops/s", _GREEN))
        if bw_delta is not None and abs(bw_delta) >= 0.001:
            parts.append(delta_arrow(bw_delta) + " " + c(fmt_delta(bw_delta, 2) + " MB/s", _CYAN))
        if lat_deltas:
            worst = max(lat_deltas, key=lambda x: abs(x[1]))
            parts.append(
                delta_arrow_lat(worst[1])
                + " " + c(f"Lat {fmt_delta(worst[1], 1)} {_MUS} [{worst[0]}]", _YELLOW)
            )
        if parts:
            print(box_row(c("Δ  ", _DIM) + "   ".join(parts), width))
    print(box_bottom(width))


def _render_insights_panel(snapshot, deltas, width):
    rows = _all_panel_rows(snapshot)
    active_rows = [r for r in rows if (as_float(r["ops_sec"]) or 0) > 0]
    meta = snapshot["meta"]

    print(box_top(INSIGHTS_PANEL_TITLE, width))

    top_op = max(active_rows, key=lambda r: as_float(r["ops_sec"]) or 0, default=None)
    opcode_rows = snapshot.get("opcodes") or []
    measured_opcodes = [
        r for r in opcode_rows if r.get("source") in ("MEASURED", "SMBMETRICS", "PROXY")
        and (as_float(r.get("ops_sec")) or 0) > 0
    ]
    if measured_opcodes:
        top_opcode = max(measured_opcodes, key=lambda r: as_float(r["ops_sec"]) or 0)
        pct_v = as_float(top_opcode.get("pct")) or 0
        print(box_row(
            c("Top Opcode       ", _DIM) + c(top_opcode["label"], _BWHITE)
            + c(f"  {format_iops(top_opcode.get('ops_sec'))} ops/s", _GREEN)
            + (c(f"  ({pct_v:.1f}%)", _DIM) if pct_v else ""),
            width,
        ))
    elif top_op:
        pct_v = as_float(top_op["pct"]) or 0
        print(box_row(
            c("Top Contributor  ", _DIM) + c(top_op["label"], _BWHITE)
            + c(f"  {pct_v:.1f}% of ops", _GREEN),
            width,
        ))

    active_with_lat = [r for r in active_rows if as_float(r["avg_us"]) is not None]
    if active_with_lat:
        hi = max(active_with_lat, key=lambda r: as_float(r["avg_us"]) or 0)
        us = as_float(hi["avg_us"])
        print(box_row(
            c("Highest Latency  ", _DIM) + c(hi["label"], _BWHITE)
            + "   " + lat_dot(us) + " " + c(f"{us:.0f} {_MUS}", _YELLOW),
            width,
        ))

    io_rows = [r for r in snapshot["data"] if as_float(r.get("bw_mbs"))]
    if io_rows:
        top_bw = max(io_rows, key=lambda r: as_float(r["bw_mbs"]) or 0)
        bw_text, _ = format_throughput_mbs(top_bw["bw_mbs"])
        size_text, _ = format_block_size(top_bw.get("avg_io_bytes"))
        line = c("Data Consumer    ", _DIM) + c(top_bw["label"], _BCYAN) + c(f"  {bw_text}", _CYAN)
        if size_text != "-":
            line += c(f"  avg I/O {size_text}", _DIM)
        print(box_row(line, width))

    md_ops = as_float(meta.get("md_iops"))
    if md_ops and md_ops > 0:
        total = _component_ops_total(meta, snapshot["data"])
        md_pct = (md_ops / total * 100) if total > 0 else 0
        print(box_row(
            c("Metadata Load    ", _DIM) + c(f"{format_iops(md_ops)} ops/s", _YELLOW)
            + c(f"  ({md_pct:.1f}% of total)", _DIM),
            width,
        ))

    top_clients = _topn_dimension_rows("client", "md_iops")
    if top_clients:
        row = top_clients[0]
        title = row.get("title", "?")
        total_md = as_float(row.get("total")) or 0
        scope_note = " (scoped)" if CLIENT_SCOPED else ""
        print(box_row(
            c("Top Client       ", _DIM) + c(title, _BWHITE)
            + c(f"  md {format_iops(total_md)} ops/s{scope_note}", _CYAN),
            width,
        ))

    top_views = _topn_dimension_rows("view", "md_iops")
    if top_views:
        row = top_views[0]
        print(box_row(
            c("Top Share        ", _DIM) + c(row.get("title", "?"), _BWHITE)
            + c(f"  md {format_iops(row.get('total'))} ops/s", _GREEN),
            width,
        ))

    if deltas:
        top_d = max(deltas.items(), key=lambda kv: abs(kv[1].get("ops", 0)), default=None)
        if top_d and abs(top_d[1].get("ops", 0)) > 0.1:
            lbl_d, d = top_d
            line = (
                c("Top Δ            ", _DIM) + c(lbl_d, _BWHITE)
                + "   " + delta_arrow(d["ops"]) + " " + c(fmt_delta(d["ops"], 2) + "/s", _GREEN)
            )
            print(box_row(line, width))

    print(box_row(
        c("Observation      ", _DIM) + c(classify_smb_workload(meta, snapshot["data"]), _YELLOW),
        width,
    ))
    print(box_bottom(width))


def _render_data_panel(rows, width):
    titles = [
        ("Operation", "label", "<"), ("Ops/s", "iops", ">"), ("Throughput", "throughput", ">"),
        ("Avg Size", "size", ">"), ("Latency", "latency", ">"),
    ]
    print(box_top(DATA_PANEL_TITLE, width))
    print(box_row(_table_header_titles(titles), width))
    print(box_sep(width))
    for row in rows:
        print(box_row(_data_row_cells(row), width))
    print(box_bottom(width))


def _render_metadata_panel(rows, meta, width):
    titles = [
        ("Operation", "label", "<"), ("Ops/s", "iops", ">"), ("", "throughput", ">"),
        ("", "size", ">"), ("Latency", "latency", ">"),
    ]
    print(box_top(METADATA_PANEL_TITLE, width))
    print(box_row(_table_header_titles(titles), width))
    print(box_sep(width))
    for row in rows:
        print(box_row(_simple_row_cells(row), width))
    note = (
        "SmbMetrics per-command not exported - SMBCommon metadata aggregates "
        f"(md {format_iops(meta.get('md_iops'))} ops/s)"
    )
    print(box_row(c(note, _DIM), width))
    print(box_bottom(width))


def _opcode_source_cell(source, hint):
    if source == "MEASURED":
        return c(pad_display("MEASURED", _OPCODE_COL["source"], ">"), _BGREEN)
    if source == "SMBMETRICS":
        return c(pad_display("SMBMETRICS", _OPCODE_COL["source"], ">"), _BGREEN)
    if source in ("PROXY", "HANDLES", "SESSIONS", "INTEROP"):
        return c(pad_display(source[:8], _OPCODE_COL["source"], ">"), _YELLOW)
    if source == "INFERRED":
        return c(pad_display("INFERRED", _OPCODE_COL["source"], ">"), _BYELLOW)
    if source == "MD_HINT":
        return c(pad_display("MD_HINT", _OPCODE_COL["source"], ">"), _BYELLOW)
    if source == "AGGREGATE":
        return c(pad_display("AGGREGATE", _OPCODE_COL["source"], ">"), _CYAN)
    if source == "MD_BUCKET":
        return c(pad_display("MD_BUCKET", _OPCODE_COL["source"], ">"), _DIM)
    return c(pad_display("N/A", _OPCODE_COL["source"], ">"), _DIM)


def _opcode_row_cells(row):
    w = _OPCODE_COL
    ops = as_float(row.get("ops_sec"))
    active = ops is not None and ops > 0
    label = row["label"]
    label_color = _BCYAN if row.get("source") == "MEASURED" and "READ" in label else (
        _BYELLOW if row.get("source") == "MEASURED" and "WRITE" in label else
        _BYELLOW if row.get("hint") else _BWHITE if active else _DIM
    )
    lat_text, lat_us = format_latency_us(row.get("avg_us"))
    lat_color = _BRED if (lat_us or 0) > 10_000 else _YELLOW if (lat_us or 0) > 1_000 else _BGREEN
    bw_text, _ = format_throughput_mbs(row.get("bw_mbs"))
    size_text, _ = format_block_size(row.get("avg_io_bytes"))
    return join_columns([
        _label_cell(label, w["label"], label_color),
        _metric_cell(format_iops(ops), w["iops"], _GREEN) if active else _dash(w["iops"]),
        _metric_cell(bw_text, w["throughput"], _CYAN) if row.get("bw_mbs") else _dash(w["throughput"]),
        _metric_cell(size_text, w["size"], _CYAN) if row.get("avg_io_bytes") else _dash(w["size"]),
        _metric_cell(lat_text, w["latency"], lat_color) if lat_us else _dash(w["latency"]),
        _opcode_source_cell(row.get("source"), row.get("hint")),
    ], _COL_SEP)


def _render_md_split_note(row, width):
    """Compact read-md / write-md split under the metadata aggregate row."""
    rd_md = as_float(row.get("_md_rd"))
    wr_md = as_float(row.get("_md_wr"))
    if not rd_md and not wr_md:
        return
    parts = []
    if rd_md:
        parts.append(f"read-md {format_iops(rd_md)}/s")
    if wr_md:
        parts.append(f"write-md {format_iops(wr_md)}/s")
    line = "    " + "  ·  ".join(parts)
    print(box_row(c(line, _DIM), width))


def _render_opcode_section_header(title, width, *, color=_BOLD):
    print(box_row(c(f"▸ {title}", color), width))


def _render_opcode_table_rows(rows, width):
    """Render opcode rows with category sub-headers."""
    last_category = None
    for row in rows:
        cat = row.get("category")
        if cat != last_category:
            print(box_row(c(_opcode_category_banner(cat), _BCYAN), width))
            last_category = cat
        print(box_row(_opcode_row_cells(row), width))
        if row.get("source") == "AGGREGATE":
            _render_md_split_note(row, width)


def _render_derived_opcode_hints(meta, data_rows, width):
    """Show classifier guesses for metadata opcodes not split by VMS."""
    if SMB_PER_COMMAND_EXPORTED:
        return
    hints = infer_likely_active_opcodes(meta, data_rows)
    if not hints:
        return
    hint_text = ", ".join(sorted(hints)[:6])
    print(box_row(c("Likely active opcodes (workload classifier)", _BCYAN), width))
    print(box_row(c(f"  {hint_text}", _DIM), width))


def _render_opcode_workflow_panel(snapshot, width):
    rows = _visible_opcode_rows(snapshot.get("opcodes") or [])
    session_rows = snapshot.get("session") or []
    meta = snapshot.get("meta") or {}
    data_rows = snapshot.get("data") or []
    derived_rows = _interop_rows_from_session(session_rows)
    auth_rows, inferred_rows = _split_opcode_rows(rows)
    derived_rows = inferred_rows + derived_rows

    titles = [
        ("SMB2 Opcode", "label", "<"), ("Ops/s", "iops", ">"), ("Throughput", "throughput", ">"),
        ("Avg Size", "size", ">"), ("Latency", "latency", ">"), ("Source", "source", ">"),
    ]
    print(box_top(OPCODE_PANEL_TITLE, width))
    if not auth_rows and not derived_rows:
        print(box_row(c("No active SMB opcodes this refresh", _DIM), width))
        print(box_bottom(width))
        return

    hdr_cells = []
    for title, key, align in titles:
        hdr_cells.append(c(pad_display(title, _OPCODE_COL[key], align), _BOLD))
    print(box_row(join_columns(hdr_cells, _COL_SEP), width))
    print(box_sep(width))

    if auth_rows:
        _render_opcode_section_header(_OPCODE_SECTION_AUTHORITATIVE, width, color=_BOLD + _BGREEN)
        _render_opcode_table_rows(auth_rows, width)

    if derived_rows or (not SMB_PER_COMMAND_EXPORTED and infer_likely_active_opcodes(meta, data_rows)):
        if auth_rows:
            print(box_sep(width))
        _render_opcode_section_header(_OPCODE_SECTION_DERIVED, width, color=_BOLD + _BYELLOW)
        if derived_rows:
            _render_opcode_table_rows(derived_rows, width)
        _render_derived_opcode_hints(meta, data_rows, width)

    if SMB_PER_COMMAND_EXPORTED:
        footer = "Per-opcode SmbMetrics active - authoritative section only"
    else:
        footer = "Authoritative: SMBCommon counters · Derived: REST snapshots, proxies, classifier"
    print(box_row(c(footer, _DIM), width))
    print(box_bottom(width))


def _render_session_panel(snapshot, width):
    session_rows = snapshot.get("session") or []
    ctx = LAST_SESSION_CONTEXT or {}

    print(box_top(SESSION_PANEL_TITLE, width))
    if session_rows:
        for row in session_rows[:5]:
            print(box_row(
                c(f"{row['label']:<16}", _BWHITE)
                + c(f" {format_iops(row.get('ops_sec'))} /s", _YELLOW),
                width,
            ))
    else:
        print(box_row(c("NFSv3+SMB interop counters idle (no lease/session pain)", _DIM), width))
        print(box_row(c("Per-command SmbMetrics not exported on this VMS build.", _DIM), width))

    handles = ctx.get("open_handles") or []
    if handles:
        locked = sum(1 for h in handles if h.get("has_locks"))
        leased = sum(1 for h in handles if h.get("has_lease"))
        print(box_row(
            c("Open SMB handles ", _DIM)
            + c(f"{len(handles)} sampled", _BWHITE)
            + c(f"  locks={locked}  leases={leased}", _YELLOW),
            width,
        ))
        for handle in handles[:3]:
            path = handle.get("open_file_path") or handle.get("path") or "-"
            client = handle.get("client_ip") or "?"
            print(box_row(
                c(f"  {client}", _CYAN) + c(f"  {path[:48]}", _DIM),
                width,
            ))

    conns = ctx.get("connections") or []
    if conns:
        print(box_row(c(f"SMB client sessions: {len(conns)}", _BGREEN), width))
        for conn in conns[:3]:
            if isinstance(conn, dict):
                line = "  ".join(
                    f"{k}={conn[k]}" for k in ("client_ip", "username", "server_ip")
                    if conn.get(k)
                )
                if line:
                    print(box_row(c(line[:width - 6], _DIM), width))
    elif CLIENT_SCOPED and not ctx.get("errors"):
        print(box_row(c("No active SMB sessions for scoped client IP(s)", _DIM), width))

    for err in (ctx.get("errors") or [])[:2]:
        print(box_row(c(f"Session probe: {err}", _DIM), width))

    print(box_bottom(width))


def _obj_name(obj, fields):
    return vast_common.resolve_object_name(obj, fields)


def _cleanup_drill_monitors():
    global DRILL_MONITORS
    for monitor_id, _name in DRILL_MONITORS:
        delete_monitor(monitor_id)
    DRILL_MONITORS = []


def _parse_sample_ts(sample):
    if not sample or sample == "-":
        return None
    try:
        return datetime.fromisoformat(str(sample).replace("Z", "+00:00"))
    except ValueError:
        return None


def _view_rate_prop_indexes(prop_idx):
    """Indexes of ViewMetrics rate/latency props used for row selection."""
    return [
        prop_idx[p] for p in (
            _VIEW_READ_IOPS, _VIEW_WRITE_IOPS,
            _VIEW_READ_MD, _VIEW_WRITE_MD,
            _VIEW_READ_LAT, _VIEW_WRITE_LAT,
            _VIEW_READ_BW, _VIEW_WRITE_BW,
            _VIEW_READ_MD_LAT, _VIEW_WRITE_MD_LAT,
        )
        if p in prop_idx
    ]


def _view_values_from_result(result):
    """Pick the newest ViewMetrics row with non-null rates.

    View monitors with no_aggregation often return duplicate timestamps per
    object_id: a padding row with null metrics followed by the real sample.
    """
    prop_list, data, prop_idx = _result_parts(result)
    if not data:
        return {}, prop_idx, "-"
    rate_idxs = _view_rate_prop_indexes(prop_idx)
    chosen = None
    for row in data:
        if rate_idxs and any(
            idx < len(row) and row[idx] is not None
            for idx in rate_idxs
        ):
            chosen = row
            break
    if chosen is None:
        chosen = data[0]
    values = {name: chosen[idx] for name, idx in prop_idx.items() if idx < len(chosen)}
    sample = chosen[0] if chosen else "-"
    return values, prop_idx, sample


def _values_from_result(result):
    prop_list, data, prop_idx = _result_parts(result)
    if not data:
        return {}, prop_idx, "-"
    row = data[0]
    sample = row[0] if row else "-"
    values = {}
    for name, idx in prop_idx.items():
        if idx < len(row):
            values[name] = row[idx]
    return values, prop_idx, sample


def _delta_rate_from_samples(result, sum_fqn):
    prop_list, data, prop_idx = _result_parts(result)
    idx = prop_idx.get(sum_fqn)
    if idx is None or len(data) < 2:
        return None
    newest, oldest = data[0], data[-1]
    t_new = _parse_sample_ts(newest[0])
    t_old = _parse_sample_ts(oldest[0])
    if not t_new or not t_old:
        return None
    dt = abs((t_new - t_old).total_seconds())
    if dt <= 0:
        return None
    delta = as_float(newest[idx])
    old = as_float(oldest[idx])
    if delta is None or old is None:
        return None
    return max(delta - old, 0.0) / dt


def _avg_from_sum_count_deltas(result, sum_fqn, count_fqn):
    prop_list, data, prop_idx = _result_parts(result)
    idx_s, idx_c = prop_idx.get(sum_fqn), prop_idx.get(count_fqn)
    if idx_s is None or idx_c is None or len(data) < 2:
        return None
    s_new, s_old = as_float(data[0][idx_s]), as_float(data[-1][idx_s])
    c_new, c_old = as_float(data[0][idx_c]), as_float(data[-1][idx_c])
    if None in (s_new, s_old, c_new, c_old):
        return None
    cnt_delta = c_new - c_old
    if cnt_delta <= 0:
        return None
    return (s_new - s_old) / cnt_delta


def _weighted_us(pairs):
    valid = [(w, v) for w, v in pairs if (w or 0) > 0 and v is not None]
    weight = sum(w for w, _v in valid)
    if weight <= 0:
        return None
    return sum(w * v for w, v in valid) / weight


def _drill_top_op(op_pairs):
    active = [(label, ops) for label, ops in op_pairs if (ops or 0) > 0]
    if not active:
        return "-", None
    top_label, top_ops = max(active, key=lambda item: item[1])
    total = sum(ops for _, ops in active)
    pct = (top_ops / total * 100.0) if total > 0 else None
    return top_label, pct


def _build_cnode_drill_row(result, obj_name):
    snapshot, _sample = build_rows_from_results(result)
    all_rows = snapshot["data"] + snapshot["metadata"]
    meta = snapshot["meta"]
    total_ops = as_float(meta.get("total_iops")) or sum(as_float(r["ops_sec"]) or 0 for r in all_rows)
    latency = as_float(meta.get("latency_us")) or weighted_latency(snapshot["data"])
    bw_mbs = as_float(meta.get("total_bw_mbs"))
    bw_gbs = (bw_mbs / 1024.0) if bw_mbs else None
    active = [r for r in all_rows if (as_float(r["ops_sec"]) or 0) > 0]
    top = max(active, key=lambda r: as_float(r["ops_sec"]) or 0, default=None)
    return {
        "name": obj_name,
        "total_ops": total_ops if total_ops > 0 else None,
        "latency_us": latency,
        "bw_gbs": bw_gbs,
        "top_rpc": top["label"] if top else "-",
        "top_rpc_pct": as_float(top["pct"]) if top else None,
    }


def _build_view_drill_row(result, obj_name):
    values, _prop_idx, _sample = _view_values_from_result(result)
    read_ops = as_float(values.get(_VIEW_READ_IOPS)) or 0.0
    write_ops = as_float(values.get(_VIEW_WRITE_IOPS)) or 0.0
    read_md = as_float(values.get(_VIEW_READ_MD)) or 0.0
    write_md = as_float(values.get(_VIEW_WRITE_MD)) or 0.0
    total_ops = read_ops + write_ops + read_md + write_md
    latency = _weighted_us([
        (read_ops, as_float(values.get(_VIEW_READ_LAT))),
        (write_ops, as_float(values.get(_VIEW_WRITE_LAT))),
        (read_md, as_float(values.get(_VIEW_READ_MD_LAT))),
        (write_md, as_float(values.get(_VIEW_WRITE_MD_LAT))),
    ])
    read_bw = raw_bw_to_gb_sec(values.get(_VIEW_READ_BW)) or 0.0
    write_bw = raw_bw_to_gb_sec(values.get(_VIEW_WRITE_BW)) or 0.0
    top_rpc, top_pct = _drill_top_op([
        ("READ", read_ops), ("WRITE", write_ops),
        ("RD MD", read_md), ("WR MD", write_md),
    ])
    return {
        "name": obj_name,
        "total_ops": total_ops if total_ops > 0 else None,
        "latency_us": latency,
        "bw_gbs": (read_bw + write_bw) if (read_bw + write_bw) > 0 else None,
        "top_rpc": top_rpc,
        "top_rpc_pct": top_pct,
    }


def _build_tenant_drill_row(result, obj_name):
    read_ops = _delta_rate_from_samples(result, _TENANT_READ_IOPS) or 0.0
    write_ops = _delta_rate_from_samples(result, _TENANT_WRITE_IOPS) or 0.0
    read_md = _delta_rate_from_samples(result, _TENANT_READ_MD) or 0.0
    write_md = _delta_rate_from_samples(result, _TENANT_WRITE_MD) or 0.0
    total_ops = read_ops + write_ops + read_md + write_md
    read_lat = _avg_from_sum_count_deltas(result, _TENANT_READ_LAT, _TENANT_READ_CNT)
    write_lat = _avg_from_sum_count_deltas(result, _TENANT_WRITE_LAT, _TENANT_WRITE_CNT)
    read_md_lat = _avg_from_sum_count_deltas(
        result, _TENANT_READ_MD_LAT_SUM, _TENANT_READ_MD_LAT_CNT,
    )
    write_md_lat = _avg_from_sum_count_deltas(
        result, _TENANT_WRITE_MD_LAT_SUM, _TENANT_WRITE_MD_LAT_CNT,
    )
    latency = _weighted_us([
        (read_ops, read_lat), (write_ops, write_lat),
        (read_md, read_md_lat), (write_md, write_md_lat),
    ])
    read_bw_gbs = raw_bw_to_gb_sec(_delta_rate_from_samples(result, _TENANT_READ_BW)) or 0.0
    write_bw_gbs = raw_bw_to_gb_sec(_delta_rate_from_samples(result, _TENANT_WRITE_BW)) or 0.0
    top_rpc, top_pct = _drill_top_op([
        ("READ", read_ops), ("WRITE", write_ops),
        ("RD MD", read_md), ("WR MD", write_md),
    ])
    return {
        "name": obj_name,
        "total_ops": total_ops if total_ops > 0 else None,
        "latency_us": latency,
        "bw_gbs": (read_bw_gbs + write_bw_gbs) if (read_bw_gbs + write_bw_gbs) > 0 else None,
        "top_rpc": top_rpc,
        "top_rpc_pct": top_pct,
    }


def _build_drill_row(mode, result, obj_name):
    if mode == "view":
        return _build_view_drill_row(result, obj_name)
    if mode == "tenant":
        return _build_tenant_drill_row(result, obj_name)
    return _build_cnode_drill_row(result, obj_name)


def _rank_drill_candidates(mode, objects, cfg):
    """Rank view/tenant candidates in chunks - scans all objects, not just the first 32."""
    if not objects:
        return []

    id_to_name = {obj["id"]: _obj_name(obj, cfg["name_fields"]) for obj in objects}
    all_ranked = []

    for chunk_start in range(0, len(objects), _DRILL_PROBE_LIMIT):
        chunk = objects[chunk_start:chunk_start + _DRILL_PROBE_LIMIT]
        object_ids = [obj["id"] for obj in chunk]
        rank_monitor_id = None
        try:
            rank_monitor_id = _create_monitor_raw(
                f"rank_{mode}_{chunk_start}",
                build_drill_rank_prop_list(mode),
                cfg["object_type"],
                object_ids,
                no_aggregation=cfg.get("no_aggregation", False),
            )
            result = api_request("GET", f"/monitors/{rank_monitor_id}/query/")
            for obj_id in object_ids:
                name = id_to_name[obj_id]
                slice_result = _slice_result_for_object(result, obj_id)
                row = _build_drill_row(mode, slice_result, name)
                all_ranked.append({
                    "id": obj_id,
                    "name": name,
                    "total_ops": as_float(row.get("total_ops")) or 0.0,
                })
        except RuntimeError:
            for obj in chunk:
                all_ranked.append({
                    "id": obj["id"],
                    "name": id_to_name[obj["id"]],
                    "total_ops": 0.0,
                })
        finally:
            delete_monitor(rank_monitor_id)

    all_ranked.sort(key=lambda item: (-item["total_ops"], item["name"].lower()))
    return [{"id": item["id"], "name": item["name"]} for item in all_ranked[:_MAX_DRILL_OBJECTS]]


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

    all_valid = [o for o in objects if "id" in o]
    if mode in ("view", "tenant"):
        DRILL_OBJECTS = _rank_drill_candidates(mode, all_valid, cfg)
    else:
        selected = all_valid[:_MAX_DRILL_OBJECTS]
        DRILL_OBJECTS = [
            {"id": o["id"], "name": _obj_name(o, cfg["name_fields"])}
            for o in selected
        ]

    if not DRILL_OBJECTS:
        DRILL_ERROR = f"No valid {mode} objects available for drill-down"
        return

    _cleanup_drill_monitors()
    prop_list = build_drill_prop_list(mode)
    new_monitors = []
    last_error = None

    if _is_batch_drill_mode(mode):
        try:
            monitor_id = _create_monitor_raw(
                f"{mode}_batch",
                prop_list,
                cfg["object_type"],
                [obj["id"] for obj in DRILL_OBJECTS],
                no_aggregation=cfg.get("no_aggregation", False),
            )
            new_monitors.append((monitor_id, None))
        except RuntimeError as e:
            last_error = str(e)
    else:
        for obj in DRILL_OBJECTS:
            try:
                monitor_id = _create_monitor_raw(
                    f"{mode}_{obj['id']}",
                    prop_list,
                    cfg["object_type"],
                    [obj["id"]],
                    no_aggregation=cfg.get("no_aggregation", False),
                )
                new_monitors.append((monitor_id, obj["name"]))
            except RuntimeError as e:
                last_error = str(e)

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

    DRILL_MONITORS = new_monitors
    DRILL_MODE = mode
    DRILL_ERROR = None
    LAST_DRILL_ROWS = []


def exit_drill_mode():
    global DRILL_MODE, DRILL_OBJECTS, LAST_DRILL_ROWS, DRILL_ERROR, DRILL_STATUS
    _cleanup_drill_monitors()
    DRILL_MODE = None
    DRILL_OBJECTS = []
    LAST_DRILL_ROWS = []
    DRILL_ERROR = None
    DRILL_STATUS = None


def fetch_drill_query():
    global LAST_DRILL_ROWS, DRILL_ERROR
    if not DRILL_MODE:
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
    """Enter drill mode with a standby message during monitor setup."""
    global DRILL_STATUS
    cfg = _DRILL_CFG.get(mode, {})
    exit_drill_mode()
    label = cfg.get("label", mode.upper())
    if mode in ("view", "tenant"):
        DRILL_STATUS = f"Ranking {label} drill-down by activity, stand by..."
    else:
        DRILL_STATUS = f"Switching to {label} drill-down, stand by..."
    render_screen()
    try:
        enter_drill_mode(mode)
        if DRILL_MODE:
            fetch_drill_query()
    finally:
        DRILL_STATUS = None


def _render_drill_panel(width):
    dc = _DRILL_COL
    if DRILL_STATUS:
        print(box_top("DRILL-DOWN", width))
        print(box_row(c(DRILL_STATUS, _YELLOW), width))
        print(box_bottom(width))
        return

    mode_label = DRILL_MODE.upper() if DRILL_MODE else "?"
    print(box_top(f"{mode_label} DRILL-DOWN", width))
    if DRILL_ERROR:
        print(box_row(c(f"Error: {DRILL_ERROR}", _BRED), width))
        print(box_row(c("Press x to return to cluster view", _DIM), width))
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


def fetch_monitor_query(*, force_aux=False):
    global LAST_ROWS, LAST_SAMPLE, PREV_ROWS
    result = api_request("GET", f"/monitors/{HEADLINE_MONITOR_ID}/query/")
    PREV_ROWS = _all_panel_rows(LAST_ROWS) if LAST_ROWS else []
    LAST_ROWS, LAST_SAMPLE = build_rows_from_results(result)
    smb_result = None
    if SMB_COMMAND_MONITOR_ID:
        try:
            smb_result = api_request("GET", f"/monitors/{SMB_COMMAND_MONITOR_ID}/query/")
        except RuntimeError:
            smb_result = None
    LAST_ROWS["opcodes"] = build_opcode_workflow_rows(
        LAST_ROWS["data"], LAST_ROWS["metadata"], LAST_ROWS["session"],
        LAST_ROWS["meta"], smb_result,
    )
    _maybe_fetch_aux_context(force=force_aux)
    write_csv_snapshot(LAST_ROWS, LAST_SAMPLE)
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
    add(LAST_ROWS.get("metadata", []), "metadata")
    add(LAST_ROWS.get("session", []), "session")
    return series


def _export_openmetrics():
    if not openmetrics.is_enabled():
        return
    openmetrics.export_snapshot(
        CLUSTER_NAME, None, CLUSTER_NAME, _openmetrics_series(), sample=LAST_SAMPLE,
    )


def poll_tick():
    """One refresh poll: drill view when active, else the headline monitors."""
    if DRILL_MODE:
        fetch_drill_query()
    else:
        fetch_monitor_query()


def manual_refresh():
    """Space-bar refresh: also force the auxiliary monitors in cluster view."""
    if DRILL_MODE:
        fetch_drill_query()
    else:
        fetch_monitor_query(force_aux=True)


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
        c("  VAST SMB", _BCYAN) + c(" opstat", _BWHITE) + c(f" v{VERSION}", _DIM)
        + f"   VMS {c(f'{VMS}:{PORT}', _BWHITE)}   cluster {c(CLUSTER_NAME or '?', _BWHITE)}"
        + c(f"   refresh {REFRESH_SECONDS}s", _DIM)
    )
    if CLIENT_SCOPED:
        client_note = CLIENT_IPS[0] if len(CLIENT_IPS) == 1 else f"{CLIENT_IPS[0]} (+{len(CLIENT_IPS) - 1})"
        title += c(f"   | clients {client_note}", _BYELLOW)
    if DRILL_MODE:
        title += c(f"   | {DRILL_MODE.upper()} DRILL", _BYELLOW)
    if CSV_FILE:
        title += c(f"   csv:{CSV_FILE}", _DIM)
    print(title)
    frame_note = f"sample-average {API_TIME_FRAME}" if SAMPLE_AVERAGE_MODE else f"frame {API_TIME_FRAME}"
    os_label = format_os_release(CLUSTER_OS)
    print(c(
        f"  sample {LAST_SAMPLE}   {frame_note}   source {METRICS_SOURCE}"
        + (f"   {os_label}" if os_label else ""),
        _DIM,
    ))
    print()

    if DRILL_MODE or DRILL_ERROR or DRILL_STATUS:
        _render_drill_panel(width)
    else:
        deltas = compute_deltas(_all_panel_rows(LAST_ROWS), PREV_ROWS)
        _render_health_panel(LAST_ROWS, deltas, width)
        print()
        _render_insights_panel(LAST_ROWS, deltas, width)
        print()
        _render_opcode_workflow_panel(LAST_ROWS, width)
        print()
    print(box_row(
        c("[q]", _BWHITE) + c(" Quit ", _DIM)
        + c("|", _DIM) + c("[c]", _BWHITE) + c(" cNode ", _DIM)
        + c("|", _DIM) + c("[v]", _BWHITE) + c(" View ", _DIM)
        + c("|", _DIM) + c("[t]", _BWHITE) + c(" Tenant ", _DIM)
        + c("|", _DIM) + c("[x]", _BWHITE) + c(" Exit drill ", _DIM)
        + c("|", _DIM) + c("[space]", _BWHITE) + c(" Refresh", _DIM),
        width,
    ), flush=True)


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


def discover_metrics(write_report_path=None):
    """Enumerate SMB-related VMS metrics, objects, and monitor probes (read-only)."""
    global CLUSTER_ID, CLUSTER_NAME
    print(f"SMB metric discovery - VMS {VMS}:{PORT}\n")
    try:
        CLUSTER_ID, CLUSTER_NAME = get_current_cluster()
        print(f"Cluster: {CLUSTER_NAME} (id={CLUSTER_ID})\n")
    except RuntimeError as e:
        print(f"ERROR: Could not connect to VMS: {e}")
        sys.exit(1)

    clusters = normalize_list_response(api_request("GET", "/clusters/"))
    protocols = clusters[0].get("protocols", []) if clusters else []
    print(f"Protocols: {protocols}\n")

    report_lines = [
        "# SMB Phase 0 - Live Discovery Results",
        "",
        f"**VMS:** `{VMS}:{PORT}`  ",
        f"**Generated:** {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}  ",
        f"**Cluster:** {CLUSTER_NAME} (id={CLUSTER_ID})  ",
        f"**Protocols:** `{protocols}`  ",
        "",
    ]

    print("[ /api/metrics/ - SMB-related entries ]")
    report_lines.append("## Metrics catalog (`GET /api/metrics/`)")
    try:
        metrics = api_request("GET", "/metrics/")
        smb_hits = filter_smb_metrics(metrics)
        print(f"  SMB-related entries: {len(smb_hits)}")
        report_lines.append(f"- SMB-related entries: **{len(smb_hits)}**")
        for entry in smb_hits[:40]:
            line = entry if isinstance(entry, str) else json.dumps(entry)
            print(f"    {line[:120]}")
            report_lines.append(f"  - `{line[:200]}`")
        if len(smb_hits) > 40:
            report_lines.append(f"  - … and {len(smb_hits) - 40} more")
    except RuntimeError as e:
        print(f"  ERROR: {e}")
        report_lines.append(f"- ERROR: `{e}`")

    print("\n[ Object endpoints ]")
    report_lines += [
        "",
        "## Object endpoints",
        "",
        "| Endpoint | Status | Count | Sample fields |",
        "|----------|--------|-------|---------------|",
    ]
    client_endpoints = []
    for endpoint in OBJECT_ENDPOINTS:
        try:
            objects = normalize_list_response(api_request("GET", endpoint))
            sample_keys = list(objects[0].keys())[:8] if objects else []
            print(f"  {endpoint:<22} {len(objects)} object(s)  keys={sample_keys}")
            report_lines.append(f"| `{endpoint}` | OK | {len(objects)} | `{sample_keys}` |")
            if any(token in endpoint for token in ("client", "smb", "host", "monitored")) and objects:
                client_endpoints.append((endpoint, objects[:3]))
        except RuntimeError as e:
            msg = str(e)
            code = "HTTP error" if "HTTP" in msg else "error"
            print(f"  {endpoint:<22} {code}: {msg[:80]}")
            report_lines.append(f"| `{endpoint}` | {code} | - | `{msg[:120]}` |")

    print("\n[ Drill object types ]")
    for mode, cfg in _DRILL_CFG.items():
        print(f"  {mode:<8} {cfg['object_type']:<8} {cfg['endpoint']}")

    report_lines += ["", "## Client IP scoping (Swagger + topn)", ""]
    report_lines.append(
        "- Primary client ranking: `GET /monitors/topn/` → `data.client` dimension"
    )
    report_lines.append(
        "- Live sessions: `GET /clusters/list_smb_client_connections/?client_ip=`"
    )
    report_lines.append("- Monitored client IPs: `GET /monitoredhosts/`")
    if client_endpoints:
        for endpoint, samples in client_endpoints:
            report_lines.append(f"### `{endpoint}` sample objects")
            for obj in samples:
                ip_fields = {
                    key: obj[key] for key in obj
                    if re.search(r"ip|addr|host|client|name|guid|title", key, re.I)
                }
                report_lines.append(
                    f"- id={obj.get('id')} fields={json.dumps(ip_fields)[:300]}"
                )
    else:
        report_lines.append("- No legacy `/smbclients/` list endpoint on this build.")

    print("\n[ Swagger SMB/session probes ]")
    report_lines += ["", "## Swagger probes", ""]
    for endpoint, params in SWAGGER_PROBE_CALLS:
        query = "&".join(f"{k}={urllib.parse.quote(str(v), safe=',')}" for k, v in params.items())
        path = f"{endpoint}?{query}" if query else endpoint
        try:
            data = api_request("GET", path)
            if isinstance(data, dict):
                keys = list(data.keys())[:8]
                detail = f"keys={keys}"
                if "data" in data and isinstance(data["data"], dict):
                    detail += f" dimensions={list(data['data'].keys())[:6]}"
                if "connections" in data:
                    detail += f" connections={len(data.get('connections') or [])}"
            elif isinstance(data, list):
                detail = f"{len(data)} object(s)"
            else:
                detail = str(data)[:80]
            print(f"  {path:<55} OK {detail}")
            report_lines.append(f"- `{path}`: OK - {detail}")
        except RuntimeError as e:
            print(f"  {path:<55} error: {str(e)[:60]}")
            report_lines.append(f"- `{path}`: error - `{str(e)[:120]}`")

    print("\n[ Monitor probes ]")
    report_lines += ["", "## Monitor probes", ""]
    for label, props in (
        ("smbcommon_headline", build_headline_monitor_props()),
        ("interop_only", list(_INTEROP_METRICS)),
        ("proto_smb_legacy", PROTO_PROBE_PROPS),
        ("smb_cmds_batch1", smb_command_props()[:20]),
        ("smb_cmds_batch2", smb_command_props()[20:40]),
    ):
        status, detail = probe_monitor(CLUSTER_ID, props, label)
        print(f"  {label:<22} {status}: {detail}")
        report_lines.append(f"- **{label}:** `{status}` - {detail}")

    if SMB_PER_COMMAND_EXPORTED is False:
        print("\n  Note: SmbMetrics per-command props are not exported on this build.")
        print("        Phase 2-3 will use SMBCommon aggregate panels (see SMB_PHASE0_RESULTS.md).")
        report_lines.append("")
        report_lines.append(
            "- **SmbMetrics verdict:** not exported - use SMBCommon aggregate proxy panels"
        )

    view_props = [
        "ViewMetrics,read_iops__rate", "ViewMetrics,write_iops__rate",
        "ViewMetrics,read_md_iops__rate", "ViewMetrics,write_md_iops__rate",
        "ViewMetrics,read_md_latency__avg", "ViewMetrics,write_md_latency__avg",
        "ViewMetrics,qos_failures", "ViewMetrics,qos_wait_for_budget_time__rate",
    ]
    try:
        views = normalize_list_response(api_request("GET", "/views/"))
        if views:
            payload = {
                "name": f"adhoc_opstat_smb_discover_view_{int(time.time())}",
                "object_type": "view",
                "object_ids": [views[0]["id"]],
                "time_frame": "10m",
                "prop_list": view_props,
            }
            created = api_request("POST", "/monitors/", payload)
            monitor_id = created.get("id")
            api_request("GET", f"/monitors/{monitor_id}/query/")
            api_request("DELETE", f"/monitors/{monitor_id}/")
            print("  view_no_aggregation ok")
            report_lines.append("- **view_no_aggregation:** `ok`")
        else:
            print("  view_no_aggregation skipped (no views)")
            report_lines.append("- **view_no_aggregation:** skipped (no views)")
    except RuntimeError as e:
        print(f"  view_no_aggregation error: {e}")
        report_lines.append(f"- **view_no_aggregation:** `{e}`")

    if CLIENT_SCOPED:
        print(f"\n[ Client scope active ]")
        print(f"  Clients: {', '.join(CLIENT_IPS)} - topn + session probes filtered")

    if write_report_path:
        out_path = write_report_path
        if not os.path.isabs(out_path):
            out_path = os.path.join(_SCRIPT_DIR, out_path)
        with open(out_path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(report_lines) + "\n")
        print(f"\nReport written: {out_path}")

    print("\nDiscovery complete.")
    return 0


def main():
    """Entry point after init_config."""
    global HEADLINE_MONITOR_ID, CLUSTER_ID, CLUSTER_NAME

    if ARGS.discover_metrics:
        return discover_metrics()

    vast_common.install_signal_handlers(signal_handler)
    vast_common.register_atexit(cleanup)

    setup_keyboard()
    CLUSTER_ID, CLUSTER_NAME = get_current_cluster()
    _capture_cluster_os()
    HEADLINE_MONITOR_ID = create_monitor("headline", build_headline_monitor_props())
    try_create_smb_command_monitor()
    ensure_csv_file()

    fetch_monitor_query()
    render_screen()
    next_refresh = time.time() + REFRESH_SECONDS

    while True:
        chars = check_keypress()
        if chars:
            if "\x03" in chars or "q" in chars.lower():
                break
            if "c" in chars.lower():
                switch_drill_mode("cnode")
            elif "v" in chars.lower():
                switch_drill_mode("view")
            elif "t" in chars.lower():
                switch_drill_mode("tenant")
            elif "x" in chars.lower():
                exit_drill_mode()
            elif " " in chars:
                vast_common.guarded_poll(manual_refresh, render_screen)
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
    """Protocol handler invoked by opstat dispatch."""
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
