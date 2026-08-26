#!/usr/bin/env python3
"""Derive an S3 endpoint for ONE view, read-only, from cluster state.

`/vippools/` on VAST 5.4.6 does not expose protocol membership, so the S3
discovery pass legitimately reported "S3-capable VIPs: 0". That is an API
metadata limitation, not an absence of S3. This walks the relationships that
DO exist - view -> view policy -> VIP pool -> VIP, plus the tenant's own pool
bindings - and reports every candidate endpoint with the exact chain that
produced it.

It guesses nothing. Where a link cannot be established it says so and dumps
the raw object, so the reasoning can be checked against the record rather
than trusted.

Every referenced object is written to the output directory verbatim, because
a summary is not evidence.

Environment:
  OPSTAT_VMS                  target VMS host (required)
  OPSTAT_PORT                 default 443
  OPSTAT_USER                 default admin
  VAST_TOKEN / VAST_PASSWORD  credentials (required, never printed)
  OPSTAT_S3_VIEW_ID           view id to resolve (preferred)
  OPSTAT_S3_BUCKET            bucket name, if the id is unknown
  OPSTAT_PROBE_OUT            directory for raw objects + summary

Safety: GET only. No monitors. No writes of any kind. No S3 request is made -
this establishes a CANDIDATE endpoint; proving it serves S3 is a separate,
deliberate step.

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
import vast_discovery                                       # noqa: E402

OUT = os.environ.get("OPSTAT_PROBE_OUT") or "."
_IPV4 = re.compile(r"^(?:\d{1,3}\.){3}\d{1,3}$")


def log(msg):
    print(msg, flush=True)


def rows(payload):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        return payload.get("results") or ([payload] if payload else [])
    return []


def get(path, label):
    """One GET, saved verbatim. Returns None rather than raising."""
    try:
        payload = vast_common.request("GET", path)
    except RuntimeError as exc:
        log("    GET %-34s -> %s" % (path, str(exc)[:70]))
        return None
    try:
        os.makedirs(OUT, exist_ok=True)
        with io.open(os.path.join(OUT, "raw-%s.json" % label), "w") as fh:
            json.dump(payload, fh, indent=2)
    except OSError:
        pass
    return payload


def pool_refs(obj, seen=None):
    """Every VIP-pool id mentioned anywhere inside an object.

    Builds vary in where they put this (vip_pools, vippool_ids, a nested
    policy fragment), so the whole structure is walked rather than guessing
    one field name.
    """
    found = set()
    if seen is None:
        seen = set()
    if id(obj) in seen:
        return found
    seen.add(id(obj))
    if isinstance(obj, dict):
        for key, value in obj.items():
            k = key.lower()
            if "vip" in k and "pool" in k:
                if isinstance(value, int):
                    found.add(value)
                elif isinstance(value, list):
                    for v in value:
                        if isinstance(v, int):
                            found.add(v)
                        elif isinstance(v, dict) and isinstance(v.get("id"), int):
                            found.add(v["id"])
            found |= pool_refs(value, seen)
    elif isinstance(obj, list):
        for v in obj:
            found |= pool_refs(v, seen)
    return found


def main():
    vms = os.environ.get("OPSTAT_VMS")
    if not vms:
        print("ERROR: OPSTAT_VMS is required.", file=sys.stderr)
        return 2
    if not (os.environ.get("VAST_TOKEN") or os.environ.get("VAST_PASSWORD")):
        print("ERROR: set VAST_TOKEN or VAST_PASSWORD.", file=sys.stderr)
        return 2
    view_id = os.environ.get("OPSTAT_S3_VIEW_ID", "").strip()
    bucket = os.environ.get("OPSTAT_S3_BUCKET", "").strip()
    if not view_id and not bucket:
        print("ERROR: set OPSTAT_S3_VIEW_ID or OPSTAT_S3_BUCKET.", file=sys.stderr)
        return 2

    port = int(os.environ.get("OPSTAT_PORT", "443"))
    base = ("https://%s/api" % vms) if port == 443 \
        else ("https://%s:%d/api" % (vms, port))
    headers, _a, _p = vast_common.resolve_auth(
        os.environ.get("OPSTAT_USER", "admin"), vms, None, "opstat/s3-endpoint-derivation")
    vast_common.configure_connection(base, headers, ssl._create_unverified_context())

    cluster = (rows(get("/clusters/", "clusters")) or [{}])[0]
    log("cluster: %s  sw_version: %s"
        % (cluster.get("name", "?"), cluster.get("sw_version", "?")))
    log("")

    # ---- 1. the view itself, in full ---------------------------------------
    log("[ 1. view ]")
    view = None
    if view_id:
        view = (rows(get("/views/%s/" % view_id, "view")) or [None])[0]
    if view is None:
        allviews = rows(get("/views/", "views-all")) or []
        for v in allviews:
            if (bucket and v.get("bucket") == bucket) or \
               (view_id and str(v.get("id")) == view_id):
                view = v
                break
    if view is None:
        log("  view not found (id=%r bucket=%r)" % (view_id, bucket))
        return 1
    log("  id=%s path=%s bucket=%s tenant_id=%s"
        % (view.get("id"), view.get("path"), view.get("bucket"),
           view.get("tenant_id")))
    log("  fields: %s" % ", ".join(sorted(view.keys())))
    for key in sorted(view):
        k = key.lower()
        if any(t in k for t in ("polic", "vip", "pool", "s3", "endpoint",
                                "protocol", "tenant")):
            log("    %-28s = %r" % (key, view[key]))
    log("")

    # ---- 2. the view policy -------------------------------------------------
    log("[ 2. view policy ]")
    policy_id = None
    for key in ("policy_id", "view_policy_id", "share_policy_id", "policy"):
        val = view.get(key)
        if isinstance(val, int):
            policy_id = val
            break
        if isinstance(val, dict) and isinstance(val.get("id"), int):
            policy_id = val["id"]
            break
    policy = None
    if policy_id is not None:
        log("  view references policy id %s" % policy_id)
        policy = (rows(get("/viewpolicies/%s/" % policy_id, "viewpolicy")) or [None])[0]
    else:
        log("  the view record names no policy id")
    if policy:
        log("  policy fields: %s" % ", ".join(sorted(policy.keys())))
        for key in sorted(policy):
            k = key.lower()
            if any(t in k for t in ("vip", "pool", "protocol", "s3")):
                log("    %-28s = %r" % (key, policy[key]))
    log("")

    # ---- 3. the tenant ------------------------------------------------------
    log("[ 3. tenant ]")
    tenant = None
    if isinstance(view.get("tenant_id"), int):
        tenant = (rows(get("/tenants/%s/" % view["tenant_id"], "tenant")) or [None])[0]
    if tenant:
        log("  tenant id=%s name=%s" % (tenant.get("id"), tenant.get("name")))
        for key in sorted(tenant):
            k = key.lower()
            if any(t in k for t in ("vip", "pool", "s3", "endpoint")):
                log("    %-28s = %r" % (key, tenant[key]))
    else:
        log("  tenant record unavailable")
    log("")

    # ---- 4. pools referenced by any of the above ----------------------------
    refs = pool_refs(view) | pool_refs(policy or {}) | pool_refs(tenant or {})
    log("[ 4. VIP pools referenced by view/policy/tenant ]")
    log("  %s" % (sorted(refs) if refs else
                  "NONE - no object in the chain names a VIP pool on this build"))

    pools = {p.get("id"): p for p in rows(get("/vippools/", "vippools")) or []}
    vips = rows(get("/vips/", "vips")) or []
    by_pool = {}
    for v in vips:
        ip = v.get("ip") or v.get("address") or ""
        pid = v.get("vippool_id") or v.get("vippool") or v.get("pool")
        if ip and _IPV4.match(ip) and not ip.startswith("192.168."):
            by_pool.setdefault(pid, []).append(ip)
    log("  %d VIPs across %d pools" % (len(vips), len(by_pool)))
    log("")

    log("[ 5. candidate endpoints ]")
    candidates = []
    for pid in sorted(refs):
        ips = sorted(by_pool.get(pid, []))
        name = (pools.get(pid) or {}).get("name", "?")
        log("  pool %s (%s): %d VIP(s) %s" % (pid, name, len(ips), ips[:6]))
        for ip in ips:
            candidates.append({"ip": ip, "pool_id": pid, "pool_name": name,
                               "chain": "view %s -> pool %s -> vip"
                                        % (view.get("id"), pid)})
    if not candidates:
        log("  NO endpoint could be derived from the view's own relationships.")
        log("  This build exposes neither pool protocols nor a view->pool link,")
        log("  so the endpoint must come from the lab's own S3 configuration.")
        log("  Whatever address is supplied is still hard-checked for ownership")
        log("  by the evidence run before any S3 request is issued.")
    log("")

    # ---- 6. what else the API offers, for the record ------------------------
    log("[ 6. read-only endpoints mentioning s3 / vip / endpoint ]")
    try:
        spec_path, spec = vast_discovery.fetch_openapi(vast_common.request_text)
        # openapi_endpoints yields (path, methods, summary) tuples; only the
        # path is matched, and only GET-able ones are worth reporting here.
        eps = vast_discovery.openapi_endpoints(spec) if spec else []
        hits = []
        for entry in eps:
            path = entry[0] if isinstance(entry, (list, tuple)) and entry else str(entry)
            methods = entry[1] if isinstance(entry, (list, tuple)) and len(entry) > 1 else []
            if re.search(r"s3|vip|endpoint", str(path), re.I) and "GET" in (methods or []):
                hits.append((str(path), ",".join(methods or [])))
        hits.sort()
        log("  OpenAPI: %s  (%d endpoints, %d GET-able and relevant)"
            % (spec_path or "unavailable", len(eps), len(hits)))
        for path, methods in hits[:30]:
            log("    %-56s %s" % (path, methods))
    except Exception as exc:                                # noqa: BLE001
        log("  OpenAPI enumeration unavailable: %s" % str(exc)[:80])

    summary = {"cluster": cluster.get("name"), "vms": vms,
               "view": {k: view.get(k) for k in
                        ("id", "path", "bucket", "tenant_id", "protocols")},
               "policy_id": policy_id, "referenced_pools": sorted(refs),
               "candidates": candidates}
    try:
        os.makedirs(OUT, exist_ok=True)
        with io.open(os.path.join(OUT, "s3-endpoint-derivation.json"), "w") as fh:
            json.dump(summary, fh, indent=2)
        log("")
        log("summary: %s" % os.path.join(OUT, "s3-endpoint-derivation.json"))
    except OSError:
        pass
    log("NOTE: ownership is not S3 capability. A derived address still has to")
    log("be confirmed as an S3 endpoint before it is treated as one.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
