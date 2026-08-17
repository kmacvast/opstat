#!/usr/bin/env python3
"""FR2 discovery: the NFSv4 delegation endpoint's real shape, read-only.

Answers the design questions D-008's evidence (VAST 5.5.0.1) left open and
that FR2 needs before any production interaction is built:

  * Is GET /tenants/{id}/nfs4_delegs/ available on this build, and does it
    still demand file_path ("['__root__->file_path: field required']")?
  * What are the REAL per-record field names? (The mock's record shape is
    modeled, not evidence - D-008 recorded the wrapper keys only.)
  * What do a live-file query, a valid-but-idle file, a nonexistent path and
    a directory path each return?
  * What does a query cost (size/duration, from the API log)?

Safety contract (D-008 is absolute):
  * Every request goes through a wrapper that REFUSES any method but GET -
    the DELETE sibling revokes live delegations and must never be invoked.
  * No monitors, no writes of any kind; the API log must contain zero
    non-GET lines, and the lab script verifies exactly that.
  * If the cluster holds no active delegations, that is a valid result: the
    shape and empty-state evidence still lands, and the report says what
    remains unproven.

Raw responses are written verbatim under --evidence-dir.

Python 3.8+, stdlib only, reuses opstat's own transport.
"""

import argparse
import json
import os
import sys
import time
import urllib.parse

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import ssl                                                  # noqa: E402

import vast_api_log                                         # noqa: E402
import vast_common                                          # noqa: E402

SUMMARY = []
EVIDENCE_DIR = None
FIELDS_SEEN = {}


def log(msg):
    print(msg, flush=True)


def verdict(name, ok, detail=""):
    line = "PROBE:%s %s %s" % (name, "PASS" if ok else "FAIL", detail)
    log(line)
    SUMMARY.append(line)


def get_only(method, path, payload=None):
    """The probe's ONLY transport. Anything but GET is refused outright.

    The nfs4_delegs family has a DELETE sibling that revokes live
    delegations on a production cluster; this wrapper makes issuing it (or
    any other mutating request) a structural impossibility rather than a
    reviewed-for absence.
    """
    if method != "GET":
        raise RuntimeError(
            "fr2 delegation probe is GET-only; refused %s %s" % (method, path))
    return vast_common.request(method, path, payload)


def save_evidence(name, body):
    if EVIDENCE_DIR is None:
        return
    with open(os.path.join(EVIDENCE_DIR, name), "w") as fh:
        fh.write(body)
    log("  evidence: %s (%d bytes)" % (name, len(body)))


def deleg_path(tenant_id, file_path=None):
    base = "/tenants/%s/nfs4_delegs/" % tenant_id
    if file_path is None:
        return base
    return base + "?file_path=" + urllib.parse.quote(file_path, safe="")


def summarize_response(payload):
    """(records, count_total, extra_keys) from the delegate_info wrapper."""
    if not isinstance(payload, dict):
        return [], None, []
    records = payload.get("delegate_info") or []
    count = payload.get("delegate_info_count_total")
    extra = sorted(k for k in payload
                   if k not in ("delegate_info", "delegate_info_count_total"))
    return records, count, extra


def record_fields(records):
    fields = {}
    for rec in records:
        if isinstance(rec, dict):
            for key, value in rec.items():
                fields.setdefault(key, type(value).__name__)
    return fields


def probe_one(tag, tenant_id, tenant_name, file_path):
    label = file_path if file_path is not None else "(no file_path)"
    try:
        payload = get_only("GET", deleg_path(tenant_id, file_path))
    except RuntimeError as exc:
        detail = str(exc)
        save_evidence("deleg-%s-t%s.txt" % (tag, tenant_id), detail)
        required = "field required" in detail
        verdict("deleg.%s" % tag, required if file_path is None else False,
                "tenant %s %s -> %s" % (tenant_name, label, detail[:140]))
        return None
    body = json.dumps(payload, indent=1, sort_keys=True)
    save_evidence("deleg-%s-t%s.json" % (tag, tenant_id), body)
    records, count, extra = summarize_response(payload)
    fields = record_fields(records)
    FIELDS_SEEN.update(fields)
    verdict("deleg.%s" % tag, True,
            "tenant %s %s -> %d record(s), count_total=%s, "
            "record fields=%s, wrapper extras=%s"
            % (tenant_name, label, len(records), count,
               sorted(fields) or "none", extra))
    for rec in records[:3]:
        log("    RECORD: %s" % json.dumps(rec, sort_keys=True)[:300])
    return payload


