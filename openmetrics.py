#!/usr/bin/env python3
################################################################################
# Script Name: openmetrics.py
# Description: Optional continuous time-series exporter for opstat. When
#              enabled via --export-openmetrics, every polling tick is streamed
#              line-by-line to a JSON Lines (.jsonl) file using an OpenMetrics /
#              OpenTelemetry-aligned data model (one JSON object per line).
#              Read-only with respect to VAST; only writes a local file.
#
# Author: KMac kmac@vastdata.com
# Version: 1.0.0
################################################################################
"""JSON Lines OpenMetrics exporter.

Each emitted line is a single valid JSON object::

    {
      "timestamp": "2026-07-08T14:30:00.000Z",
      "metric_name": "vast.nfs3.operations",
      "metric_type": "gauge",
      "value": 14250.0,
      "unit": "ops/s",
      "attributes": {"cluster": ..., "vms": ..., "protocol": "nfs3",
                     "operation": "READ", "category": "data",
                     "drill_mode": "cluster", "target_name": ...}
    }

Rates (operations) are modeled as OpenMetrics gauges because the values are
instantaneous per-second rates rather than monotonic cumulative counters.
"""

import atexit
import json
import os
from datetime import datetime, timezone

_ENABLED = False
_PATH = None
_FILE = None
_PROTOCOL = None
_VMS = None

# (row field, metric name suffix, metric type, unit)
_METRIC_DEFS = (
    ("ops_sec", "operations", "gauge", "ops/s"),
    ("avg_us", "latency", "gauge", "microseconds"),
    ("bw_bytes_sec", "throughput", "gauge", "bytes/s"),
    ("io_bytes", "io_size", "gauge", "bytes"),
)


def _default_filename(protocol, vms):
    safe_vms = "".join(c if c.isalnum() or c in ".-_" else "_" for c in str(vms))
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return os.path.join(
        "/tmp", f"opstat-openmetrics-{protocol}-{safe_vms}-{stamp}.jsonl"
    )


def configure(enabled, filename, protocol, vms):
    """Open the .jsonl export file when enabled; return its path or None."""
    global _ENABLED, _PATH, _FILE, _PROTOCOL, _VMS
    close()
    _ENABLED = bool(enabled)
    if not _ENABLED:
        return None
    _PROTOCOL = protocol
    _VMS = str(vms)
    _PATH = os.path.expanduser(filename) if filename else _default_filename(protocol, vms)
    _FILE = open(_PATH, "a", encoding="utf-8")
    atexit.register(close)
    return _PATH


def is_enabled():
    return _ENABLED and _FILE is not None


def path():
    return _PATH if _ENABLED else None


def close():
    """Flush and close the export file handle (idempotent)."""
    global _FILE, _ENABLED, _PATH, _PROTOCOL, _VMS
    if _FILE is not None:
        try:
            _FILE.flush()
            _FILE.close()
        except Exception:
            pass
    _FILE = None
    _ENABLED = False
    _PATH = None
    _PROTOCOL = None
    _VMS = None


def mbps_to_bytes_sec(mbs):
    """Convert MB/s (decimal, 1e6) to bytes/s."""
    return None if mbs is None else float(mbs) * 1_000_000.0


def gbps_to_bytes_sec(gbs):
    """Convert GB/s (decimal, 1e9) to bytes/s."""
    return None if gbs is None else float(gbs) * 1_000_000_000.0


def _now_iso():
    dt = datetime.now(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def _iso_timestamp(sample):
    """Normalize a VMS sample timestamp to millisecond ISO-8601 Z, else use now."""
    if isinstance(sample, str) and sample.strip() not in ("", "-"):
        token = sample.strip().split(" ")[0]  # drop any "(warming up…)" suffix
        try:
            dt = datetime.fromisoformat(token.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            dt = dt.astimezone(timezone.utc)
            return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"
        except ValueError:
            pass
    return _now_iso()


def _write(obj):
    if _FILE is None:
        return
    _FILE.write(json.dumps(obj, separators=(",", ":")) + "\n")
    _FILE.flush()


def _emit(timestamp, attributes, values):
    """Write one line per non-null metric value for a single series item."""
    for field, suffix, mtype, unit in _METRIC_DEFS:
        value = values.get(field)
        if value is None:
            continue
        _write({
            "timestamp": timestamp,
            "metric_name": f"vast.{_PROTOCOL}.{suffix}",
            "metric_type": mtype,
            "value": float(value),
            "unit": unit,
            "attributes": dict(attributes),
        })


def export_snapshot(cluster, drill_mode, target_name, series, sample=None):
    """Emit per-operation metrics for one cluster/scope polling tick.

    *series* items are dicts with keys: operation, category, ops_sec, avg_us,
    bw_bytes_sec, io_bytes (any of the metric values may be None to skip).
    """
    if not is_enabled():
        return
    timestamp = _iso_timestamp(sample)
    for item in series:
        attributes = {
            "cluster": cluster or "",
            "vms": _VMS or "",
            "protocol": _PROTOCOL or "",
            "operation": item.get("operation", ""),
            "category": item.get("category", ""),
            "drill_mode": drill_mode or "cluster",
            "target_name": target_name or cluster or "",
        }
        _emit(timestamp, attributes, item)


def export_drill(cluster, drill_mode, rows, sample=None):
    """Emit per-target aggregate metrics while a drill-down is active."""
    if not is_enabled() or not rows:
        return
    timestamp = _iso_timestamp(sample)
    for row in rows:
        bw_bytes = None
        if row.get("bw_gbs") is not None:
            bw_bytes = gbps_to_bytes_sec(row.get("bw_gbs"))
        elif row.get("bw_mbs") is not None:
            bw_bytes = mbps_to_bytes_sec(row.get("bw_mbs"))
        attributes = {
            "cluster": cluster or "",
            "vms": _VMS or "",
            "protocol": _PROTOCOL or "",
            "operation": "TOTAL",
            "category": "drill",
            "drill_mode": drill_mode or "cluster",
            "target_name": row.get("name", ""),
        }
        ops = row.get("total_ops")
        if ops is None:
            ops = row.get("total_iops")
        _emit(timestamp, attributes, {
            "ops_sec": ops,
            "avg_us": row.get("latency_us"),
            "bw_bytes_sec": bw_bytes,
            "io_bytes": None,
        })
