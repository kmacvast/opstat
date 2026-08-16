#!/usr/bin/env python3
"""In-process mock VAST VMS REST API for exercising the opstat TUI end to end.

Serves just enough of the VMS surface (``/clusters/``, ``/monitors/``,
``/cnodes/``, ``/views/``, ``/tenants/``, ``/volumes/``, ``/metrics/`` ...) over
HTTPS with a self-signed certificate so a real ``opstat`` process can be driven
against it. Every request is recorded, which makes the mock the measurement
instrument for API-call accounting as well as a functional test double.

Run standalone::

    python3 tests/mock_vms.py --port 8443

Or embed::

    with MockVMS() as vms:
        ...
        print(vms.counts())
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ---------------------------------------------------------------------------
# Synthetic cluster inventory
# ---------------------------------------------------------------------------
CLUSTERS = [
    {
        "id": 1,
        "name": "mock-cluster",
        "local": True,
        "sw_version": "5.4.3.1.14178074658457882785",
        "guid": "mock-guid",
    }
]

CNODES = [
    {"id": 100 + i, "name": f"cnode-{i:02d}", "hostname": f"cnode-{i:02d}.mock",
     "mgmt_ip": f"10.0.0.{10 + i}"}
    for i in range(12)
]

# Deliberately large (the real cluster this was validated against has ~429)
# so the drill ranking path cannot rely on scanning everything cheaply.
VIEWS = [
    {"id": 1000 + i, "path": f"/view/{i:03d}", "name": f"view-{i:03d}",
     "title": f"view-{i:03d}"}
    for i in range(429)
]

TENANTS = [{"id": 10 + i, "name": f"tenant-{i}"} for i in range(6)]

VOLUMES = [
    {"id": 500 + i, "name": f"vol-{i:02d}", "subsystem_name": f"subsys-{i % 3}",
     "size": 1 << 40}
    for i in range(24)
]

VIPS = [{"id": 700 + i, "ip": f"10.1.0.{i + 1}", "name": f"vip-{i}"} for i in range(4)]

# Modeled from real var203 evidence (probe rounds 2-4): /blockhosts/ returns
# six objects, and blockhost-scoped monitors echo BlockMetrics unrewritten
# while carrying no per-object rows on that build (the D-013 dead-scope
# shape, reproduced via state.batch_unsplittable, not hardcoded here).
BLOCKHOSTS = [
    {"id": 1 + i, "name": f"blockhost-{i}",
     "nqn": f"nqn.2014-08.org.nvmexpress:uuid:mock-{i:04d}"}
    for i in range(6)
]


# Which views/tenants actually carry load, most-active first. Deliberately
# placed deep in the /views/ listing so an implementation that ranks by
# "first N objects returned" picks the wrong set and the tests catch it.
_ACTIVE_VIEW_INDEXES = [317, 288, 401, 12, 355, 190, 260, 99, 333, 77, 210, 44]
_ACTIVE_TENANT_INDEXES = [4, 1, 5, 2]

assert max(_ACTIVE_VIEW_INDEXES) < len(VIEWS)
assert max(_ACTIVE_TENANT_INDEXES) < len(TENANTS)

_ACTIVE_VIEW_IDS = {VIEWS[i]["id"]: rank
                    for rank, i in enumerate(_ACTIVE_VIEW_INDEXES)}
_ACTIVE_TENANT_IDS = {TENANTS[i]["id"]: rank
                      for rank, i in enumerate(_ACTIVE_TENANT_INDEXES)}

# Block (NVMe) activity, planted the same way: the busiest cNode is index 10
# of 12 and the busiest VIP is index 3 of 4, so an engine that head-slices the
# first 8 objects by API order misses the hot cNode entirely. Ranking
# correctness is therefore observable, exactly as for views/tenants.
_ACTIVE_BLOCK_INDEXES_CNODE = (10, 4, 1)
_ACTIVE_BLOCK_INDEXES_VIP = (3, 0)
_ACTIVE_BLOCK_IDS = dict(
    [(CNODES[i]["id"], rank)
     for rank, i in enumerate(_ACTIVE_BLOCK_INDEXES_CNODE)]
    + [(VIPS[i]["id"], rank)
       for rank, i in enumerate(_ACTIVE_BLOCK_INDEXES_VIP)]
)


# Metric names a VAST build advertises via /metrics/. The default set models
# a cluster that exports NFSv4.1 state/session counters but no pNFS/layout
# telemetry, which is the interesting case for discovery.
_CATALOG_STATE_OPS = (
    "open", "close", "open_downgrade", "lock", "locku", "lockt",
    "release_lockowner", "delegreturn", "sequence", "exchange_id",
    "create_session", "destroy_session", "reclaim_complete",
)
_CATALOG_V3_OPS = (
    "read", "write", "getattr", "setattr", "lookup", "access", "readdir",
    "readdirplus", "create", "remove", "rename", "mkdir", "rmdir", "link",
    "symlink", "readlink", "commit",
)
DEFAULT_CATALOG = (
    [f"NfsMetrics,nfs_{op}_latency__rate" for op in _CATALOG_V3_OPS]
    + [f"NfsMetrics,nfs_{op}_latency__avg" for op in _CATALOG_V3_OPS]
    + [f"NfsMetrics,nfs_{op}_latency__rate" for op in _CATALOG_STATE_OPS]
    + [f"NfsMetrics,nfs_{op}_latency__avg" for op in _CATALOG_STATE_OPS]
    + [
        "ProtoMetrics,proto_name=NFS4Common,rd_iops",
        "ProtoMetrics,proto_name=NFS4Common,wr_iops",
        "ProtoMetrics,proto_name=NFS4Common,md_iops",
    ]
    # VAST OS 5.5.0.1 publishes a full statistical surface beside every
    # ProtoMetrics gauge; opstat reads only __avg unless told otherwise.
    + [
        f"ProtoMetrics,proto_name=NFS4Common,{base}{suffix}"
        for base in ("read_latency", "write_latency", "read_size", "write_size")
        for suffix in ("__avg", "__max", "__std", "__rate", "__time_avg",
                       "__num_samples", "__sum", "__sum_squares")
    ]
    + [
        "ProtoMetrics,proto_name=NFSCommon,rd_bw",
        "ProtoMetrics,proto_name=NFSCommon,wr_bw",
        "ProtoMetrics,proto_name=SMBCommon,md_iops",
        "ProtoMetrics,proto_name=S3Common,rd_bw",
        "ViewMetrics,read_iops__rate",
        "TenantMetrics,read_iops__sum",
    ]
)


def _activity_scale(prop, object_id):
    """Per-object activity multiplier for object-scoped metric families.

    Cluster-scope props (no object) always carry load. View/Tenant props only
    carry load for the objects in the activity tables above, so ranking
    correctness is observable.
    """
    if prop.startswith("ViewMetrics,"):
        rank = _ACTIVE_VIEW_IDS.get(object_id)
        return 0.0 if rank is None else 1.0 / (rank + 1)
    if prop.startswith("TenantMetrics,"):
        rank = _ACTIVE_TENANT_IDS.get(object_id)
        return 0.0 if rank is None else 1.0 / (rank + 1)
    if object_id is not None and (
            prop.startswith("BlockMetrics,") or prop.startswith("VolumeMetrics,")):
        rank = _ACTIVE_BLOCK_IDS.get(object_id)
        return 0.0 if rank is None else 1.0 / (rank + 1)
    return 1.0


def _metric_value(prop, seed, t, object_id=None):
    """Deterministic-but-lively synthetic value for a monitor property."""
    h = 0
    for ch in prop:
        h = (h * 31 + ord(ch)) & 0xFFFF
    base = (h % 997) + 1
    scale = _activity_scale(prop, object_id)
    wobble = 1.0 + 0.25 * math.sin((t + seed) / 7.0 + h)
    if "__sum" in prop or "num_samples" in prop:
        # Cumulative counters: a large lifetime base plus monotonic growth
        # proportional to this object's activity, as VMS TenantMetrics do.
        return round(base * 1_000_000.0 + t * base * 20.0 * scale, 1)
    if prop.startswith("BlockMetrics,") and prop.endswith("_req"):
        # BlockMetrics *_req are cumulative lifetime counters: the engine's
        # own extraction differences them (rate_from_counter_delta /
        # rate_from_timeseries, with counter-reset handling), so the mock
        # publishes the matching monotonic shape rather than a gauge.
        return round(base * 1_000_000.0 + t * base * 20.0 * scale, 1)
    if scale == 0.0:
        return 0.0
    # Keep a gauge's statistical variants mutually consistent: max above
    # average, std a fraction of it. Independent values made the derived
    # distribution panel show a max below its own average.
    if "__max" in prop:
        return round(base * 3.0 * wobble * 2.5, 3)
    if "__std" in prop:
        return round(base * 3.0 * wobble * 0.3, 3)
    if "latency" in prop and "__avg" in prop:
        return round(base * 3.0 * wobble, 3)
    if "_bw" in prop or "bw__" in prop:
        return round(base * 5.0e6 * wobble * scale, 1)
    return round(base * 0.5 * wobble * scale, 3)


OPENAPI_SPEC = {
    "openapi": "3.0.0",
    "info": {"title": "VAST Management System", "version": "5.5.0.1"},
    "paths": {
        "/clusters/": {"get": {"summary": "List clusters"}},
        "/views/": {"get": {"summary": "List views (NFS/SMB/S3 exports)"}},
        "/viewpolicies/": {"get": {"summary": "View policies incl. NFS security flavors"}},
        "/tenants/": {"get": {"summary": "List tenants"}},
        "/cnodes/": {"get": {"summary": "List cnodes"}},
        "/vippools/": {"get": {"summary": "VIP pools used by NFS clients"}},
        "/monitors/": {"get": {"summary": "List monitors"}, "post": {"summary": "Create monitor"}},
        "/monitors/topn/": {"get": {"summary": "Top-N performers by metric"}},
        "/monitors/{id}/query/": {"get": {"summary": "Query a monitor"}},
        "/metrics/": {"get": {"summary": "Metric catalog"}},
        "/prometheusmetrics/": {"get": {"summary": "Prometheus exporter, cluster scope"}},
        "/prometheusmetrics/views": {"get": {"summary": "Prometheus exporter, per view"}},
        "/prometheusmetrics/basic": {"get": {"summary": "Prometheus exporter, basic scope"}},
        "/prometheusmetrics/host_view": {"get": {"summary": "Prometheus exporter, per client host"}},
        "/prometheusmetrics/user_view": {"get": {"summary": "Prometheus exporter, per user"}},
        "/prometheusmetrics/vip_view": {"get": {"summary": "Prometheus exporter, per VIP"}},
        "/prometheusmetrics/tenants": {"get": {"summary": "Prometheus exporter, per tenant"}},
        "/clusters/list_nfs_client_connections/": {
            "get": {"summary": "Active NFS client connections"}},
        "/openfilehandles/": {"get": {"summary": "Open file handles by protocol"}},
    },
}

# Exposition-format bodies keyed by exporter path.
PROMETHEUS_BODIES = {
    "/api/prometheusmetrics/": """# HELP vast_cluster_nfs_iops NFS operations per second, cluster wide
