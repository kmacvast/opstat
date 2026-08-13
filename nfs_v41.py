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
import vast_drill
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
# pNFS / layout candidates. Nothing is displayed for these unless the cluster
# actually exports them - they exist so discovery can report whether VAST
# publishes pNFS telemetry at all.
PNFS_OPS = [
    ("layoutget", "LAYOUTGET"),
    ("layoutreturn", "LAYOUTRETURN"),
    ("layoutcommit", "LAYOUTCOMMIT"),
    ("getdeviceinfo", "GETDEVICEINFO"),
    ("getdevicelist", "GETDEVICELIST"),
]

# Rendered in this order in the STATE / LOCKING / SESSION panel.
STATE_PANEL_OPS = STATE_OPS + DELEGATION_OPS + SESSION_OPS_V41
STATE_PANEL_TITLE = "STATE / LOCKING / SESSION (NfsMetrics)"

PNFS_PANEL_TITLE = "pNFS / LAYOUT (NfsMetrics)"

# Every v4.1-specific op we know how to ask for, grouped for discovery.
DISCOVERY_OP_GROUPS = (
    ("open / lock state", STATE_OPS),
    ("delegation", DELEGATION_OPS),
    ("session / client", SESSION_OPS_V41),
    ("pNFS / layout", PNFS_OPS),
)

# VMS has used more than one spelling for NfsMetrics op counters across
# builds, and the v4.1 ops may not follow the v3 convention at all. Discovery
# tries each pattern so a naming difference shows up as evidence rather than
# as a silently empty panel.
_OP_NAME_PATTERNS = (
    "NfsMetrics,nfs_{op}_latency__rate",
    "NfsMetrics,nfs_{op}_latency__avg",
    "NfsMetrics,nfs_{op}",
    "NfsMetrics,nfs4_{op}_latency__rate",
    "NfsMetrics,nfs4_{op}",
)

# Statistical variants VMS publishes alongside a ProtoMetrics gauge. opstat
# uses only __avg today; the others carry tail latency and request-size
# spread, which the catalog on VAST OS 5.5.0.1 confirms exist.
_STAT_SUFFIXES = ("__avg", "__max", "__std", "__rate", "__time_avg",
                  "__num_samples", "__sum", "__sum_squares")
_DISTRIBUTION_BASES = ("read_latency", "write_latency", "read_size", "write_size")

# Families worth dumping in full: everything NFS-related, including ones
# opstat does not read today.
INVENTORY_FAMILIES = ("NfsMetrics", "NfsSampledMetrics", "ProtoMetrics")
INVENTORY_PROTO_NAMES = ("NFS4Common", "NFSCommon")

# Concept keywords used to sweep the metric catalog for anything v4.1-ish.
NFS41_CONCEPTS = (
    "nfs4", "nfsv4", "session", "sequence", "exchange", "open", "close",
    "lock", "deleg", "layout", "device", "stateid", "reclaim", "compound",
    "callback", "backchannel", "trunk", "grace", "replay", "retry",
)

_DRILL_CFG = {
    "cnode": {
        "object_type": "cnode",
        "endpoint": "/cnodes/",
        "name_fields": ("name", "hostname", "mgmt_ip"),
        "no_aggregation": False,
        "ranked": False,
    },
    "view": {
        "object_type": "view",
        "endpoint": "/views/",
        "name_fields": ("path", "title", "name"),
        "no_aggregation": vast_drill.VIEW_NO_AGGREGATION,
        "ranked": True,
    },
    "tenant": {
        "object_type": "tenant",
        "endpoint": "/tenants/",
        "name_fields": ("name",),
        "no_aggregation": vast_drill.TENANT_NO_AGGREGATION,
        "ranked": True,
    },
}
_MAX_DRILL_OBJECTS = 8
_DRILL_PROBE_LIMIT = 32
_DRILL_MIN_QUERY_INTERVAL = 15.0

# Shared ranking / batching / throttle machinery; built in init_config.
DRILL = None

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
PNFS_OPS_AVAILABLE = []    # pNFS/layout ops, only when VMS exports them
DISTRIBUTION_AVAILABLE = []  # (base, label) pairs whose __max/__std VMS returns
METRICS_SOURCE = "NFS4Common"
SORT_MODE = "default"   # default | ops | latency
LAST_ROWS = {"data": [], "stateful": [], "state": [], "pnfs": [],
             "distribution": [], "session": [], "meta": {}}
LAST_SAMPLE = "-"
DRILL_MODE = DRILL_ERROR = None
DRILL_OBJECTS = []
DRILL_MONITORS = []
LAST_DRILL_ROWS = []


def init_config(args):
    global ARGS, VMS, PORT, USER, PASSWORD, REFRESH_SECONDS, API_TIME_FRAME
    global SAMPLE_AVERAGE_MODE, BASE_URL, AUTH, HEADERS, _COLOR, DRILL
    global DRILL_MODE, DRILL_ERROR, DRILL_OBJECTS, DRILL_MONITORS, LAST_DRILL_ROWS

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

    DRILL = vast_drill.DrillSession(
        request_fn=api_request,
        create_monitor_fn=_create_monitor_raw,
        delete_monitor_fn=delete_monitor,
        max_objects=_MAX_DRILL_OBJECTS,
        min_batch=_DRILL_PROBE_LIMIT,
        min_query_interval=_DRILL_MIN_QUERY_INTERVAL,
    )
    DRILL_MODE = DRILL_ERROR = None
    DRILL_OBJECTS = []
    DRILL_MONITORS = []
    LAST_DRILL_ROWS = []


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


