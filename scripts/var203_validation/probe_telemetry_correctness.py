#!/usr/bin/env python3
"""Telemetry-correctness evidence probe (FR1 + FR3), var203 lab host only.

Collects, in one bounded read-only run, the real-VMS evidence the Telemetry
Correctness milestone needs:

  A. FR1 - NFSv3 VIEW attribution: ViewMetrics and host_view(protocol=NFS3)
     observed side by side under live NFSv3 load, with raw payloads kept.
  B. FR3 - host_view latency unit: raw exporter metadata (# HELP / # TYPE),
     raw catalog and OpenAPI schema descriptions, and a bounded loop of
     paired samples against the PROVEN-microseconds NFS4Common reference.
  C. FR3 - BlockMetrics / VolumeMetrics: paired samples against the
     host_view BLOCK gauge (decisive transitively once B lands).
  D. FR3 - SMB / S3: catalog/schema description extraction only.

Safety contract (same as probe_var203.py):
  * GETs plus temporary adhoc_opstat_probe_* monitors; every id recorded,
    deleted by exact id, 404-verified; enumerate-and-delete never used
  * cleanup runs in a finally, so a raising section still tears down
  * never modifies VMS configuration
  * sections are independent: one idle/failed source does not abort the rest

Raw evidence (verbatim bodies, comments included) is written under
--evidence-dir so every conclusion can be audited independently.

Python 3.8+, stdlib only, reuses opstat's own transport.
"""

import argparse
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import ssl                                                  # noqa: E402

import nfs4_native                                          # noqa: E402
import vast_api_log                                         # noqa: E402
import vast_common                                          # noqa: E402
import vast_drill                                           # noqa: E402

TIME_FRAME = "10m"
CREATED = []
SUMMARY = []
EVIDENCE_DIR = None

# The proven-microseconds reference (D-003). Everything in section B keys off
# comparing an unknown source against this under the SAME live traffic.
REFERENCE_PROP = "ProtoMetrics,proto_name=NFS4Common,read_latency__avg"

# Candidate OpenAPI paths, mirroring vast_discovery's survey list.
OPENAPI_PATHS = (
    "/openapi.json", "/swagger.json", "/schema/?format=openapi",
    "/?format=openapi", "/latest/openapi.json",
)

UNIT_HINT_TOKENS = ("unit", "usec", "microsec", "millisec", "nanosec",
                    "latency", " ms", " us", "µs")


def log(msg):
    print(msg, flush=True)


def verdict(name, ok, detail=""):
    line = "PROBE:%s %s %s" % (name, "PASS" if ok else "FAIL", detail)
    log(line)
    SUMMARY.append(line)


def api(method, path, payload=None):
    return vast_common.request(method, path, payload)


def save_evidence(name, body):
    """Write one raw evidence file verbatim - metadata and comments intact."""
    if EVIDENCE_DIR is None:
        return None
    path = os.path.join(EVIDENCE_DIR, name)
    mode = "wb" if isinstance(body, bytes) else "w"
    with open(path, mode) as fh:
        fh.write(body)
    log("  evidence: %s (%d bytes)" % (path, len(body)))
    return path


def append_jsonl(name, record):
    if EVIDENCE_DIR is None:
        return
    with open(os.path.join(EVIDENCE_DIR, name), "a") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")


def create_probe_monitor(suffix, prop_list, object_type, object_ids,
                         no_aggregation=False):
    name = "adhoc_opstat_probe_%s_%d" % (suffix, int(time.time()))
    monitor_id = vast_common.create_monitor_raw(
        api, name, prop_list, object_type, object_ids,
        time_frame=TIME_FRAME, no_aggregation=no_aggregation,
    )
    CREATED.append(monitor_id)
    log("  created monitor %s (%s)" % (monitor_id, name))
    return monitor_id


def delete_probe_monitor(monitor_id):
    try:
        vast_common.delete_monitor(api, monitor_id)
        log("  deleted monitor %s" % monitor_id)
    except RuntimeError as exc:
        log("  WARNING: delete of %s failed: %s" % (monitor_id, exc))


