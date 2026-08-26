#!/usr/bin/env python3
"""Read-only feasibility audit for a dedicated FR14 S3 test target.

Answers, from cluster state alone and without writing anything:

  1. Is there an existing var203 VIP that is demonstrably S3-capable AND
     usable by the default tenant?
  2. Is the one proven S3 VIP (172.200.13.168, pool 87) restricted to
     mars-k8s-tenant, or legitimately reachable by the default tenant?
  3. Is any other VIP provably S3-capable without generating traffic?
  4. What minimal cluster-side objects would a dedicated target need?
  5. Is the proposed path/bucket name free?
  6. What cleanup would removing it require?

The distinction this exists to protect: a pool's ROLE matters. Pool 87 on
var203 is role=QUERY_ENGINE_CNODE_GROUP - a VAST DB query-engine pool - not
role=PROTOCOLS. S3 traffic observed there is the database engine reaching its
own view, which is not the same thing as a general-purpose S3 endpoint. The
audit reports role explicitly rather than letting "it serves S3" stand alone.

Safety: GET only. No POST/PATCH/PUT/DELETE. No S3 request of any kind.

Environment:
  OPSTAT_VMS / OPSTAT_PORT / OPSTAT_USER
  VAST_TOKEN or VAST_PASSWORD
  OPSTAT_FR14_PATH     proposed view path   (default /kmacs/opstat-fr14-s3)
  OPSTAT_FR14_BUCKET   proposed bucket name (default kmac-opstat-fr14-s3)
  OPSTAT_PROBE_OUT     directory for raw captures

Python 3.8+, stdlib only.
"""

import io
import json
import os
import re
import ssl
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import vast_common                                          # noqa: E402
from derive_s3_endpoint import (                            # noqa: E402
    index_vips_by_pool, pool_vips, s3_vips_from_vip_view,
    s3_ips_from_true_ip_config, rows, _IPV4,
)

OUT = os.environ.get("OPSTAT_PROBE_OUT") or "."
WANT_PATH = os.environ.get("OPSTAT_FR14_PATH", "/kmacs/opstat-fr14-s3")
WANT_BUCKET = os.environ.get("OPSTAT_FR14_BUCKET", "kmac-opstat-fr14-s3")


def log(msg):
    print(msg, flush=True)


def get(path, label, text=False):
    try:
        payload = (vast_common.request_text("GET", path) if text
                   else vast_common.request("GET", path))
    except RuntimeError as exc:
        log("    GET %-40s -> %s" % (path, str(exc)[:60]))
        return None
    try:
        os.makedirs(OUT, exist_ok=True)
        with io.open(os.path.join(OUT, "audit-%s.%s"
                                  % (label, "txt" if text else "json")), "w") as fh:
            fh.write(payload if text else json.dumps(payload, indent=2))
    except OSError:
        pass
    return payload


