#!/usr/bin/env python3
"""FR14 real-VMS validation: protocol-attribution correctness in production.

Drives the PRODUCTION engines in-process against a real cluster and checks the
implemented FR14 contract MECHANICALLY - nobody has to eyeball a screen and
judge whether a view "looks like" it belongs to another protocol.

The contamination test is derived from the cluster's own exposition rather
than from hardcoded lab paths: one unfiltered host_view scrape yields the set
of paths/tenants that carry NO traffic for the engine's protocol, and the
drill frame must contain none of them. That works on any cluster.

Checks, per engine (selected with OPSTAT_FR14_ENGINES):

  smb      VIEW/TENANT are host_view protocol=SMB2 only; no foreign path or
           tenant appears; the share column is populated from SMB2 rows; the
           header names host_view/SMB2; entry creates ZERO monitors.
  nfs_v3   VIEW and TENANT render the honest capability notices at ZERO API
           cost; cluster-level NFSv3 telemetry still populates.
  nfs_v41  VIEW/TENANT are host_view protocol=NFS4 only; native and hosts
           drills still render; the delegation prompt still opens.
  s3       BUCKET/TENANT render capability notices at ZERO API cost; VIP
           preserves a measured zero and never substitutes topn rows.

Safety contract: production GETs plus each engine's own headline monitors,
which its cleanup() deletes; every created monitor is verified deleted by
exact id. The FR14 drills create no monitors at all. Nothing else is touched.

Python 3.8+, stdlib only.
"""

import contextlib
import io
import os
import re
import sys
import time
from types import SimpleNamespace

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import nfs4_native                                          # noqa: E402
import vast_api_log                                         # noqa: E402
import vast_common                                          # noqa: E402

SUMMARY = []
_ANSI = re.compile(r"\033\[[0-9;?]*[A-Za-z]")

# A contamination check over an EMPTY drill is vacuous: "no foreign object
# appears" is trivially true when nothing appears at all. So an engine whose
# validation depends on live traffic must prove the cluster attributed some
# to that protocol during the window, or the run is not evidence. Set
# OPSTAT_FR14_REQUIRE_TRAFFIC=0 only for a deliberate idle-behaviour run.
REQUIRE_TRAFFIC = os.environ.get("OPSTAT_FR14_REQUIRE_TRAFFIC", "1") != "0"


def log(msg):
    print(msg, flush=True)


def verdict(name, ok, detail=""):
    log("CHECK:%s %s %s" % (name, "PASS" if ok else "FAIL", detail))
    SUMMARY.append((name, bool(ok), detail))


def plain(text):
    return _ANSI.sub("", text or "")


def frame_of(module):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        module._render_frame()
    return plain(buf.getvalue())


_LOG_PATHS = []


def api_lines():
    """Lines of the ACTIVE engine's API log.

    The path is latched on first sight because ``cleanup()`` tears the logger
    down - reading ``log_path()`` afterwards returns None, which silently
    turned the post-cleanup monitor checks into no-ops.
    """
    path = vast_api_log.log_path()
    if path and (not _LOG_PATHS or _LOG_PATHS[-1] != path):
        _LOG_PATHS.append(path)
    if not _LOG_PATHS:
        return []
    active = _LOG_PATHS[-1]
    if not os.path.exists(active):
        return []
    with open(active) as fh:
        return fh.readlines()


def call_count():
    return sum(1 for l in api_lines()
               if re.search(r"\b(GET|POST|DELETE|PUT|PATCH)\s+https?://", l))


def created_monitor_ids():
    ids = []
    for line in api_lines():
        if re.search(r"POST\s+\S*/monitors/\s", line):
            m = re.search(r'body=\{"id":\s*(\d+)', line)
            if m:
                ids.append(int(m.group(1)))
    return ids


