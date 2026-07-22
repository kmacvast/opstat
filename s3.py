#!/usr/bin/env python3
################################################################################
# Script:      s3.py
#
# Descr:       S3 object storage performance statistics for opstat. Polls
#              S3Common ProtoMetrics (with legacy S3 fallback) and optional
#              S3Metrics per-opcode counters/histograms when exported.
#
# Version:     0.1.2
# Author:      KMac
#
# Usage:
#   ./opstat --s3 --vms <VMS_IP>
#   ./opstat --s3 --discover-metrics --vms <VMS_IP>
#   ./opstat --s3 --buckets bucket1,bucket2 --tenants default --vms <VMS_IP>
#
# Controls:
#   Space  - Refresh immediately
#   c      - cNode drill-down
#   b      - Bucket / view drill-down
#   t      - Tenant drill-down
#   i      - VIP drill-down
#   x      - Exit drill-down
#   q      - Quit
################################################################################

import base64
import csv
import getpass
import io
import ipaddress
import json
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
from tui_layout import (
    display_width, format_fixed_number, format_scaled_metric, join_columns,
    pad_display, truncate_display, c, set_color, set_unicode, glyph_set,
    as_float, raw_bw_to_mb_sec, format_throughput_mbs,
    format_iops, format_block_size, format_os_release,
    _RST, _BOLD, _DIM, _GREEN, _YELLOW, _CYAN,
    _BRED, _BGREEN, _BYELLOW, _BCYAN, _BWHITE,
)

VERSION = "0.1.2"

DEFAULT_PORT = 443
DEFAULT_USER = "admin"
DEFAULT_REFRESH_SECONDS = 5
DEFAULT_API_TIME_FRAME = "10m"

_PROTO_S3_COMMON = "ProtoMetrics,proto_name=S3Common"
_PROTO_S3_LEGACY = "ProtoMetrics,proto_name=S3"
_PROTO_ACTIVE = _PROTO_S3_COMMON

METRICS_SOURCE = "S3Common"
S3_METRICS_EXPORTED = False

# S3Metrics counters (properties) confirmed via vast-exporter / vastpy.
S3_COUNTER_OPS = (
    "get_object", "put_object", "multi_part_upload", "multi_part_upload_fallback",
    "cmd_parse_failed", "cmd_not_supported", "cmd_errors",
    "bad_http_request", "bad_https_request",
)

# S3Metrics histogram ops (latency rate/avg).
S3_HISTOGRAM_OPS = (
    "get_service", "put_bucket", "delete_bucket", "get_bucket",
    "get_bucket_location", "head_bucket", "delete_object", "delete_objects",
    "head_object", "put_bucket_acl", "put_object_acl", "get_bucket_acl",
    "get_object_acl", "put_object_copy", "initiate_mpu", "complete_mpu",
)

# S3 REST call rows (order = troubleshooting priority). Labels are API names.
S3_OPCODES = (
    ("GET", "data", "get_object"),
    ("PUT", "data", "put_object"),
    ("DELETE", "data", "delete_object"),
    ("HEAD", "metadata", "head_object"),
    ("LIST", "metadata", "get_bucket"),
    ("MULTIPART", "data", "multi_part_upload"),
    ("INIT_MPU", "data", "initiate_mpu"),
    ("COMPLETE_MPU", "data", "complete_mpu"),
)

_OPCODE_COL = {"label": 14, "iops": 11, "throughput": 12, "size": 9, "latency": 10, "source": 10}

OBJECT_ENDPOINTS = (
    "/cnodes/", "/views/", "/tenants/", "/vips/",
)

# View/tenant drill scopes use ViewMetrics/TenantMetrics (S3Common is cluster/cnode/vip).
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

_BUCKET_VIEW_READ_IOPS = "BucketViewMetrics,read_iops__rate"
_BUCKET_VIEW_WRITE_IOPS = "BucketViewMetrics,write_iops__rate"
_BUCKET_VIEW_READ_BW = "BucketViewMetrics,read_bw__rate"
_BUCKET_VIEW_WRITE_BW = "BucketViewMetrics,write_bw__rate"

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

_DRILL_CFG = {
    "cnode": {
        "label": "CNODE",
        "object_type": "cnode",
        "endpoint": "/cnodes/",
        "name_fields": ("name", "hostname", "mgmt_ip"),
        "no_aggregation": False,
    },
    "bucket": {
        "label": "BUCKET",
        "object_type": "view",
        "endpoint": "/views/",
        "name_fields": ("path", "title", "name", "bucket"),
        "no_aggregation": True,
    },
    "tenant": {
        "label": "TENANT",
        "object_type": "tenant",
        "endpoint": "/tenants/",
        "name_fields": ("name",),
        "no_aggregation": False,
    },
    "vip": {
        "label": "VIP",
        "object_type": "vip",
        "endpoint": "/vips/",
        # Prefer human names; never lead with internal 192.168.* IPs.
        "name_fields": ("name", "vippool", "pool", "title", "hostname", "ip", "address"),
        "no_aggregation": False,
    },
}

_MAX_DRILL_OBJECTS = 8
_DRILL_PROBE_LIMIT = 32
# Bucket/VIP drill: per-REST-call rates + auto-scaled bandwidth.
_DRILL_COL = {
    "name": 18, "get": 9, "put": 9, "delete": 9, "list": 9,
    "bw": 11, "lat": 8, "top": 8, "pct": 5,
}

HEALTH_PANEL_TITLE = "S3 HEALTH & WORKLOAD"
REST_PANEL_TITLE = "S3 REST OPERATIONS"

DATA_OPS = [("get", "GET"), ("put", "PUT")]
METADATA_OPS = [
    ("md_total", "METADATA"),
    ("rd_md", "RD METADATA"),
    ("wr_md", "WR METADATA"),
]

_COL_SEP = "  "

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
S3_METRICS_MONITOR_ID = None
BUCKET_SCOPED = False
BUCKET_NAMES = []
TENANT_SCOPED = False
TENANT_NAMES = []
LAST_ROWS = {}
PREV_ROWS = {}
LAST_SAMPLE = "-"
DRILL_MODE = None
DRILL_OBJECTS = []
DRILL_MONITORS = []
LAST_DRILL_ROWS = []
DRILL_ERROR = None
DRILL_STATUS = None
VIP_TOPN = None
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
    global _COLOR

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
    HEADERS["User-Agent"] = f"opstat/s3/{VERSION}"
    vast_common.configure_connection(BASE_URL, HEADERS, SSL_CTX)
    log_path = vast_api_log.configure(
        getattr(args, "log_api_calls", False), "s3", VMS, PORT,
    )
    if log_path:
        print(f"API call logging enabled: {log_path}", file=sys.stderr, flush=True)
    om_path = openmetrics.configure(
        getattr(args, "export_openmetrics", False),
        getattr(args, "openmetrics_file", None),
        "s3", VMS,
    )
    if om_path:
        print(f"OpenMetrics export enabled: {om_path}", file=sys.stderr, flush=True)
    _COLOR = sys.stdout.isatty() and not args.no_color
    set_color(_COLOR)
    set_unicode(_UTF8)
    CSV_FILE = getattr(args, "csv", None)
    RUN_STARTED_AT = datetime.now()
    configure_bucket_scope(args)
    configure_tenant_scope(args)


def configure_bucket_scope(args):
    """Parse --buckets; filters bucket/view drill candidates by name/path/title."""
    global BUCKET_SCOPED, BUCKET_NAMES
    raw = getattr(args, "buckets", None)
    if not raw:
        BUCKET_SCOPED = False
        BUCKET_NAMES = []
        return
    BUCKET_NAMES = [s.strip() for s in raw.split(",") if s.strip()]
    BUCKET_SCOPED = bool(BUCKET_NAMES)


def configure_tenant_scope(args):
    """Parse --tenants; filters tenant drill candidates by name."""
    global TENANT_SCOPED, TENANT_NAMES
    raw = getattr(args, "tenants", None)
    if not raw:
        TENANT_SCOPED = False
        TENANT_NAMES = []
        return
    TENANT_NAMES = [s.strip() for s in raw.split(",") if s.strip()]
    TENANT_SCOPED = bool(TENANT_NAMES)


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