# TYPE vast_cluster_nfs_iops gauge
vast_cluster_nfs_iops{cluster="mock-cluster",protocol="NFS4"} 421.5
vast_cluster_nfs_iops{cluster="mock-cluster",protocol="NFS3"} 88.0
# HELP vast_cluster_nfs_client_count Distinct NFS clients seen in the last window
# TYPE vast_cluster_nfs_client_count gauge
vast_cluster_nfs_client_count{cluster="mock-cluster"} 17
""",
    "/api/prometheusmetrics/basic": """# HELP vast_collector_errors_total Multiprocess metric
# TYPE vast_collector_errors_total counter
vast_collector_errors_total 0
""",
    "/api/prometheusmetrics/all": """# HELP vast_collector_errors_total Multiprocess metric
# TYPE vast_collector_errors_total counter
vast_collector_errors_total 0
""",
    "/api/prometheusmetrics/views": """# HELP vast_view_logical_capacity View Logical Capacity
# TYPE vast_view_logical_capacity gauge
vast_view_logical_capacity{cluster="mock-cluster",name="v317",path="/view/317",protocols="['NFS4', 'SMB']",tenant_name="tenant-4"} 1024
vast_view_logical_capacity{cluster="mock-cluster",name="v288",path="/view/288",protocols="['S3']",tenant_name="tenant-1"} 2048
""",
    # Per-client attribution, the shape VAST OS 5.5.0.1 actually serves:
    # twelve gauges per client IP x view path x protocol.
    "/api/prometheusmetrics/host_view": "".join(
        [f"# HELP vast_host_view_{field} VMS host-view {field}\n"
         f"# TYPE vast_host_view_{field} gauge\n"
         + "".join(
             f'vast_host_view_{field}{{alias="",bucket="",cluster="mock-cluster",'
             f'ip="{ip}",path="{path}",protocol="{proto}",share="",'
             f'tenant="{tenant}"}} {value}\n'
             for ip, path, proto, tenant, value in (
                 ("10.9.0.1", "/view/317", "NFS4", "tenant-4", base * 1.0),
                 ("10.9.0.2", "/view/288", "NFS4", "tenant-1", base * 0.27),
                 ("10.9.0.3", "/share/a", "SMB", "tenant-0", base * 0.07),
                 ("10.9.0.4", "/view/012", "NFS3", "tenant-2", base * 0.5),
             ))
         for field, base in (
             ("iops", 44.2), ("read_iops", 30.1), ("write_iops", 14.1),
             ("md_iops", 6.0), ("bw", 1048576.0), ("read_bw", 700000.0),
             ("write_bw", 348576.0), ("latency", 812.0),
         )]),
    "/api/prometheusmetrics/user_view": """# HELP vast_user_view_iops VMS user-view iops
