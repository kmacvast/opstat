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


def server_path_for(client_path, mountpoint, export_path):
    """Client file path -> server-side namespace path.

    mount: <server>:<export_path> on <mountpoint>. A client file
    <mountpoint>/a/b maps to <export_path>/a/b. Returns None when the client
    path is not under the mountpoint (never guess).
    """
    mp = mountpoint.rstrip("/")
    if client_path != mp and not client_path.startswith(mp + "/"):
        return None
    rel = client_path[len(mp):]
    base = export_path.rstrip("/")
    return (base + rel) or "/"


def candidate_views(views, server_path):
    """Views that could own *server_path*: exact match first, then prefix
    matches, longest first (the root view "/" matches everything - the FR1
    lesson: NFS mounts traverse the root view when no exact view exists).
    Only NFS-capable views count."""
    out = []
    for v in views:
        if not isinstance(v, dict) or "id" not in v:
            continue
        path = (v.get("path") or "").rstrip("/") or "/"
        protos = v.get("protocols") or []
        if protos and not any(str(p).upper().startswith("NFS") for p in protos):
            continue
        if server_path == path:
            out.append((0, -len(path), v, "exact"))
        elif path == "/" or server_path.startswith(path + "/"):
            out.append((1, -len(path), v, "prefix"))
    out.sort(key=lambda t: (t[0], t[1]))
    return [(v, kind) for _a, _b, v, kind in out]


def candidate_tenants(cands, cap=3):
    """Ordered distinct (tenant_id, tenant_name, via_view) from candidate
    views. Returns (tenants, ambiguous): ambiguous means MORE than *cap*
    distinct tenants could own the namespace - record and stop rather than
    spraying queries at random tenants."""
    seen, tenants = set(), []
    for v, kind in cands:
        tid = v.get("tenant_id")
        if tid is None or tid in seen:
            continue
        seen.add(tid)
        tenants.append((tid, v.get("tenant_name", tid),
                        "%s view id %s path %s" % (kind, v.get("id"), v.get("path"))))
    return tenants[:cap], len(tenants) > cap


def path_representations(server_path, view_path, export_path):
    """Bounded, DERIVED file_path syntaxes for one known-existing file:
    full namespace path; view-relative; export-relative. Deduplicated,
    never sprayed."""
    reps = [server_path]
    for base in (view_path, export_path):
        base = (base or "").rstrip("/")
        if base and base != "/" and server_path.startswith(base + "/"):
            reps.append(server_path[len(base):])
    out = []
    for r in reps:
        if r and r not in out:
            out.append(r)
    return out[:3]


def probe_one(tag, tenant_id, tenant_name, file_path):
    label = file_path if file_path is not None else "(no file_path)"
    started = time.monotonic()
    try:
        payload = get_only("GET", deleg_path(tenant_id, file_path))
    except RuntimeError as exc:
        detail = str(exc)
        save_evidence("deleg-%s-t%s.txt" % (tag, tenant_id), detail)
        elapsed_ms = (time.monotonic() - started) * 1000.0
        required = "field required" in detail
        verdict("deleg.%s" % tag, required if file_path is None else False,
                "tenant %s %s [%.0fms] -> %s"
                % (tenant_name, label, elapsed_ms, detail[:140]))
        return None
    body = json.dumps(payload, indent=1, sort_keys=True)
    save_evidence("deleg-%s-t%s.json" % (tag, tenant_id), body)
    elapsed_ms = (time.monotonic() - started) * 1000.0
    records, count, extra = summarize_response(payload)
    fields = record_fields(records)
    FIELDS_SEEN.update(fields)
    verdict("deleg.%s" % tag, True,
            "tenant %s %s [%.0fms] -> %d record(s), count_total=%s, "
            "record fields=%s, wrapper extras=%s"
            % (tenant_name, label, elapsed_ms, len(records), count,
               sorted(fields) or "none", extra))
    for rec in records[:3]:
        log("    RECORD: %s" % json.dumps(rec, sort_keys=True)[:300])
    return payload


