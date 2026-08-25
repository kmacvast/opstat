#!/usr/bin/env python3
"""host_view protocol-attribution capability probe. GET-only, no monitors.

Decides, per protocol label, whether ``/prometheusmetrics/host_view`` on the
target cluster can answer "which views are carrying <protocol> traffic right
now" - the capability question behind D-016's reopen clause (NFS3 on
5.5.0.1-class builds) and behind rebuilding the SMB VIEW / S3 BUCKET drills
away from the all-protocol ViewMetrics family (FR14).

For each scrape it records, per ``protocol=`` label value:
  - series count and distinct (ip, path, tenant) row keys
  - distinct view paths, client ips, tenant label values
  - which of the twelve gauge fields actually appear
  - summed iops / bw, so a live loadgen shows up as non-zero attribution
and parses the target protocols through the PRODUCTION parser
(``nfs4_native.parse_host_view``) so the probe proves the exact code path the
drill would use, not a lookalike.

Safety contract: read-only GETs only (/clusters/ and /vips/ once, then one
host_view scrape per sample); no monitors, no writes, no non-GET requests of
any kind.

Target-consistency hard-fail: OPSTAT_WORKLOAD_IP names the server address the
workload actually targets (the wrapper derives it from the mount or loadgen
configuration). The probe fetches the VMS's own /vips/ inventory and REFUSES
to run the scrapes unless that address belongs to the probed cluster - a
workload pointed at another cluster must never be accepted as evidence. The
first FR14 NFS3 run demonstrated exactly that failure: 866k proven client ops
against 172.200.202.x while var204 was probed, yielding a misleading
PRESENT(idle).

Environment:
  OPSTAT_VMS                  target VMS host (required)
  OPSTAT_PORT                 default 443
  OPSTAT_USER                 default admin
  VAST_TOKEN / VAST_PASSWORD  credentials (required, never printed)
  OPSTAT_PROBE_SAMPLES        default 6
  OPSTAT_PROBE_INTERVAL       seconds between scrapes, default 10
  OPSTAT_PROBE_PROTOCOLS      default NFS3,NFS4,SMB2,S3,BLOCK,NDB
  OPSTAT_PROBE_OUT            output directory (required; the lab wrapper
                              points it beneath the run dir)
  OPSTAT_WORKLOAD_IP          server IP the workload targets (required; the
                              wrapper derives it - the probe exits 3 if it
                              is absent or not owned by the probed VMS)

Python 3.8+, stdlib only.
"""

import io
import json
import os
import re
import ssl
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import nfs4_native                                          # noqa: E402
import vast_api_log                                         # noqa: E402
import vast_common                                          # noqa: E402

_SERIES = re.compile(r"^(vast_host_view_[a-z_]+)\{([^}]*)\}\s+(\S+)")
_LABEL = re.compile(r'(\w+)="([^"]*)"')
_IPV4 = re.compile(r"^(?:\d{1,3}\.){3}\d{1,3}$")


def _ip_key(ip):
    try:
        a, b, c, d = (int(x) for x in ip.split("."))
    except ValueError:
        return None
    if max(a, b, c, d) > 255:
        return None
    return (a << 24) | (b << 16) | (c << 8) | d


def collect_vip_addresses(payload):
    """Every IPv4 literal in the /vips/ payload, plus (start, end) ranges.

    VIP objects vary by build (ip/address literals, or start/end range
    fields), so gather both rather than assuming one schema."""
    literals, ranges = set(), []

    def walk(node):
        if isinstance(node, dict):
            starts = {k: v for k, v in node.items()
                      if isinstance(v, str) and _IPV4.match(v)
                      and "start" in k.lower()}
            ends = {k: v for k, v in node.items()
                    if isinstance(v, str) and _IPV4.match(v)
                    and "end" in k.lower()}
            if starts and ends:
                for sv in starts.values():
                    for ev in ends.values():
                        ranges.append((sv, ev))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
        elif isinstance(node, str) and _IPV4.match(node):
            literals.add(node)

    walk(payload)
    return literals, ranges