def main():
    global EVIDENCE_DIR
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--vms", required=True)
    parser.add_argument("--port", type=int, default=443)
    parser.add_argument("--user", default="admin")
    parser.add_argument("--file-paths", default="",
                        help="comma-separated SERVER-side file paths to "
                             "query (files the NFSv4.1 loadgen holds open "
                             "give the best chance of live delegations)")
    parser.add_argument("--dir-paths", default="",
                        help="comma-separated directory paths, to establish "
                             "directory/prefix semantics")
    parser.add_argument("--evidence-dir", default=None)
    args = parser.parse_args()

    if not (os.environ.get("VAST_TOKEN") or os.environ.get("VAST_PASSWORD")):
        print("ERROR: set VAST_TOKEN or VAST_PASSWORD in the environment.",
              file=sys.stderr)
        return 2

    EVIDENCE_DIR = args.evidence_dir or (
        "/tmp/opstat-fr2-evidence-%d" % int(time.time()))
    os.makedirs(EVIDENCE_DIR, exist_ok=True)

    base = ("https://%s/api" % args.vms if args.port == 443
            else "https://%s:%d/api" % (args.vms, args.port))
    headers, _auth, _pw = vast_common.resolve_auth(
        args.user, args.vms, None, "opstat/fr2-delegation-probe")
    vast_common.configure_connection(
        base, headers, ssl._create_unverified_context())
    vast_api_log.configure(True, "fr2-delegations", args.vms, args.port,
                           directory=EVIDENCE_DIR)
    log("api log: %s" % vast_api_log.log_path())
    log("fr2 delegation discovery - %s"
        % time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))

    try:
        cluster = get_only("GET", "/clusters/")
        first = cluster[0] if isinstance(cluster, list) and cluster else {}
        log("cluster: %s (%s)" % (
            first.get("name", "?"),
            vast_common.os_release_from_cluster(first) or "os unknown"))

        tenants = get_only("GET", "/tenants/")
        tenants = (tenants.get("results", tenants)
                   if isinstance(tenants, dict) else tenants) or []
        tenants = [(t["id"], t.get("name", t["id"])) for t in tenants
                   if isinstance(t, dict) and "id" in t]
        log("tenants: %s" % tenants)
        verdict("tenants", bool(tenants), "%d tenants" % len(tenants))

        file_paths = [p for p in args.file_paths.split(",") if p]
        dir_paths = [p for p in args.dir_paths.split(",") if p]
        for tid, name in tenants[:3]:
            # Availability + required-parameter contract on THIS build.
            probe_one("availability", tid, name, None)
            for i, fp in enumerate(file_paths[:6]):
                probe_one("file%d" % i, tid, name, fp)
            for i, dp in enumerate(dir_paths[:2]):
                probe_one("dir%d" % i, tid, name, dp)
            missing = "/does-not-exist-opstat-fr2-%d" % int(time.time())
            probe_one("missing", tid, name, missing)
        if not file_paths:
            log("  NOTE: no --file-paths supplied; live-delegation evidence "
                "cannot be gathered this run")
        if FIELDS_SEEN:
            log("\nOBSERVED RECORD FIELDS (union): %s"
                % json.dumps(FIELDS_SEEN, indent=1, sort_keys=True))
            save_evidence("record-fields.json",
                          json.dumps(FIELDS_SEEN, indent=1, sort_keys=True))
        else:
            log("\nNo delegation records observed; field names remain "
                "unproven by this run (shape/empty evidence still captured).")
    finally:
        vast_common.close_connection()

    log("\n=== RESULT SUMMARY ===")
    for line in SUMMARY:
        log(line)
    log("evidence directory: %s" % EVIDENCE_DIR)
    log("SAFETY: this probe issues GET requests only; the API log must "
        "contain zero non-GET lines.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