DISTRIBUTION_PANEL_TITLE = "LATENCY & REQUEST SIZE DISTRIBUTION (NFS4Common)"

# VMS publishes a full statistical surface beside each ProtoMetrics gauge.
# opstat historically read only __avg, discarding tail latency and spread
# that the cluster already computes - confirmed present on VAST OS 5.5.0.1.
DISTRIBUTION_BASES = (
    ("read_latency", "READ latency", "latency"),
    ("write_latency", "WRITE latency", "latency"),
    ("read_size", "READ size", "size"),
    ("write_size", "WRITE size", "size"),
)
DISTRIBUTION_SUFFIXES = ("__avg", "__max", "__std")


def build_distribution_props(bases=None):
    """NFS4Common avg/max/std props for the latency and size distributions."""
    return [
        f"{_NFS4},{base}{suffix}"
        for base, _label, _kind in (bases or DISTRIBUTION_BASES)
        for suffix in DISTRIBUTION_SUFFIXES
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


def op_name_candidates(op):
    """Every metric spelling we know to try for one NFSv4.1 operation."""
    return [pattern.format(op=op) for pattern in _OP_NAME_PATTERNS]


def _catalog_exports_op(names, op):
    """True when the catalog contains an NFSv4-style counter for *op*.

    The op must appear as a whole token behind an ``nfs_``/``nfs4_`` prefix.
    Loose substring matching produced false positives on a real 5.5.0.1
    catalog: ``NfsMetrics,nfs3_open_file_handle_cnt`` was reported as an
    NFSv4 OPEN counter, and ``nfs3_smb_interop_handles_closed`` as CLOSE.
    Requiring the ``nfs_``/``nfs4_`` prefix excludes the v3/SMB-interop
    counters, and anchoring on ``_latency`` (or end of name, for bare
    counters like ``nfs_null``) stops ``nfs_open_`` matching
    ``nfs_open_downgrade_latency``.
    """
    for prefix in ("nfs", "nfs4"):
        bare = f"{prefix}_{op}"
        latency = f"{prefix}_{op}_latency"
        for name in names:
            lowered = name.lower()
            if latency in lowered or lowered.endswith(bare) or f"{bare}," in lowered:
                return True
    return False


def _name_tokens(name):
    """Split a metric FQN into lowercase alphanumeric tokens."""
    return [t for t in re.split(r"[^a-z0-9]+", name.lower()) if t]


def _matches_concept(name, keyword):
    """True when any token of *name* starts with *keyword*.

    Plain substring matching swamped the report: ``lock`` matched every one
    of the 382 ``BlockMetrics`` names through the "b-lock" substring, 431 of
    434 hits being noise. Token-prefix matching keeps the useful behavior
    (``deleg`` finds ``delegreturn``, ``nfs4`` finds ``NFS4Common``) without
    it.
    """
    return any(token.startswith(keyword) for token in _name_tokens(name))


def probe_available_state_ops(names=None, ops=None):
    """Return the subset of *ops* the cluster's metric catalog exports.

    Best-effort and read-only. Returns:
      - a (possibly empty) list of (op, label) when the catalog is readable, or
      - None when the catalog cannot be read, so the caller can fall back to a
        trial monitor-creation attempt.
    """
    if names is None:
        names = vast_common.fetch_metric_catalog(api_request)
        if not names:
            return None
    return [(op, label) for op, label in (ops or STATE_PANEL_OPS)
            if _catalog_exports_op(names, op)]


def concept_scan(names):
    """Group catalog metric names by NFSv4.1 concept keyword.

    Returns {keyword: sorted names}. A name can appear under several
    keywords; that is intentional, the point is to make every v4.1-adjacent
    metric easy to find in the report.
    """
    hits = {}
    for keyword in NFS41_CONCEPTS:
        matched = sorted(n for n in names if _matches_concept(n, keyword))
        if matched:
            hits[keyword] = matched
    return hits


def probe_prop_support(props, chunk=40):
    """Return (supported, rejected) prop names, verified against live monitors.

    Catalog presence does not guarantee a property is queryable, so this
    creates a temporary monitor per chunk, reads which props VMS echoes back
    in the query's prop_list, and deletes the monitor. Read-only in effect:
    nothing survives the call. A chunk whose create is rejected outright is
    reported as rejected rather than retried prop-by-prop, to bound cost.
    """
    supported, rejected = [], []
    for start in range(0, len(props), chunk):
        batch = props[start:start + chunk]
        monitor_id = None
        try:
            monitor_id = create_monitor(f"discover_{start}", batch)
            result = api_request("GET", f"/monitors/{monitor_id}/query/")
            returned = set(result.get("prop_list", []) or []) if isinstance(result, dict) else set()
            for prop in batch:
                (supported if prop in returned else rejected).append(prop)
        except RuntimeError:
            rejected.extend(batch)
        finally:
            if monitor_id is not None:
                delete_monitor(monitor_id)
    return supported, rejected


def build_drill_prop_list(mode="cnode"):
    """Scope-aware drill props.

    NFS4Common/NfsMetrics/NFSCommon are cluster- and cNode-scoped families; a
    monitor requesting them with object_type=view or =tenant is rejected by
    current VMS builds, so view and tenant scopes use ViewMetrics and
    TenantMetrics instead (the same families the NFSv3 engine uses, since
    these are object-scope metrics rather than NFS-version ones).
    """
    if mode == "view":
        return vast_drill.view_display_props()
    if mode == "tenant":
        return vast_drill.tenant_display_props()
    return (
        build_data_monitor_props()
        + build_supplement_monitor_props()
        + build_bw_monitor_props()
        + build_meta_monitor_props()
    )


def build_drill_rank_prop_list(mode):
    """Minimal props for ranking view/tenant candidates by activity."""
    if mode == "view":
        return vast_drill.view_rank_props()
    if mode == "tenant":
        return vast_drill.tenant_rank_props()
    return build_drill_prop_list(mode)


def _create_monitor_raw(name_suffix, prop_list, object_type, object_ids,
                        *, no_aggregation=False):
    name = f"adhoc_opstat_nfs41_{name_suffix}_{int(time.time())}"
    return vast_common.create_monitor_raw(
        api_request, name, prop_list, object_type, object_ids,
        time_frame=API_TIME_FRAME, no_aggregation=no_aggregation,
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


def _latest_row(result, prop_names=None):
    """Values from the newest usable sample row.

    VMS publishes the newest bucket while it is still filling (a real cluster
    monitor returned 2 of 46 metrics populated), so this must not read
    data[0] verbatim - see vast_common.latest_complete_row.
    """
    _prop_list, data, prop_idx = _result_parts(result)
    return vast_common.latest_complete_values(data, prop_idx, prop_names)


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


def _build_pnfs_rows(state_values):
    """pNFS/layout rows. Empty unless the cluster exports layout counters -
    opstat never synthesises pNFS activity."""
    if not PNFS_OPS_AVAILABLE:
        return []
    return _rows_with_pct(
        PNFS_OPS_AVAILABLE,
        lambda k: _nfs_op_metrics(state_values, k),
    )


def _build_distribution_rows(values):
    """avg/max/std rows for whichever distributions the cluster returned."""
    rows = []
    for base, label, kind in DISTRIBUTION_AVAILABLE:
        rows.append({
            "label": label,
            "kind": kind,
            "avg": as_float(values.get(f"{_NFS4},{base}__avg")),
            "max": as_float(values.get(f"{_NFS4},{base}__max")),
            "std": as_float(values.get(f"{_NFS4},{base}__std")),
        })
    return rows


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
    # Each family scores against its own props. One merged monitor carries
    # all of them, and they do not fill the same sample bucket: on a real
    # cluster only the NFSCommon bandwidth columns were populated in the
    # newest cNode row while NfsMetrics landed in older ones.
    nfs4_values, sample = _latest_row(data_result, build_data_monitor_props())
    supplement_values, _ = (
        _latest_row(supplement_result, build_supplement_monitor_props())
        if supplement_result else ({}, sample))
    bw_values, _ = (
        _latest_row(bw_result, build_bw_monitor_props())
        if bw_result else ({}, sample))
    meta_values, _ = (
        _latest_row(meta_result, build_meta_monitor_props())
        if meta_result else ({}, sample))
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
    pnfs_rows = _build_pnfs_rows(state_values)
    distribution_rows = _build_distribution_rows(
        _latest_row(data_result, build_distribution_props())[0]
        if DISTRIBUTION_AVAILABLE else {})
    session_rows = _build_session_rows(meta)

    return {
        "data": data_rows,
        "stateful": stateful_rows,
        "state": state_rows,
        "pnfs": pnfs_rows,
        "distribution": distribution_rows,
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
    # Be explicit about the ceiling: VAST OS 5.5.0.1 exports no NFSv4.1
    # protocol-state counters at all (no session/sequence/exchange/open/lock/
    # delegation/layout metrics exist in its catalog), so these rows are
    # NFS-generic namespace operations, not v4.1 state machinery.
    note = (
        "This VMS build exports no NFSv4.1 state/session/layout counters; "
        "showing NfsMetrics namespace ops - "
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


def _render_pnfs_panel(rows, width):
    """pNFS / layout activity. Only reached when the cluster exports it."""
    titles = [
        ("Operation", "label", "<"), ("Ops/s", "iops", ">"), ("", "throughput", ">"),
        ("", "size", ">"), ("Latency", "latency", ">"),
    ]
    print(box_top(PNFS_PANEL_TITLE, width))
    print(box_row(_table_header_titles(titles), width))
    print(box_sep(width))
    active = [r for r in rows if (as_float(r.get("ops_sec")) or 0) > 0]
    for row in _sort_rows(active or rows):
        print(box_row(_simple_row_cells(row), width))
    if not active:
        print(box_row(
            c("No layout activity this sample (clients are not using pNFS).", _DIM),
            width,
        ))
    print(box_bottom(width))


def _format_distribution_value(value, kind):
    if value is None:
        return "-"
    text, _ = (format_latency_us(value) if kind == "latency"
               else format_block_size(value))
    return text


def _render_distribution_panel(rows, width):
    """Tail latency and request-size spread, straight from NFS4Common.

    VMS computes avg/max/std per sample window; opstat previously read only
    the average, so a workload with a long tail looked identical to a steady
    one.
    """
    titles = [
        ("Metric", "label", "<"), ("Average", "iops", ">"),
        ("Max", "throughput", ">"), ("Std dev", "size", ">"), ("", "latency", ">"),
    ]
    print(box_top(DISTRIBUTION_PANEL_TITLE, width))
    print(box_row(_table_header_titles(titles), width))
    print(box_sep(width))
    for row in rows:
        kind = row["kind"]
        has_data = row["avg"] is not None or row["max"] is not None
        print(box_row(join_columns([
            _label_cell(row["label"], _COL["label"], _BWHITE if has_data else _DIM),
            _metric_cell(_format_distribution_value(row["avg"], kind),
                         _COL["iops"], _BGREEN if has_data else _DIM),
            _metric_cell(_format_distribution_value(row["max"], kind),
                         _COL["throughput"], _YELLOW if has_data else _DIM),
            _metric_cell(_format_distribution_value(row["std"], kind),
                         _COL["size"], _CYAN if has_data else _DIM),
            _dash(_COL["latency"]),
        ], _COL_SEP), width))
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
    # Only claim SEQUENCE is missing when it really is: the state panel shows
    # it whenever the cluster exports it, and the two must not disagree.
    sequence_shown = any(op == "sequence" for op, _label in STATE_OPS_AVAILABLE)
    lead = ("NFS4Common metadata workload profile" if sequence_shown
            else "SEQUENCE unexported on this VMS - NFS4Common metadata workload profile")
    note = (
        f"{lead} "
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
    # DRILL_MONITORS must be declared global here: without it the assignment
    # below created a function-local list, so the module-level registry stayed
    # empty, fetch_drill_query never queried anything, and the drill panel sat
    # on "Waiting for data…" forever while the created monitors leaked.
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
    if cfg.get("ranked"):
        # Ranking matters here: a cluster can list hundreds of views, and
        # taking the head of /views/ showed eight arbitrary (usually idle)
        # ones rather than the busiest.
        DRILL_OBJECTS = DRILL.rank(
            mode, all_valid,
            object_type=cfg["object_type"],
            rank_props=build_drill_rank_prop_list(mode),
            score_fn=lambda sliced: as_float(
                _build_drill_row(mode, sliced, "")["total_ops"]) or 0.0,
            time_frame=API_TIME_FRAME,
            name_of=lambda obj: _obj_name(obj, cfg["name_fields"]),
            no_aggregation=cfg.get("no_aggregation", False),
        )
    else:
        DRILL_OBJECTS = [
            {"id": o["id"], "name": _obj_name(o, cfg["name_fields"])}
            for o in all_valid[:_MAX_DRILL_OBJECTS]
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
        validate_batch=(mode == "cnode"),
    )
    if not new_monitors:
        detail = f": {last_error}" if last_error else ""
        DRILL_ERROR = (
            f"Could not create any {mode} monitors "
            f"(object_type='{cfg['object_type']}' may not be supported){detail}"
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


def _build_cnode_drill_row(result, obj_name):
    """cNode rows reuse the cluster row builders (same NFS4Common families)."""
    snapshot, _sample = build_rows_from_results(result, result, result, result)
    data = snapshot["data"]
    total_ops = sum(as_float(r["ops_sec"]) or 0 for r in data)
    total_bw = sum(as_float(r["bw_mbs"]) or 0 for r in data) / 1024.0
    active = [r for r in data if (as_float(r["ops_sec"]) or 0) > 0]
    top = max(active, key=lambda r: as_float(r["ops_sec"]) or 0, default=None)
    return {
        "name": obj_name,
        "total_ops": total_ops if total_ops > 0 else None,
        "latency_us": weighted_latency(data),
        "bw_gbs": total_bw if total_bw else None,
        "top_rpc": top["label"] if top else "-",
        "top_rpc_pct": as_float(top["pct"]) if top else None,
    }


def _build_drill_row(mode, result, obj_name):
    if mode == "view":
        return vast_drill.build_view_row(result, obj_name)
    if mode == "tenant":
        return vast_drill.build_tenant_row(result, obj_name)
    return _build_cnode_drill_row(result, obj_name)


def fetch_drill_query(force=False):
    """Re-query the drill monitors, throttled unless *force*.

    Object-scoped metric families publish roughly once a minute, so a 5s
    dashboard tick re-fetched identical payloads.
    """
    global LAST_DRILL_ROWS, DRILL_ERROR
    if not DRILL_MODE:
        return
    if not DRILL.should_query(force=force, have_data=bool(LAST_DRILL_ROWS)):
        return
    drill_rows = []
    query_errors = 0

    if DRILL.batch_active(DRILL_MONITORS):
        monitor_id, _name = DRILL_MONITORS[0]
        try:
            result = api_request("GET", f"/monitors/{monitor_id}/query/")
            for obj in DRILL_OBJECTS:
                drill_rows.append(_build_drill_row(
                    DRILL_MODE,
                    vast_drill.slice_result_for_object(result, obj["id"]),
                    obj["name"],
                ))
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
        drill_rows, key=lambda r: r["total_ops"] or 0, reverse=True)
    if openmetrics.is_enabled() and DRILL_MODE:
        openmetrics.export_drill(CLUSTER_NAME, DRILL_MODE, LAST_DRILL_ROWS, sample=LAST_SAMPLE)
    if not LAST_DRILL_ROWS and query_errors:
        DRILL_ERROR = (
            f"{DRILL_MODE} drill monitors returned no data "
            f"({query_errors}/{len(DRILL_OBJECTS)} queries failed)"
        )


def _drill_coverage_note():
    """State how much cluster activity the shown objects actually account for.

    ViewMetrics/TenantMetrics attribute only the operations VMS can tie to a
    view or tenant, so drill rows legitimately sum to less than the cluster
    total (and these are only the busiest few). Saying so beats letting the
    gap read as a bug.
    """
    if not LAST_DRILL_ROWS or DRILL_MODE not in ("view", "tenant"):
        return ""
    shown = sum(as_float(r.get("total_ops")) or 0.0 for r in LAST_DRILL_ROWS)
    data = LAST_ROWS.get("data") or []
    cluster = sum(as_float(r.get("ops_sec")) or 0.0 for r in data)
    cluster += as_float((LAST_ROWS.get("meta") or {}).get("md_iops")) or 0.0
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
    coverage = _drill_coverage_note()
    if coverage:
        print(box_row(coverage, width))
    print(box_row(c("Press x to return to cluster view", _DIM), width))
    print(box_bottom(width))


def fetch_monitor_query():
    global LAST_ROWS, LAST_SAMPLE
    data_result = api_request("GET", f"/monitors/{DATA_MONITOR_ID}/query/")

    def _result_for(monitor_id):
        """Reuse the merged-headline query when the ids collapse to one monitor."""
        if monitor_id == DATA_MONITOR_ID:
            return data_result
        return api_request("GET", f"/monitors/{monitor_id}/query/")

    supplement_result = _result_for(SUPPLEMENT_MONITOR_ID)
    bw_result = _result_for(BW_MONITOR_ID)
    meta_result = _result_for(META_MONITOR_ID)
    state_result = _result_for(STATE_MONITOR_ID) if STATE_MONITOR_ID else None
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


_NAV_CONTROLS = (
    ("q", "Quit"),
    ("o", "Ops"),
    ("l", "Lat"),
    ("n", "Name"),
    ("c", "cNode"),
    ("v", "View"),
    ("t", "Tenant"),
    ("x", "Exit drill"),
    ("space", "Refresh"),
)

# Never let a narrow terminal collapse the frame to the point where the
# controls vanish entirely; box_row truncates content to width - 4.
_MIN_FRAME_WIDTH = 24


def _frame_width():
    return max(_MIN_FRAME_WIDTH,
               min(shutil.get_terminal_size((120, 40)).columns, 120))


def _render_nav_footer(width):
    """Application navigation bar, shown in every mode including drill-downs."""
    parts = []
    for key, label in _NAV_CONTROLS:
        if parts:
            parts.append(c("|", _DIM))
        parts.append(c(f"[{key}]", _BWHITE) + c(f" {label} ", _DIM))
    print(box_row("".join(parts), width), flush=True)


def _render_frame():
    width = _frame_width()
    title = (
        c("  VAST NFSv41", _BCYAN) + c(" opstat", _BWHITE) + c(f" v{VERSION}", _DIM)
        + f"   VMS {c(f'{VMS}:{PORT}', _BWHITE)}   cluster {c(CLUSTER_NAME, _BWHITE)}"
        + c(f"   refresh {REFRESH_SECONDS}s", _DIM)
    )
    if DRILL_MODE:
        title += c(f"   | {DRILL_MODE.upper()} DRILL", _BYELLOW)
    # Header lines sit outside the box borders, so they must be truncated
    # explicitly: an untruncated line wraps on a narrow terminal, shifting
    # every row below it and corrupting the frame.
    print(truncate_display(title, width))
    os_label = format_os_release(CLUSTER_OS)
    print(truncate_display(c(
        f"  sample {LAST_SAMPLE}   frame {API_TIME_FRAME}   source {METRICS_SOURCE}"
        + f"   sort {_sort_label()}"
        + (f"   {os_label}" if os_label else ""),
        _DIM,
    ), width))
    print()
    # Body: drill panel or the cluster panels. The navigation footer below is
    # rendered by this common path for every mode - a drill panel returning
    # early used to take the footer with it, leaving drill modes with no
    # visible controls at all.
    if DRILL_MODE:
        _render_drill_panel(width)
    else:
        _render_health_panel(LAST_ROWS, width)
        print()
        _render_data_panel(LAST_ROWS["data"], width)
        print()
        if STATE_OPS_AVAILABLE:
            _render_state_panel(LAST_ROWS["state"], width)
        else:
            _render_stateful_panel(LAST_ROWS["stateful"], LAST_ROWS["meta"], width)
        print()
        if PNFS_OPS_AVAILABLE:
            _render_pnfs_panel(LAST_ROWS.get("pnfs", []), width)
            print()
        if DISTRIBUTION_AVAILABLE:
            _render_distribution_panel(LAST_ROWS.get("distribution", []), width)
            print()
        _render_session_panel(LAST_ROWS["session"], LAST_ROWS["meta"], width)
    print()
    _render_nav_footer(width)


def _discovery_report_path():
    safe = "".join(ch if ch.isalnum() or ch in ".-_" else "_" for ch in str(VMS))
    return os.path.join("/tmp", f"opstat-nfs41-discovery-{safe}-{os.getpid()}.txt")


def discover_metrics():
    """Read-only survey of the NFSv4.1 telemetry this VMS actually exports.

    Prints a short console summary and writes the full evidence (every
    catalog name matching an NFSv4.1 concept, plus per-property monitor probe
    results) to a file, so the report can be sent back for analysis without
    scrolling a terminal. Creates only temporary monitors, each deleted
    before the function returns.
    """
    global CLUSTER_ID, CLUSTER_NAME

    lines = []

    def emit(text="", console=True):
        lines.append(text)
        if console:
            print(text)

    emit(f"NFS v4.1 metric discovery - VMS {VMS}:{PORT}")
    try:
        CLUSTER_ID, CLUSTER_NAME = get_current_cluster()
        os_label = format_os_release(vast_common.get_current_cluster_os(api_request))
        emit(f"Cluster: {CLUSTER_NAME} (id={CLUSTER_ID})  {os_label}")
    except RuntimeError as e:
        print(f"ERROR: Could not connect to VMS: {e}")
        sys.exit(1)
    emit()

    # -- 1. catalog ---------------------------------------------------------
    names = vast_common.fetch_metric_catalog(api_request)
    emit("[ 1. Metric catalog ]")
    if not names:
        emit("  /metrics/ unreadable or empty - falling back to monitor probes only")
    else:
        families = {}
        for name in names:
            fam = vast_common.metric_family(name)
            families[fam] = families.get(fam, 0) + 1
        emit(f"  {len(names)} metric names across {len(families)} families")
        for fam, count in sorted(families.items(), key=lambda kv: -kv[1]):
            emit(f"    {fam:<28} {count}")
    emit()

    # -- 2. concept scan ----------------------------------------------------
    emit("[ 2. NFSv4.1 concept scan ]")
    hits = concept_scan(names) if names else {}
    if not names:
        emit("  (skipped - no catalog)")
    else:
        for keyword in NFS41_CONCEPTS:
            matched = hits.get(keyword, [])
            emit(f"  {keyword:<12} {len(matched):>4} name(s)")
            for name in matched:
                lines.append(f"        {name}")          # file only
        emit("  (full name lists are in the report file)")
    emit()

    # -- 3. per-op probe ----------------------------------------------------
    emit("[ 3. NFSv4.1 operation probe (temporary monitors, then deleted) ]")
    candidates = []
    for _group, ops in DISCOVERY_OP_GROUPS:
        for op, _label in ops:
            candidates.extend(op_name_candidates(op))
    # Probe only spellings the catalog knows about when we have one; a full
    # blind sweep is what the no-catalog path is for.
    to_probe = [p for p in candidates if p in names] if names else candidates
    supported, _rejected = probe_prop_support(sorted(set(to_probe)))
    supported_set = set(supported)
    for group, ops in DISCOVERY_OP_GROUPS:
        emit(f"  -- {group} --")
        for op, label in ops:
            hit = [p for p in op_name_candidates(op) if p in supported_set]
            in_catalog = bool(names) and _catalog_exports_op(names, op)
            if hit:
                emit(f"    {label:<14} QUERYABLE   {hit[0]}")
                for extra in hit[1:]:
                    lines.append(f"                                {extra}")
            elif in_catalog:
                emit(f"    {label:<14} in catalog, not queryable under known names")
            else:
                emit(f"    {label:<14} not exported")
    emit()

    # -- 4. families that already work --------------------------------------
    emit("[ 4. Families in use today ]")
    for label, props in (
        ("NFS4Common data path", build_data_monitor_props()),
        ("NfsMetrics supplement", build_supplement_monitor_props()[:4]),
        ("NFSCommon bandwidth", build_bw_monitor_props()),
        ("NFS4Common session/md", build_meta_monitor_props()),
    ):
        ok, bad = probe_prop_support(list(props))
        emit(f"  {label:<24} {len(ok)}/{len(ok) + len(bad)} queryable")
        for prop in bad:
            lines.append(f"        rejected: {prop}")
    emit()

    # -- 5. object-scope support -------------------------------------------
    emit("[ 5. Object scopes ]")
    for mode, cfg in _DRILL_CFG.items():
        try:
            objects = normalize_list_response(api_request("GET", cfg["endpoint"]))
            count = len(objects)
        except RuntimeError as e:
            emit(f"  {mode:<8} {cfg['endpoint']:<12} error: {e}")
            continue
        probe_ids = [o["id"] for o in objects[:4] if "id" in o]
        status = "no objects"
        if probe_ids:
            monitor_id = None
            try:
                monitor_id = _create_monitor_raw(
                    f"discover_{mode}", build_drill_prop_list(mode),
                    cfg["object_type"], probe_ids,
                    no_aggregation=cfg.get("no_aggregation", False),
                )
                result = api_request("GET", f"/monitors/{monitor_id}/query/")
                returned = set(result.get("prop_list", []) or [])
                metrics = [p for p in returned if "," in p]
                rows = len(result.get("data", []) or [])
                status = (f"{len(metrics)}/{len(build_drill_prop_list(mode))} props, "
                          f"{rows} rows, object_id={'yes' if 'object_id' in returned else 'no'}")
            except RuntimeError as e:
                status = f"monitor rejected: {str(e)[:70]}"
            finally:
                if monitor_id is not None:
                    delete_monitor(monitor_id)
        emit(f"  {mode:<8} {cfg['endpoint']:<12} {count:>4} object(s)  {status}")
    emit()

    # -- 6. statistical surface ---------------------------------------------
    emit("[ 6. NFS4Common statistical surface ]")
    dist_props = [
        f"{_NFS4},{base}{suffix}"
        for base in _DISTRIBUTION_BASES for suffix in _STAT_SUFFIXES
    ]
    in_catalog = [p for p in dist_props if not names or p in names]
    dist_ok, _dist_bad = probe_prop_support(in_catalog)
    dist_ok_set = set(dist_ok)
    for base in _DISTRIBUTION_BASES:
        usable = [s for s in _STAT_SUFFIXES
                  if f"{_NFS4},{base}{s}" in dist_ok_set]
        emit(f"  {base:<16} queryable: {', '.join(usable) if usable else 'none'}")
    emit()

    # -- 7. full inventory of NFS-related families ---------------------------
    emit("[ 7. NFS family inventory ]")
    inventory = {}
    for name in names:
        family = vast_common.metric_family(name)
        if family not in INVENTORY_FAMILIES:
            continue
        if family == "ProtoMetrics" and not any(
                p in name for p in INVENTORY_PROTO_NAMES):
            continue
        inventory.setdefault(family, []).append(name)
    if not inventory:
        emit("  (no catalog to inventory)")
    for family, entries in sorted(inventory.items()):
        emit(f"  {family:<20} {len(entries)} name(s) - full list in report file")
        for entry in sorted(entries):
            lines.append(f"        {entry}")
    emit()

    emit("[ 8. Summary ]")
    populated = [label for group, ops in DISCOVERY_OP_GROUPS for op, label in ops
                 if any(p in supported_set for p in op_name_candidates(op))]
    if populated:
        emit(f"  Native v4.1 ops available: {', '.join(populated)}")
    else:
        emit("  No native v4.1 state/session/layout ops are queryable on this build;")
        emit("  the dashboard falls back to NfsMetrics namespace counters.")

    path = _discovery_report_path()
    try:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
        print(f"\nFull report written to {path}")
    except OSError as e:
        print(f"\nCould not write report file: {e}")


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


def _distributions_present(returned_props):
    """Keep distributions whose avg or max VMS actually echoed back.

    Catalog presence is not enough: on a real cluster OPEN/CLOSE appeared in
    the catalog yet no monitor would return them, so every panel gates on
    what a live query really produced.
    """
    # Require __max: the panel exists to show spread, and an average alone is
    # already on the DATA OPERATIONS panel.
    return [
        (base, label, kind) for base, label, kind in DISTRIBUTION_BASES
        if f"{_NFS4},{base}__max" in returned_props
    ]


def _ops_present(ops, returned_props):
    """Keep only ops whose counters VMS actually echoed in a query prop_list."""
    return [(op, label) for op, label in ops
            if any(_nfs_fqn(op, kind) in returned_props for kind in ("rate", "avg"))]


def _init_state_monitor(candidates=None, pnfs=None):
    """Create the state/locking/session monitor from whatever the cluster exports.

    Uses the metric catalog to trim candidates, then verifies by creating the
    monitor. On any failure the feature is disabled and the classic NfsMetrics
    proxy panel is shown instead - never breaking the dashboard.
    """
    global STATE_MONITOR_ID, STATE_OPS_AVAILABLE, PNFS_OPS_AVAILABLE
    if candidates is None:
        candidates = probe_available_state_ops()
    if candidates is None:            # catalog unreadable - try the full set
        candidates = STATE_PANEL_OPS
    if pnfs is None:
        pnfs = PNFS_OPS
    wanted = list(candidates) + list(pnfs)
    if not wanted:
        STATE_OPS_AVAILABLE = PNFS_OPS_AVAILABLE = []
        return
    try:
        STATE_MONITOR_ID = create_monitor("state", build_state_monitor_props(wanted))
        result = api_request("GET", f"/monitors/{STATE_MONITOR_ID}/query/")
        returned = set(result.get("prop_list", []) or []) if isinstance(result, dict) else set()
        STATE_OPS_AVAILABLE = _ops_present(candidates, returned)
        PNFS_OPS_AVAILABLE = _ops_present(pnfs, returned)
    except RuntimeError:
        STATE_MONITOR_ID = None
        STATE_OPS_AVAILABLE = PNFS_OPS_AVAILABLE = []


def create_headline_monitors():
    """Create the headline monitors, merged into a single monitor when possible.

    The five headline prop groups (data, supplement, bw, meta, state) are all
    cluster-scope monitors; this engine's own drill-down already carries the
    first four groups in one monitor per object, so a merged headline monitor
    uses a VMS capability the engine relies on today. Merging drops the
    per-refresh query count from 5 to 1. The merged monitor is validated with
    one probe query; on create failure or missing prop families the engine
    falls back to the historical five split monitors.
    """
    global DATA_MONITOR_ID, SUPPLEMENT_MONITOR_ID, BW_MONITOR_ID, META_MONITOR_ID
    global STATE_MONITOR_ID, STATE_OPS_AVAILABLE, PNFS_OPS_AVAILABLE
    global DISTRIBUTION_AVAILABLE

    data_props = build_data_monitor_props()
    dist_props = build_distribution_props()
    supplement_props = build_supplement_monitor_props()
    bw_props = build_bw_monitor_props()
    meta_props = build_meta_monitor_props()
    core_groups = (data_props, supplement_props, bw_props, meta_props)

    # One catalog read decides both the state and the pNFS candidate sets;
    # both ride in the merged headline monitor, so neither costs an extra
    # query per refresh. Nothing is rendered for an op the cluster does not
    # actually return in the monitor's prop_list.
    catalog = vast_common.fetch_metric_catalog(api_request)
    if catalog:
        state_candidates = probe_available_state_ops(catalog, STATE_PANEL_OPS)
        pnfs_candidates = probe_available_state_ops(catalog, PNFS_OPS)
    else:
        state_candidates = pnfs_candidates = None
    trial_state = STATE_PANEL_OPS if state_candidates is None else state_candidates
    trial_pnfs = PNFS_OPS if pnfs_candidates is None else pnfs_candidates
    state_props = (
        build_state_monitor_props(trial_state + trial_pnfs)
        if (trial_state or trial_pnfs) else []
    )

    # Try merged including state props, then merged without them (state
    # metrics are the most build-dependent), then the classic split layout.
    for attempt_state_props, _attempt_candidates in (
        (state_props, trial_state),
        ([], []),
    ):
        merged_props = ([p for group in core_groups for p in group]
                        + dist_props + attempt_state_props)
        merged_id = None
        try:
            merged_id = create_monitor("headline", merged_props)
            result = api_request("GET", f"/monitors/{merged_id}/query/")
            returned = (
                set(result.get("prop_list", []) or [])
                if isinstance(result, dict) else set()
            )
            if all(any(p in returned for p in group) for group in core_groups):
                DATA_MONITOR_ID = SUPPLEMENT_MONITOR_ID = merged_id
                BW_MONITOR_ID = META_MONITOR_ID = merged_id
                DISTRIBUTION_AVAILABLE = _distributions_present(returned)
                if attempt_state_props and any(p in returned for p in attempt_state_props):
                    STATE_MONITOR_ID = merged_id
                    STATE_OPS_AVAILABLE = _ops_present(trial_state, returned)
                    PNFS_OPS_AVAILABLE = _ops_present(trial_pnfs, returned)
                else:
                    STATE_MONITOR_ID = None
                    STATE_OPS_AVAILABLE = []
                    PNFS_OPS_AVAILABLE = []
                return
            delete_monitor(merged_id)
        except RuntimeError:
            delete_monitor(merged_id)
        if not attempt_state_props:
            break

    DISTRIBUTION_AVAILABLE = []
    DATA_MONITOR_ID = create_monitor("data", data_props + dist_props)
    SUPPLEMENT_MONITOR_ID = create_monitor("supplement", supplement_props)
    BW_MONITOR_ID = create_monitor("bw", bw_props)
    META_MONITOR_ID = create_monitor("meta", meta_props)
    try:
        probe = api_request("GET", f"/monitors/{DATA_MONITOR_ID}/query/")
        DISTRIBUTION_AVAILABLE = _distributions_present(
            set(probe.get("prop_list", []) or []) if isinstance(probe, dict) else set())
    except RuntimeError:
        DISTRIBUTION_AVAILABLE = []
    _init_state_monitor(candidates=state_candidates, pnfs=pnfs_candidates)


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
    create_headline_monitors()

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
                    fetch_drill_query(force=True)
            elif "v" in chars.lower():
                exit_drill_mode()
                enter_drill_mode("view")
                if DRILL_MODE:
                    fetch_drill_query(force=True)
            elif "t" in chars.lower():
                exit_drill_mode()
                enter_drill_mode("tenant")
                if DRILL_MODE:
                    fetch_drill_query(force=True)
            elif "x" in chars.lower():
                exit_drill_mode()
            elif " " in chars:
                vast_common.guarded_poll(manual_refresh, render_screen)
                next_refresh = time.time() + REFRESH_SECONDS
                continue
            render_screen()
            continue

        now = time.time()
        if now >= next_refresh:
            vast_common.guarded_poll(poll_tick, render_screen)
            next_refresh = time.time() + REFRESH_SECONDS
            continue
        vast_common.wait_for_input(next_refresh - now)
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
