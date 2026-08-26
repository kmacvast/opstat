#!/usr/bin/env python3
"""Derive an S3 endpoint for ONE view, read-only, from cluster state.

`/vippools/` on VAST 5.4.6 exposes no protocol membership, so a protocol
filter over it legitimately finds nothing. This walks every read-only surface
that *does* carry the association and reports each candidate with the exact
chain that produced it.

Two kinds of evidence are kept strictly apart, because conflating them is how
the 202.x evidence went wrong:

  OWNERSHIP    - the address belongs to this cluster. Necessary, never
                 sufficient.
  S3 CAPABILITY - affirmative evidence that the address actually serves S3.
                 The only sources that can establish it here are
                 `/clusters/{id}/s3_true_ip_config/` (S3 IP configuration by
                 name and purpose) and `/prometheusmetrics/vip_view`, whose
                 rows carry a `protocol` label - a VIP observed carrying
                 `protocol="S3"` traffic is serving S3, as a fact rather than
                 an inference.

A pool whose *name* contains "s3" is recorded as a hint and never counted as
capability. No S3 request is issued: discovering which address works by
spraying requests at candidates is exactly what this exists to avoid.

Environment:
  OPSTAT_VMS                  target VMS host (required)
  OPSTAT_PORT                 default 443
  OPSTAT_USER                 default admin
  VAST_TOKEN / VAST_PASSWORD  credentials (required, never printed)
  OPSTAT_S3_VIEW_ID           view id to resolve (preferred)
  OPSTAT_S3_BUCKET            bucket name, if the id is unknown
  OPSTAT_PROBE_OUT            directory for raw objects + summary

Safety: GET only. No monitors, no writes, no S3 requests.

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
_LABELS = re.compile(r'(\w+)="([^"]*)"')


def log(msg):
    print(msg, flush=True)


def rows(payload):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        return payload.get("results") or ([payload] if payload else [])
    return []


# ---------------------------------------------------------------------------
# Pure helpers (regression-tested in tests/test_s3_endpoint_derivation.py)
# ---------------------------------------------------------------------------
def index_vips_by_pool(vips):
    """Map BOTH pool id and pool name to the VIP addresses in that pool.

    The first version keyed only on whatever single field it found first. On
    var203 that field held the pool NAME, while the ids resolved from the
    tenant were integers, so every lookup missed and all 15 candidate pools
    reported "0 VIP(s)" against 386 known VIPs. Indexing under every
    identifier a record offers removes the guess.
    """
    out = {}
    for vip in vips or []:
        ip = vip.get("ip") or vip.get("address") or ""
        if not ip or not _IPV4.match(str(ip)) or str(ip).startswith("192.168."):
            continue
        keys = set()
        for key, value in vip.items():
            k = key.lower()
            if "pool" not in k:
                continue
            if isinstance(value, (int, str)) and str(value).strip():
                keys.add(str(value).strip())
            elif isinstance(value, dict):
                for sub in ("id", "name"):
                    if value.get(sub) is not None:
                        keys.add(str(value[sub]))
        for key in keys:
            out.setdefault(key, [])
            if ip not in out[key]:
                out[key].append(ip)
    return out


def pool_vips(index, pool_id, pool_name):
    """VIPs for a pool, looked up by id or by name - whichever the build used."""
    found = []
    for key in (pool_id, pool_name):
        if key is None:
            continue
        for ip in index.get(str(key), []):
            if ip not in found:
                found.append(ip)
    return sorted(found)


def s3_vips_from_vip_view(text):
    """VIPs the exporter observed carrying protocol="S3" traffic.

    This is the one source that establishes capability as an observation
    rather than an inference: the row exists because that VIP served S3.
    Returns {vip: {"paths": set, "tenants": set, "value": float}}.
    """
    found = {}
    for line in (text or "").splitlines():
        if not line or line[0] == "#" or "vast_vip_view" not in line:
            continue
        labels = dict(_LABELS.findall(line))
        if labels.get("protocol") != "S3":
            continue
        vip = labels.get("vip") or labels.get("ip")
        if not vip:
            continue
        entry = found.setdefault(vip, {"paths": set(), "tenants": set(),
                                       "pools": set(), "value": 0.0})
        if labels.get("path"):
            entry["paths"].add(labels["path"])
        if labels.get("tenant"):
            entry["tenants"].add(labels["tenant"])
        if labels.get("vippool"):
            entry["pools"].add(labels["vippool"])
        try:
            entry["value"] += float(line.rsplit(None, 1)[-1])
        except (TypeError, ValueError):
            pass
    return found


def s3_ips_from_true_ip_config(payload):
    """Every IPv4 literal in /clusters/{id}/s3_true_ip_config/.

    The endpoint is named for S3 IP configuration, so an address appearing in
    it is affirmative S3 evidence rather than a hint.
    """
    found = set()

    def walk(node):
        if isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
        elif isinstance(node, str) and _IPV4.match(node):
            found.add(node)
    walk(payload)
    return found


def correlate_candidates(refs, pools, index, s3_capable, view_id=None):
    """Candidate rows, with capability kept strictly separate from ownership.

    `s3_capable` maps ip -> set of AFFIRMATIVE evidence strings. A pool whose
    name contains "s3" sets `pool_name_hint` and NOTHING else: promoting that
    hint into `s3_evidence` is precisely the inference this whole exercise
    exists to refuse, so it is done in one place and regression-tested.
    """
    # A pool is typically referenced BOTH by id ("87") and by name
    # ("mars-k8s-vip-qe"); both resolve to the same pool record, so iterating
    # refs naively emitted every VIP twice. Identity is (ip, canonical pool),
    # deduplicated here in the data rather than hidden at print time - the
    # JSON is the evidence, and a duplicated row misreads as two findings.
    # A pool is typically referenced BOTH by id ("87") and by name
    # ("mars-k8s-vip-qe"), and both resolve to the same VIPs. The stable
    # identity of a CANDIDATE is therefore the address: one endpoint reached
    # two ways is one endpoint. Deduplicated here in the data, not at print
    # time - the JSON is the evidence, and a repeated row reads as two
    # independent findings. Every reference that reached it is still recorded.
    by_ip = {}
    order = []
    for ref in sorted(refs or []):
        pool = (pools or {}).get(str(ref)) or {}
        resolved = bool(pool)
        pid = pool.get("id", ref)
        pname = pool.get("name", ref)
        for ip in pool_vips(index, pid, pname):
            row = by_ip.get(ip)
            if row is None:
                row = {
                    "ip": ip, "pool_id": pid, "pool_name": pname,
                    "owned_by_cluster": True,
                    "s3_evidence": sorted((s3_capable or {}).get(ip, [])),
                    "pool_name_hint": "s3" in str(pname).lower(),
                    "reached_via": [str(ref)],
                    "chain": "view %s -> pool %s (%s) -> vip"
                             % (view_id, pid, pname),
                }
                by_ip[ip] = row
                order.append(ip)
                continue
            if str(ref) not in row["reached_via"]:
                row["reached_via"].append(str(ref))
            # Prefer the identity that came from an actual pool record.
            if resolved and not isinstance(row["pool_id"], int):
                row["pool_id"], row["pool_name"] = pid, pname
                row["chain"] = ("view %s -> pool %s (%s) -> vip"
                                % (view_id, pid, pname))
            row["pool_name_hint"] = row["pool_name_hint"] or \
                "s3" in str(pname).lower()
    candidates = [by_ip[ip] for ip in order]
    known = set(by_ip)
    for ip, why in sorted((s3_capable or {}).items()):
        if ip not in known:
            known.add(ip)
            candidates.append({
                "ip": ip, "pool_id": None, "pool_name": None,
                "owned_by_cluster": None, "s3_evidence": sorted(why),
                "pool_name_hint": False,
                "chain": "not in this view's pools; S3 evidence only",
            })
    return candidates


def proven_candidates(candidates):
    """Only rows with affirmative S3 evidence. Ownership is not enough.

    One row per address: the same VIP reached through two references is one
    endpoint, and listing it twice reads as two independent findings.
    """
    out, seen = [], set()
    for c in candidates or []:
        if c.get("s3_evidence") and c["ip"] not in seen:
            seen.add(c["ip"])
            out.append(c)
    return out


def pool_refs(obj, seen=None):
    """Every VIP-pool reference (id or name) mentioned anywhere in an object."""
    found = set()
    if seen is None:
        seen = set()
    if id(obj) in seen:
        return found
    seen.add(id(obj))
    if isinstance(obj, dict):
        for key, value in obj.items():
            if "vip" in key.lower() and "pool" in key.lower():
                if isinstance(value, (int, str)) and str(value).strip():
                    found.add(str(value).strip())
                elif isinstance(value, list):
                    for v in value:
                        if isinstance(v, (int, str)):
                            found.add(str(v))
                        elif isinstance(v, dict):
                            for sub in ("id", "name"):
                                if v.get(sub) is not None:
                                    found.add(str(v[sub]))
            found |= pool_refs(value, seen)
    elif isinstance(obj, list):
        for v in obj:
            found |= pool_refs(v, seen)
    return found


# ---------------------------------------------------------------------------
def get(path, label, text=False):
    """One GET, saved verbatim. Returns None rather than raising."""
    try:
        payload = (vast_common.request_text("GET", path) if text
                   else vast_common.request("GET", path))
    except RuntimeError as exc:
        log("    GET %-40s -> %s" % (path, str(exc)[:60]))
        return None
    try:
        os.makedirs(OUT, exist_ok=True)
        name = "raw-%s.%s" % (label, "txt" if text else "json")
        with io.open(os.path.join(OUT, name), "w") as fh:
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
    view_id = os.environ.get("OPSTAT_S3_VIEW_ID", "").strip()
    bucket = os.environ.get("OPSTAT_S3_BUCKET", "").strip()
    if not view_id and not bucket:
        print("ERROR: set OPSTAT_S3_VIEW_ID or OPSTAT_S3_BUCKET.", file=sys.stderr)
        return 2

    port = int(os.environ.get("OPSTAT_PORT", "443"))
    base = ("https://%s/api" % vms) if port == 443 \
        else ("https://%s:%d/api" % (vms, port))
    headers, _a, _p = vast_common.resolve_auth(
        os.environ.get("OPSTAT_USER", "admin"), vms, None,
        "opstat/s3-endpoint-derivation")
    vast_common.configure_connection(base, headers, ssl._create_unverified_context())

    cluster = (rows(get("/clusters/", "clusters")) or [{}])[0]
    cluster_id = cluster.get("id")
    log("cluster: %s  id=%s  sw_version: %s"
        % (cluster.get("name", "?"), cluster_id, cluster.get("sw_version", "?")))
    log("")

    # ---- 1. the view --------------------------------------------------------
    log("[ 1. view ]")
    view = None
    if view_id:
        view = (rows(get("/views/%s/" % view_id, "view")) or [None])[0]
    if view is None:
        for v in rows(get("/views/", "views-all")) or []:
            if (bucket and v.get("bucket") == bucket) or \
               (view_id and str(v.get("id")) == view_id):
                view = v
                break
    if view is None:
        log("  view not found (id=%r bucket=%r)" % (view_id, bucket))
        return 1
    log("  id=%s path=%s bucket=%s tenant=%s protocols=%s"
        % (view.get("id"), view.get("path"), view.get("bucket"),
           view.get("tenant_name"), view.get("protocols")))
    policy_id = view.get("policy_id") if isinstance(view.get("policy_id"), int) else None
    tenant_id = view.get("tenant_id")
    log("")

    # ---- 2. policy and tenant ----------------------------------------------
    log("[ 2. policy and tenant pool bindings ]")
    policy = None
    if policy_id is not None:
        policy = (rows(get("/viewpolicies/%s/" % policy_id, "viewpolicy")) or [None])[0]
        if policy:
            log("  policy %s vip_pools=%r s3_read_write=%r"
                % (policy_id, policy.get("vip_pools"), policy.get("s3_read_write")))
    tenant = None
    if isinstance(tenant_id, int):
        tenant = (rows(get("/tenants/%s/" % tenant_id, "tenant")) or [None])[0]
    if tenant:
        log("  tenant %s vippools=%s"
            % (tenant.get("name"),
               [p.get("name") for p in (tenant.get("vippools") or [])][:20]))
    refs = pool_refs(view) | pool_refs(policy or {}) | pool_refs(tenant or {})
    log("  pool references from the chain: %s" % (sorted(refs) or "none"))
    log("")

    # ---- 3. pools and VIPs, indexed by id AND name -------------------------
    log("[ 3. VIP inventory ]")
    pools = {}
    for p in rows(get("/vippools/", "vippools")) or []:
        pools[str(p.get("id"))] = p
        if p.get("name"):
            pools[str(p["name"])] = p
    vips = rows(get("/vips/", "vips")) or []
    index = index_vips_by_pool(vips)
    log("  %d VIPs; indexed under %d pool identifiers" % (len(vips), len(index)))
    for extra, label in (("/monitoredvippools/", "monitoredvippools"),
                         ("/monitoredvips/", "monitoredvips")):
        payload = get(extra, label)
        if payload is not None:
            extra_rows = rows(payload)
            log("  %-22s %d row(s)" % (extra, len(extra_rows)))
            more = index_vips_by_pool(extra_rows)
            for k, ips in more.items():
                for ip in ips:
                    index.setdefault(k, [])
                    if ip not in index[k]:
                        index[k].append(ip)
    if isinstance(tenant_id, int):
        ranges = get("/tenants/%s/vippool_ip_ranges/" % tenant_id, "vippool_ip_ranges")
        if ranges is not None:
            log("  /tenants/%s/vippool_ip_ranges/ captured" % tenant_id)
    log("")

    # ---- 4. AFFIRMATIVE S3 capability evidence -----------------------------
    log("[ 4. affirmative S3 capability evidence ]")
    s3_capable = {}
    if cluster_id is not None:
        cfg = get("/clusters/%s/s3_true_ip_config/" % cluster_id, "s3_true_ip_config")
        if cfg is not None:
            ips = s3_ips_from_true_ip_config(cfg)
            log("  s3_true_ip_config: %d IPv4 address(es) %s"
                % (len(ips), sorted(ips)[:8]))
            for ip in ips:
                s3_capable.setdefault(ip, set()).add("s3_true_ip_config")
        else:
            log("  s3_true_ip_config: unavailable on this build")
    vv = get("/prometheusmetrics/vip_view", "vip_view", text=True)
    if vv:
        observed = s3_vips_from_vip_view(vv)
        log("  vip_view protocol=S3: %d VIP(s) observed serving S3" % len(observed))
        for ip, info in sorted(observed.items(),
                               key=lambda kv: -kv[1]["value"])[:10]:
            log("    %-16s value=%.2f paths=%s tenants=%s"
                % (ip, info["value"], sorted(info["paths"])[:3],
                   sorted(info["tenants"])[:3]))
            s3_capable.setdefault(ip, set()).add("vip_view protocol=S3")
    else:
        log("  /prometheusmetrics/vip_view unavailable")
    if get("/prometheusmetrics/vips", "vips_exporter", text=True):
        log("  /prometheusmetrics/vips captured for the record")
    vdb = get("/vastdb/vips/", "vastdb_vips")
    if vdb is not None:
        log("  /vastdb/vips/: %d row(s) - VAST DB, NOT evidence of S3"
            % len(rows(vdb)))
    log("")

    # ---- 5. correlation ----------------------------------------------------
    log("[ 5. candidate correlation ]")
    candidates = correlate_candidates(refs, pools, index, s3_capable,
                                      view_id=view.get("id"))
    proven = proven_candidates(candidates)
    if not candidates:
        log("  no candidate addresses at all")
    for c in candidates[:30]:
        log("  %-16s pool=%-22s s3_evidence=%s%s"
            % (c["ip"], "%s/%s" % (c["pool_id"], c["pool_name"]),
               c["s3_evidence"] or "NONE",
               "  (name hint only)" if c["pool_name_hint"] and not c["s3_evidence"] else ""))
    log("")
    log("  PROVEN S3-capable: %d" % len(proven))
    for c in proven[:10]:
        log("    %-16s via %s" % (c["ip"], ", ".join(c["s3_evidence"])))
    if not proven:
        log("    none - ownership was established for %d address(es), but no"
            % len([c for c in candidates if c["owned_by_cluster"]]))
        log("    source affirmed S3 capability. A pool name containing 's3' is")
        log("    a hint, not evidence, and is deliberately not promoted here.")

    summary = {
        "cluster": cluster.get("name"), "cluster_id": cluster_id, "vms": vms,
        "view": {k: view.get(k) for k in
                 ("id", "path", "bucket", "tenant_id", "tenant_name", "protocols")},
        "policy_id": policy_id, "pool_refs": sorted(refs),
        "candidates": candidates,
        "proven_s3_capable": sorted({c["ip"] for c in proven}),
    }
    try:
        os.makedirs(OUT, exist_ok=True)
        with io.open(os.path.join(OUT, "s3-endpoint-derivation.json"), "w") as fh:
            json.dump(summary, fh, indent=2)
        log("")
        log("summary: %s" % os.path.join(OUT, "s3-endpoint-derivation.json"))
    except OSError:
        pass
    log("NOTE: ownership is not S3 capability, and a pool name is not evidence.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
