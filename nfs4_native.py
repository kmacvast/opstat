#!/usr/bin/env python3
"""Native NFSv4 telemetry from the VMS Prometheus exporter.

The VMS monitor API exposes no NFSv4.1 protocol-state counters, but the
Prometheus exporter carries an ``Nfs4Metrics`` family with 29 native NFSv4
operations plus an open-connection gauge, at both cluster and cNode scope
(established against VAST OS 5.5.0.1 - see ``--discover-metrics``).

Semantics proven against a real cluster:

* ``*_req_latency_count`` and ``*_req_latency_sum`` are **cumulative** totals
  published as Prometheus gauges. Rates require differencing two scrapes.
* the sums are **microseconds**: a lifetime mean read latency of 541.4
  against ``NFS4Common read_latency__avg`` of 588.5 us, a ratio of 0.92, and
  a physically coherent ordering from ``getfh`` at 0.2 us through ``open`` at
  1204 us.
* the narrowest endpoint carrying the whole family is
  ``/prometheusmetrics/basic`` at ~276 KB, answering in 1.2-2.4 s. That cost
  is why this is an on-demand drill and never sits on the dashboard refresh.

``vast_host_view_*`` is a separate, much cheaper endpoint (~5 KB) whose
series are instantaneous gauges, so client attribution needs only one scrape.
"""

import re
import time

# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
# Narrowest endpoint proven to carry the complete Nfs4Metrics family. Never
# /prometheusmetrics/all: 4.8 MB for the same 118 series.
NFS4_ENDPOINT = "/prometheusmetrics/basic"
HOST_VIEW_ENDPOINT = "/prometheusmetrics/host_view"

# The exporter recomputes on its own schedule and each scrape costs seconds,
# so re-scrape far less often than the dashboard refreshes.
DEFAULT_MIN_INTERVAL = 30.0

_CLUSTER_PREFIX = "vast_cluster_metrics_Nfs4Metrics_nfs4_"
_CNODE_PREFIX = "vast_cnode_metrics_Nfs4Metrics_nfs4_"
_OPEN_CONNECTIONS = "open_connections_cnt"

_SAMPLE_RE = re.compile(
    r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{([^}]*)\})?\s+([-+0-9.eENaN]+)\s*$")
_LABEL_RE = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)="((?:[^"\\]|\\.)*)"')

# Session and client-lifecycle operations, in the order they are displayed.
SESSION_OPS = (
    ("sequence", "SEQUENCE"),
    ("exchange_id", "EXCHANGE_ID"),
    ("create_session", "CREATE_SESSION"),
    ("destroy_session", "DESTROY_SESSION"),
    ("destroy_clientid", "DESTROY_CLIENTID"),
    ("reclaim_complete", "RECLAIM_COMPLETE"),
)

# Stateful file operations. Deliberately excludes LOCK/LOCKU/delegation/pNFS:
# the exporter does not publish them, and opstat does not invent metrics.
FILE_STATE_OPS = (
    ("open", "OPEN"),
    ("close", "CLOSE"),
    ("free_stateid", "FREE_STATEID"),
    ("test_stateid", "TEST_STATEID"),
)


def _parse_labels(block):
    return dict(_LABEL_RE.findall(block or ""))


