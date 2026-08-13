#!/usr/bin/env python3
"""Read-only survey of the VMS observability surface beyond /metrics/.

The metric catalog and the custom-monitor API are only two of the interfaces
a VAST cluster exposes. Before concluding that a protocol's telemetry ceiling
has been reached, this module surveys the rest:

* the cluster's own OpenAPI/Swagger definition, which lists every REST
  resource the Web UI and VCLI are built on;
* the Prometheus/OpenMetrics exporter, whose ``# HELP``/``# TYPE`` metadata
  documents metric semantics far better than a bare metric name does;
* read-only REST resources that carry operational context (client sessions,
  export configuration, VIP pools) even though they are not time series;
* the top-N / analytics endpoints.

Everything here issues GET requests only. Callers that need temporary
monitors for queryability tests create and delete them themselves.
"""

import json
import re
import time

# Candidate locations for the cluster's OpenAPI definition. VAST builds have
# moved this around, and the Swagger UI itself lives outside /api.
_OPENAPI_CANDIDATES = (
    ("/openapi.json", False),
    ("/swagger.json", False),
    ("/schema/?format=openapi", False),
    ("/?format=openapi", False),
    ("/latest/openapi.json", False),
    ("/docs/?format=openapi", False),
    ("/openapi.json", True),
    ("/docs/index.html", True),
)

# Documented and plausible Prometheus exporter roots. Discovery probes each
# and keeps whatever answers; nothing is assumed to exist.
_PROMETHEUS_ROOTS = (
    "/prometheusmetrics/",
    "/latest/prometheusmetrics/",
    "/prometheus_metrics/",
    "/metrics/prometheus/",
)

# Scopes documented for the VMS exporter, plus the object types opstat cares
# about. Probed under each working root.
_PROMETHEUS_SCOPES = (
    "", "cluster", "views", "users", "tenants", "quotas", "cnodes", "vips",
    "protocols", "nfs", "clients", "hosts", "network", "capacity",
)

# Endpoint keywords worth reporting from the OpenAPI inventory.
REST_KEYWORDS = (
    "nfs", "nfs4", "protocol", "session", "client", "connection", "open",
    "lock", "deleg", "layout", "state", "reclaim", "compound", "callback",
    "view", "tenant", "user", "cnode", "vip", "statistics", "stat",
    "performance", "telemetry", "metric", "monitor", "topn", "analytics",
    "activity", "quota", "export",
)


# ---------------------------------------------------------------------------
# OpenAPI / Swagger
# ---------------------------------------------------------------------------
def fetch_openapi(request_text_fn):
    """Return (path_used, spec_dict) for the cluster's OpenAPI definition.

    Returns (None, None) when no candidate answers with a usable document.
    HTML responses (the Swagger UI page rather than the definition) are
    reported by path so the operator knows the UI exists even when the raw
    definition is elsewhere.
    """
    html_seen = None
    for path, root in _OPENAPI_CANDIDATES:
        try:
            body = request_text_fn("GET", path, root=root)
        except RuntimeError:
            continue
        if not body:
            continue
        stripped = body.lstrip()
        if stripped.startswith("{"):
            try:
                spec = json.loads(body)
            except ValueError:
                continue
            if isinstance(spec, dict) and ("paths" in spec or "swagger" in spec
                                           or "openapi" in spec):
                return ("(root)" if root else "/api") + path, spec
        elif html_seen is None and "<html" in stripped[:400].lower():
            html_seen = ("(root)" if root else "/api") + path
    return html_seen, None


def openapi_endpoints(spec):
    """Return sorted (path, methods, summary) tuples from an OpenAPI spec."""
    if not isinstance(spec, dict):
        return []
    paths = spec.get("paths")
    if not isinstance(paths, dict):
        return []
    out = []
    for path, item in paths.items():
        if not isinstance(item, dict):
            continue
        methods = sorted(
            m.upper() for m in item
            if m.lower() in ("get", "post", "put", "patch", "delete")
        )
        summary = ""
        for method in ("get", "post"):
            block = item.get(method)
            if isinstance(block, dict):
                summary = str(block.get("summary")
                              or block.get("description") or "").strip()
                if summary:
                    break
        out.append((path, methods, summary.split("\n")[0][:100]))
    return sorted(out)


def match_endpoints(endpoints, keywords):
    """Group endpoints by which keyword their path or summary mentions."""
    hits = {}
    for path, methods, summary in endpoints:
        haystack = f"{path} {summary}".lower()
        for keyword in keywords:
            if keyword in haystack:
                hits.setdefault(keyword, []).append((path, methods, summary))
    return hits