def cleanup_all():
    """Delete exactly this session's monitors; per-id GET, 404 = gone."""
    for monitor_id in list(CREATED):
        delete_probe_monitor(monitor_id)
    leaked, unknown = [], []
    for monitor_id in CREATED:
        try:
            api("GET", "/monitors/%s/" % monitor_id)
            leaked.append(monitor_id)
        except RuntimeError as exc:
            if "404" in str(exc):
                continue
            unknown.append("%s (%s)" % (monitor_id, exc))
    if leaked or unknown:
        verdict("cleanup.exact_ids", False,
                "leaked=%s unverifiable=%s" % (leaked, unknown))
        log("  ACTION REQUIRED: report these ids; do NOT sweep other "
            "adhoc_opstat_* monitors.")
    else:
        verdict("cleanup.exact_ids", True,
                "all %d session ids confirmed gone by per-id GET"
                % len(CREATED))


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested; no I/O)
# ---------------------------------------------------------------------------
def bounded_paired_sampling(sample_fn, decisive_fn, attempts, interval,
                            sleep_fn=time.sleep):
    """Sample up to *attempts* times, *interval* apart, keeping every sample.

    Returns (samples, decisive). The round-3/round-4 latency probes sampled
    ONCE and read an idle instant as "no evidence" three runs in a row; this
    loop keeps going until decisive_fn(sample) is true or the bounded budget
    is honestly exhausted. All samples are retained so the conclusion (and
    any idleness) can be audited.
    """
    samples = []
    for attempt in range(attempts):
        sample = sample_fn()
        samples.append(sample)
        if decisive_fn(sample):
            return samples, True
        if attempt < attempts - 1:
            sleep_fn(interval)
    return samples, False


def unit_hypothesis(reference_us, candidate):
    """Classify a candidate latency against a proven-microseconds reference.

    Same traffic, same moment. Returns (verdict, ratio) where verdict is one
    of "us", "ms", "ns", "inconclusive". ratio = reference_us / candidate.
    A candidate ~1000x SMALLER than the microsecond truth is reporting
    milliseconds; ~1000x larger is nanoseconds. Bands are deliberately wide
    (sampling semantics differ) but non-overlapping.
    """
    if not reference_us or not candidate or reference_us <= 0 or candidate <= 0:
        return "inconclusive", None
    ratio = reference_us / candidate
    if 0.2 <= ratio <= 5.0:
        return "us", ratio
    if 200.0 <= ratio <= 20000.0:
        return "ms", ratio
    if 0.00005 <= ratio <= 0.005:
        return "ns", ratio
    return "inconclusive", ratio


def host_view_rows(text, protocol):
    """Per-client rows for one protocol, via the production parser."""
    return nfs4_native.parse_host_view(text or "", protocol=protocol)


def aggregate_by_path(rows):
    """host_view client rows -> per-path totals (IOPS-summed, iops-weighted
    latency), mirroring how a view panel would aggregate them."""
    paths = {}
    for row in rows:
        agg = paths.setdefault(row["path"], {
            "path": row["path"], "iops": 0.0, "read_iops": 0.0,
            "write_iops": 0.0, "md_iops": 0.0, "bw": 0.0,
            "_lat_weight": 0.0, "_lat_sum": 0.0, "clients": 0,
        })
        agg["clients"] += 1
        for field in ("iops", "read_iops", "write_iops", "md_iops", "bw"):
            agg[field] += row.get(field) or 0.0
        lat = row.get("latency_us")
        iops = row.get("iops") or 0.0
        if lat is not None and iops > 0:
            agg["_lat_sum"] += lat * iops
            agg["_lat_weight"] += iops
    out = []
    for agg in paths.values():
        weight = agg.pop("_lat_weight")
        lat_sum = agg.pop("_lat_sum")
        agg["latency"] = (lat_sum / weight) if weight else None
        out.append(agg)
    return sorted(out, key=lambda r: -r["iops"])