def deleted_monitor_ids():
    """Ids the process actually issued a DELETE for, from its own API log."""
    out = []
    for line in api_lines():
        m = re.search(r"DELETE\s+\S*/monitors/(\d+)/", line)
        if m:
            out.append(int(m.group(1)))
    return out


def args_for(vms, port, **extra):
    base = dict(
        vms=vms, port=port, user=os.environ.get("OPSTAT_USER", "admin"),
        password=None, sample_average=None, refresh=5, csv=None,
        no_color=True, discover_metrics=False, log_api_calls=True,
        export_openmetrics=False, openmetrics_file=None,
        clients=None, buckets=None, tenants=None, volumes=None, volume=None,
        version=None,
    )
    base.update(extra)
    return SimpleNamespace(**base)


def foreign_sets(protocol):
    """Paths and tenants the cluster attributes to OTHER protocols only.

    One unfiltered scrape; anything carrying a *protocol* series is excluded.
    The result is exactly what must never appear in that engine's drill.
    """
    body = vast_common.request_text("GET", nfs4_native.HOST_VIEW_ENDPOINT)
    mine_paths, mine_tenants = set(), set()
    all_paths, all_tenants = set(), set()
    for line in (body or "").splitlines():
        if not line or line[0] == "#" or "vast_host_view_" not in line:
            continue
        labels = dict(re.findall(r'(\w+)="([^"]*)"', line))
        path, tenant = labels.get("path"), labels.get("tenant")
        if not path:
            continue
        all_paths.add(path)
        all_tenants.add(tenant)
        if labels.get("protocol") == protocol:
            mine_paths.add(path)
            mine_tenants.add(tenant)
    return (all_paths - mine_paths, all_tenants - mine_tenants,
            mine_paths, mine_tenants)


def attributed_iops(protocol):
    """Total IOPS the cluster attributes to *protocol* right now."""
    rows = nfs4_native.parse_host_view(
        vast_common.request_text("GET", nfs4_native.HOST_VIEW_ENDPOINT),
        protocol)
    return sum(r.get("iops") or 0.0 for r in rows)


def require_live_traffic(tag, protocol):
    """Hard gate: without attributed traffic the contamination checks below
    cannot distinguish a correct drill from an empty one."""
    total = attributed_iops(protocol)
    ok = total > 0 or not REQUIRE_TRAFFIC
    verdict("%s.live_traffic" % tag, ok,
            "%s attributed %.2f IOPS during the window%s"
            % (protocol, total,
               "" if total > 0 else
               " - the contamination checks would be VACUOUS; drive load "
               "through this cluster, or set "
               "OPSTAT_FR14_REQUIRE_TRAFFIC=0 to accept an idle run"))
    return total > 0


def assert_no_foreign(tag, frame, foreign, mine, kind):
    """No other protocol's object may appear; at least one of ours should."""
    leaked = sorted(f for f in foreign if f and f in frame)
    verdict("%s.no_foreign_%s" % (tag, kind), not leaked,
            "leaked=%s" % (leaked[:5],) if leaked
            else "checked %d foreign %s from the live exposition"
                 % (len(foreign), kind))
    shown = sorted(m for m in mine if m and m in frame)
    # `or not mine` is an escape hatch ONLY for an explicitly-accepted idle
    # run. With traffic required, an empty drill is a failure, not a pass:
    # otherwise a run with no traffic satisfies every assertion above.
    lenient = (not mine) and not REQUIRE_TRAFFIC
    verdict("%s.own_%s_present" % (tag, kind), bool(shown) or lenient,
            "shown=%s" % (shown[:5],) if shown
            else "NO %s carried this protocol's traffic - the 'no foreign %s' "
                 "check above is vacuous" % (kind, kind))