def parse_nfs4_exposition(text):
    """Extract Nfs4Metrics series from one exporter scrape.

    Returns ``(cluster, cnodes, connections)`` where *cluster* maps op ->
    {"count": float, "sum": float}, *cnodes* maps (cnode_id, hostname) -> the
    same shape, and *connections* holds the open-connection gauges. One pass
    over the body; non-Nfs4Metrics lines are skipped without parsing labels.
    """
    cluster = {}
    cnodes = {}
    connections = {"cluster": None, "cnodes": {}}

    for line in (text or "").splitlines():
        if not line or line[0] == "#":
            continue
        if "Nfs4Metrics" not in line:
            continue
        match = _SAMPLE_RE.match(line.strip())
        if not match:
            continue
        name, label_block, raw = match.groups()
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if value != value:                      # NaN
            continue

        if name.startswith(_CLUSTER_PREFIX):
            suffix = name[len(_CLUSTER_PREFIX):]
            target, key = cluster, None
        elif name.startswith(_CNODE_PREFIX):
            suffix = name[len(_CNODE_PREFIX):]
            labels = _parse_labels(label_block)
            key = (labels.get("cnode_id", "?"), labels.get("hostname", "?"))
            target = cnodes.setdefault(key, {})
        else:
            continue

        if suffix == _OPEN_CONNECTIONS:
            if key is None:
                connections["cluster"] = value
            else:
                connections["cnodes"][key] = value
            continue

        if suffix.endswith("_req_latency_count"):
            op, field = suffix[:-len("_req_latency_count")], "count"
        elif suffix.endswith("_req_latency_sum"):
            op, field = suffix[:-len("_req_latency_sum")], "sum"
        else:
            continue
        target.setdefault(op, {})[field] = value

    return cluster, cnodes, connections


def _rate(previous, current, elapsed):
    """Derive (ops_sec, avg_us, count_delta) between two cumulative readings.

    Returns None when the pair cannot yield a trustworthy rate: a counter
    reset, a non-positive interval, or a missing reading. Callers treat that
    as "re-baseline", never as zero.
    """
    if not previous or not current or elapsed is None or elapsed <= 0:
        return None
    c0, c1 = previous.get("count"), current.get("count")
    if c0 is None or c1 is None:
        return None
    count_delta = c1 - c0
    if count_delta < 0:
        return None                              # counter reset
    s0, s1 = previous.get("sum"), current.get("sum")
    sum_delta = None
    if s0 is not None and s1 is not None:
        sum_delta = s1 - s0
        if sum_delta < 0:
            return None                          # counter reset
    avg_us = (sum_delta / count_delta) if (count_delta > 0 and sum_delta) else None
    return count_delta / elapsed, avg_us, count_delta


