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

Safety contract: two read-only GETs per sample (/clusters/ once, then the
host_view scrape); no monitors, no writes, no non-GET requests of any kind.

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

    report = {
        "cluster": cluster_name, "sw_version": sw_version,
        "vms": vms, "port": port, "targets": targets,
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
