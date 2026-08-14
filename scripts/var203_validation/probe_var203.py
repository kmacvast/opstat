#!/usr/bin/env python3
"""Automated var203 probes for the continuation pass (work laptop only).

Answers, in one run, every live-cluster question the local (mock) work could
not: NVMe batch-monitor acceptance and splittability, rank-monitor acceptance,
and the unproven latency source units. Read-only by construction: GET requests
plus temporary monitors that are always deleted, exact-id verified.

Usage (work laptop, repository root):

    export VAST_PASSWORD=...      # or VAST_TOKEN; never on the command line
    python3 scripts/var203_validation/probe_var203.py \
        --vms var203.selab.vastdata.com --user admin \
        > /tmp/opstat-var203-probe.txt 2>&1

Safety contract:
  * targets the --vms host only; never modifies VMS configuration
  * creates only monitors named adhoc_opstat_probe_*, records every id,
    deletes exactly those ids, and verifies by GET that none remain
  * never touches other adhoc_opstat_* monitors (concurrent sessions exist
    on the shared lab cluster)
  * plain GETs otherwise; no DELETE outside the recorded monitor ids

Return the FULL output file. Every section prints PROBE:<name> lines with
machine-readable verdicts; the RESULT SUMMARY at the end is what feeds the
decision records.

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

import vast_common                                          # noqa: E402
import vast_drill                                           # noqa: E402

TIME_FRAME = "10m"
CREATED = []          # every monitor id this run creates, in order
SUMMARY = []


def log(msg):
    print(msg, flush=True)


def verdict(name, ok, detail=""):
    line = "PROBE:%s %s %s" % (name, "PASS" if ok else "FAIL", detail)
    log(line)
    SUMMARY.append(line)


def api(method, path, payload=None):
    return vast_common.request(method, path, payload)


def create_probe_monitor(suffix, prop_list, object_type, object_ids):
    name = "adhoc_opstat_probe_%s_%d" % (suffix, int(time.time()))
    monitor_id = vast_common.create_monitor_raw(
        api, name, prop_list, object_type, object_ids, time_frame=TIME_FRAME,
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
    """Delete exactly this session's monitors and prove each one is gone.

    Verification is **per id**, not by listing every monitor: a shared lab
    cluster carries other sessions' `adhoc_opstat_*` monitors that are none of
    our business, and depending on a list endpoint made the check fail for the
    wrong reason when that route was unavailable. Each id is probed
    individually and a 404 is the proof it is gone.
    """
    for monitor_id in list(CREATED):
        delete_probe_monitor(monitor_id)

    leaked, unknown = [], []
    for monitor_id in CREATED:
        try:
            api("GET", "/monitors/%s/" % monitor_id)
            leaked.append(monitor_id)          # still answers -> still there
        except RuntimeError as exc:
            if "404" in str(exc):
                continue                       # gone, as required
            unknown.append("%s (%s)" % (monitor_id, exc))
    if leaked or unknown:
        verdict("cleanup.exact_ids", False,
                "leaked=%s unverifiable=%s" % (leaked, unknown))
        log("  ACTION REQUIRED: report these ids; do NOT sweep other "
            "adhoc_opstat_* monitors.")
    else:
        verdict("cleanup.exact_ids", True,
                "all %d session ids confirmed gone by per-id GET" % len(CREATED))


def object_ids_for(endpoint, limit):
    data = api("GET", endpoint)
    if isinstance(data, dict):
        data = data.get("results", []) or []
    ids = [o["id"] for o in data if isinstance(o, dict) and "id" in o]
    log("  %s -> %d objects (using first %d)" % (endpoint, len(ids), limit))
    return ids[:limit]


# ---------------------------------------------------------------------------
# Probe 1: NVMe batch display monitors (multi-object_id, per scope)
# ---------------------------------------------------------------------------
def probe_batch(object_type, endpoint):
    """Can one BlockMetrics op-group monitor cover several objects, and is the
    response splittable per object_id? Decides whether the new NVMe batched
    drill layout engages on this cluster or falls back per-object."""
    log("\n=== batch monitor probe: object_type=%s ===" % object_type)
    try:
        ids = object_ids_for(endpoint, 4)
    except RuntimeError as exc:
        verdict("batch.%s.objects" % object_type, False, str(exc))
        return
    if len(ids) < 2:
        verdict("batch.%s.objects" % object_type, False,
                "fewer than 2 objects; probe not meaningful")
        return
    props = ["BlockMetrics,read_req", "BlockMetrics,read_latency__avg"]
    monitor_id = None
    try:
        monitor_id = create_probe_monitor(
            "batch_%s" % object_type, props, object_type, ids)
        verdict("batch.%s.create" % object_type, True, "ids=%s" % ids)
    except RuntimeError as exc:
        verdict("batch.%s.create" % object_type, False, str(exc))
        return
    try:
        result = api("GET", "/monitors/%s/query/" % monitor_id)
        prop_list = (result or {}).get("prop_list", [])
        has_oid = "object_id" in prop_list
        rows_per_obj = {
            oid: len((vast_drill.slice_result_for_object(result, oid) or {})
                     .get("data") or [])
            for oid in ids
        }
        splittable = has_oid and any(rows_per_obj.values())
        verdict("batch.%s.query" % object_type, True,
                "prop_list=%s" % prop_list)
        verdict("batch.%s.splittable" % object_type, splittable,
                "rows_per_object=%s" % json.dumps(rows_per_obj))
    except RuntimeError as exc:
        verdict("batch.%s.query" % object_type, False, str(exc))


# ---------------------------------------------------------------------------
# Probe 2: NVMe rank monitor (two same-family counters, multi-object)
# ---------------------------------------------------------------------------
def probe_rank(object_type, endpoint):
    log("\n=== rank monitor probe: object_type=%s ===" % object_type)
    try:
        ids = object_ids_for(endpoint, 8)
    except RuntimeError as exc:
        verdict("rank.%s.objects" % object_type, False, str(exc))
        return
    if len(ids) < 2:
        verdict("rank.%s.objects" % object_type, False, "fewer than 2 objects")
        return
    props = ["BlockMetrics,read_req", "BlockMetrics,write_req"]
    try:
        monitor_id = create_probe_monitor(
            "rank_%s" % object_type, props, object_type, ids)
        result = api("GET", "/monitors/%s/query/" % monitor_id)
        scores = {
            oid: vast_drill.delta_rate_from_samples(
                vast_drill.slice_result_for_object(result, oid),
                "BlockMetrics,read_req")
            for oid in ids
        }
        populated = sum(1 for v in scores.values() if v is not None)
        verdict("rank.%s.accepted" % object_type, True,
                "scores(read_req d/s)=%s" % json.dumps(
                    {str(k): (round(v, 3) if v else v)
                     for k, v in scores.items()}))
        log("  NOTE: %d/%d objects yielded a delta; zeros on an idle cluster "
            "are expected (run the block loadgen for a rate signal)"
            % (populated, len(ids)))
    except RuntimeError as exc:
        verdict("rank.%s.accepted" % object_type, False, str(exc))


# ---------------------------------------------------------------------------
# Probe 3: latency source units (compare unknown vs proven-µs under load)
# ---------------------------------------------------------------------------
def probe_latency_units(cluster_id):
    """Prints value pairs; the unit verdict is a human comparison.

    D-003 proved ProtoMetrics NFS4Common read_latency__avg is microseconds.
    If the unknown sources are the same order of magnitude for the same
    workload, they are µs too; a ~1000x gap means ms or ns. Requires load
    (NFS4 for host_view, block loadgen for BlockMetrics) to be meaningful.
    """
    log("\n=== latency unit cross-checks ===")
    # 3a. Known-µs reference: NFS4Common cluster read latency.
    try:
        mid = create_probe_monitor(
            "lat_ref", ["ProtoMetrics,proto_name=NFS4Common,read_latency__avg"],
            "cluster", [cluster_id])
        result = api("GET", "/monitors/%s/query/" % mid)
        vals, _idx, sample = vast_drill.latest_complete_values(result)
        log("  reference NFS4Common read_latency__avg (PROVEN us): %s @ %s"
            % (vals, sample))
        verdict("latency.reference", True, "values=%s" % vals)
    except RuntimeError as exc:
        verdict("latency.reference", False, str(exc))
    # 3b. Unknown: BlockMetrics read_latency__avg at cluster scope.
    try:
        mid = create_probe_monitor(
            "lat_block", ["BlockMetrics,read_latency__avg"], "cluster", [cluster_id])
        result = api("GET", "/monitors/%s/query/" % mid)
        vals, _idx, sample = vast_drill.latest_complete_values(result)
        log("  BlockMetrics read_latency__avg (unit UNPROVEN): %s @ %s"
            % (vals, sample))
        verdict("latency.blockmetrics", True, "values=%s" % vals)
    except RuntimeError as exc:
        verdict("latency.blockmetrics", False, str(exc))
    # 3c. Unknown: host_view latency gauge (exporter; needs NFS4 load).
    try:
        body = vast_common.request_text(
            "GET", "/prometheusmetrics/host_view")
        lat_lines = [l for l in body.splitlines()
                     if l.startswith("vast_host_view_latency")][:6]
        log("  host_view latency series (unit UNPROVEN):")
        for line in lat_lines:
            log("    %s" % line)
        verdict("latency.host_view", bool(lat_lines),
                "%d latency series" % len(lat_lines))
    except RuntimeError as exc:
        verdict("latency.host_view", False, str(exc))
    log("  INTERPRETATION: same order of magnitude as the reference for the "
        "same traffic -> microseconds; ~1000x smaller -> ms; ~1000x larger -> ns.")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--vms", required=True)
    parser.add_argument("--port", type=int, default=443)
    parser.add_argument("--user", default="admin")
    args = parser.parse_args()

    if not (os.environ.get("VAST_TOKEN") or os.environ.get("VAST_PASSWORD")):
        print("ERROR: set VAST_TOKEN or VAST_PASSWORD in the environment "
              "(never on the command line).", file=sys.stderr)
        return 2

    base = ("https://%s/api" % args.vms if args.port == 443
            else "https://%s:%d/api" % (args.vms, args.port))
    headers, _auth, _pw = vast_common.resolve_auth(
        args.user, args.vms, None, "opstat/var203-probe")
    vast_common.configure_connection(
        base, headers, ssl._create_unverified_context())

    log("var203 continuation-pass probe - %s" % time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    log("target %s:%s as %s; time_frame %s" % (args.vms, args.port, args.user, TIME_FRAME))

    try:
        cluster = api("GET", "/clusters/")
        first = cluster[0] if isinstance(cluster, list) and cluster else {}
        cluster_id = first.get("id")
        log("cluster: %s (id %s)" % (first.get("name", "?"), cluster_id))

        probe_batch("cnode", "/cnodes/")
        probe_batch("vip", "/vips/")
        probe_batch("blockhost", "/blockhosts/")
        probe_rank("cnode", "/cnodes/")
        probe_latency_units(cluster_id)
    finally:
        log("\n=== cleanup ===")
        cleanup_all()
        vast_common.close_connection()

    log("\n=== RESULT SUMMARY ===")
    for line in SUMMARY:
        log(line)
    log("monitors created this run: %s" % CREATED)
    return 0


if __name__ == "__main__":
    sys.exit(main())