# TYPE vast_user_view_iops gauge
vast_user_view_iops{cluster="mock-cluster",path="/view/317",protocol="NFS4",tenant="tenant-4",uid="1001",username="alice"} 30.1
""",
    "/api/prometheusmetrics/vip_view": """# HELP vast_vip_view_iops VMS vip-view iops
# TYPE vast_vip_view_iops gauge
vast_vip_view_iops{cluster="mock-cluster",path="/view/317",protocol="NFS4",tenant="tenant-4",vip="10.1.0.1",vippool="nfs-pool"} 41.0
""",
}


# NFSv4 operations VAST OS 5.5.0.1 exposes through the Prometheus exporter's
# Nfs4Metrics family (cluster and cNode scope). Latency sums are microseconds.
NFS4_EXPORTER_OPS = (
    "access", "close", "commit", "create", "create_session",
    "destroy_clientid", "destroy_session", "exchange_id", "free_stateid",
    "getattr", "getfh", "lookup", "lookupp", "open", "putfh", "putpubfh",
    "putrootfh", "read", "readdir", "reclaim_complete", "remove",
    "restorefh", "savefh", "secinfo", "secinfo_no_name", "sequence",
    "setattr", "test_stateid", "write",
)
# Real hostnames differ only in a trailing digit, which is what made a
# right-truncated column render every cNode identically.
_NFS4_CNODES = ((1, "se-az-arrow-cb4-cn1"), (2, "se-az-arrow-cb4-cn2"),
                (3, "se-az-arrow-cb4-cn3"))

# Observed on VAST OS 5.5.0.1: SEQUENCE and GETFH are well under 1 us.
_NFS4_SUBMICRO_OPS = {"sequence": 0.42, "getfh": 0.19, "putfh": 3.1}


def _nfs4_exposition(elapsed):
    """Cumulative count/sum gauges that grow with elapsed time.

    Mirrors the real exporter: gauges by TYPE, but monotonically increasing,
    so a client must difference two scrapes to obtain a rate.
    """
    out = []
    for idx, op in enumerate(NFS4_EXPORTER_OPS):
        rate = 5.0 + idx * 1.5              # operations per second
        # Match the real cluster's shape: session-slot and filehandle
        # bookkeeping is sub-microsecond, data operations are hundreds of us.
        latency_us = _NFS4_SUBMICRO_OPS.get(op, 200.0 + idx * 37.0)
        count = 1_000_000 + rate * elapsed
        total = count * latency_us
        base = f"vast_cluster_metrics_Nfs4Metrics_nfs4_{op}_req_latency"
        out.append(f"# TYPE {base}_count gauge")
        out.append(f'{base}_count{{cluster="mock-cluster"}} {count:.0f}')
        out.append(f"# TYPE {base}_sum gauge")
        out.append(f'{base}_sum{{cluster="mock-cluster"}} {total:.0f}')
        for cnode_id, hostname in _NFS4_CNODES:
            cbase = f"vast_cnode_metrics_Nfs4Metrics_nfs4_{op}_req_latency"
            ccount = count / len(_NFS4_CNODES)
            out.append(
                f'{cbase}_count{{cluster="mock-cluster",cnode_id="{cnode_id}",'
                f'hostname="{hostname}"}} {ccount:.0f}')
            out.append(
                f'{cbase}_sum{{cluster="mock-cluster",cnode_id="{cnode_id}",'
                f'hostname="{hostname}"}} {ccount * latency_us:.0f}')
    out.append("# HELP vast_cluster_metrics_Nfs4Metrics_nfs4_open_connections_cnt Open NFSv4 connections")
    out.append("# TYPE vast_cluster_metrics_Nfs4Metrics_nfs4_open_connections_cnt gauge")
    out.append('vast_cluster_metrics_Nfs4Metrics_nfs4_open_connections_cnt{cluster="mock-cluster"} 42')
    # The real exporter publishes the connection gauge per cNode too.
    for idx, (cnode_id, hostname) in enumerate(_NFS4_CNODES):
        out.append(
            'vast_cnode_metrics_Nfs4Metrics_nfs4_open_connections_cnt'
            f'{{cluster="mock-cluster",cnode_id="{cnode_id}",'
            f'hostname="{hostname}"}} {12 + idx}')
    return "\n".join(out) + "\n"


class _State:
    def __init__(self):
        self.lock = threading.Lock()
        self.monitors = {}
        self.next_monitor_id = 9000
        self.calls = []          # [(monotonic_ts, method, path, status)]
        self.connections = 0     # TCP connections accepted (keep-alive metric)
        self.open_sockets = set()  # live sockets, severed on stop()
        self.reject_granularity_auto = False
        # Prop-name prefixes this "cluster build" does not export: they are
        # accepted at create time but omitted from query prop_lists, matching
        # how engines probe real VMS capability differences.
        self.unsupported_prop_prefixes = ()
        # When True, POST /monitors/ rejects prop_lists that mix metric
        # families (text before the first comma), for fallback-path testing.
        self.reject_mixed_families = False
        # Prop-name prefixes that make POST /monitors/ fail outright, for
        # engines whose fallback path reacts to a rejected create.
        self.reject_prop_prefixes = ()
        # object_type values POST /monitors/ rejects outright. Models a build
        # that does not support a monitor scope at all (e.g. object_type=vip),
        # so an engine's scope-specific fallback path is exercised.
        self.reject_object_types = ()
        # Observed on var203 (VAST OS 5.5.0.1): a multi-object_id monitor at
        # object_type=vip / =blockhost is CREATED and QUERIED successfully but
        # its response carries no usable per-object rows, while the same shape
        # at object_type=cnode splits correctly. Creation succeeding is
        # therefore not evidence that a batch layout is usable, and an engine
        # must validate the response before committing to one.
        # Maps object_type -> "no_object_id" (response omits the column) or
        # "no_matching_rows" (column present, no rows for the requested ids).
        # The exact var203 shape is not yet distinguished, so both are
        # modelled and both must force the per-object fallback.
        self.batch_unsplittable = {}
        # Monitor ids whose DELETE fails with HTTP 500 (each id fails every
        # attempt). Models the round-3 var203 shutdown, where one failing
        # delete aborted the drain loop and orphaned the monitors after it.
        self.fail_delete_ids = set()
        # Population overrides for scaling tests: the dead-scope discovery
        # cost must be O(1) in the object count (round 4: 378 VIPs devolved
        # into 189 rank monitors), so tests need to grow these arbitrarily.
        # None -> the module-level defaults.
        self.vips = None
        self.cnodes = None
        self.blockhosts = None
        # Names of every monitor created since the last reset_calls(), in
        # order. calls() records only (ts, method, path, status), so tests
        # that need to count monitors BY PURPOSE (rank vs batch vs headline)
        # read this - filtering the calls tuple for a name matches nothing,
        # which made two budget assertions vacuous until a storm slipped by.
        self.created_names = []
        # Reproduce observed VMS 5.5.0.1 behavior: the newest bucket of an
        # object-scoped monitor is still filling, so every property except
        # the ones listed here comes back null in that row. Set to None to
        # emit fully-populated newest rows.
        self.partial_newest_props = ("ViewMetrics,read_md_iops__rate",)
        # Largest object_ids list POST /monitors/ will accept (None = no cap).
        # Real clusters cap this; engines must discover it and adapt.
        self.max_object_ids = None
        # Serve /monitors/topn/ (some builds/permissions do not).
        self.topn_enabled = True
        # Metric names /metrics/ advertises. Defaults model a build that
        # exports v4.1 state/session counters but no pNFS/layout telemetry.
        self.catalog = set(DEFAULT_CATALOG)
        # Alternative observability surfaces.
        self.openapi_enabled = True
        self.prometheus = dict(PROMETHEUS_BODIES)
        # Nfs4Metrics rides the broad exporter endpoints, as on a real cluster.
        self.nfs4_exporter = True
        self.nfs4_exporter_paths = {"/api/prometheusmetrics/",
                                    "/api/prometheusmetrics/basic",
                                    "/api/prometheusmetrics/all"}
        self.delegations_enabled = True
        self.latency_s = 0.0
        self.t0 = time.time()


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    state: _State = None  # injected

    # -- plumbing ----------------------------------------------------------
    def setup(self):
        super().setup()
        with self.state.lock:
            self.state.connections += 1
            self.state.open_sockets.add(self.connection)

    def finish(self):
        with self.state.lock:
            self.state.open_sockets.discard(self.connection)
        super().finish()

    def log_message(self, *_args):  # silence stderr noise
        pass

    def _record(self, status):
        with self.state.lock:
            self.state.calls.append(
                (time.monotonic(), self.command, self.path, status)
            )

    def _send(self, payload, status=200):
        body = b"" if payload is None else json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)
        self._record(status)

    def _error(self, status, detail):
        self._send({"detail": detail}, status)

    def _read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return None
        try:
            return json.loads(self.rfile.read(length).decode())
        except ValueError:
            return None

    # -- routing -----------------------------------------------------------
    def do_GET(self):
        if self.state.latency_s:
            time.sleep(self.state.latency_s)
        path = urllib.parse.urlsplit(self.path).path
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)

        simple = {
            "/api/clusters/": CLUSTERS,
            "/api/cnodes/": self.state.cnodes if self.state.cnodes is not None else CNODES,
            "/api/views/": VIEWS,
            "/api/tenants/": TENANTS,
            "/api/volumes/": VOLUMES,
            "/api/vips/": self.state.vips if self.state.vips is not None else VIPS,
            "/api/blockhosts/": (self.state.blockhosts
                                 if self.state.blockhosts is not None else BLOCKHOSTS),
        }
        if path in simple:
            return self._send(simple[path])

        if path == "/api/metrics/":
            entries = [{"metric": m} for m in sorted(self.state.catalog)]
            return self._send({"data": entries})

        if path == "/api/openfilehandles/":
            return self._send({"results": [
                {"id": i, "path": f"/share/file{i}", "client_ip": "10.2.0.5"}
                for i in range(8)
            ]})

        if path == "/api/monitors/topn/":
            if not self.state.topn_enabled:
                return self._error(404, "topn not available on this build")
            obj_type = (query.get("object_type") or [None])[0]
            if obj_type == "view":
                # Ranked most-active first, titled by view path so callers can
                # map back to ids from /views/.
                return self._send({"data": {"view": {"iops": [
                    {"title": VIEWS[i]["path"], "value": 500.0 - i}
                    for i in _ACTIVE_VIEW_INDEXES[:16]
                ]}}})
            if obj_type == "tenant":
                return self._send({"data": {"tenant": {"iops": [
                    {"title": TENANTS[i]["name"], "value": 400.0 - i}
                    for i in _ACTIVE_TENANT_INDEXES[:16]
                ]}}})

            def block(prefix):
                return [{"title": f"{prefix}{i}", "value": 1000.0 - i * 37}
                        for i in range(10)]

            # Real VMS topn rows carry {title, total, read, write}; the vip
            # topn-only fallback reads those, so model them for vip. External
            # (non-192.168) titles so the fallback survives IP filtering.
            def vip_block(scale):
                return [{"title": f"10.1.0.{i + 1}", "total": scale - i * 40,
                         "read": scale * 0.6 - i * 24, "write": scale * 0.4 - i * 16}
                        for i in range(8)]
            return self._send({"data": {
                "client": {"md_iops": block("10.2.0."), "iops": block("10.2.0."),
                           "bw": block("10.2.0."), "latency": block("10.2.0.")},
                "view": {"md_iops": block("/view/"), "iops": block("/view/"),
                         "bw": block("/view/")},
                "vip": {"iops": vip_block(500.0), "bw": vip_block(120.0),
                        "latency": vip_block(800.0)},
                "user": {"md_iops": block("user")},
            }})

        if path == "/api/clusters/list_smb_client_connections/":
            return self._send({"connections": [
                {"client_ip": (query.get("client_ip") or ["?"])[0],
                 "user": "mock", "share": "data"}
            ]})

        if path in ("/api/openapi.json", "/openapi.json"):
            if not self.state.openapi_enabled:
                return self._error(404, "no openapi here")
            return self._send(OPENAPI_SPEC)

        m = re.match(r"^/api/tenants/(\d+)/nfs4_delegs/$", path)
        if m:
            if not self.state.delegations_enabled:
                return self._error(404, "delegation endpoint absent")
            tid = int(m.group(1))
            if "file_path" not in query:
                return self._error(
                    400, "['__root__->file_path: field required']")
            records = [
                {"client_ip": f"10.9.0.{i}", "path": f"/view/{300 + i}",
                 "stateid": f"0x{tid:04x}{i:04x}", "deleg_type": "READ",
                 "tenant_id": tid, "created_at": "2026-08-13T14:00:00Z"}
                for i in range(2 if tid % 2 == 0 else 0)
            ]
            # Real VMS wraps records in delegate_info beside pagination keys.
            return self._send({
                "delegate_info": records,
                "delegate_info_count_total": len(records),
                "xeystore_pagination": None,
                "xeystore_pagination_next_client_id": None,
            })

        if path.startswith("/api/prometheusmetrics"):
            body = self.state.prometheus.get(path)
            if body is None:
                return self._error(404, "no exporter at this scope")
            if self.state.nfs4_exporter and path in self.state.nfs4_exporter_paths:
                body = body + _nfs4_exposition(time.time() - self.state.t0)
            raw = body.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return self._record(200)

        if path == "/api/clusters/list_nfs_client_connections/":
            return self._send({"connections": [
                {"client_ip": f"10.9.0.{i}", "protocol": "NFS4.1", "view": "/view/317"}
                for i in range(5)
            ]})

        if path == "/api/viewpolicies/":
            return self._send([
                {"id": 1, "name": "default", "nfs_no_squash": ["*"],
                 "flavor": "SYS", "nfs_version": "4.1", "protocols": ["NFS4"]},
            ])

        if path == "/api/vippools/":
            return self._send([
                {"id": 1, "name": "nfs-pool", "role": "PROTOCOLS",
                 "vips": ["10.1.0.1", "10.1.0.2"]},
            ])

        m = re.match(r"^/api/monitors/(\d+)/query/$", path)
        if m:
            return self._monitor_query(int(m.group(1)))

        return self._error(404, f"no mock route for {path}")

    def do_POST(self):
        if self.state.latency_s:
            time.sleep(self.state.latency_s)
        path = urllib.parse.urlsplit(self.path).path
        payload = self._read_body() or {}
        if path != "/api/monitors/":
            return self._error(404, f"no mock route for {path}")
        if self.state.reject_granularity_auto and payload.get("granularity") == "auto":
            return self._error(400, "Invalid granularity: auto")
        if self.state.reject_mixed_families:
            families = {str(p).split(",", 1)[0] for p in payload.get("prop_list") or []}
            if len(families) > 1:
                return self._error(400, "cannot mix metric families in one monitor")
        if self.state.reject_prop_prefixes:
            for p in payload.get("prop_list") or []:
                if str(p).startswith(tuple(self.state.reject_prop_prefixes)):
                    return self._error(400, f"unsupported metric: {p}")
        if self.state.reject_object_types and \
                payload.get("object_type") in self.state.reject_object_types:
            return self._error(
                400, f"unsupported object_type: {payload.get('object_type')}")
        cap = self.state.max_object_ids
        if cap is not None and len(payload.get("object_ids") or []) > cap:
            return self._error(400, f"too many object_ids (max {cap})")
        with self.state.lock:
            self.state.created_names.append(str(payload.get("name", "")))
            monitor_id = self.state.next_monitor_id
            self.state.next_monitor_id += 1
            self.state.monitors[monitor_id] = {
                "prop_list": list(payload.get("prop_list") or []),
                "object_ids": list(payload.get("object_ids") or []),
                "object_type": payload.get("object_type"),
            }
        return self._send({"id": monitor_id, "name": payload.get("name")}, 201)

    def do_DELETE(self):
        if self.state.latency_s:
            time.sleep(self.state.latency_s)
        path = urllib.parse.urlsplit(self.path).path
        m = re.match(r"^/api/monitors/(\d+)/$", path)
        if not m:
            return self._error(404, f"no mock route for {path}")
        monitor_id = int(m.group(1))
        if monitor_id in self.state.fail_delete_ids:
            return self._error(500, "delete failed (injected)")
        with self.state.lock:
            existed = self.state.monitors.pop(monitor_id, None)
        if existed is None:
            return self._error(404, "monitor not found")
        return self._send({"deleted": monitor_id})

    # -- monitor query synthesis -------------------------------------------
    def _monitor_query(self, monitor_id):
        with self.state.lock:
            mon = self.state.monitors.get(monitor_id)
        if mon is None:
            return self._error(404, "monitor not found")

        props = [
            p for p in mon["prop_list"]
            if not str(p).startswith(tuple(self.state.unsupported_prop_prefixes))
        ] if self.state.unsupported_prop_prefixes else mon["prop_list"]
        object_ids = mon["object_ids"] or [None]
        batch = len(object_ids) > 1
        # A batch this "cluster build" accepts but cannot actually split. The
        # request succeeds; the response is simply unusable per object.
        unsplittable = (
            self.state.batch_unsplittable.get(mon.get("object_type"))
            if batch else None
        )
        if unsplittable == "no_matching_rows":
            # object_id column present, but every row belongs to an id the
            # caller did not ask for, so each requested id slices to zero rows.
            object_ids = [-1]
        batch_column = batch and unsplittable != "no_object_id"
        prop_list = ["timestamp"] + (["object_id"] if batch_column else []) + props

        now = time.time() - self.state.t0
        partial = self.state.partial_newest_props
        rows = []
        # Newest first, matching the VMS ordering the engines assume.
        for step in range(12):
            t = now - step * 5.0
            stamp = time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.state.t0 + t)
            )
            for oid in object_ids:
                seed = 0 if oid is None else (int(oid) if str(oid).isdigit() else 0)
                row = [stamp]
                if batch_column:
                    row.append(oid)
                for p in props:
                    # The newest bucket is still filling on a real VMS: only
                    # a subset of properties has landed, the rest are null.
                    if step == 0 and partial is not None and p not in partial:
                        row.append(None)
                    else:
                        row.append(_metric_value(p, seed, t, oid))
                rows.append(row)
        return self._send({"prop_list": prop_list, "data": rows})


def _self_signed_cert(directory):
    """Return (cert_path, key_path), generating a throwaway pair if needed."""
    cert = os.path.join(directory, "mock-vms.pem")
    if os.path.exists(cert):
        return cert, cert
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
         "-keyout", cert, "-out", cert, "-days", "2",
         "-subj", "/CN=localhost"],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return cert, cert


class _QuietServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        # Broken pipes are expected when stop() severs keep-alive sockets.
        pass


class MockVMS:
    """Threaded HTTPS mock VMS. Use as a context manager."""

    def __init__(self, port=0, certdir=None):
        self.state = _State()
        self._certdir = certdir or tempfile.gettempdir()
        handler = type("BoundHandler", (_Handler,), {"state": self.state})
        self._server = _QuietServer(("127.0.0.1", port), handler)
        cert, key = _self_signed_cert(self._certdir)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, key)
        self._server.socket = ctx.wrap_socket(self._server.socket, server_side=True)
        self.port = self._server.server_address[1]
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        kwargs={"poll_interval": 0.05}, daemon=True)
        self._thread.start()
        return self

    def stop(self):
        self._server.shutdown()
        self._server.server_close()
        # Sever established keep-alive connections so a stopped mock actually
        # looks like an outage to a client holding a persistent socket.
        with self.state.lock:
            sockets = list(self.state.open_sockets)
            self.state.open_sockets.clear()
        for sock in sockets:
            # shutdown(), not close(): the handler thread holds makefile()
            # references, so close() alone defers the FIN and the client
            # would keep getting served after the "outage".
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            try:
                sock.close()
            except Exception:
                pass
        if self._thread:
            self._thread.join(timeout=5)

    def __enter__(self):
        return self.start()

    def __exit__(self, *_exc):
        self.stop()

    # -- measurement -------------------------------------------------------
    def calls(self):
        with self.state.lock:
            return list(self.state.calls)

    def reset_calls(self):
        with self.state.lock:
            self.state.calls.clear()
            self.state.created_names.clear()

    def created_monitor_names(self):
        """Names of monitors created since the last reset_calls(), in order."""
        with self.state.lock:
            return list(self.state.created_names)

    def counts(self):
        """Return {"METHOD normalized-path": count} with ids collapsed."""
        out = {}
        for _ts, method, path, _status in self.calls():
            norm = re.sub(r"/\d+/", "/{id}/", urllib.parse.urlsplit(path).path)
            key = f"{method} {norm}"
            out[key] = out.get(key, 0) + 1
        return out

    def live_monitors(self):
        with self.state.lock:
            return dict(self.state.monitors)

    def connection_count(self):
        with self.state.lock:
            return self.state.connections


def main(argv=None):
    ap = argparse.ArgumentParser(description="Standalone mock VAST VMS API")
    ap.add_argument("--port", type=int, default=8443)
    args = ap.parse_args(argv)
    vms = MockVMS(port=args.port).start()
    print(f"mock VMS listening on https://127.0.0.1:{vms.port}/api", flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        vms.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