def build_parser():
    """The probe's CLI contract, factored so tests can hold the committed
    lab script to it. The second lab trip failed at argparse: a refactor
    dropped --evidence-dir from the parser while the committed script (and
    main() itself) still used it, and nothing in the gate ran the two
    against each other."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--vms", required=True)
    parser.add_argument("--port", type=int, default=443)
    parser.add_argument("--user", default="admin")
    parser.add_argument("--mountpoint", required=True,
                        help="client mountpoint of the NFSv4.1 filesystem")
    parser.add_argument("--export-path", required=True,
                        help="server-side export path of that mount "
                             "(from the mount table, e.g. /kmacs/nfstest)")
    parser.add_argument("--client-files", default="",
                        help="comma-separated CLIENT paths of real existing "
                             "files beneath the mountpoint")
    parser.add_argument("--dir-paths", default="",
                        help="comma-separated server-side directory paths "
                             "for the directory-semantics check")
    parser.add_argument("--evidence-dir", default=None,
                        help="directory for raw evidence files and the API "
                             "log; the lab workflow routes this beneath the "
                             "run's DTS tree so nothing lands in /tmp")
    return parser


def main():
    global EVIDENCE_DIR
    args = build_parser().parse_args()

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

        # ---- mount -> view -> tenant correlation (never tenant-list order:
        # pass 1 queried tenants 37/25/51 by API order and every path came
        # back GetHandleByPathCode.ILLEGAL_PATH - wrong tenants, not a
        # broken endpoint) ----
        views = get_only("GET", "/views/")
        views = (views.get("results", views)
                 if isinstance(views, dict) else views) or []
        client_files = [f for f in args.client_files.split(",") if f]
        server_files = []
        for cf in client_files:
            sp = server_path_for(cf, args.mountpoint, args.export_path)
            if sp:
                server_files.append((cf, sp))
            else:
                log("  SKIP: %s is not under %s" % (cf, args.mountpoint))
        probe_target = (server_files[0][1] if server_files
                        else args.export_path)
        cands = candidate_views(views, probe_target)
        save_evidence("view-candidates.json", json.dumps(
            [{"id": v.get("id"), "path": v.get("path"),
              "tenant_id": v.get("tenant_id"),
              "tenant_name": v.get("tenant_name"),
              "protocols": v.get("protocols"), "match": kind}
             for v, kind in cands[:10]], indent=1))
        tenants, ambiguous = candidate_tenants(cands)
        verdict("correlation.views", bool(cands),
                "%d candidate view(s) for %s; top: %s"
                % (len(cands), probe_target,
                   [(v.get("id"), v.get("path"), kind)
                    for v, kind in cands[:3]]))
        if ambiguous:
            verdict("correlation.tenant", False,
                    "more than %d distinct tenants could own %s - recording "
                    "the ambiguity and stopping rather than querying random "
                    "tenants" % (len(tenants), probe_target))
            return 1
        verdict("correlation.tenant", bool(tenants),
                "namespace tenant candidates (derived, ordered): %s"
                % [(tid, name, via) for tid, name, via in tenants])
        if not tenants:
            return 1

        mapping = "\n".join(
            "client %s -> server %s" % (cf, sp) for cf, sp in server_files)
        save_evidence("file-mapping.txt", mapping or "no client files")
        log("  file mapping:\n    %s"
            % (mapping.replace("\n", "\n    ") or "none"))

        # Availability contract on the DERIVED tenant only.
        tid0, name0, _via = tenants[0]
        probe_one("availability", tid0, name0, None)

        # ---- find the accepted (tenant, path-syntax) pair with the FIRST
        # real file; bounded: <=3 tenants x <=3 derived representations ----
        winner = None
        if server_files:
            first_cf, first_sp = server_files[0]
            view_path = cands[0][0].get("path") if cands else None
            reps = path_representations(first_sp, view_path, args.export_path)
            log("  path representations to try (derived, bounded): %s" % reps)
            attempt = 0
            for tid, name, _via in tenants:
                for rep in reps:
                    payload = probe_one("try%d" % attempt, tid, name, rep)
                    attempt += 1
                    if payload is not None:
                        winner = (tid, name, rep, first_sp)
                        break
                if winner:
                    break
        if winner:
            tid, name, rep, first_sp = winner
            syntax = ("full-namespace" if rep == first_sp else "relative")
            verdict("correlation.winner", True,
                    "tenant %s (%s) accepts %s syntax: %s"
                    % (tid, name, syntax, rep))
            # Remaining real files with the accepted syntax.
            for i, (cf, sp) in enumerate(server_files[1:6], start=1):
                use = sp if rep == first_sp else sp[len(first_sp) - len(rep):]
                probe_one("file%d" % i, tid, name, use)
            # Directory + nonexistent semantics, now that targeting is right.
            dir_paths = [d for d in args.dir_paths.split(",") if d][:1]
            for dp in dir_paths:
                use = dp if rep == first_sp else dp[
                    max(0, len(first_sp) - len(rep)):] or dp
                probe_one("dir", tid, name, use)
            missing = (first_sp if rep == first_sp else rep).rsplit("/", 1)[0]
            probe_one("missing", tid, name,
                      missing + "/does-not-exist-opstat-fr2")
        elif server_files:
            verdict("correlation.winner", False,
                    "no (tenant, syntax) pair produced an HTTP success for a "
                    "real existing file - every attempt recorded verbatim")
        else:
            verdict("correlation.winner", False,
                    "no real client files supplied; targeting cannot be "
                    "proven this run")

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
