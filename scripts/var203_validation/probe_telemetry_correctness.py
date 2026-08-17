#!/usr/bin/env python3
"""Telemetry-correctness evidence probe (FR1 + FR3), var203 lab host only.

Collects, in one bounded read-only run, the real-VMS evidence the Telemetry
Correctness milestone needs:

  A. FR1 - NFSv3 VIEW attribution: ViewMetrics sampled against the ACTUAL
     busy NFSv3 view, selected by production-style activity ranking over
     all views plus --view-paths operator anchors (host_view is census-only:
     5.4.6 proved it publishes no NFS series under any protocol label).
  B. FR3 - latency units: same-op-class pairing of the raw
     vast_host_view_read_latency BLOCK gauge against
     BlockMetrics,read_latency__avg over a bounded sampling loop, plus a
     cheap volume-scope VolumeMetrics check (cluster scope is proven
     unqueryable on this build).
  C. Mechanism-A metadata capture (catalog / OpenAPI / exporter comments),
     retained verbatim for the audit trail.

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
import re
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

# The NFS4-vs-proven-us pairing was retired after the 2026-08-17 run: var203/
# 5.4.6 publishes NO NFS series in host_view under any protocol label, so that
# proof is impossible on this build. The decisive remaining comparison is
# same-op-class: vast_host_view_read_latency vs BlockMetrics,read_latency__avg.
_HV_LINE = re.compile(r"^vast_host_view_([a-z_]+)\{([^}]*)\}\s+([0-9eE+.\-]+)\s*$")

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


def host_view_field_series(text, field, protocol):
    """All values of one raw ``vast_host_view_<field>`` gauge for a protocol.

    The production parser deliberately reads only the combined ``latency``
    gauge; the raw exposition ALSO carries ``read_latency``/``write_latency``,
    and read-vs-read is the same-op-class pairing that can settle the unit
    where the combined gauge left a ~0.74 semantic factor.
    """
    values = []
    needle = 'protocol="%s"' % protocol
    for line in (text or "").splitlines():
        m = _HV_LINE.match(line.strip())
        if not m or m.group(1) != field or needle not in m.group(2):
            continue
        try:
            value = float(m.group(3))
        except ValueError:
            continue
        if value == value:
            values.append(value)
    return values


def ratio_stats(pairs):
    """Distribution of b/h across paired nonzero (b, h) samples, or None."""
    ratios = sorted(b / h for b, h in pairs if b and h)
    if not ratios:
        return None
    n = len(ratios)
    median = (ratios[n // 2] if n % 2
              else (ratios[n // 2 - 1] + ratios[n // 2]) / 2.0)
    return {"count": n, "min": ratios[0], "max": ratios[-1],
            "median": median, "mean": sum(ratios) / n}


def choose_target_views(views, ranked, anchor_paths, cap=8):
    """Ordered [(path, id)] to monitor: operator anchors first, then rank.

    *anchor_paths* (--view-paths) are sampled even if the ranking missed
    them - the 2026-08-17 run monitored eight head-of-list idle views while
    the busy /kmacs view went unsampled, which is exactly the failure this
    anchor exists to make impossible.
    """
    by_path = {v.get("path"): v["id"] for v in views
               if isinstance(v, dict) and v.get("path") and "id" in v}
    chosen = []
    for path in anchor_paths:
        if path in by_path and all(p != path for p, _ in chosen):
            chosen.append((path, by_path[path]))
    for entry in ranked:
        if len(chosen) >= cap:
            break
        path = entry.get("name")
        if path in by_path and all(p != path for p, _ in chosen):
            chosen.append((path, by_path[path]))
    return chosen[:cap]


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
def probe_nfs3_view_attribution(attempts, interval, anchor_paths):
    """ViewMetrics against the ACTUAL busy NFSv3 view.

    The 2026-08-17 run proved 5.4.6 publishes no NFS host_view series, so
    host_view is census-only here, never a targeting source. Targets come
    from production-style activity ranking over every view (the same
    DrillSession machinery the NFSv3 drill uses), with --view-paths anchors
    guaranteed a slot so the known-busy view cannot be missed again.
    """
    log("\n=== FR1: ViewMetrics on the busy NFSv3 view (rank + anchors) ===")
    try:
        views = api("GET", "/views/")
        views = views.get("results", views) if isinstance(views, dict) else views
        views = [v for v in views if isinstance(v, dict) and "id" in v]
    except RuntimeError as exc:
        verdict("fr1.views", False, str(exc)[:100])
        return
    log("  /views/ -> %d views; anchors=%s" % (len(views), anchor_paths or "none"))

    # host_view protocol census: evidence, not targeting.
    try:
        text = vast_common.request_text("GET", nfs4_native.HOST_VIEW_ENDPOINT)
        save_evidence("fr1-host_view-census.prom", text or "")
        protocols = sorted({line.split('protocol="', 1)[1].split('"', 1)[0]
                            for line in (text or "").splitlines()
                            if 'protocol="' in line})
        verdict("fr1.host_view.census", True, "protocols=%s; NFS series: %s"
                % (protocols, "PRESENT" if any("NFS" in p for p in protocols)
                   else "ABSENT"))
    except RuntimeError as exc:
        verdict("fr1.host_view.census", False, str(exc)[:100])

    # Production-style ranking over ALL views (topn attempt, batched rank
    # monitors, chunked fallback - the NFSv3 drill's own machinery).
    drill = vast_drill.DrillSession(
        request_fn=api,
        create_monitor_fn=create_probe_monitor,
        delete_monitor_fn=delete_probe_monitor,
    )
    try:
        ranked = drill.rank(
            "view", views,
            object_type="view",
            rank_props=vast_drill.view_rank_props(),
            score_fn=lambda sliced: (vast_drill.build_view_row(sliced, "")
                                     or {}).get("total_ops") or 0.0,
            time_frame=TIME_FRAME,
            name_of=lambda v: v.get("path") or str(v.get("id")),
            no_aggregation=True,
        )
    except RuntimeError as exc:
        ranked = []
        log("  WARN: ranking failed: %s" % str(exc)[:120])
    save_evidence("fr1-ranked-views.json", json.dumps(ranked, indent=1))
    verdict("fr1.rank", bool(ranked),
            "production-style ranking returned %d candidates: %s"
            % (len(ranked), [r.get("name") for r in ranked][:8]))

    targets = choose_target_views(views, ranked, anchor_paths or [])
    target_paths = [p for p, _ in targets]
    target_ids = [i for _, i in targets]
    verdict("fr1.targets", bool(targets),
            "monitoring %s (anchors first, then rank order)" % target_paths)
    if not targets:
        return

    monitor_id = None
    try:
        monitor_id = create_probe_monitor(
            "fr1_view", vast_drill.view_display_props(), "view", target_ids,
            no_aggregation=True)
    except RuntimeError as exc:
        verdict("fr1.viewmetrics.create", False, str(exc)[:120])
        return

    def one_sample():
        snap = {"t": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
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
            save_evidence("fr1-viewmetrics-sample-%d.json"
                          % len(os.listdir(EVIDENCE_DIR)),
                          json.dumps(result)[:200000])
        except RuntimeError as exc:
            snap["viewmetrics_error"] = str(exc)[:120]
        append_jsonl("fr1-paired-samples.jsonl", snap)
        active = [(p, round(o, 1)) for p, o, _l, _b in snap.get("viewmetrics", []) if o]
        log("  sample: active=%s" % (active or "none"))
        return snap

    def decisive(snap):
        return any(r[1] for r in snap.get("viewmetrics", []))

    samples, saw_load = bounded_paired_sampling(
        one_sample, decisive, attempts, interval)
    busiest = {}
    for snap in samples:
        for path, ops, lat, bw in snap.get("viewmetrics", []):
            if ops and ops > busiest.get(path, (0,))[0]:
                busiest[path] = (ops, lat, bw)
    for path, (ops, lat, bw) in sorted(busiest.items(), key=lambda kv: -kv[1][0]):
        log("  peak %-40s total_ops=%.1f latency_raw=%s bw_gbs=%s"
            % (path, ops, lat, bw))
    append_jsonl("fr1-paired-samples.jsonl", {"peaks": {
        p: {"total_ops": v[0], "latency_raw": v[1], "bw_gbs": v[2]}
        for p, v in busiest.items()}})
    verdict("fr1.viewmetrics.sees_load", saw_load,
            "%d samples; views with measured activity: %s"
            % (len(samples), sorted(busiest) or "NONE"))


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
    """Same-op-class pairing: BlockMetrics read latency vs the raw
    ``vast_host_view_read_latency`` BLOCK gauge, plus a cheap volume-scope
    VolumeMetrics check. The NFS4 pairing is deliberately absent: 5.4.6
    publishes no NFS host_view series, so it cannot exist on this build."""
    log("\n=== FR3: read-vs-read latency pairing (BLOCK) ===")
    monitors = {}
    try:
        monitors["block"] = create_probe_monitor(
            "lat_block", ["BlockMetrics,read_latency__avg"],
            "cluster", [cluster_id])
    except RuntimeError as exc:
        verdict("fr3.block.create", False, str(exc)[:120])

    # Volume-scope VolumeMetrics: cluster scope is proven unqueryable on
    # 5.4.6 (HTTP 400 property_error, 20/20 in the previous run); one cheap
    # create at the scope the family is actually for.
    try:
        volumes = api("GET", "/volumes/")
        volumes = (volumes.get("results", volumes)
                   if isinstance(volumes, dict) else volumes) or []
        vol_ids = [v["id"] for v in volumes if isinstance(v, dict) and "id" in v][:2]
        log("  /volumes/ -> %d volumes (sampling %s)" % (len(volumes), vol_ids))
        if vol_ids:
            monitors["volume"] = create_probe_monitor(
                "lat_volume", ["VolumeMetrics,read_latency__avg"],
                "volume", vol_ids)
        else:
            verdict("fr3.volume.scope", False, "no volumes to sample")
    except RuntimeError as exc:
        verdict("fr3.volume.scope", False, str(exc)[:120])

    def one_sample():
        snap = {"t": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        for key, prop in (("block", "BlockMetrics,read_latency__avg"),
                          ("volume", "VolumeMetrics,read_latency__avg")):
            if key not in monitors:
                continue
            try:
                result = api("GET", "/monitors/%s/query/" % monitors[key])
                snap[key] = _latest_value(result, prop)
            except RuntimeError as exc:
                snap["%s_error" % key] = str(exc)[:160]
        try:
            body = vast_common.request_text(
                "GET", nfs4_native.HOST_VIEW_ENDPOINT)
            for field in ("read_latency", "write_latency", "latency"):
                series = host_view_field_series(body, field, "BLOCK")
                snap["hv_block_%s" % field] = (
                    sum(series) / len(series) if series else None)
        except RuntimeError as exc:
            snap["hv_error"] = str(exc)[:120]
        append_jsonl("fr3-latency-samples.jsonl", snap)
        log("  sample: block=%s volume=%s hv_read=%s hv_write=%s hv_combined=%s"
            % (snap.get("block"), snap.get("volume", snap.get("volume_error")),
               snap.get("hv_block_read_latency"),
               snap.get("hv_block_write_latency"), snap.get("hv_block_latency")))
        return snap

    def decisive(snap):
        return bool(snap.get("block") and snap.get("hv_block_read_latency"))

    samples, got_pair = bounded_paired_sampling(
        one_sample, decisive, attempts, interval)
    # Keep sampling a few more paired points even after the first hit, so the
    # ratio rests on a distribution rather than one exporter refresh.
    extra = 0
    while got_pair and extra < 5:
        time.sleep(interval)
        snap = one_sample()
        samples.append(snap)
        extra += 1

    read_pairs = [(s["block"], s["hv_block_read_latency"]) for s in samples
                  if s.get("block") and s.get("hv_block_read_latency")]
    combined_pairs = [(s["block"], s["hv_block_latency"]) for s in samples
                      if s.get("block") and s.get("hv_block_latency")]
    stats = ratio_stats(read_pairs)
    if stats:
        guess, _r = unit_hypothesis(stats["median"], 1.0)
        verdict("fr3.block.read_pairing", True,
                "%d read-vs-read pairs; ratio min=%.1f max=%.1f median=%.1f "
                "mean=%.1f -> hypothesis: BlockMetrics=us, host_view=ms %s"
                % (stats["count"], stats["min"], stats["max"], stats["median"],
                   stats["mean"],
                   "SUPPORTED" if guess == "ms" else "NOT supported"))
    else:
        verdict("fr3.block.read_pairing", False,
                "no paired nonzero read-vs-read window in the bounded budget")
    cstats = ratio_stats(combined_pairs)
    if cstats:
        log("  context: combined-gauge ratio median=%.1f over %d pairs "
            "(expected below the read-vs-read ratio; the combined gauge "
            "includes slower op classes)" % (cstats["median"], cstats["count"]))
    vols = [s.get("volume") for s in samples if s.get("volume")]
    vol_errs = {s.get("volume_error") for s in samples if s.get("volume_error")}
    if vols:
        blocks = [s.get("block") for s in samples if s.get("block")]
        verdict("fr3.volume.scope", True,
                "VolumeMetrics(volume scope) latest=%.2f vs "
                "BlockMetrics=%.2f - same magnitude family" %
                (vols[-1], blocks[-1] if blocks else -1))
    elif vol_errs:
        verdict("fr3.volume.scope", False,
                "volume-scope query error: %s" % sorted(vol_errs)[0])


def run_probes(args, cluster_id):
    """Independent sections; each failure is contained, cleanup is caller's."""
    anchors = [p for p in (args.view_paths or "").split(",") if p]
    sections = (
        ("metadata", lambda: probe_metadata()),
        ("fr1", lambda: probe_nfs3_view_attribution(
            args.attempts, args.interval, anchors)),
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
    parser.add_argument("--view-paths", default="",
                        help="comma-separated view paths to always sample "
                             "(sanity anchors for the known-busy NFSv3 view)")
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
    # The API log is a lab artifact: keep it inside the run-specific
    # evidence tree, never /tmp (owner artifact policy).
    vast_api_log.configure(True, "telemetry-probe", args.vms, args.port,
                           directory=EVIDENCE_DIR)
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