# ---------------------------------------------------------------------------
# Engines
# ---------------------------------------------------------------------------
def validate_smb(vms, port):
    import smb

    smb.init_config(args_for(vms, port))
    smb.CLUSTER_ID, smb.CLUSTER_NAME = smb.get_current_cluster()
    log("  cluster %s" % smb.CLUSTER_NAME)
    smb.create_headline_monitors()
    smb.fetch_monitor_query()
    verdict("smb.cluster_telemetry", bool(smb.LAST_ROWS),
            "headline rows populated")

    require_live_traffic("smb", "SMB2")
    foreign_p, foreign_t, mine_p, mine_t = foreign_sets("SMB2")
    log("  live exposition: %d SMB2 paths, %d foreign paths"
        % (len(mine_p), len(foreign_p)))

    before_ids = set(created_monitor_ids())
    smb.enter_hostview_mode("view")
    frame = frame_of(smb)
    new_ids = set(created_monitor_ids()) - before_ids
    verdict("smb.view_creates_no_monitors", not new_ids, "new=%s" % (new_ids or "none"))
    verdict("smb.view_provenance", "source host_view/SMB2" in frame,
            "header names the rendered source")
    verdict("smb.view_not_smbcommon", "source SMBCommon" not in frame)
    assert_no_foreign("smb.view", frame, foreign_p, mine_p, "paths")
    shares = sorted({r["share"] for r in smb.HOSTVIEW.rows if r.get("share")})
    verdict("smb.view_share_column", bool(shares) or not mine_p,
            "shares seen: %s" % (shares[:5] or "none (no SMB2 traffic)"))

    smb.enter_hostview_mode("tenant")
    tframe = frame_of(smb)
    assert_no_foreign("smb.tenant", tframe, foreign_t, mine_t, "tenants")
    verdict("smb.tenant_provenance", "source host_view/SMB2" in tframe)

    smb.exit_hostview_mode()
    back = frame_of(smb)
    verdict("smb.exit_restores_dashboard", smb.HEALTH_PANEL_TITLE in back)
    return smb


def validate_nfs_v3(vms, port):
    import nfs_v3

    nfs_v3.init_config(args_for(vms, port))
    nfs_v3.CLUSTER_ID, nfs_v3.CLUSTER_NAME = nfs_v3.get_current_cluster()
    log("  cluster %s" % nfs_v3.CLUSTER_NAME)
    nfs_v3.create_headline_monitors()
    nfs_v3.fetch_monitor_query()
    verdict("nfs_v3.cluster_telemetry", bool(nfs_v3.LAST_ROWS),
            "cluster-level NFSv3 telemetry still available")
    # "Cluster-level NFSv3 telemetry remains available" is the promise the
    # capability notice makes. Rows full of zeros do not demonstrate it, so
    # require the cluster to be reporting real NFSv3 work. host_view cannot
    # supply this proof - not attributing NFS3 is the FR14 finding - so the
    # headline path is the evidence.
    ops = sum(float(r.get("ops_sec") or 0.0) for r in (nfs_v3.LAST_ROWS or []))
    verdict("nfs_v3.live_traffic", ops > 0 or not REQUIRE_TRAFFIC,
            "cluster NFSv3 headline total %.2f ops/s%s" % (ops,
            "" if ops > 0 else " - no NFSv3 workload reached THIS cluster; "
            "the 'cluster telemetry still available' claim is unproven"))

    for mode, marker in (("view", nfs_v3.VIEW_UNAVAILABLE_MARKER),
                         ("tenant", nfs_v3.TENANT_UNAVAILABLE_MARKER)):
        before = call_count()
        nfs_v3.enter_drill_mode(mode)
        cost = call_count() - before
        frame = frame_of(nfs_v3)
        verdict("nfs_v3.%s_notice" % mode, marker in frame, marker)
        verdict("nfs_v3.%s_zero_cost" % mode, cost == 0, "%d API calls" % cost)
        verdict("nfs_v3.%s_footer" % mode, "[x] Exit drill" in frame)
        nfs_v3.exit_drill_mode()
    return nfs_v3