def _common_fqn(suffix, proto=None):
    return f"{proto or _PROTO_ACTIVE},{suffix}"


def _first_positive(*values):
    for value in values:
        parsed = as_float(value)
        if parsed is not None and parsed > 0:
            return parsed
    return None


def format_latency_ms(us, active=True):
    """Format latency for S3 TUI: always milliseconds (never µs)."""
    if not active:
        return "-", None
    us = as_float(us)
    if us is None or us <= 0:
        return "-", None
    return f"{us / 1000.0:.2f} ms", us


def fmt_delta(value, precision=2):
    if value is None:
        return "-"
    sign = "+" if value > 0 else ""
    return f"{sign}{value:,.{precision}f}"


def _is_192_168_ip(value):
    """True when *value* is an IPv4 address in 192.168.0.0/16."""
    if value is None:
        return False
    token = str(value).strip().split()[0].split("/")[0]
    try:
        ip = ipaddress.ip_address(token)
    except ValueError:
        return False
    return isinstance(ip, ipaddress.IPv4Address) and ip in ipaddress.ip_network("192.168.0.0/16")


def _vip_display_name(obj):
    """Human VIP label that never surfaces 192.168.* addresses."""
    for field in ("name", "vippool", "pool", "title", "hostname"):
        val = obj.get(field)
        if val is None:
            continue
        text = str(val).strip()
        if text and not _is_192_168_ip(text):
            return text
    for field in ("ip", "address", "vip"):
        val = obj.get(field)
        if val is None:
            continue
        text = str(val).strip()
        if text and not _is_192_168_ip(text):
            return text
    pool = obj.get("vippool") or obj.get("pool") or "vip"
    return f"{pool}-{obj.get('id', '?')}"


def _vip_objects_for_drill(objects):
    """Filter VIP list and attach safe display names (hide 192.168.*)."""
    selected = []
    for obj in objects:
        if "id" not in obj:
            continue
        # Skip VIPs whose only identifiable address is 192.168.* with no other label.
        name = _vip_display_name(obj)
        ip_val = obj.get("ip") or obj.get("address")
        if _is_192_168_ip(ip_val) and name == str(ip_val).strip():
            continue
        selected.append({"id": obj["id"], "name": name, "_raw": obj})
    return selected


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


def build_headline_monitor_props(proto=None):
    """S3Common (or legacy S3) cluster monitor props."""
    fqn = lambda suffix: _common_fqn(suffix, proto)
    return [
        fqn("iops"), fqn("bw"),
        fqn("rd_iops"), fqn("wr_iops"),
        fqn("rd_bw"), fqn("wr_bw"),
        fqn("md_iops"), fqn("rd_md_iops"), fqn("wr_md_iops"),
        fqn("read_latency__avg"), fqn("write_latency__avg"),
        fqn("read_latency__rate"), fqn("write_latency__rate"),
        fqn("read_size__avg"), fqn("write_size__avg"),
        fqn("rd_latency"), fqn("wr_latency"),
    ]


def s3_metric_fqns(op):
    """Candidate S3Metrics FQNs for an op (histogram and counter forms)."""
    return [
        f"S3Metrics,{op}__rate",
        f"S3Metrics,{op}__avg",
        f"S3Metrics,{op}_latency__rate",
        f"S3Metrics,{op}_latency__avg",
    ]


def build_s3_metrics_props():
    """All S3Metrics counter + histogram property candidates for probing."""
    props = []
    seen = set()
    for op in S3_COUNTER_OPS + S3_HISTOGRAM_OPS:
        for fqn in s3_metric_fqns(op):
            if fqn not in seen:
                seen.add(fqn)
                props.append(fqn)
        # Counters may also appear as bare property names without suffixes.
        bare = f"S3Metrics,{op}"
        if bare not in seen:
            seen.add(bare)
            props.append(bare)
    return props