class Nfs4Collector:
    """Holds successive exporter scrapes and derives native NFSv4 rates.

    A single scrape is a warm-up: cumulative counters carry no rate on their
    own. Rates and interval latencies appear only once two consistent samples
    exist, and a counter reset silently re-baselines rather than producing a
    negative rate.
    """

    def __init__(self, request_text_fn, endpoint=NFS4_ENDPOINT,
                 min_interval=DEFAULT_MIN_INTERVAL):
        self._request_text = request_text_fn
        self.endpoint = endpoint
        self.min_interval = min_interval
        self.reset()

    def reset(self):
        self._previous = None          # (monotonic, cluster, cnodes)
        self._current = None
        self.connections = {"cluster": None, "cnodes": {}}
        self.last_scrape_at = 0.0
        self.last_bytes = 0
        self.last_elapsed = 0.0
        self.error = None
        self.scrapes = 0

    # -- acquisition -------------------------------------------------------
    @property
    def warm(self):
        """True once two consistent samples allow rate derivation."""
        return self._previous is not None and self._current is not None

    def due(self, force=False):
        if force or self.scrapes == 0:
            return True
        return (time.monotonic() - self.last_scrape_at) >= self.min_interval

    def scrape(self, force=False):
        """Fetch and fold in one scrape. Returns True when a sample landed.

        A failure leaves the previous samples intact so the panel keeps
        showing the last known good values alongside the error.
        """
        if not self.due(force=force):
            return False
        started = time.monotonic()
        try:
            body = self._request_text("GET", self.endpoint)
        except RuntimeError as exc:
            self.error = str(exc)
            self.last_scrape_at = time.monotonic()
            return False
        self.last_elapsed = time.monotonic() - started
        self.last_scrape_at = time.monotonic()
        self.last_bytes = len(body or "")
        cluster, cnodes, connections = parse_nfs4_exposition(body)
        if not cluster:
            self.error = (
                f"{self.endpoint} returned no Nfs4Metrics series "
                f"({self.last_bytes} bytes)")
            return False
        self.error = None
        self.scrapes += 1
        self.connections = connections
        self._previous, self._current = self._current, (
            time.monotonic(), cluster, cnodes)
        return True

    # -- derivation --------------------------------------------------------
    def _elapsed(self):
        if not self.warm:
            return None
        return self._current[0] - self._previous[0]

    def cluster_rows(self):
        """One row per exported operation, with rate, latency and share.

        Empty until warm. Operations the cluster has never used are still
        listed with a zero rate: a genuine zero is information, and hiding it
        would misrepresent an idle session machinery as missing telemetry.
        """
        if not self.warm:
            return []
        elapsed = self._elapsed()
        _t0, prev_cluster, _n0 = self._previous
        _t1, cur_cluster, _n1 = self._current
        rows = []
        for op in sorted(cur_cluster):
            derived = _rate(prev_cluster.get(op), cur_cluster[op], elapsed)
            if derived is None:
                continue
            ops_sec, avg_us, count_delta = derived
            rows.append({
                "op": op,
                "label": op.upper(),
                "ops_sec": ops_sec,
                "avg_us": avg_us,
                "count_delta": count_delta,
            })
        total = sum(r["ops_sec"] for r in rows) or 0.0
        for row in rows:
            row["share_pct"] = (row["ops_sec"] / total * 100.0) if total else 0.0
        return rows

    def ranked_rows(self):
        """Operations ordered by current rate, busiest first."""
        return sorted(self.cluster_rows(),
                      key=lambda r: (-r["ops_sec"], r["label"]))

    def rows_by_op(self):
        return {r["op"]: r for r in self.cluster_rows()}

    def ops_per_compound(self):
        """DERIVED RATIO - not a native VMS metric.

        VMS publishes no compound counter. NFSv4.1 carries exactly one
        SEQUENCE per compound, so dividing every *other* operation's rate by
        the SEQUENCE rate estimates operations per compound. SEQUENCE is
        excluded from the numerator by definition.
        """
        rows = self.rows_by_op()
        sequence = rows.get("sequence")
        if not sequence or not sequence["ops_sec"]:
            return None
        others = sum(r["ops_sec"] for op, r in rows.items() if op != "sequence")
        return others / sequence["ops_sec"]

    def cnode_rows(self):
        """Per-cNode native NFSv4 activity from the same scrape.

        VMS exporter cNode attribution. Reconciliation has since been
        observed under load on a real cluster: summing the per-cNode rates
        reproduced the cluster figures exactly for every operation checked
        (SEQUENCE 1551.3 + 1.78 + 0.00 = 1553.08 against a cluster 1553.1).
        """
        if not self.warm:
            return []
        elapsed = self._elapsed()
        _t0, _c0, prev_cnodes = self._previous
        _t1, _c1, cur_cnodes = self._current
        rows = []
        for key in sorted(cur_cnodes, key=lambda k: (str(k[1]), str(k[0]))):
            previous = prev_cnodes.get(key, {})
            current = cur_cnodes[key]
            per_op, total_ops, weighted_us, weight = {}, 0.0, 0.0, 0.0
            for op, reading in current.items():
                derived = _rate(previous.get(op), reading, elapsed)
                if derived is None:
                    continue
                ops_sec, avg_us, count_delta = derived
                per_op[op] = ops_sec
                total_ops += ops_sec
                if avg_us is not None and count_delta > 0:
                    weighted_us += avg_us * count_delta
                    weight += count_delta
            rows.append({
                "cnode_id": key[0],
                "hostname": key[1],
                "total_ops": total_ops,
                "ops": per_op,
                "avg_us": (weighted_us / weight) if weight else None,
                "connections": self.connections["cnodes"].get(key),
            })
        return sorted(rows, key=lambda r: -r["total_ops"])