def validate_nfs_v41(vms, port):
    import nfs_v41

    nfs_v41.init_config(args_for(vms, port))
    nfs_v41.CLUSTER_ID, nfs_v41.CLUSTER_NAME = nfs_v41.get_current_cluster()
    log("  cluster %s" % nfs_v41.CLUSTER_NAME)
    nfs_v41.create_headline_monitors()
    nfs_v41.fetch_monitor_query()
    verdict("nfs_v41.cluster_telemetry", bool(nfs_v41.LAST_ROWS))

    require_live_traffic("nfs_v41", "NFS4")
    foreign_p, foreign_t, mine_p, mine_t = foreign_sets("NFS4")
    log("  live exposition: %d NFS4 paths, %d foreign paths"
        % (len(mine_p), len(foreign_p)))

    before_ids = set(created_monitor_ids())
    nfs_v41.enter_exporter_mode("view")
    vframe = frame_of(nfs_v41)
    verdict("nfs_v41.view_source", "protocol=NFS4" in vframe,
            "panel names its source and filter")
    assert_no_foreign("nfs_v41.view", vframe, foreign_p, mine_p, "paths")

    nfs_v41.enter_exporter_mode("tenant")
    tframe = frame_of(nfs_v41)
    verdict("nfs_v41.tenant_source", "protocol=NFS4" in tframe)
    assert_no_foreign("nfs_v41.tenant", tframe, foreign_t, mine_t, "tenants")

    new_ids = set(created_monitor_ids()) - before_ids
    verdict("nfs_v41.exporter_creates_no_monitors", not new_ids,
            "new=%s" % (new_ids or "none"))

    # Not regressed: the other exporter drills still render, and [d] opens.
    for mode, needle in (("hosts", "NFSv4 HOSTS"), ("native", "NFSv4")):
        nfs_v41.enter_exporter_mode(mode)
        verdict("nfs_v41.%s_renders" % mode, needle in frame_of(nfs_v41))
    nfs_v41.exit_exporter_mode()
    nfs_v41._dispatch_key("d")
    verdict("nfs_v41.delegation_prompt_opens", nfs_v41.DELEG_PROMPT is not None)
    nfs_v41.DELEG_PROMPT = None
    return nfs_v41


def validate_s3(vms, port):
    import s3

    s3.init_config(args_for(vms, port))
    s3.CLUSTER_ID, s3.CLUSTER_NAME = s3.get_current_cluster()
    log("  cluster %s" % s3.CLUSTER_NAME)
    s3.create_headline_monitors()
    s3.fetch_monitor_query()
    verdict("s3.cluster_telemetry", bool(s3.LAST_ROWS))

    for mode, marker in (("bucket", s3.BUCKET_UNAVAILABLE_MARKER),
                         ("tenant", s3.TENANT_UNAVAILABLE_MARKER)):
        before = call_count()
        s3.enter_drill_mode(mode)
        cost = call_count() - before
        frame = frame_of(s3)
        verdict("s3.%s_notice" % mode, marker in frame, marker)
        verdict("s3.%s_zero_cost" % mode, cost == 0, "%d API calls" % cost)
        verdict("s3.%s_no_rest_verbs" % mode, "GET/s" not in frame,
                "no REST-verb table without a protocol-scoped source")
        s3.exit_drill_mode()

    s3.enter_drill_mode("vip")
    if s3.DRILL_MODE == "vip":
        s3.fetch_drill_query(force=True)
        before = call_count()
        s3.fetch_drill_query(force=True)
        topn_after = [l for l in api_lines()[-(call_count() - before or 1):]
                      if "monitors/topn" in l]
        zero_rows = [r for r in s3.LAST_DRILL_ROWS
                     if (r.get("total_ops") or 0) == 0]
        verdict("s3.vip_rows_present", bool(s3.LAST_DRILL_ROWS),
                "%d rows (%d measured zero)"
                % (len(s3.LAST_DRILL_ROWS), len(zero_rows)))
        verdict("s3.vip_no_topn_substitution", not topn_after,
                "topn must never replace a measured protocol-scoped result")
    else:
        verdict("s3.vip_notice",
                s3.VIP_UNAVAILABLE_MARKER in (s3.DRILL_ERROR or ""),
                "this build refuses vip-scope monitors: honest notice, not topn")
    s3.exit_drill_mode()
    return s3


