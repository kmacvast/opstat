#!/usr/bin/env python3
"""GET-only survey of a cluster's S3 surface: which VIPs serve S3, and which
buckets already exist.

FR14 needs a controlled FIRST-PARTY S3 workload aimed at the cluster being
probed. Two facts have to come from the cluster rather than from memory:

  * which address to send S3 at - the lab's persistent s3-loadgen is pinned
    to a different cluster entirely, so its endpoint must not be reused; and
  * which bucket is safe to write to - the buckets observed on var203 belong
    to other people's workloads, and FR14's safety rules forbid modifying a
    bucket that was not created for this validation.

This answers both, read-only, so the operator can choose deliberately. It
writes nothing to the cluster and issues GET requests only.

Environment:
  OPSTAT_VMS                  target VMS host (required)
  OPSTAT_PORT                 default 443
  OPSTAT_USER                 default admin
  VAST_TOKEN / VAST_PASSWORD  credentials (required, never printed)
  OPSTAT_PROBE_OUT            directory for the JSON summary (optional)

Python 3.8+, stdlib only.
"""

import io
import json
import os
import ssl
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import vast_common                                          # noqa: E402


def log(msg):
    print(msg, flush=True)


def rows(payload):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        return payload.get("results") or []
    return []


def protocols_of(obj):
    """A view's configured protocol list, however this build spells it."""
    for key in ("protocols", "protocol", "share_protocols"):
        value = obj.get(key)
        if isinstance(value, list):
            return [str(v).upper() for v in value]
        if isinstance(value, str) and value:
            return [p.strip().upper().strip("[]'\"")
                    for p in value.split(",") if p.strip()]
    return []


def main():
    vms = os.environ.get("OPSTAT_VMS")
    if not vms:
        print("ERROR: OPSTAT_VMS is required.", file=sys.stderr)
        return 2
    if not (os.environ.get("VAST_TOKEN") or os.environ.get("VAST_PASSWORD")):
        print("ERROR: set VAST_TOKEN or VAST_PASSWORD in the environment.",
              file=sys.stderr)
        return 2
    port = int(os.environ.get("OPSTAT_PORT", "443"))
    base = ("https://%s/api" % vms) if port == 443 \
        else ("https://%s:%d/api" % (vms, port))
    headers, _a, _p = vast_common.resolve_auth(
        os.environ.get("OPSTAT_USER", "admin"), vms, None, "opstat/s3-discovery")
    vast_common.configure_connection(base, headers, ssl._create_unverified_context())

    clusters = rows(vast_common.request("GET", "/clusters/")) or [{}]
    cluster = clusters[0]
    log("cluster: %s  sw_version: %s  (%s:%d)"
        % (cluster.get("name", "?"), cluster.get("sw_version", "?"), vms, port))
    log("")

    # ---- S3-capable views (a VAST S3 bucket is a view with S3 configured) --
    views = rows(vast_common.request("GET", "/views/"))
    s3_views = []
    for v in views:
        protos = protocols_of(v)
        if any(p.startswith("S3") for p in protos):
            s3_views.append({
                "id": v.get("id"),
                "path": v.get("path"),
                "bucket": v.get("bucket") or v.get("s3_bucket") or "",
                "tenant_id": v.get("tenant_id"),
                "tenant_name": v.get("tenant_name") or "",
                "protocols": protos,
            })
    log("[ S3-configured views / buckets ]  %d of %d views"
        % (len(s3_views), len(views)))
    if not s3_views:
        log("  none - this cluster exposes no S3 view, so there is nothing to")
        log("  drive a first-party S3 workload at.")
    for v in sorted(s3_views, key=lambda r: str(r["path"]))[:40]:
        log("  bucket=%-28s path=%-34s tenant=%s"
            % (v["bucket"] or "(none)", v["path"], v["tenant_name"] or v["tenant_id"]))
    log("")

    # ---- VIP pools and their protocols -------------------------------------
    pools = rows(vast_common.request("GET", "/vippools/"))
    log("[ VIP pools ]  %d" % len(pools))
    pool_protocols = {}
    for p in pools:
        protos = protocols_of(p) or protocols_of({"protocols": p.get("vast_protocols")})
        pool_protocols[p.get("id")] = protos
        log("  pool=%-22s id=%-5s protocols=%s  role=%s"
            % (p.get("name", "?"), p.get("id"), protos or "(unspecified)",
               p.get("role", "")))
    log("")

    vips = rows(vast_common.request("GET", "/vips/"))
    candidates = []
    for v in vips:
        ip = v.get("ip") or v.get("address") or ""
        pool_id = v.get("vippool_id") or v.get("vippool") or v.get("pool")
        protos = pool_protocols.get(pool_id) or []
        if not ip or ip.startswith("192.168."):
            continue                       # internal addresses are never S3
        entry = {"ip": ip, "pool_id": pool_id, "name": v.get("name", ""),
                 "protocols": protos}
        if any(p.startswith("S3") for p in protos):
            candidates.append(entry)
    log("[ S3-capable VIPs ]  %d of %d VIPs" % (len(candidates), len(vips)))
    for c in candidates[:25]:
        log("  %-16s pool=%-6s %s" % (c["ip"], c["pool_id"], c["protocols"]))
    if not candidates:
        log("  no VIP pool advertised an S3 protocol. Either this build does")
        log("  not expose pool protocols through /vippools/, or S3 is served")
        log("  from a pool whose protocol list is empty here. Choose the")
        log("  endpoint from the lab's own S3 configuration and let the probe's")
        log("  /vips/ ownership check confirm it belongs to this cluster.")
    log("")

    out = os.environ.get("OPSTAT_PROBE_OUT")
    if out:
        os.makedirs(out, exist_ok=True)
        path = os.path.join(out, "s3-targets.json")
        with io.open(path, "w") as fh:
            json.dump({"cluster": cluster.get("name"),
                       "sw_version": cluster.get("sw_version"),
                       "vms": vms, "s3_views": s3_views,
                       "s3_vip_candidates": candidates,
                       "vip_pools": [{"id": p.get("id"), "name": p.get("name"),
                                      "protocols": pool_protocols.get(p.get("id"))}
                                     for p in pools]}, fh, indent=2)
        log("summary: %s" % path)

    log("NEXT: choose ONE S3 endpoint IP owned by this cluster, and ONE bucket")
    log("that is SAFE to write to - FR14 forbids modifying a bucket that was")
    log("not created for this validation. If no dedicated bucket exists, create")
    log("one deliberately before the evidence run.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