def summarize_side_by_side(view_rows, hv_paths):
    """Accounting for the FR1 comparison: who saw the NFSv3 workload.

    view_rows: [(path_or_name, iops_or_None)] extracted from ViewMetrics.
    hv_paths:  aggregate_by_path() output for protocol=NFS3.
    """
    vm_active = [name for name, iops in view_rows if iops]
    hv_active = [r["path"] for r in hv_paths if r["iops"]]
    overlap = sorted(set(vm_active) & set(hv_active))
    return {
        "viewmetrics_paths_total": len(view_rows),
        "viewmetrics_paths_active": sorted(vm_active),
        "host_view_nfs3_paths_active": sorted(hv_active),
        "overlap": overlap,
        "viewmetrics_only": sorted(set(vm_active) - set(hv_active)),
        "host_view_only": sorted(set(hv_active) - set(vm_active)),
    }


def extract_unit_hints(text, tokens=UNIT_HINT_TOKENS):
    """Lines from a raw metadata body that might describe units."""
    hits = []
    for line in (text or "").splitlines():
        low = line.lower()
        if any(tok.strip() in low for tok in tokens):
            hits.append(line.strip())
    return hits


# ---------------------------------------------------------------------------
# Section: raw metadata (proof mechanism A)
# ---------------------------------------------------------------------------
def probe_metadata():
    log("\n=== A-mechanism metadata: catalog, OpenAPI, exporter comments ===")
    # Raw /metrics/ catalog pages, verbatim (descriptions currently discarded
    # by the production catalog reader may carry unit information).
    path, pages = "/metrics/", 0
    unit_hits = []
    for page in range(20):
        try:
            body = vast_common.request_text("GET", path)
        except RuntimeError as exc:
            verdict("meta.catalog", pages > 0, "stopped at page %d: %s"
                    % (page, str(exc)[:80]))
            break
        if not body:
            break
        pages += 1
        save_evidence("catalog-page-%02d.json" % page, body)
        unit_hits.extend(extract_unit_hints(body))
        try:
            payload = json.loads(body)
        except ValueError:
            break
        nxt = payload.get("next") if isinstance(payload, dict) else None
        if not nxt or not isinstance(nxt, str):
            break
        marker = "/api"
        path = nxt[nxt.index(marker) + len(marker):] if marker in nxt else nxt
    if pages:
        verdict("meta.catalog", True, "%d raw pages saved, %d unit-hint lines"
                % (pages, len(unit_hits)))
    for line in unit_hits[:40]:
        log("    HINT: %s" % line[:200])

    # OpenAPI / schema, first candidate that answers.
    got_schema = False
    for candidate in OPENAPI_PATHS:
        try:
            body = vast_common.request_text("GET", candidate, root=True)
        except (RuntimeError, TypeError):
            try:
                body = vast_common.request_text("GET", candidate)
            except RuntimeError:
                continue
        if body and len(body) > 200:
            save_evidence("openapi%s.json" % candidate.replace("/", "_")
                          .replace("?", "_"), body)
            hints = extract_unit_hints(body)
            verdict("meta.openapi", True, "%s: %d bytes, %d unit-hint lines"
                    % (candidate, len(body), len(hints)))
            for line in hints[:20]:
                log("    HINT: %s" % line[:200])
            got_schema = True
            break
    if not got_schema:
        verdict("meta.openapi", False, "no OpenAPI/schema endpoint answered")

    # Raw exporter expositions INCLUDING comments. host_view is small; basic
    # is ~276 KB and fetched once, as evidence, never on any refresh path.
    for name, endpoint in (("host_view", nfs4_native.HOST_VIEW_ENDPOINT),
                           ("basic", nfs4_native.NFS4_ENDPOINT)):
        try:
            body = vast_common.request_text("GET", endpoint)
        except RuntimeError as exc:
            verdict("meta.exporter.%s" % name, False, str(exc)[:100])
            continue
        save_evidence("exporter-%s.prom" % name, body or "")
        comments = [l for l in (body or "").splitlines()
                    if l.startswith("#")]
        help_lat = [l for l in comments if "latency" in l.lower()]
        verdict("meta.exporter.%s" % name, True,
                "%d bytes, %d comment lines, %d latency comments"
                % (len(body or ""), len(comments), len(help_lat)))
        for line in help_lat[:10]:
            log("    COMMENT: %s" % line[:200])