# ---------------------------------------------------------------------------
# Client / view attribution
# ---------------------------------------------------------------------------
_HOST_VIEW_PREFIX = "vast_host_view_"

# Gauges published per client IP x view path x protocol.
_HOST_VIEW_FIELDS = (
    "iops", "read_iops", "write_iops", "md_iops",
    "bw", "read_bw", "write_bw", "latency",
)


def parse_host_view(text, protocol="NFS4"):
    """Rows of per-client attribution for one protocol.

    ``vast_host_view_*`` series are instantaneous gauges, so a single scrape
    suffices - no differencing, no warm-up. Series for other protocols are
    discarded here rather than displayed and explained.
    """
    grouped = {}
    for line in (text or "").splitlines():
        if not line or line[0] == "#" or _HOST_VIEW_PREFIX not in line:
            continue
        match = _SAMPLE_RE.match(line.strip())
        if not match:
            continue
        name, label_block, raw = match.groups()
        if not name.startswith(_HOST_VIEW_PREFIX):
            continue
        field = name[len(_HOST_VIEW_PREFIX):]
        if field not in _HOST_VIEW_FIELDS:
            continue
        labels = _parse_labels(label_block)
        if protocol and labels.get("protocol") != protocol:
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if value != value:
            continue
        key = (labels.get("ip", "?"), labels.get("path", "?"),
               labels.get("tenant", "?"), labels.get("share", ""))
        grouped.setdefault(key, {})[field] = value

    rows = []
    for (ip, path, tenant, share), fields in grouped.items():
        latency_ms = fields.get("latency")
        rows.append({
            "ip": ip,
            "path": path,
            "tenant": tenant,
            "share": share,
            "iops": fields.get("iops"),
            "read_iops": fields.get("read_iops"),
            "write_iops": fields.get("write_iops"),
            "md_iops": fields.get("md_iops"),
            "bw": fields.get("bw"),
            "read_bw": fields.get("read_bw"),
            "write_bw": fields.get("write_bw"),
            # The gauge is native MILLISECONDS (D-014: same-traffic pairing
            # against BlockMetrics-us, ratio median 969x over six samples).
            # Convert at ingestion so every downstream consumer and formatter
            # stays on the proven microsecond pipeline. The prior passthrough
            # understated latency ~1000x on builds with NFS host_view series.
            "latency_us": (latency_ms * 1000.0) if latency_ms is not None else None,
        })
    # Rank by activity, not by the order the exporter happened to emit.
    return sorted(rows, key=lambda r: (-(r["iops"] or 0.0), r["ip"], r["path"]))


class HostViewCollector:
    """On-demand per-client NFSv4 attribution. One cheap scrape, no deltas."""

    def __init__(self, request_text_fn, endpoint=HOST_VIEW_ENDPOINT,
                 min_interval=DEFAULT_MIN_INTERVAL, protocol="NFS4"):
        self._request_text = request_text_fn
        self.endpoint = endpoint
        self.min_interval = min_interval
        self.protocol = protocol
        self.reset()

    def reset(self):
        self.rows = []
        self.last_scrape_at = 0.0
        self.last_bytes = 0
        self.last_elapsed = 0.0
        self.error = None
        self.scrapes = 0

    def due(self, force=False):
        if force or self.scrapes == 0:
            return True
        return (time.monotonic() - self.last_scrape_at) >= self.min_interval

    def scrape(self, force=False):
        if not self.due(force=force):
            return False
        started = time.monotonic()
        try:
            body = self._request_text("GET", self.endpoint)
        except RuntimeError as exc:
            self.error = str(exc)
            self.last_scrape_at = time.monotonic()
            return False
        self.last_elapsed = time.monotonic() - started
        self.last_scrape_at = time.monotonic()
        self.last_bytes = len(body or "")
        self.rows = parse_host_view(body, self.protocol)
        self.error = None
        self.scrapes += 1
        return True


