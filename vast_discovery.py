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
_PROM_SAMPLE = re.compile(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(\{[^}]*\})?\s+")


def parse_prometheus(text):
    """Parse an exposition-format body into {metric: {help, type, labels, samples}}.

    The ``# HELP`` text is the authoritative description of what a metric
    means - better evidence than inferring semantics from a metric name.
    """
    metrics = {}

    def entry(name):
        return metrics.setdefault(
            name, {"help": "", "type": "", "labels": set(), "samples": 0})

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
        if m.group(2):
            for pair in m.group(2).strip("{}").split(","):
                key = pair.split("=", 1)[0].strip()
                if key:
                    record["labels"].add(key)
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
    """Summarize a REST response: record count and the field names available."""
    records = payload
    if isinstance(payload, dict):
        for key in ("results", "data", "objects", "items"):
            if isinstance(payload.get(key), list):
                records = payload[key]
                break
        else:
            records = [payload]
    if not isinstance(records, list):
        return 0, []
    fields = set()
    for record in records[:5]:
        if isinstance(record, dict):
            fields |= set(record)
    return len(records), sorted(fields)[:sample_fields]


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