def main():
    vms = os.environ.get("OPSTAT_VMS")
    if not vms:
        print("ERROR: OPSTAT_VMS is required.", file=sys.stderr)
        return 2
    if not (os.environ.get("VAST_TOKEN") or os.environ.get("VAST_PASSWORD")):
        print("ERROR: set VAST_TOKEN or VAST_PASSWORD.", file=sys.stderr)
        return 2
    port = int(os.environ.get("OPSTAT_PORT", "443"))
    base = ("https://%s/api" % vms) if port == 443 \
        else ("https://%s:%d/api" % (vms, port))
    headers, _a, _p = vast_common.resolve_auth(
        os.environ.get("OPSTAT_USER", "admin"), vms, None, "opstat/fr14-s3-audit")
    vast_common.configure_connection(base, headers, ssl._create_unverified_context())

    cluster = (rows(get("/clusters/", "clusters")) or [{}])[0]
    cid = cluster.get("id")
    log("cluster %s id=%s sw=%s" % (cluster.get("name"), cid,
                                    cluster.get("sw_version")))
    log("")

    # ---- pools, with ROLE, which is the fact that decides usability --------
    log("[ 1. VIP pools, by role ]")
    pools = rows(get("/vippools/", "vippools")) or []
    vips = rows(get("/vips/", "vips")) or []
    index = index_vips_by_pool(vips)
    by_role = {}
    for p in pools:
        by_role.setdefault(str(p.get("role")), []).append(p)
    for role, plist in sorted(by_role.items()):
        log("  role=%-26s %d pool(s)" % (role, len(plist)))
    log("")

    # ---- affirmative S3 capability -----------------------------------------
    log("[ 2. affirmative S3 capability ]")
    s3_capable = {}
    if cid is not None:
        cfg = get("/clusters/%s/s3_true_ip_config/" % cid, "s3_true_ip_config")
        for ip in s3_ips_from_true_ip_config(cfg):
            s3_capable.setdefault(ip, set()).add("s3_true_ip_config")
    observed = s3_vips_from_vip_view(get("/prometheusmetrics/vip_view",
                                         "vip_view", text=True))
    for ip, info in observed.items():
        s3_capable.setdefault(ip, set()).add("vip_view protocol=S3")
    if not s3_capable:
        log("  NONE. No address is affirmatively S3-capable right now.")
    for ip, why in sorted(s3_capable.items()):
        info = observed.get(ip, {})
        pool = None
        for p in pools:
            if ip in pool_vips(index, p.get("id"), p.get("name")):
                pool = p
                break
        log("  %-16s evidence=%s" % (ip, sorted(why)))
        log("      pool      : %s (id=%s) ROLE=%s"
            % ((pool or {}).get("name"), (pool or {}).get("id"),
               (pool or {}).get("role")))
        log("      observed  : paths=%s tenants=%s"
            % (sorted(info.get("paths", []))[:3],
               sorted(info.get("tenants", []))[:3]))
        if str((pool or {}).get("role", "")).upper() != "PROTOCOLS":
            log("      CAUTION   : this pool's role is not PROTOCOLS. S3 seen")
            log("                  here is likely the query engine reaching its")
            log("                  own view, NOT a general S3 endpoint.")
    log("")

    # ---- default-tenant reachability ---------------------------------------
    log("[ 3. default tenant reachability ]")
    tenant = (rows(get("/tenants/1/", "tenant1")) or [None])[0]
    tenant_pools = {str(p.get("id")): p.get("name")
                    for p in ((tenant or {}).get("vippools") or [])}
    log("  default tenant lists %d pool(s)" % len(tenant_pools))
    for ip in sorted(s3_capable):
        owner = None
        for p in pools:
            if ip in pool_vips(index, p.get("id"), p.get("name")):
                owner = p
                break
        pid = str((owner or {}).get("id"))
        listed = pid in tenant_pools
        log("  %-16s in a default-tenant pool: %s (%s)"
            % (ip, "YES" if listed else "NO",
               tenant_pools.get(pid, (owner or {}).get("name"))))
        if listed and str((owner or {}).get("role", "")).upper() != "PROTOCOLS":
            log("      but role=%s - listed is not the same as appropriate"
                % (owner or {}).get("role"))
    log("")

    # ---- PROTOCOLS-role pools the default tenant could use -----------------
    log("[ 4. default-tenant pools with role=PROTOCOLS ]")
    usable = []
    for p in pools:
        if str(p.get("role", "")).upper() != "PROTOCOLS":
            continue
        if str(p.get("id")) not in tenant_pools:
            continue
        ips = pool_vips(index, p.get("id"), p.get("name"))
        proven = [i for i in ips if i in s3_capable]
        usable.append({"id": p.get("id"), "name": p.get("name"),
                       "vips": ips[:8], "proven_s3": proven})
        log("  pool %-4s %-24s %d VIP(s)  proven-S3: %s"
            % (p.get("id"), p.get("name"), len(ips), proven or "none"))
    if not any(u["proven_s3"] for u in usable):
        log("  NO default-tenant PROTOCOLS pool contains an affirmatively")
        log("  S3-capable address. Absence of current S3 traffic is NOT proof")
        log("  that a pool cannot serve S3 - it means nothing is exercising it")
        log("  right now, so capability there is UNPROVEN either way.")
    log("")

    # ---- name availability --------------------------------------------------
    log("[ 5. proposed target availability ]")
    views = rows(get("/views/", "views")) or []
    path_taken = [v for v in views if str(v.get("path", "")).rstrip("/")
                  == WANT_PATH.rstrip("/")]
    bucket_taken = [v for v in views if v.get("bucket") == WANT_BUCKET]
    near = sorted({str(v.get("path")) for v in views
                   if str(v.get("path", "")).startswith(WANT_PATH.split("/")[1]
                                                        and "/" + WANT_PATH.split("/")[1])})
    log("  proposed path   %-34s %s" % (WANT_PATH, "TAKEN" if path_taken else "free"))
    log("  proposed bucket %-34s %s" % (WANT_BUCKET, "TAKEN" if bucket_taken else "free"))
    if near:
        log("  existing views sharing the top-level namespace: %s" % near[:10])
    log("")

    summary = {
        "cluster": cluster.get("name"), "cluster_id": cid,
        "s3_capable": {ip: sorted(w) for ip, w in s3_capable.items()},
        "default_tenant_pool_ids": sorted(tenant_pools),
        "protocols_pools_for_default_tenant": usable,
        "proposed": {"path": WANT_PATH, "bucket": WANT_BUCKET,
                     "path_free": not path_taken, "bucket_free": not bucket_taken},
    }
    try:
        os.makedirs(OUT, exist_ok=True)
        with io.open(os.path.join(OUT, "s3-target-feasibility.json"), "w") as fh:
            json.dump(summary, fh, indent=2)
        log("summary: %s" % os.path.join(OUT, "s3-target-feasibility.json"))
    except OSError:
        pass
    log("")
    log("This audit changes nothing and issues no S3 request. Creating a view")
    log("or bucket is a cluster-side action and is NOT performed here.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