def build_drill_prop_list(mode):
    """Scope-aware monitor props for S3 drill-down.

    Bucket (view) monitors use ViewMetrics only. VMS rejects mixing
    ViewMetrics and BucketViewMetrics in one monitor; BucketViewMetrics
    is probed separately during --discover-metrics.
    """
    if mode == "bucket":
        return [
            _VIEW_READ_IOPS, _VIEW_WRITE_IOPS,
            _VIEW_READ_MD, _VIEW_WRITE_MD,
            _VIEW_READ_LAT, _VIEW_WRITE_LAT,
            _VIEW_READ_BW, _VIEW_WRITE_BW,
            _VIEW_READ_MD_LAT, _VIEW_WRITE_MD_LAT,
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
    """Minimal props for one-shot batch ranking of bucket/tenant candidates."""
    if mode == "bucket":
        return [_VIEW_READ_IOPS, _VIEW_WRITE_IOPS, _VIEW_READ_MD, _VIEW_WRITE_MD]
    if mode == "tenant":
        return [
            _TENANT_READ_IOPS, _TENANT_WRITE_IOPS,
            _TENANT_READ_MD, _TENANT_WRITE_MD,
        ]
    return build_drill_prop_list(mode)


def _is_batch_drill_mode(mode=None):
    mode = mode or DRILL_MODE
    return mode in ("bucket", "tenant")


def _normalize_object_id(value):
    """Coerce VMS object_id values for reliable batch-monitor slicing."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


def _bucket_field_tokens(obj):
    tokens = []
    for field in ("name", "path", "title", "bucket", "bucket_name"):
        val = obj.get(field)
        if val is not None and str(val).strip():
            tokens.append(str(val).strip().lower())
    return tokens


def _bucket_matches_scope(obj):
    """Return True when view/bucket object matches --buckets filter (or scope off)."""
    if not BUCKET_SCOPED or not BUCKET_NAMES:
        return True
    tokens = _bucket_field_tokens(obj)
    if not tokens:
        return False
    for wanted in BUCKET_NAMES:
        w = wanted.lower()
        for token in tokens:
            if w == token or w in token or token in w:
                return True
    return False


def _tenant_matches_scope(obj):
    """Return True when tenant object matches --tenants filter (or scope off)."""
    if not TENANT_SCOPED or not TENANT_NAMES:
        return True
    name = str(obj.get("name") or "").strip().lower()
    if not name:
        return False
    for wanted in TENANT_NAMES:
        w = wanted.lower()
        if w == name or w in name or name in w:
            return True
    return False


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
    name = f"adhoc_opstat_s3_{name_suffix}_{int(time.time())}"
    return vast_common.create_monitor_raw(
        api_request, name, prop_list, object_type, object_ids,
        time_frame=API_TIME_FRAME, no_aggregation=no_aggregation,
    )


def create_monitor(name_suffix, prop_list):
    return _create_monitor_raw(name_suffix, prop_list, "cluster", [CLUSTER_ID])


def create_headline_monitor():
    """Create S3Common headline monitor; fall back to legacy S3 ProtoMetrics."""
    global METRICS_SOURCE, _PROTO_ACTIVE
    try:
        _PROTO_ACTIVE = _PROTO_S3_COMMON
        monitor_id = create_monitor("headline", build_headline_monitor_props(_PROTO_S3_COMMON))
        METRICS_SOURCE = "S3Common"
        return monitor_id
    except RuntimeError:
        _PROTO_ACTIVE = _PROTO_S3_LEGACY
        monitor_id = create_monitor("headline_legacy", build_headline_monitor_props(_PROTO_S3_LEGACY))
        METRICS_SOURCE = "S3"
        return monitor_id


def try_create_s3_metrics_monitor():
    """Probe S3Metrics export; keep monitor when prop_list includes S3Metrics."""
    global S3_METRICS_EXPORTED, S3_METRICS_MONITOR_ID
    S3_METRICS_EXPORTED = False
    S3_METRICS_MONITOR_ID = None
    monitor_id = None
    props = build_s3_metrics_props()
    try:
        monitor_id = _create_monitor_raw(
            "s3_metrics", props, "cluster", [CLUSTER_ID], no_aggregation=False,
        )
        result = api_request("GET", f"/monitors/{monitor_id}/query/")
        returned = set(result.get("prop_list", []) if isinstance(result, dict) else [])
        if any(str(p).startswith("S3Metrics,") for p in returned):
            S3_METRICS_MONITOR_ID = monitor_id
            S3_METRICS_EXPORTED = True
            return
        delete_monitor(monitor_id)
    except RuntimeError:
        delete_monitor(monitor_id)


def delete_monitor(monitor_id):
    vast_common.delete_monitor(api_request, monitor_id)


def _result_parts(result):
    prop_list = result.get("prop_list", []) if isinstance(result, dict) else []
    data = result.get("data", []) if isinstance(result, dict) else []
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


def _component_ops_total(meta, data_rows):
    """Sum GET + PUT + MD components - authoritative mix denominator."""
    get_ops = next((as_float(r["ops_sec"]) or 0 for r in data_rows if r["key"] == "get"), 0)
    put_ops = next((as_float(r["ops_sec"]) or 0 for r in data_rows if r["key"] == "put"), 0)
    md_ops = as_float(meta.get("md_iops")) or 0
    return get_ops + put_ops + md_ops


def build_rows_from_results(headline_result):
    """Map S3Common/S3 monitor sample to panel rows."""
    global METRICS_SOURCE
    values, sample = _latest_row(headline_result)

    get_ops = _metric(values, "rd_iops")
    put_ops = _metric(values, "wr_iops")
    get_lat = _first_positive(
        _metric(values, "read_latency__avg"),
        _metric(values, "read_latency__rate"),
        _metric(values, "rd_latency"),
    )
    put_lat = _first_positive(
        _metric(values, "write_latency__avg"),
        _metric(values, "write_latency__rate"),
        _metric(values, "wr_latency"),
    )
    get_bw = raw_bw_to_mb_sec(_metric(values, "rd_bw"))
    put_bw = raw_bw_to_mb_sec(_metric(values, "wr_bw"))
    get_size = _metric(values, "read_size__avg")
    put_size = _metric(values, "write_size__avg")

    md_iops = _metric(values, "md_iops")
    rd_md = _metric(values, "rd_md_iops")
    wr_md = _metric(values, "wr_md_iops")
    get_val = as_float(get_ops) or 0
    put_val = as_float(put_ops) or 0
    md_val = as_float(md_iops) or 0
    component_total = get_val + put_val + md_val
    total_iops = component_total if component_total > 0 else _metric(values, "iops")
    total_bw_mbs = _first_positive(
        raw_bw_to_mb_sec(_metric(values, "bw")),
        ((get_bw or 0) + (put_bw or 0)) or None,
    )

    active = any(
        (as_float(v) or 0) > 0
        for v in (get_ops, put_ops, md_iops, total_iops)
    )
    if active:
        METRICS_SOURCE = "S3Common" if _PROTO_ACTIVE == _PROTO_S3_COMMON else "S3"
    else:
        METRICS_SOURCE = "idle"

    def _data_metric(key):
        if key == "get":
            return {
                "ops_sec": get_ops, "avg_us": get_lat,
                "bw_mbs": get_bw, "avg_io_bytes": get_size,
            }
        return {
            "ops_sec": put_ops, "avg_us": put_lat,
            "bw_mbs": put_bw, "avg_io_bytes": put_size,
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
    }
    return {
        "data": data_rows,
        "metadata": metadata_rows,
        "opcodes": [],
        "meta": meta,
    }, sample


def _s3_op_rate_and_lat(result, op):
    """Resolve ops/s and latency for an S3Metrics op across FQN forms.

    Instantaneous ``*__rate`` / ``*_latency__rate`` fields are taken from the
    latest sample as-is. Bare counters (``S3Metrics,{op}``) are cumulative and
    must be converted via sample deltas; never treat the raw counter as ops/s.
    """
    if not result:
        return None, None
    rate_fqns = (
        f"S3Metrics,{op}__rate",
        f"S3Metrics,{op}_latency__rate",
    )
    counter_fqn = f"S3Metrics,{op}"
    avg_candidates = (
        f"S3Metrics,{op}__avg",
        f"S3Metrics,{op}_latency__avg",
    )

    values, _sample = _latest_row(result)
    ops = None
    for fqn in rate_fqns:
        instant = as_float(values.get(fqn))
        if instant is not None and instant > 0:
            ops = instant
            break
    if ops is None:
        # Cumulative counter only: delta over the monitor window.
        ops = _delta_rate_from_samples(result, counter_fqn)
        if ops is not None and ops <= 0:
            ops = None

    lat = None
    for avg_fqn in avg_candidates:
        lat = as_float(values.get(avg_fqn))
        if lat is not None and lat > 0:
            break
        lat = None
    if lat is None:
        for avg_fqn in avg_candidates:
            for rate_fqn in rate_fqns + (counter_fqn,):
                lat = _avg_from_sum_count_deltas(result, avg_fqn, rate_fqn)
                if lat is not None:
                    break
            if lat is not None:
                break
    return ops, lat


def _build_opcode_rows_from_s3metrics(result):
    """Native per-opcode rows when VMS exports S3Metrics."""
    if not result:
        return []
    rows = []
    for label, category, cmd in S3_OPCODES:
        ops, lat = _s3_op_rate_and_lat(result, cmd)
        rows.append({
            "label": label,
            "category": category,
            "cmd": cmd,
            "ops_sec": ops if ops and ops > 0 else None,
            "avg_us": lat,
            "bw_mbs": None,
            "avg_io_bytes": None,
            "source": "S3METRICS",
            "hint": False,
        })
    return rows


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


def _s3metrics_looks_like_cumulative(native_rows, meta):
    """Reject S3Metrics rates that dwarf S3Common (classic cumulative misuse)."""
    native_total = sum(as_float(r.get("ops_sec")) or 0 for r in native_rows)
    if native_total <= 0:
        return False
    common_total = as_float(meta.get("total_iops")) or 0
    if common_total <= 0:
        # No S3Common baseline; still reject absurd absolute rates.
        return native_total > 1_000_000
    return native_total > max(common_total * 20.0, common_total + 10_000)


def build_opcode_breakdown_rows(data_rows, metadata_rows, meta, s3_metrics_result):
    """Build the unified S3 REST operations table (one row per call name)."""
    if s3_metrics_result and S3_METRICS_EXPORTED:
        native = _build_opcode_rows_from_s3metrics(s3_metrics_result)
        # Attach S3Common GET/PUT throughput + latency onto matching native rows.
        data_by_key = {r["key"]: r for r in data_rows}
        for row in native:
            if row["cmd"] == "get_object" and data_by_key.get("get"):
                src = data_by_key["get"]
                if row.get("bw_mbs") is None:
                    row["bw_mbs"] = src.get("bw_mbs")
                if row.get("avg_io_bytes") is None:
                    row["avg_io_bytes"] = src.get("avg_io_bytes")
                if row.get("avg_us") is None:
                    row["avg_us"] = src.get("avg_us")
            if row["cmd"] == "put_object" and data_by_key.get("put"):
                src = data_by_key["put"]
                if row.get("bw_mbs") is None:
                    row["bw_mbs"] = src.get("bw_mbs")
                if row.get("avg_io_bytes") is None:
                    row["avg_io_bytes"] = src.get("avg_io_bytes")
                if row.get("avg_us") is None:
                    row["avg_us"] = src.get("avg_us")
        total = sum(as_float(r["ops_sec"]) or 0 for r in native)
        if total > 0 and not _s3metrics_looks_like_cumulative(native, meta):
            for row in native:
                ops = as_float(row["ops_sec"]) or 0
                row["pct"] = (ops / total * 100) if total > 0 else None
            return _visible_opcode_rows(native)

    data_by_key = {r["key"]: r for r in data_rows}
    rd_md = as_float(meta.get("rd_md_iops"))
    wr_md = as_float(meta.get("wr_md_iops"))
    md_total = as_float(meta.get("md_iops"))
    rows = []

    get_src = data_by_key.get("get")
    if get_src and (as_float(get_src.get("ops_sec")) or 0) > 0:
        rows.append({
            "label": "GET",
            "category": "data",
            "cmd": "get_object",
            "ops_sec": get_src.get("ops_sec"),
            "avg_us": get_src.get("avg_us"),
            "bw_mbs": get_src.get("bw_mbs"),
            "avg_io_bytes": get_src.get("avg_io_bytes"),
            "source": "MEASURED",
            "hint": False,
        })
    put_src = data_by_key.get("put")
    if put_src and (as_float(put_src.get("ops_sec")) or 0) > 0:
        rows.append({
            "label": "PUT",
            "category": "data",
            "cmd": "put_object",
            "ops_sec": put_src.get("ops_sec"),
            "avg_us": put_src.get("avg_us"),
            "bw_mbs": put_src.get("bw_mbs"),
            "avg_io_bytes": put_src.get("avg_io_bytes"),
            "source": "MEASURED",
            "hint": False,
        })

    # Map S3Common metadata components to REST call names (no opaque METADATA row).
    delete_ops = wr_md if wr_md and wr_md > 0 else None
    list_ops = rd_md if rd_md and rd_md > 0 else None
    if delete_ops is None and list_ops is None and md_total and md_total > 0:
        # Only aggregate md_iops available: attribute to LIST (common listing load).
        list_ops = md_total

    if delete_ops:
        rows.append({
            "label": "DELETE",
            "category": "metadata",
            "cmd": "delete_object",
            "ops_sec": delete_ops,
            "avg_us": None,
            "bw_mbs": None,
            "avg_io_bytes": None,
            "source": "AGGREGATE",
            "hint": False,
        })
    if list_ops:
        rows.append({
            "label": "LIST",
            "category": "metadata",
            "cmd": "get_bucket",
            "ops_sec": list_ops,
            "avg_us": None,
            "bw_mbs": None,
            "avg_io_bytes": None,
            "source": "AGGREGATE",
            "hint": False,
        })

    active_ops = sum(as_float(r["ops_sec"]) or 0 for r in rows)
    for row in rows:
        ops = as_float(row.get("ops_sec")) or 0
        row["pct"] = (ops / active_ops * 100) if active_ops > 0 else None
    return _visible_opcode_rows(rows)


def s3_workload_mix(meta, data_rows, opcodes=None):
    """Return (get, put, delete, list_head) percentages from REST call rates."""
    opcodes = opcodes or []
    by_label = {}
    for row in opcodes:
        label = (row.get("label") or "").upper()
        ops = as_float(row.get("ops_sec")) or 0
        if ops > 0:
            by_label[label] = by_label.get(label, 0.0) + ops

    if by_label:
        get_ops = by_label.get("GET", 0.0)
        put_ops = by_label.get("PUT", 0.0)
        delete_ops = by_label.get("DELETE", 0.0)
        list_ops = by_label.get("LIST", 0.0) + by_label.get("HEAD", 0.0)
        for label, ops in by_label.items():
            if label in ("GET", "PUT", "DELETE", "LIST", "HEAD"):
                continue
            # Multipart and other calls roll into list/head bucket for mix bars.
            list_ops += ops
    else:
        get_ops = next((as_float(r["ops_sec"]) or 0 for r in data_rows if r["key"] == "get"), 0)
        put_ops = next((as_float(r["ops_sec"]) or 0 for r in data_rows if r["key"] == "put"), 0)
        delete_ops = as_float(meta.get("wr_md_iops")) or 0
        list_ops = as_float(meta.get("rd_md_iops")) or 0
        if delete_ops <= 0 and list_ops <= 0:
            list_ops = as_float(meta.get("md_iops")) or 0

    total = get_ops + put_ops + delete_ops + list_ops
    if total <= 0:
        return 0.0, 0.0, 0.0, 0.0
    return (
        get_ops / total * 100,
        put_ops / total * 100,
        delete_ops / total * 100,
        list_ops / total * 100,
    )


def classify_s3_workload(meta, data_rows, opcodes=None):
    """Return a human-readable S3 workload description."""
    get_pct, put_pct, delete_pct, list_pct = s3_workload_mix(meta, data_rows, opcodes)
    total = get_pct + put_pct + delete_pct + list_pct
    if total < 0.5:
        return "Idle / no S3 load"

    get_io = next((as_float(r.get("avg_io_bytes")) for r in data_rows if r["key"] == "get"), None)
    size_tag = ""
    if get_io:
        if get_io < 8_192:
            size_tag = "small-object "
        elif get_io >= 1_048_576:
            size_tag = "large-object "

    other_pct = delete_pct + list_pct
    if other_pct >= 60:
        if delete_pct >= list_pct:
            return f"{size_tag}DELETE-heavy S3 workload"
        return f"{size_tag}LIST/HEAD-heavy S3 workload"
    if get_pct > put_pct * 2:
        return f"{size_tag}GET-biased S3 workload"
    if put_pct > get_pct * 2:
        return f"{size_tag}PUT-biased S3 workload"
    if other_pct > 25:
        return f"{size_tag}mixed data + LIST/DELETE S3 workload"
    return f"{size_tag}balanced S3 workload"


def s3_health_label(total_ops, combined_latency_us):
    """Return (ACTIVE|IDLE|HOT, color) badge for the health panel."""
    if total_ops is None or total_ops < 0.5:
        return "IDLE", _DIM
    if combined_latency_us is not None and combined_latency_us > 10_000:
        return "HOT", _BRED
    if total_ops > 10_000 or (
        combined_latency_us is not None and combined_latency_us > 5_000
    ):
        return "HOT", _YELLOW
    return "ACTIVE", _BGREEN


def _all_panel_rows(snapshot):
    return snapshot.get("data", []) + snapshot.get("metadata", [])


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
            ("data", snapshot.get("data") or []),
            ("metadata", snapshot.get("metadata") or []),
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


def _render_health_panel(snapshot, deltas, width):
    meta = snapshot["meta"]
    data_rows = snapshot["data"]
    opcodes = snapshot.get("opcodes") or []
    total_ops = as_float(meta.get("total_iops")) or 0
    combined_lat = as_float(meta.get("latency_us"))
    total_bw_mbs = as_float(meta.get("total_bw_mbs"))
    get_pct, put_pct, delete_pct, list_pct = s3_workload_mix(meta, data_rows, opcodes)
    health_lbl, health_color = s3_health_label(total_ops, combined_lat)
    workload_type = classify_s3_workload(meta, data_rows, opcodes)
    ops_delta, bw_delta, lat_deltas = cluster_delta_summary(deltas)

    print(box_top(HEALTH_PANEL_TITLE, width))
    ops_s = c(f"{total_ops:,.2f} ops/s" if total_ops else "- ops/s", _BWHITE)
    lat_text, _ = format_latency_ms(combined_lat)
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
    print(box_row(c(f"{'GET':<10}", _DIM) + workload_bar(get_pct, 22, _BGREEN), width))
    print(box_row(c(f"{'PUT':<10}", _DIM) + workload_bar(put_pct, 22, _BYELLOW), width))
    print(box_row(c(f"{'DELETE':<10}", _DIM) + workload_bar(delete_pct, 22, _CYAN), width))
    print(box_row(c(f"{'LIST/HEAD':<10}", _DIM) + workload_bar(list_pct, 22, _BCYAN), width))
    if deltas:
        parts = []
        if ops_delta is not None and abs(ops_delta) >= 0.001:
            parts.append(delta_arrow(ops_delta) + " " + c(fmt_delta(ops_delta, 2) + " ops/s", _GREEN))
        if bw_delta is not None and abs(bw_delta) >= 0.001:
            parts.append(delta_arrow(bw_delta) + " " + c(fmt_delta(bw_delta, 2) + " MB/s", _CYAN))
        if lat_deltas:
            worst = max(lat_deltas, key=lambda x: abs(x[1]))
            # worst[1] is still microseconds; display as ms.
            lat_ms_delta = worst[1] / 1000.0
            parts.append(
                delta_arrow_lat(worst[1])
                + " " + c(f"Lat {fmt_delta(lat_ms_delta, 2)} ms [{worst[0]}]", _YELLOW)
            )
        if parts:
            print(box_row(c("Delta ", _DIM) + "   ".join(parts), width))
    print(box_bottom(width))


def _opcode_source_cell(source):
    if source == "MEASURED":
        return c(pad_display("MEASURED", _OPCODE_COL["source"], ">"), _BGREEN)
    if source == "S3METRICS":
        return c(pad_display("S3METRICS", _OPCODE_COL["source"], ">"), _BGREEN)
    if source == "AGGREGATE":
        return c(pad_display("AGGREGATE", _OPCODE_COL["source"], ">"), _CYAN)
    return c(pad_display("N/A", _OPCODE_COL["source"], ">"), _DIM)


def _opcode_row_cells(row):
    w = _OPCODE_COL
    ops = as_float(row.get("ops_sec"))
    active = ops is not None and ops > 0
    label = (row.get("label") or "").upper()
    if label == "GET":
        label_color = _BCYAN
    elif label == "PUT":
        label_color = _BYELLOW
    elif active:
        label_color = _BWHITE
    else:
        label_color = _DIM
    lat_text, lat_us = format_latency_ms(row.get("avg_us"))
    lat_color = _BRED if (lat_us or 0) > 10_000 else _YELLOW if (lat_us or 0) > 1_000 else _BGREEN
    bw_text, _ = format_throughput_mbs(row.get("bw_mbs"))
    size_text, _ = format_block_size(row.get("avg_io_bytes"))
    return join_columns([
        _label_cell(row["label"], w["label"], label_color),
        _metric_cell(format_iops(ops), w["iops"], _GREEN) if active else _dash(w["iops"]),
        _metric_cell(bw_text, w["throughput"], _CYAN) if row.get("bw_mbs") else _dash(w["throughput"]),
        _metric_cell(size_text, w["size"], _CYAN) if row.get("avg_io_bytes") else _dash(w["size"]),
        _metric_cell(lat_text, w["latency"], lat_color) if lat_us else _dash(w["latency"]),
        _opcode_source_cell(row.get("source")),
    ], _COL_SEP)


def _render_rest_panel(snapshot, width):
    """Unified S3 REST OPERATIONS panel (one row per call name)."""
    rows = _visible_opcode_rows(snapshot.get("opcodes") or [])
    titles = [
        ("S3 Call", "label", "<"), ("Ops/s", "iops", ">"), ("Throughput", "throughput", ">"),
        ("Avg Size", "size", ">"), ("Latency", "latency", ">"), ("Source", "source", ">"),
    ]
    print(box_top(REST_PANEL_TITLE, width))
    if not rows:
        print(box_row(c("No active S3 REST calls this refresh", _DIM), width))
        print(box_bottom(width))
        return

    hdr_cells = []
    for title, key, align in titles:
        hdr_cells.append(c(pad_display(title, _OPCODE_COL[key], align), _BOLD))
    print(box_row(join_columns(hdr_cells, _COL_SEP), width))
    print(box_sep(width))
    for row in rows:
        print(box_row(_opcode_row_cells(row), width))

    if S3_METRICS_EXPORTED:
        footer = "Per-call S3Metrics (native counters/histograms)"
    else:
        footer = (
            "MEASURED: S3Common GET/PUT · AGGREGATE: DELETE←wr_md, LIST←rd_md "
            f"(source {METRICS_SOURCE})"
        )
    print(box_row(c(footer, _DIM), width))
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
    """Indexes of ViewMetrics / BucketViewMetrics rate props used for row selection."""
    return [
        prop_idx[p] for p in (
            _VIEW_READ_IOPS, _VIEW_WRITE_IOPS,
            _VIEW_READ_MD, _VIEW_WRITE_MD,
            _VIEW_READ_LAT, _VIEW_WRITE_LAT,
            _VIEW_READ_BW, _VIEW_WRITE_BW,
            _BUCKET_VIEW_READ_IOPS, _BUCKET_VIEW_WRITE_IOPS,
            _BUCKET_VIEW_READ_BW, _BUCKET_VIEW_WRITE_BW,
        )
        if p in prop_idx
    ]


def _view_values_from_result(result):
    """Pick the newest ViewMetrics row with non-null rates."""
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


def _ns_avg_to_us(value):
    """Convert ViewMetrics / TenantMetrics latency averages to microseconds.

    ProtoMetrics latencies are already µs. ViewMetrics ``*latency__avg`` and
    TenantMetrics latency sums are nanoseconds (live VMS: ~3.8e6 ≈ 3.8 ms).
    """
    raw = as_float(value)
    if raw is None or raw <= 0:
        return None
    return raw / 1000.0


def _view_bw_to_mbs(value):
    """Convert ViewMetrics / BucketViewMetrics bandwidth to MB/s.

    Unlike ProtoMetrics (bytes/s), view ``*_bw__rate`` values are already MB/s
    (live VMS: ~9.65 with matching iops for ~1 MiB objects). Defensively accept
    bytes/s magnitudes if a future build changes units.
    """
    raw = as_float(value)
    if raw is None or raw <= 0:
        return None
    if raw >= 1_000_000:
        return raw / 1_000_000.0
    return raw


def _drill_top_op(op_pairs):
    active = [(label, ops) for label, ops in op_pairs if (ops or 0) > 0]
    if not active:
        return "-", None
    top_label, top_ops = max(active, key=lambda item: item[1])
    total = sum(ops for _, ops in active)
    pct = (top_ops / total * 100.0) if total > 0 else None
    return top_label, pct


def _drill_rest_fields(get_ops, put_ops, delete_ops, list_ops, latency_us, bw_mbs, name):
    """Shared drill row fields: per-REST rates, auto-scaled BW, S3 Top Op."""
    get_ops = get_ops or 0.0
    put_ops = put_ops or 0.0
    delete_ops = delete_ops or 0.0
    list_ops = list_ops or 0.0
    total_ops = get_ops + put_ops + delete_ops + list_ops
    top_rpc, top_pct = _drill_top_op([
        ("GET", get_ops), ("PUT", put_ops),
        ("DELETE", delete_ops), ("LIST", list_ops),
    ])
    return {
        "name": name,
        "get_ops": get_ops if get_ops > 0 else None,
        "put_ops": put_ops if put_ops > 0 else None,
        "delete_ops": delete_ops if delete_ops > 0 else None,
        "list_ops": list_ops if list_ops > 0 else None,
        "total_ops": total_ops if total_ops > 0 else None,
        "latency_us": latency_us,
        "bw_mbs": bw_mbs if bw_mbs and bw_mbs > 0 else None,
        "top_rpc": top_rpc,
        "top_rpc_pct": top_pct,
    }


def _build_cnode_drill_row(result, obj_name):
    snapshot, _sample = build_rows_from_results(result)
    meta = snapshot["meta"]
    data_by_key = {r["key"]: r for r in snapshot["data"]}
    get_ops = as_float((data_by_key.get("get") or {}).get("ops_sec")) or 0.0
    put_ops = as_float((data_by_key.get("put") or {}).get("ops_sec")) or 0.0
    delete_ops = as_float(meta.get("wr_md_iops")) or 0.0
    list_ops = as_float(meta.get("rd_md_iops")) or 0.0
    if delete_ops <= 0 and list_ops <= 0:
        list_ops = as_float(meta.get("md_iops")) or 0.0
    latency = as_float(meta.get("latency_us")) or weighted_latency(snapshot["data"])
    bw_mbs = as_float(meta.get("total_bw_mbs"))
    return _drill_rest_fields(get_ops, put_ops, delete_ops, list_ops, latency, bw_mbs, obj_name)


def _build_vip_drill_row(result, obj_name):
    """VIP drill uses S3Common rates when present."""
    return _build_cnode_drill_row(result, obj_name)


def _fetch_vip_topn():
    """Load VIP activity from /monitors/topn/ (ProtoMetrics on vip is often empty)."""
    global VIP_TOPN
    frame = urllib.parse.quote(API_TIME_FRAME, safe="")
    candidates = (
        f"/monitors/topn/?key=vip&time_frame={frame}&limit=16",
        f"/monitors/topn/?object_type=vip&prop_list="
        f"{urllib.parse.quote(_common_fqn('iops'), safe=',')}"
        f"&time_frame={frame}&limit=16",
        f"/monitors/topn/?object_type=vip&prop_list="
        f"{urllib.parse.quote(_common_fqn('bw'), safe=',')}"
        f"&time_frame={frame}&limit=16",
    )
    for path in candidates:
        try:
            VIP_TOPN = api_request("GET", path)
            if isinstance(VIP_TOPN, dict):
                return VIP_TOPN
        except RuntimeError:
            continue
    VIP_TOPN = None
    return None


def _vip_topn_activity_rows():
    """Flatten topn VIP rows into ranked activity dicts.

    VMS topn `key=vip` returns several metric buckets that all share the same
    shape ``{title, total, read, write}``. Only some are ops/s:

    - ``iops``: total/read/write are ops/s (GET←read, PUT←write)
    - ``md_iops``: metadata ops/s (LIST←read, DELETE←write)
    - ``bw``: MB/s already
    - ``latency``: microseconds (must NOT be treated as ops)

    Earlier code took max(read/write) across every non-bw bucket, so latency
    values like write=68000 were shown as 68k PUT/s.
    """
    if not isinstance(VIP_TOPN, dict):
        return []
    data = VIP_TOPN.get("data") or {}
    buckets = []
    vip_block = data.get("vip")
    if isinstance(vip_block, dict):
        for metric, rows in vip_block.items():
            if isinstance(rows, list):
                buckets.append((str(metric).lower(), rows))
    elif isinstance(vip_block, list):
        buckets.append(("iops", vip_block))
    else:
        for key, rows in data.items():
            if isinstance(rows, list):
                buckets.append((str(key).lower(), rows))
            elif isinstance(rows, dict):
                for metric, nested in rows.items():
                    if isinstance(nested, list):
                        buckets.append((str(metric).lower(), nested))

    by_title = {}
    for metric, rows in buckets:
        for row in rows:
            if not isinstance(row, dict):
                continue
            title = str(row.get("title") or row.get("name") or "").strip()
            if not title or _is_192_168_ip(title.split()[0]):
                continue
            entry = by_title.setdefault(title, {
                "title": title,
                "ops": 0.0,
                "bw_mbs": 0.0,
                "read": 0.0,
                "write": 0.0,
                "md_read": 0.0,
                "md_write": 0.0,
                "latency_us": None,
            })
            total = as_float(row.get("total"))
            read = as_float(row.get("read")) or 0.0
            write = as_float(row.get("write")) or 0.0

            if metric in ("bw", "bandwidth") or metric.endswith("_bw"):
                if total is not None and total > 0:
                    # topn bw is commonly MB/s already; ProtoMetrics bw is bytes/s.
                    entry["bw_mbs"] = max(
                        entry["bw_mbs"], total if total < 1_000_000 else total / 1e6,
                    )
            elif metric == "iops" or metric in ("read_iops", "write_iops"):
                if total is not None and total > 0:
                    entry["ops"] = max(entry["ops"], total)
                if metric == "read_iops" and total is not None and total > 0:
                    entry["read"] = max(entry["read"], total)
                elif metric == "write_iops" and total is not None and total > 0:
                    entry["write"] = max(entry["write"], total)
                else:
                    if read > 0:
                        entry["read"] = max(entry["read"], read)
                    if write > 0:
                        entry["write"] = max(entry["write"], write)
            elif metric in ("md_iops", "read_md_iops", "write_md_iops"):
                if metric == "read_md_iops" and total is not None and total > 0:
                    entry["md_read"] = max(entry["md_read"], total)
                elif metric == "write_md_iops" and total is not None and total > 0:
                    entry["md_write"] = max(entry["md_write"], total)
                else:
                    if read > 0:
                        entry["md_read"] = max(entry["md_read"], read)
                    if write > 0:
                        entry["md_write"] = max(entry["md_write"], write)
            elif metric == "latency":
                # Prefer combined total; fall back to weighted-ish max of sides.
                lat = total
                if lat is None or lat <= 0:
                    sides = [v for v in (read, write) if v > 0]
                    lat = sum(sides) / len(sides) if sides else None
                if lat is not None and lat > 0:
                    prev = entry["latency_us"]
                    entry["latency_us"] = lat if prev is None else max(prev, lat)
            # Ignore qos_wait_time, rows, and any other non-rate buckets.

    ranked = sorted(
        by_title.values(),
        key=lambda r: (-(r["ops"] or 0), -(r["bw_mbs"] or 0), r["title"]),
    )
    return ranked


def _build_vip_rows_from_topn():
    """Build drill rows from VIP topn when ProtoMetrics monitors are idle."""
    rows = []
    for item in _vip_topn_activity_rows()[:_MAX_DRILL_OBJECTS]:
        get_ops = item.get("read") or 0.0
        put_ops = item.get("write") or 0.0
        delete_ops = item.get("md_write") or 0.0
        list_ops = item.get("md_read") or 0.0
        ops = item.get("ops") or 0.0
        bw_mbs = item.get("bw_mbs") or None
        latency_us = item.get("latency_us")
        if not any((ops, bw_mbs, get_ops, put_ops, delete_ops, list_ops)):
            continue
        row = _drill_rest_fields(
            get_ops, put_ops, delete_ops, list_ops, latency_us, bw_mbs, item["title"],
        )
        # If iops.total is present but read/write split is missing, keep total.
        if (row.get("total_ops") or 0) <= 0 and ops > 0:
            row["total_ops"] = ops
        rows.append(row)
    return rows


def _build_bucket_drill_row(result, obj_name):
    values, _prop_idx, _sample = _view_values_from_result(result)
    read_ops = (
        as_float(values.get(_VIEW_READ_IOPS))
        or as_float(values.get(_BUCKET_VIEW_READ_IOPS))
        or 0.0
    )
    write_ops = (
        as_float(values.get(_VIEW_WRITE_IOPS))
        or as_float(values.get(_BUCKET_VIEW_WRITE_IOPS))
        or 0.0
    )
    read_md = as_float(values.get(_VIEW_READ_MD)) or 0.0
    write_md = as_float(values.get(_VIEW_WRITE_MD)) or 0.0
    # ViewMetrics *latency__avg is nanoseconds; normalize to µs for Avg ms display.
    latency = _weighted_us([
        (read_ops, _ns_avg_to_us(values.get(_VIEW_READ_LAT))),
        (write_ops, _ns_avg_to_us(values.get(_VIEW_WRITE_LAT))),
        (read_md, _ns_avg_to_us(values.get(_VIEW_READ_MD_LAT))),
        (write_md, _ns_avg_to_us(values.get(_VIEW_WRITE_MD_LAT))),
    ])
    read_bw = (
        _view_bw_to_mbs(values.get(_VIEW_READ_BW))
        or _view_bw_to_mbs(values.get(_BUCKET_VIEW_READ_BW))
        or 0.0
    )
    write_bw = (
        _view_bw_to_mbs(values.get(_VIEW_WRITE_BW))
        or _view_bw_to_mbs(values.get(_BUCKET_VIEW_WRITE_BW))
        or 0.0
    )
    return _drill_rest_fields(
        read_ops, write_ops, write_md, read_md, latency, read_bw + write_bw, obj_name,
    )


def _build_tenant_drill_row(result, obj_name):
    read_ops = _delta_rate_from_samples(result, _TENANT_READ_IOPS) or 0.0
    write_ops = _delta_rate_from_samples(result, _TENANT_WRITE_IOPS) or 0.0
    read_md = _delta_rate_from_samples(result, _TENANT_READ_MD) or 0.0
    write_md = _delta_rate_from_samples(result, _TENANT_WRITE_MD) or 0.0
    # TenantMetrics latency sums are nanoseconds; avg(delta) → µs.
    read_lat = _ns_avg_to_us(
        _avg_from_sum_count_deltas(result, _TENANT_READ_LAT, _TENANT_READ_CNT)
    )
    write_lat = _ns_avg_to_us(
        _avg_from_sum_count_deltas(result, _TENANT_WRITE_LAT, _TENANT_WRITE_CNT)
    )
    read_md_lat = _ns_avg_to_us(_avg_from_sum_count_deltas(
        result, _TENANT_READ_MD_LAT_SUM, _TENANT_READ_MD_LAT_CNT,
    ))
    write_md_lat = _ns_avg_to_us(_avg_from_sum_count_deltas(
        result, _TENANT_WRITE_MD_LAT_SUM, _TENANT_WRITE_MD_LAT_CNT,
    ))
    latency = _weighted_us([
        (read_ops, read_lat), (write_ops, write_lat),
        (read_md, read_md_lat), (write_md, write_md_lat),
    ])
    # Tenant bw sums are cumulative bytes → delta rate is bytes/s.
    read_bw = raw_bw_to_mb_sec(_delta_rate_from_samples(result, _TENANT_READ_BW)) or 0.0
    write_bw = raw_bw_to_mb_sec(_delta_rate_from_samples(result, _TENANT_WRITE_BW)) or 0.0
    return _drill_rest_fields(
        read_ops, write_ops, write_md, read_md, latency, read_bw + write_bw, obj_name,
    )


def _build_drill_row(mode, result, obj_name):
    if mode == "bucket":
        return _build_bucket_drill_row(result, obj_name)
    if mode == "tenant":
        return _build_tenant_drill_row(result, obj_name)
    if mode == "vip":
        return _build_vip_drill_row(result, obj_name)
    return _build_cnode_drill_row(result, obj_name)


def _rank_drill_candidates(mode, objects, cfg):
    """Rank bucket/tenant candidates in chunks - scans all objects, not just first 32."""
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
    if mode == "bucket" and BUCKET_SCOPED:
        all_valid = [o for o in all_valid if _bucket_matches_scope(o)]
        if not all_valid:
            DRILL_ERROR = (
                f"No views/buckets match --buckets filter: {', '.join(BUCKET_NAMES)}"
            )
            return
    if mode == "tenant" and TENANT_SCOPED:
        all_valid = [o for o in all_valid if _tenant_matches_scope(o)]
        if not all_valid:
            DRILL_ERROR = (
                f"No tenants match --tenants filter: {', '.join(TENANT_NAMES)}"
            )
            return

    if mode == "vip":
        # Hide 192.168.* labels; prefer pool/name. Rank by topn activity when available.
        _fetch_vip_topn()
        vip_entries = _vip_objects_for_drill(all_valid)
        if not vip_entries:
            DRILL_ERROR = "No VIP objects available after filtering internal 192.168.* addresses"
            return
        topn_titles = [r["title"].lower() for r in _vip_topn_activity_rows()]
        if topn_titles:
            def _vip_rank(entry):
                name = entry["name"].lower()
                for idx, title in enumerate(topn_titles):
                    if name in title or title in name:
                        return idx
                return len(topn_titles) + 1
            vip_entries.sort(key=_vip_rank)
        DRILL_OBJECTS = [
            {"id": e["id"], "name": e["name"]} for e in vip_entries[:_MAX_DRILL_OBJECTS]
        ]
    elif mode in ("bucket", "tenant"):
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
        # VIP often lacks ProtoMetrics on object_type=vip; fall back to topn-only.
        if mode == "vip":
            _fetch_vip_topn()
            topn_rows = _build_vip_rows_from_topn()
            if topn_rows:
                DRILL_MONITORS = []
                DRILL_MODE = mode
                DRILL_ERROR = None
                LAST_DRILL_ROWS = topn_rows
                return
        hint = ""
        if mode == "bucket":
            hint = " (bucket/view monitors require seconds resolution without aggregation)"
        elif mode == "tenant":
            hint = " (tenant scope requires TenantMetrics counters)"
        elif mode == "vip":
            hint = " (vip object_type may not support S3Common; topn also empty)"
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

    if DRILL_MODE == "vip" and not DRILL_MONITORS:
        # Topn-only VIP mode (no ProtoMetrics monitors).
        _fetch_vip_topn()
        LAST_DRILL_ROWS = _build_vip_rows_from_topn()
        if openmetrics.is_enabled():
            openmetrics.export_drill(CLUSTER_NAME, DRILL_MODE, LAST_DRILL_ROWS, sample=LAST_SAMPLE)
        if not LAST_DRILL_ROWS:
            DRILL_ERROR = "VIP topn returned no activity"
        return

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

    # VIP ProtoMetrics often returns zeros; prefer topn activity when idle.
    if DRILL_MODE == "vip":
        active = sum(as_float(r.get("total_ops")) or 0 for r in drill_rows)
        if active <= 0:
            _fetch_vip_topn()
            topn_rows = _build_vip_rows_from_topn()
            if topn_rows:
                drill_rows = topn_rows

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
    if mode in ("bucket", "tenant"):
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
        c(pad_display("GET/s", dc["get"], ">"), _BOLD),
        c(pad_display("PUT/s", dc["put"], ">"), _BOLD),
        c(pad_display("DEL/s", dc["delete"], ">"), _BOLD),
        c(pad_display("LIST/s", dc["list"], ">"), _BOLD),
        c(pad_display("BW", dc["bw"], ">"), _BOLD),
        c(pad_display("Avg ms", dc["lat"], ">"), _BOLD),
        c(pad_display("Top Op", dc["top"], ">"), _BOLD),
        c(pad_display("Top%", dc["pct"], ">"), _BOLD),
    ], " ")
    print(box_row(header, width))
    print(box_sep(width))
    for dr in LAST_DRILL_ROWS:
        pct_val = dr.get("top_rpc_pct")
        pct = pad_display(f"{pct_val:.1f}%" if pct_val is not None else "-", dc["pct"], ">")
        lat_us = as_float(dr.get("latency_us"))
        lat_ms = (lat_us / 1000.0) if lat_us is not None else None
        bw_text, bw_val = format_throughput_mbs(dr.get("bw_mbs"))
        line = join_columns([
            pad_display(dr["name"], dc["name"], "<"),
            c(format_fixed_number(dr.get("get_ops"), dc["get"], 1), _BCYAN),
            c(format_fixed_number(dr.get("put_ops"), dc["put"], 1), _BYELLOW),
            c(format_fixed_number(dr.get("delete_ops"), dc["delete"], 1), _CYAN),
            c(format_fixed_number(dr.get("list_ops"), dc["list"], 1), _BWHITE),
            c(pad_display(bw_text if bw_val else "-", dc["bw"], ">"), _CYAN),
            c(format_fixed_number(lat_ms, dc["lat"], 2), _BGREEN),
            c(pad_display(dr.get("top_rpc") or "-", dc["top"], ">"), _BWHITE),
            c(pct, _DIM),
        ], " ")
        print(box_row(line, width))
    print(box_sep(width))
    print(box_row(c("Press x to return to cluster view", _DIM), width))
    print(box_bottom(width))


def fetch_monitor_query():
    global LAST_ROWS, LAST_SAMPLE, PREV_ROWS
    result = api_request("GET", f"/monitors/{HEADLINE_MONITOR_ID}/query/")
    PREV_ROWS = _all_panel_rows(LAST_ROWS) if LAST_ROWS else []
    LAST_ROWS, LAST_SAMPLE = build_rows_from_results(result)
    s3_result = None
    if S3_METRICS_MONITOR_ID:
        try:
            s3_result = api_request("GET", f"/monitors/{S3_METRICS_MONITOR_ID}/query/")
        except RuntimeError:
            s3_result = None
    LAST_ROWS["opcodes"] = build_opcode_breakdown_rows(
        LAST_ROWS["data"], LAST_ROWS["metadata"], LAST_ROWS["meta"], s3_result,
    )
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
    add(LAST_ROWS.get("opcodes", []), "opcode")
    return series


def _export_openmetrics():
    if not openmetrics.is_enabled():
        return
    openmetrics.export_snapshot(
        CLUSTER_NAME, None, CLUSTER_NAME, _openmetrics_series(), sample=LAST_SAMPLE,
    )


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
        c("  VAST S3", _BCYAN) + c(" opstat", _BWHITE) + c(f" v{VERSION}", _DIM)
        + f"   VMS {c(f'{VMS}:{PORT}', _BWHITE)}   cluster {c(CLUSTER_NAME or '?', _BWHITE)}"
        + c(f"   refresh {REFRESH_SECONDS}s", _DIM)
    )
    if BUCKET_SCOPED:
        note = BUCKET_NAMES[0] if len(BUCKET_NAMES) == 1 else f"{BUCKET_NAMES[0]} (+{len(BUCKET_NAMES) - 1})"
        title += c(f"   | buckets {note}", _BYELLOW)
    if TENANT_SCOPED:
        note = TENANT_NAMES[0] if len(TENANT_NAMES) == 1 else f"{TENANT_NAMES[0]} (+{len(TENANT_NAMES) - 1})"
        title += c(f"   | tenants {note}", _BYELLOW)
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
        _render_rest_panel(LAST_ROWS, width)
        print()
    print(box_row(
        c("[q]", _BWHITE) + c(" Quit ", _DIM)
        + c("|", _DIM) + c("[c]", _BWHITE) + c(" cNode ", _DIM)
        + c("|", _DIM) + c("[b]", _BWHITE) + c(" Bucket ", _DIM)
        + c("|", _DIM) + c("[t]", _BWHITE) + c(" Tenant ", _DIM)
        + c("|", _DIM) + c("[i]", _BWHITE) + c(" VIP ", _DIM)
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


def filter_s3_metrics(metrics):
    """Return metrics catalog entries that mention S3 or BucketView."""
    hits = []
    for entry in metrics if isinstance(metrics, list) else []:
        text = json.dumps(entry) if isinstance(entry, dict) else str(entry)
        if re.search(r"s3|S3|BucketView", text):
            hits.append(entry)
    return hits


def probe_monitor(cluster_id, prop_list, label):
    """Create a temporary monitor, query it, delete it; return (status, detail)."""
    payload = {
        "name": f"adhoc_opstat_s3_discover_{label}_{int(time.time())}",
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


def discover_metrics():
    """Enumerate S3-related VMS metrics, objects, and monitor probes."""
    global CLUSTER_ID, CLUSTER_NAME
    print(f"S3 metric discovery - VMS {VMS}:{PORT}\n")
    try:
        CLUSTER_ID, CLUSTER_NAME = get_current_cluster()
        print(f"Cluster: {CLUSTER_NAME} (id={CLUSTER_ID})\n")
    except RuntimeError as e:
        print(f"ERROR: Could not connect to VMS: {e}")
        sys.exit(1)

    clusters = normalize_list_response(api_request("GET", "/clusters/"))
    protocols = clusters[0].get("protocols", []) if clusters else []
    print(f"Protocols: {protocols}\n")

    print("[ /api/metrics/ - S3 / BucketView entries ]")
    try:
        metrics = api_request("GET", "/metrics/")
        s3_hits = filter_s3_metrics(metrics)
        print(f"  S3-related entries: {len(s3_hits)}")
        for entry in s3_hits[:40]:
            line = entry if isinstance(entry, str) else json.dumps(entry)
            print(f"    {line[:120]}")
        if len(s3_hits) > 40:
            print(f"    ... and {len(s3_hits) - 40} more")
    except RuntimeError as e:
        print(f"  ERROR: {e}")

    print("\n[ Object / drill endpoints ]")
    for endpoint in OBJECT_ENDPOINTS:
        try:
            objects = normalize_list_response(api_request("GET", endpoint))
            sample_keys = list(objects[0].keys())[:8] if objects else []
            print(f"  {endpoint:<22} {len(objects)} object(s)  keys={sample_keys}")
        except RuntimeError as e:
            print(f"  {endpoint:<22} error: {str(e)[:80]}")

    print("\n[ Drill object types ]")
    for mode, cfg in _DRILL_CFG.items():
        print(f"  {mode:<8} {cfg['object_type']:<8} {cfg['endpoint']}")

    print("\n[ Monitor probes ]")
    counter_props = [f"S3Metrics,{op}" for op in S3_COUNTER_OPS]
    hist_batch1 = []
    for op in S3_HISTOGRAM_OPS[:8]:
        hist_batch1.extend(s3_metric_fqns(op)[:2])
    hist_batch2 = []
    for op in S3_HISTOGRAM_OPS[8:]:
        hist_batch2.extend(s3_metric_fqns(op)[:2])

    for label, props in (
        ("s3common_headline", build_headline_monitor_props(_PROTO_S3_COMMON)),
        ("s3_legacy_headline", build_headline_monitor_props(_PROTO_S3_LEGACY)),
        ("s3metrics_counters", counter_props),
        ("s3metrics_hist_b1", hist_batch1),
        ("s3metrics_hist_b2", hist_batch2),
    ):
        status, detail = probe_monitor(CLUSTER_ID, props, label)
        print(f"  {label:<22} {status}: {detail}")

    # Probe ViewMetrics and BucketViewMetrics separately (cannot mix classes).
    try:
        views = normalize_list_response(api_request("GET", "/views/"))
        if views:
            view_id = views[0]["id"]
            for label, props in (
                ("viewmetrics_sample", [
                    _VIEW_READ_IOPS, _VIEW_WRITE_IOPS,
                    _VIEW_READ_MD, _VIEW_WRITE_MD,
                    _VIEW_READ_BW, _VIEW_WRITE_BW,
                ]),
                ("bucketview_sample", [
                    _BUCKET_VIEW_READ_IOPS, _BUCKET_VIEW_WRITE_IOPS,
                    _BUCKET_VIEW_READ_BW, _BUCKET_VIEW_WRITE_BW,
                ]),
            ):
                try:
                    payload = {
                        "name": f"adhoc_opstat_s3_discover_{label}_{int(time.time())}",
                        "object_type": "view",
                        "object_ids": [view_id],
                        "time_frame": "10m",
                        "prop_list": props,
                    }
                    created = api_request("POST", "/monitors/", payload)
                    monitor_id = created.get("id") if isinstance(created, dict) else None
                    if monitor_id:
                        api_request("GET", f"/monitors/{monitor_id}/query/")
                        api_request("DELETE", f"/monitors/{monitor_id}/")
                        print(f"  {label:<22} ok")
                    else:
                        print(f"  {label:<22} create_failed")
                except RuntimeError as e:
                    print(f"  {label:<22} error: {str(e)[:80]}")
        else:
            print("  viewmetrics_sample     skipped (no views)")
            print("  bucketview_sample      skipped (no views)")
    except RuntimeError as e:
        print(f"  view/bucketview probe error: {e}")

    if BUCKET_SCOPED:
        print(f"\n[ Bucket scope active ] {', '.join(BUCKET_NAMES)}")
    if TENANT_SCOPED:
        print(f"\n[ Tenant scope active ] {', '.join(TENANT_NAMES)}")

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
    HEADLINE_MONITOR_ID = create_headline_monitor()
    try_create_s3_metrics_monitor()
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
            elif "b" in chars.lower():
                switch_drill_mode("bucket")
            elif "t" in chars.lower():
                switch_drill_mode("tenant")
            elif "i" in chars.lower():
                switch_drill_mode("vip")
            elif "x" in chars.lower():
                exit_drill_mode()
            elif " " in chars:
                if DRILL_MODE:
                    fetch_drill_query()
                else:
                    fetch_monitor_query()
                next_refresh = time.time() + REFRESH_SECONDS
            render_screen()
            continue

        if time.time() >= next_refresh:
            if DRILL_MODE:
                fetch_drill_query()
            else:
                fetch_monitor_query()
            render_screen()
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