# ---------------------------------------------------------------------------
# Prometheus / OpenMetrics exporter
# ---------------------------------------------------------------------------
def prometheus_candidates(spec):
    """Exporter paths to probe: those in the OpenAPI spec plus known roots."""
    found = []
    for path, _methods, _summary in openapi_endpoints(spec) if spec else []:
        if "prometheus" in path.lower():
            found.append(path if path.startswith("/") else "/" + path)
    for root in _PROMETHEUS_ROOTS:
        for scope in _PROMETHEUS_SCOPES:
            found.append(root + scope if scope else root)
    seen, ordered = set(), []
    for path in found:
        if path not in seen:
            seen.add(path)
            ordered.append(path)
    return ordered


_PROM_HELP = re.compile(r"^#\s*HELP\s+(\S+)\s*(.*)$")
_PROM_TYPE = re.compile(r"^#\s*TYPE\s+(\S+)\s+(\S+)")
_PROM_SAMPLE = re.compile(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(\{(.*)\})?\s+(\S+)\s*$")
# Label pairs are name="value" where the value may itself contain commas and
# escaped quotes - VAST emits protocols="['NFS4', 'SMB']". Splitting the label
# block on commas therefore shredded such values into nonsense label names.
_PROM_LABEL = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)="((?:[^"\\]|\\.)*)"')

# Per metric, retain at most this many distinct values per label so a
# high-cardinality label (client IP, view path) cannot bloat the report.
_MAX_LABEL_VALUES = 12


def parse_prometheus(text):
    """Parse an exposition-format body into per-metric metadata.

    Returns {metric: {help, type, labels, label_values, samples}} where
    ``label_values`` maps each label name to a bounded set of observed values.
    The ``# HELP`` text is the authoritative description of what a metric
    means, and the label values establish what the series can actually be
    attributed to - a ``protocol`` label is only useful once we know it
    carries values like NFS4.
    """
    metrics = {}

    def entry(name):
        return metrics.setdefault(name, {
            "help": "", "type": "", "labels": set(),
            "label_values": {}, "samples": 0,
        })

    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            m = _PROM_HELP.match(line)
            if m:
                entry(m.group(1))["help"] = m.group(2).strip()
                continue
            m = _PROM_TYPE.match(line)
            if m:
                entry(m.group(1))["type"] = m.group(2).strip()
            continue
        m = _PROM_SAMPLE.match(line)
        if not m:
            continue
        record = entry(m.group(1))
        record["samples"] += 1
        for key, value in _PROM_LABEL.findall(m.group(3) or ""):
            record["labels"].add(key)
            seen = record["label_values"].setdefault(key, set())
            if len(seen) < _MAX_LABEL_VALUES:
                seen.add(value)
    return metrics


def probe_prometheus(request_text_fn, spec, limit=40):
    """Probe exporter paths; return [(path, metrics_dict, note)] for responders."""
    results = []
    for path in prometheus_candidates(spec)[:limit]:
        try:
            body = request_text_fn("GET", path)
        except RuntimeError as exc:
            continue
        if not body or not body.strip():
            results.append((path, {}, "empty response"))
            continue
        metrics = parse_prometheus(body)
        if metrics:
            results.append((path, metrics, ""))
        else:
            results.append((path, {}, "responded but no exposition-format metrics"))
    return results


# ---------------------------------------------------------------------------
# Read-only REST resource probing
# ---------------------------------------------------------------------------
def describe_payload(payload, sample_fields=12):
    """Summarize a REST response: record count and the field names available.

    Descends through paging envelopes. ``/monitors/topn/`` wraps its results
    in ``{data, next, previous, timestamp}`` where ``data`` is a dict keyed by
    dimension, so reporting the envelope's own keys made every top-N probe
    look like a single record with four fields - availability mistaken for
    content.
    """
    records = payload
    if isinstance(payload, dict):
        for key in ("results", "data", "objects", "items"):
            inner = payload.get(key)
            if isinstance(inner, list):
                records = inner
                break
            if isinstance(inner, dict):
                # Dimension-keyed payload: describe the nested structure.
                return _describe_nested(inner, sample_fields)
        else:
            records = [payload]
    if not isinstance(records, list):
        return 0, []
    fields = set()
    for record in records[:5]:
        if isinstance(record, dict):
            fields |= set(record)
    return len(records), sorted(fields)[:sample_fields]


def _describe_nested(block, sample_fields):
    """Count leaf rows in a dimension-keyed payload and sample their fields."""
    total, fields = 0, set()
    for dimension, value in block.items():
        rows = value
        if isinstance(value, dict):
            for metric_rows in value.values():
                if isinstance(metric_rows, list):
                    total += len(metric_rows)
                    for row in metric_rows[:3]:
                        if isinstance(row, dict):
                            fields |= {f"{dimension}.{k}" for k in row}
            continue
        if isinstance(rows, list):
            total += len(rows)
            for row in rows[:3]:
                if isinstance(row, dict):
                    fields |= {f"{dimension}.{k}" for k in row}
    return total, sorted(fields)[:sample_fields]