# ---------------------------------------------------------------------------
# Section: FR1 side-by-side (ViewMetrics vs host_view protocol=NFS3)
# ---------------------------------------------------------------------------
def probe_nfs3_view_attribution(attempts, interval):
    log("\n=== FR1: ViewMetrics vs host_view(protocol=NFS3), live load ===")
    try:
        views = api("GET", "/views/")
        views = views.get("results", views) if isinstance(views, dict) else views
        views = [v for v in views if isinstance(v, dict) and "id" in v]
    except RuntimeError as exc:
        verdict("fr1.views", False, str(exc)[:100])
        return
    by_path = {v.get("path"): v["id"] for v in views if v.get("path")}
    log("  /views/ -> %d views" % len(views))

    # First scrape decides which views to monitor: the NFS3-active paths if
    # any, padded with head-of-list views for contrast.
    try:
        text = vast_common.request_text("GET", nfs4_native.HOST_VIEW_ENDPOINT)
    except RuntimeError as exc:
        text = ""
        log("  WARN: initial host_view scrape failed: %s" % str(exc)[:100])
    save_evidence("fr1-host_view-initial.prom", text or "")
    protocols = sorted({line.split('protocol="', 1)[1].split('"', 1)[0]
                        for line in (text or "").splitlines()
                        if 'protocol="' in line})
    log("  protocols present in host_view: %s" % (protocols or "none"))
    nfs3_paths = [r["path"] for r in
                  aggregate_by_path(host_view_rows(text, "NFS3"))]
    target_paths = [p for p in nfs3_paths if p in by_path][:6]
    for v in views:
        if len(target_paths) >= 8:
            break
        if v.get("path") and v["path"] not in target_paths:
            target_paths.append(v["path"])
    target_ids = [by_path[p] for p in target_paths if p in by_path][:8]
    log("  monitoring views: %s" % target_paths[:8])
    if not target_ids:
        verdict("fr1.targets", False, "no view ids to monitor")
        return

    monitor_id = None
    try:
        monitor_id = create_probe_monitor(
            "fr1_view", vast_drill.view_display_props(), "view", target_ids,
            no_aggregation=True)
    except RuntimeError as exc:
        verdict("fr1.viewmetrics.create", False, str(exc)[:120])

    def one_sample():
        snap = {"t": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        if monitor_id is not None:
            try:
                result = api("GET", "/monitors/%s/query/" % monitor_id)
                rows = []
                for oid, path in zip(target_ids, target_paths):
                    sliced = vast_drill.slice_result_for_object(result, oid)
                    row = vast_drill.build_view_row(sliced, path) if sliced else None
                    rows.append((path, (row or {}).get("total_ops"),
                                 (row or {}).get("latency_us"),
                                 (row or {}).get("bw_gbs")))
                snap["viewmetrics"] = rows
                snap["viewmetrics_raw_saved"] = bool(
                    save_evidence("fr1-viewmetrics-sample-%d.json"
                                  % len(os.listdir(EVIDENCE_DIR)),
                                  json.dumps(result)[:200000]))
            except RuntimeError as exc:
                snap["viewmetrics_error"] = str(exc)[:120]
        try:
            body = vast_common.request_text(
                "GET", nfs4_native.HOST_VIEW_ENDPOINT)
            snap["host_view_nfs3"] = aggregate_by_path(
                host_view_rows(body, "NFS3"))
        except RuntimeError as exc:
            snap["host_view_error"] = str(exc)[:120]
        append_jsonl("fr1-paired-samples.jsonl", snap)
        return snap

    def decisive(snap):
        vm = any(r[1] for r in snap.get("viewmetrics", []))
        hv = any(r["iops"] for r in snap.get("host_view_nfs3", []))
        return vm and hv

    samples, got_both = bounded_paired_sampling(
        one_sample, decisive, attempts, interval)
    last = samples[-1]
    vm_rows = [(p, ops or 0)
               for p, ops, _lat, _bw in last.get("viewmetrics", [])]
    side = summarize_side_by_side(vm_rows, last.get("host_view_nfs3", []))
    log("  side-by-side (last sample): %s" % json.dumps(side, sort_keys=True))
    append_jsonl("fr1-paired-samples.jsonl", {"summary": side})
    verdict("fr1.host_view.sees_nfs3",
            bool(side["host_view_nfs3_paths_active"]),
            "%d active NFS3 paths" % len(side["host_view_nfs3_paths_active"]))
    verdict("fr1.viewmetrics.sees_load", bool(side["viewmetrics_paths_active"]),
            "%d active paths of %d monitored"
            % (len(side["viewmetrics_paths_active"]), len(target_ids)))
    verdict("fr1.paired_window", got_both,
            "%d samples; both sources active simultaneously: %s"
            % (len(samples), got_both))


# ---------------------------------------------------------------------------
# Section: latency units (proof mechanism B; bounded loops)
# ---------------------------------------------------------------------------
def _latest_value(result, prop):
    vals, idx, _sample = vast_drill.latest_complete_values(result)
    if not vals or prop not in (idx or {}):
        return None
    try:
        return float(vals[prop])
    except (KeyError, TypeError, ValueError):
        return None


def probe_latency_units(cluster_id, attempts, interval):
    log("\n=== FR3: latency units - paired against the proven-us reference ===")
    monitors = {}
    for key, props in (
            ("ref", [REFERENCE_PROP]),
            ("block", ["BlockMetrics,read_latency__avg"]),
            ("volume", ["VolumeMetrics,read_latency__avg"])):
        try:
            monitors[key] = create_probe_monitor(
                "lat_%s" % key, props, "cluster", [cluster_id])
        except RuntimeError as exc:
            verdict("fr3.%s.create" % key, False, str(exc)[:120])

    def one_sample():
        snap = {"t": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        for key, prop in (("ref", REFERENCE_PROP),
                          ("block", "BlockMetrics,read_latency__avg"),
                          ("volume", "VolumeMetrics,read_latency__avg")):
            if key not in monitors:
                continue
            try:
                result = api("GET", "/monitors/%s/query/" % monitors[key])
                snap[key] = _latest_value(result, prop)
            except RuntimeError as exc:
                snap["%s_error" % key] = str(exc)[:120]
        try:
            body = vast_common.request_text(
                "GET", nfs4_native.HOST_VIEW_ENDPOINT)
            for proto in ("NFS4", "NFS3", "BLOCK", "SMB2"):
                rows = aggregate_by_path(host_view_rows(body, proto))
                busy = [r for r in rows if r["iops"] and r["latency"]]
                snap["hv_%s" % proto.lower()] = (
                    {"paths": len(rows), "latency": busy[0]["latency"],
                     "iops": busy[0]["iops"]} if busy else None)
        except RuntimeError as exc:
            snap["hv_error"] = str(exc)[:120]
        append_jsonl("fr3-latency-samples.jsonl", snap)
        log("  sample: ref=%s block=%s volume=%s hv_nfs4=%s hv_block=%s"
            % (snap.get("ref"), snap.get("block"), snap.get("volume"),
               (snap.get("hv_nfs4") or {}).get("latency"),
               (snap.get("hv_block") or {}).get("latency")))
        return snap

    def decisive(snap):
        return bool(snap.get("ref") and snap.get("hv_nfs4"))

    samples, got_pair = bounded_paired_sampling(
        one_sample, decisive, attempts, interval)
    verdict("fr3.paired_window", got_pair,
            "%d samples; nonzero NFS4 reference paired with host_view NFS4: %s"
            % (len(samples), got_pair))

    # Explicit unit hypotheses from the best paired sample.
    best = next((s for s in reversed(samples)
                 if s.get("ref") and s.get("hv_nfs4")), None)
    if best:
        ref = best["ref"]
        hv = best["hv_nfs4"]["latency"]
        guess, ratio = unit_hypothesis(ref, hv)
        verdict("fr3.host_view.unit", guess != "inconclusive",
                "reference=%.1fus host_view=%.4f ratio=%.1f -> "
                "hypothesis=%s (ms hypothesis %s)"
                % (ref, hv, ratio or -1, guess,
                   "SUPPORTED" if guess == "ms" else "not supported"))
    else:
        verdict("fr3.host_view.unit", False,
                "no paired nonzero window inside the bounded budget - "
                "unit remains unproven by this run")
    best_blk = next((s for s in reversed(samples)
                     if s.get("block") and s.get("hv_block")), None)
    if best_blk:
        blk = best_blk["block"]
        hvb = best_blk["hv_block"]["latency"]
        guess, ratio = unit_hypothesis(blk, hvb)
        log("  BlockMetrics(assumed us)=%.2f vs host_view BLOCK=%.4f "
            "ratio=%.1f -> consistent with BlockMetrics in us if host_view "
            "is ms: %s" % (blk, hvb, ratio or -1,
                           "YES" if guess == "ms" else "no/inconclusive"))
        verdict("fr3.block.pairing", True,
                "block=%.2f hv_block=%.4f ratio=%.1f" % (blk, hvb, ratio or -1))
    else:
        verdict("fr3.block.pairing", False,
                "no paired nonzero BLOCK window in the bounded budget")
    vols = [s.get("volume") for s in samples if s.get("volume")]
    blks = [s.get("block") for s in samples if s.get("block")]
    if vols and blks:
        verdict("fr3.volume.corroboration", True,
                "VolumeMetrics=%.2f vs BlockMetrics=%.2f (same scope/moment)"
                % (vols[-1], blks[-1]))
    else:
        verdict("fr3.volume.corroboration", False,
                "VolumeMetrics or BlockMetrics never nonzero in the window")


def run_probes(args, cluster_id):
    """Independent sections; each failure is contained, cleanup is caller's."""
    sections = (
        ("metadata", lambda: probe_metadata()),
        ("fr1", lambda: probe_nfs3_view_attribution(
            args.attempts, args.interval)),
        ("fr3", lambda: probe_latency_units(
            cluster_id, args.attempts, args.interval)),
    )
    for name, fn in sections:
        try:
            fn()
        except Exception as exc:            # noqa: BLE001 - resilience by design
            verdict("%s.section" % name, False,
                    "section raised: %r" % exc)


def main():
    global EVIDENCE_DIR
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--vms", required=True)
    parser.add_argument("--port", type=int, default=443)
    parser.add_argument("--user", default="admin")
    parser.add_argument("--evidence-dir", default=None,
                        help="directory for raw evidence files")
    parser.add_argument("--attempts", type=int, default=20,
                        help="bounded samples per paired loop")
    parser.add_argument("--interval", type=float, default=15.0,
                        help="seconds between paired samples")
    args = parser.parse_args()

    if not (os.environ.get("VAST_TOKEN") or os.environ.get("VAST_PASSWORD")):
        print("ERROR: set VAST_TOKEN or VAST_PASSWORD in the environment "
              "(never on the command line).", file=sys.stderr)
        return 2

    EVIDENCE_DIR = args.evidence_dir or (
        "/tmp/opstat-telemetry-evidence-%d" % int(time.time()))
    os.makedirs(EVIDENCE_DIR, exist_ok=True)

    base = ("https://%s/api" % args.vms if args.port == 443
            else "https://%s:%d/api" % (args.vms, args.port))
    headers, _auth, _pw = vast_common.resolve_auth(
        args.user, args.vms, None, "opstat/telemetry-probe")
    vast_common.configure_connection(
        base, headers, ssl._create_unverified_context())
    vast_api_log.configure(True, "telemetry-probe", args.vms, args.port)
    log("api log: %s" % vast_api_log.log_path())

    log("telemetry-correctness probe - %s"
        % time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    log("target %s:%s as %s; time_frame %s; evidence %s"
        % (args.vms, args.port, args.user, TIME_FRAME, EVIDENCE_DIR))

    try:
        cluster = api("GET", "/clusters/")
        first = cluster[0] if isinstance(cluster, list) and cluster else {}
        cluster_id = first.get("id")
        log("cluster: %s (id %s, %s)" % (
            first.get("name", "?"), cluster_id,
            vast_common.os_release_from_cluster(first) or "os unknown"))
        run_probes(args, cluster_id)
    finally:
        log("\n=== cleanup ===")
        cleanup_all()
        vast_common.close_connection()

    log("\n=== RESULT SUMMARY ===")
    for line in SUMMARY:
        log(line)
    log("monitors created this run: %s" % CREATED)
    log("evidence directory: %s" % EVIDENCE_DIR)
    return 0


if __name__ == "__main__":
    sys.exit(main())