ENGINES = {"smb": validate_smb, "nfs_v3": validate_nfs_v3,
           "nfs_v41": validate_nfs_v41, "s3": validate_s3}


def main():
    vms = os.environ.get("OPSTAT_VMS")
    if not vms:
        print("ERROR: OPSTAT_VMS is required.", file=sys.stderr)
        return 2
    if not (os.environ.get("VAST_TOKEN") or os.environ.get("VAST_PASSWORD")):
        print("ERROR: set VAST_TOKEN or VAST_PASSWORD in the environment.",
              file=sys.stderr)
        return 2
    port = int(os.environ.get("OPSTAT_PORT", "443"))
    wanted = [e.strip() for e in
              os.environ.get("OPSTAT_FR14_ENGINES", "").split(",") if e.strip()]
    if not wanted:
        print("ERROR: set OPSTAT_FR14_ENGINES (comma-separated: %s)"
              % ",".join(sorted(ENGINES)), file=sys.stderr)
        return 2
    unknown = [e for e in wanted if e not in ENGINES]
    if unknown:
        print("ERROR: unknown engine(s) %s" % unknown, file=sys.stderr)
        return 2

    log("FR14 attribution validation - %s"
        % time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    log("target %s:%d; engines: %s" % (vms, port, ", ".join(wanted)))

    rc = 0
    for name in wanted:
        log("")
        log("== %s ==" % name)
        module = None
        try:
            module = ENGINES[name](vms, port)
        except Exception as exc:                      # noqa: BLE001
            verdict("%s.ran" % name, False, "raised %s: %s"
                    % (type(exc).__name__, exc))
            rc = 1
        finally:
            if module is not None:
                created = set(created_monitor_ids())
                try:
                    module.cleanup()
                except Exception:                     # noqa: BLE001
                    pass
                module._CLEANED_UP = False
                # Every monitor this engine created must be gone. Checked
                # two independent ways, because either alone can be vacuous:
                #  1. the process must have ISSUED a DELETE for each id (read
                #     from its own API log - works on any cluster or mock);
                #  2. a live GET of the id must 404 (authoritative on a real
                #     VMS; some mocks do not route it, hence check 1).
                deleted = set(deleted_monitor_ids())
                undeleted = sorted(created - deleted)
                verdict("%s.monitor_delete_issued" % name, not undeleted,
                        "created=%d no DELETE issued for %s"
                        % (len(created), undeleted or "none"))
                leaked, unverifiable = [], []
                for mid in sorted(created):
                    try:
                        vast_common.request("GET", "/monitors/%d/" % mid)
                        leaked.append(mid)            # still alive
                    except RuntimeError as exc:
                        if "404" not in str(exc):
                            unverifiable.append("%d:%s" % (mid, str(exc)[:40]))
                verdict("%s.monitor_cleanup" % name, not leaked,
                        "created=%d leaked=%s%s"
                        % (len(created), leaked or "none",
                           "; NOT VERIFIABLE (non-404 errors): %s" % unverifiable
                           if unverifiable else ""))
                vast_common.close_connection()

    log("")
    failed = [n for n, ok, _ in SUMMARY if not ok]
    log("=" * 62)
    log("%d checks, %d failed" % (len(SUMMARY), len(failed)))
    for name in failed:
        log("  FAILED: %s" % name)
    log("=" * 62)
    return 1 if (failed or rc) else 0


if __name__ == "__main__":
    sys.exit(main())