def vms_owns_ip(ip, literals, ranges):
    if ip in literals:
        return True
    key = _ip_key(ip)
    if key is None:
        return False
    for start, end in ranges:
        lo, hi = _ip_key(start), _ip_key(end)
        if lo is not None and hi is not None and lo <= key <= hi:
            return True
    return False


def log(msg):
    print(msg, flush=True)


def scan_exposition(text):
    """Label-level inventory of every vast_host_view_* series, per protocol."""
    protocols = {}
    label_keys = set()
    for line in (text or "").splitlines():
        if not line or line[0] == "#" or "vast_host_view_" not in line:
            continue
        match = _SERIES.match(line.strip())
        if not match:
            continue
        name, label_block, raw = match.groups()
        labels = dict(_LABEL.findall(label_block))
        label_keys.update(labels)
        proto = labels.get("protocol", "<unlabelled>")
        entry = protocols.setdefault(proto, {
            "series": 0, "fields": set(), "paths": set(), "ips": set(),
            "tenants": set(), "iops_sum": 0.0, "bw_sum": 0.0,
        })
        entry["series"] += 1
        entry["fields"].add(name[len("vast_host_view_"):])
        entry["paths"].add(labels.get("path", "?"))
        entry["ips"].add(labels.get("ip", "?"))
        if "tenant" in labels:
            entry["tenants"].add(labels["tenant"])
        try:
            value = float(raw)
        except ValueError:
            continue
        if value != value:
            continue
        if name == "vast_host_view_iops":
            entry["iops_sum"] += value
        elif name == "vast_host_view_bw":
            entry["bw_sum"] += value
    return protocols, sorted(label_keys)