def probe_readonly(request_fn, path):
    """GET one endpoint; return a dict describing what came back.

    Never issues anything but GET, so it cannot alter cluster configuration.
    """
    try:
        payload = request_fn("GET", path)
    except RuntimeError as exc:
        return {"path": path, "ok": False, "detail": str(exc)[:110],
                "count": 0, "fields": []}
    count, fields = describe_payload(payload)
    return {"path": path, "ok": True, "detail": "", "count": count,
            "fields": fields}


# ---------------------------------------------------------------------------
# Series semantics
# ---------------------------------------------------------------------------
def classify_series(values):
    """Classify a metric's samples as cumulative, varying, constant or empty.

    Rate-versus-cumulative is decided from the data rather than from the
    suffix, because a name alone does not establish it.
    """
    numeric = [v for v in values if isinstance(v, (int, float))]
    if not numeric:
        return "no data"
    if len(numeric) < 2:
        return "single sample"
    if len(set(numeric)) == 1:
        return "constant"
    ascending = all(b >= a for a, b in zip(numeric, numeric[1:]))
    descending = all(b <= a for a, b in zip(numeric, numeric[1:]))
    # Monitor rows arrive newest-first, so a lifetime counter reads as
    # monotonically descending down the list.
    if ascending or descending:
        return "cumulative"
    return "rate/gauge"


def candidate_block(*, api_path, source, scope, provides, read_only,
                    queried, opstat_use, caveats):
    """Render one candidate source in the agreed reporting format."""
    return [
        f"  API path:            {api_path}",
        f"  Data source:         {source}",
        f"  Scope:               {scope}",
        f"  What it provides:    {provides}",
        f"  Read-only:           {read_only}",
        f"  Successfully queried:{queried}",
        f"  Potential opstat use:{opstat_use}",
        f"  Caveats:             {caveats}",
        "",
    ]


# ---------------------------------------------------------------------------
# Timed scraping and counter-delta interrogation
# ---------------------------------------------------------------------------
def scrape_timed(request_text_fn, path):
    """GET an exporter path; return (body, elapsed_seconds, byte_count)."""
    started = time.monotonic()
    try:
        body = request_text_fn("GET", path)
    except RuntimeError as exc:
        return None, time.monotonic() - started, 0, str(exc)[:90]
    return body, time.monotonic() - started, len(body or ""), ""


def sample_values(text, prefix=""):
    """Return {(metric, frozenset(label items)): float} for one scrape.

    Keyed by metric *and* labels so per-cNode series stay distinct.
    """
    out = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = _PROM_SAMPLE.match(line)
        if not m:
            continue
        name = m.group(1)
        if prefix and not name.startswith(prefix):
            continue
        try:
            value = float(m.group(4))
        except (TypeError, ValueError):
            continue
        labels = frozenset(_PROM_LABEL.findall(m.group(3) or ""))
        out[(name, labels)] = value
    return out


def counter_deltas(first, second, elapsed):
    """Compare two scrapes; return per-series delta and derived rate.

    Establishes empirically whether an exporter gauge behaves as a cumulative
    counter (delta >= 0 and generally growing) or as an instantaneous value.
    """
    rows = []
    for key, later in second.items():
        if key not in first:
            continue
        earlier = first[key]
        delta = later - earlier
        rows.append({
            "metric": key[0],
            "labels": dict(key[1]),
            "first": earlier,
            "second": later,
            "delta": delta,
            "per_sec": (delta / elapsed) if elapsed > 0 else None,
        })
    return rows


def summarize_counter_behavior(rows):
    """Classify a set of delta rows: cumulative, static, or resetting."""
    if not rows:
        return "no comparable series"
    deltas = [r["delta"] for r in rows]
    grew = sum(1 for d in deltas if d > 0)
    shrank = sum(1 for d in deltas if d < 0)
    if shrank:
        return f"non-monotonic ({shrank} of {len(deltas)} series decreased)"
    if grew:
        return f"cumulative ({grew} of {len(deltas)} series grew)"
    return "static over this interval (no activity, or not a counter)"


def derive_latency(count_delta, sum_delta):
    """Mean latency per operation from paired count/sum deltas, or None."""
    if not count_delta or count_delta <= 0 or sum_delta is None:
        return None
    return sum_delta / count_delta


def infer_time_unit(derived, reference_us):
    """Guess the unit of *derived* by comparing it to a known-microsecond value.

    Returns (unit_guess, ratio) or (None, None) when no comparison is
    possible. The guess is reported as evidence, never used to scale a
    displayed number without confirmation.
    """
    if not derived or not reference_us or reference_us <= 0:
        return None, None
    ratio = derived / reference_us
    for unit, low, high in (
        ("nanoseconds", 500.0, 2000.0),
        ("microseconds", 0.2, 5.0),
        ("milliseconds", 0.0002, 0.005),
        ("seconds", 2e-7, 5e-6),
    ):
        if low <= ratio <= high:
            return unit, ratio
    return "indeterminate", ratio