def aggregate_by_path(rows):
    """Roll per-host rows up to one row per view path.

    ``vast_host_view_*`` is emitted per client IP x view path. Summing the
    rates across clients gives per-view NFSv4 attribution that ViewMetrics
    does not provide: on a live cluster the ViewMetrics view drill showed no
    meaningful activity while Nfs4Metrics recorded ~1553 SEQUENCE/s and the
    same host_view scrape attributed that traffic to specific paths.

    Latency is averaged weighted by IOPS, so a busy client dominates the
    figure rather than a quiet one skewing it.
    """
    grouped = {}
    for row in rows:
        key = (row["path"], row["tenant"])
        agg = grouped.setdefault(key, {
            "path": row["path"], "tenant": row["tenant"], "clients": set(),
            "shares": set(),
            "iops": 0.0, "read_iops": 0.0, "write_iops": 0.0, "md_iops": 0.0,
            "bw": 0.0, "read_bw": 0.0, "write_bw": 0.0,
            "_lat_weight": 0.0, "_lat_sum": 0.0,
        })
        agg["clients"].add(row["ip"])
        if row.get("share"):
            agg["shares"].add(row["share"])
        for field in ("iops", "read_iops", "write_iops", "md_iops",
                      "bw", "read_bw", "write_bw"):
            value = row.get(field)
            if value:
                agg[field] += value
        weight = row.get("iops") or 0.0
        latency = row.get("latency_us")
        if latency is not None and weight > 0:
            agg["_lat_weight"] += weight
            agg["_lat_sum"] += latency * weight

    out = []
    for agg in grouped.values():
        agg["client_count"] = len(agg.pop("clients"))
        shares = agg.pop("shares")
        agg["share"] = ",".join(sorted(shares)) if shares else ""
        weight = agg.pop("_lat_weight")
        total = agg.pop("_lat_sum")
        agg["latency_us"] = (total / weight) if weight else None
        out.append(agg)
    return sorted(out, key=lambda r: (-(r["iops"] or 0.0), r["path"]))


def aggregate_by_tenant(rows):
    """Roll per-host rows up to one row per tenant label.

    The tenant dimension comes from host_view's own ``tenant`` label on rows
    already filtered to ONE protocol by parse_host_view, so the aggregation
    inherits the protocol scoping - unlike the monitor API's TenantMetrics,
    which carries no protocol discriminator (D-016) and therefore cannot
    answer "which tenants are carrying <protocol> traffic". Latency is
    IOPS-weighted, as in aggregate_by_path.
    """
    grouped = {}
    for row in rows:
        agg = grouped.setdefault(row["tenant"], {
            "tenant": row["tenant"], "clients": set(), "paths": set(),
            "iops": 0.0, "read_iops": 0.0, "write_iops": 0.0, "md_iops": 0.0,
            "bw": 0.0, "read_bw": 0.0, "write_bw": 0.0,
            "_lat_weight": 0.0, "_lat_sum": 0.0,
        })
        agg["clients"].add(row["ip"])
        agg["paths"].add(row["path"])
        for field in ("iops", "read_iops", "write_iops", "md_iops",
                      "bw", "read_bw", "write_bw"):
            value = row.get(field)
            if value:
                agg[field] += value
        weight = row.get("iops") or 0.0
        latency = row.get("latency_us")
        if latency is not None and weight > 0:
            agg["_lat_weight"] += weight
            agg["_lat_sum"] += latency * weight

    out = []
    for agg in grouped.values():
        agg["client_count"] = len(agg.pop("clients"))
        agg["path_count"] = len(agg.pop("paths"))
        weight = agg.pop("_lat_weight")
        total = agg.pop("_lat_sum")
        agg["latency_us"] = (total / weight) if weight else None
        out.append(agg)
    return sorted(out, key=lambda r: (-(r["iops"] or 0.0), r["tenant"]))