def main():
    vms = os.environ.get("OPSTAT_VMS")
    out = os.environ.get("OPSTAT_PROBE_OUT")
    if not vms or not out:
        print("ERROR: OPSTAT_VMS and OPSTAT_PROBE_OUT are required.",
              file=sys.stderr)
        return 2
    if not (os.environ.get("VAST_TOKEN") or os.environ.get("VAST_PASSWORD")):
        print("ERROR: set VAST_TOKEN or VAST_PASSWORD in the environment.",
              file=sys.stderr)
        return 2
    port = int(os.environ.get("OPSTAT_PORT", "443"))
    user = os.environ.get("OPSTAT_USER", "admin")
    samples = int(os.environ.get("OPSTAT_PROBE_SAMPLES", "6"))
    interval = float(os.environ.get("OPSTAT_PROBE_INTERVAL", "10"))
    targets = [p.strip() for p in os.environ.get(
        "OPSTAT_PROBE_PROTOCOLS", "NFS3,NFS4,SMB2,S3,BLOCK,NDB").split(",")
        if p.strip()]
    os.makedirs(out, exist_ok=True)

    base_url = ("https://%s/api" % vms) if port == 443 \
        else ("https://%s:%d/api" % (vms, port))
    headers, _auth, _pw = vast_common.resolve_auth(
        user, vms, None, "opstat/hostview-probe")
    # Same TLS posture as the production engines (self-signed VMS certs).
    ctx = ssl._create_unverified_context()
    vast_common.configure_connection(base_url, headers, ctx)
    vast_api_log.configure(True, "hostview-probe", vms, port)
    log("api log: %s" % vast_api_log.log_path())

    clusters = vast_common.request("GET", "/clusters/")
    row = (clusters if isinstance(clusters, list)
           else clusters.get("results", [{}]))[0]
    cluster_name = row.get("name", "?")
    sw_version = row.get("sw_version", "?")
    log("cluster: %s  sw_version: %s  target: %s:%d"
        % (cluster_name, sw_version, vms, port))

    # ---- target-consistency hard-fail (never accept cross-cluster load) ----
    workload_ip = os.environ.get("OPSTAT_WORKLOAD_IP", "").strip()
    if not workload_ip or not _IPV4.match(workload_ip):
        print("ERROR: OPSTAT_WORKLOAD_IP is missing or not an IPv4 address "
              "(%r). The wrapper must derive the workload's actual server "
              "address; refusing to scrape without it." % workload_ip,
              file=sys.stderr)
        return 3
    vips_payload = vast_common.request("GET", "/vips/")
    literals, ranges = collect_vip_addresses(vips_payload)
    if not vms_owns_ip(workload_ip, literals, ranges):
        print("ERROR: TARGET MISMATCH - the workload targets %s, but %s "
              "(cluster %s) does not own that address.\n  VIP literals: %s\n"
              "  VIP ranges: %s\nEvidence from a workload pointed at another "
              "cluster is not evidence about this one; fix the mount/loadgen "
              "target or probe the cluster that owns %s."
              % (workload_ip, vms, cluster_name, sorted(literals),
                 ranges, workload_ip), file=sys.stderr)
        return 3
    log("workload target %s is owned by %s - consistency check PASS"
        % (workload_ip, cluster_name))

    report = {
        "cluster": cluster_name, "sw_version": sw_version,
        "vms": vms, "port": port, "targets": targets,
        "workload_ip": workload_ip,
        "vip_literals": sorted(literals), "vip_ranges": ranges,
        "collected_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "samples": [],
    }
    failures = 0
    for n in range(1, samples + 1):
        started = time.monotonic()
        try:
            body = vast_common.request_text(
                "GET", nfs4_native.HOST_VIEW_ENDPOINT)
        except RuntimeError as exc:
            log("SCRAPE %d FAILED: %s" % (n, exc))
            failures += 1
            report["samples"].append({"n": n, "error": str(exc)})
            time.sleep(interval)
            continue
        elapsed = time.monotonic() - started
        raw_path = os.path.join(out, "host_view-%02d.txt" % n)
        with io.open(raw_path, "w") as fh:
            fh.write(body or "")
        protocols, label_keys = scan_exposition(body)
        sample = {
            "n": n, "bytes": len(body or ""), "elapsed_s": round(elapsed, 3),
            "label_keys": label_keys,
            "protocols": {
                proto: {
                    "series": e["series"],
                    "fields": sorted(e["fields"]),
                    "paths": sorted(e["paths"])[:50],
                    "path_count": len(e["paths"]),
                    "ip_count": len(e["ips"]),
                    "tenant_values": sorted(e["tenants"])[:20],
                    "iops_sum": round(e["iops_sum"], 2),
                    "bw_sum": round(e["bw_sum"], 2),
                } for proto, e in sorted(protocols.items())
            },
            "production_parser": {},
        }
        # The exact rows the drill would show, via the production parser.
        for proto in targets:
            rows = nfs4_native.parse_host_view(body, proto)
            sample["production_parser"][proto] = [
                {"path": r["path"], "ip": r["ip"], "tenant": r["tenant"],
                 "iops": r["iops"], "bw": r["bw"],
                 "latency_us": r["latency_us"]}
                for r in rows[:10]
            ]
        report["samples"].append(sample)
        log("scrape %d/%d: %d bytes in %.2fs; protocol labels: %s"
            % (n, samples, sample["bytes"], elapsed,
               ", ".join("%s(%d series, %d paths, iops %.1f)"
                         % (p, d["series"], d["path_count"], d["iops_sum"])
                         for p, d in sample["protocols"].items()) or "NONE"))
        if n < samples:
            time.sleep(interval)

    # Aggregate verdicts - PRESENT means the label appeared with usable rows.
    verdicts = {}
    for proto in targets:
        seen = [s["protocols"].get(proto) for s in report["samples"]
                if "protocols" in s and proto in s.get("protocols", {})]
        if not seen:
            verdicts[proto] = "ABSENT"
        else:
            active = any(d["iops_sum"] > 0 for d in seen)
            verdicts[proto] = "PRESENT+ACTIVE" if active else "PRESENT(idle)"
    report["verdicts"] = verdicts

    json_path = os.path.join(out, "hostview-probe-summary.json")
    with io.open(json_path, "w") as fh:
        json.dump(report, fh, indent=2)
    log("")
    log("VERDICTS (per target protocol label, this cluster, this window):")
    for proto in targets:
        log("  %-6s : %s" % (proto, verdicts[proto]))
    log("")
    log("PRESENT+ACTIVE means host_view published the label with non-zero")
    log("attributed iops during the window. Correlate against the loadgen")
    log("evidence in the wrapper's ZIP before treating it as decisive.")
    log("summary: %s" % json_path)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
