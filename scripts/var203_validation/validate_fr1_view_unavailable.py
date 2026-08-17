#!/usr/bin/env python3
"""FR1 real-VMS validation: the NFSv3 VIEW drill's honest unavailable state.

Drives the PRODUCTION nfs_v3 engine in-process against the real cluster -
init_config, cluster resolution, headline monitors, a real headline fetch,
then the actual enter/exit drill path and the actual render function. This is
the strongest committed non-PTY mechanism: the exercised code is production
code end-to-end; only terminal keystroke dispatch is not driven (it is covered
by the engine's unit tests). The report states that boundary explicitly.

Validates on the cluster (D-016):
  * the NFSv3 dashboard is healthy with live telemetry (loadgen active)
  * entering the VIEW drill renders the capability notice, not an error
  * no unrelated per-view rows are presented as NFSv3 data
  * VIEW entry issues ZERO API calls and creates ZERO monitors
  * x-equivalent exit returns to the dashboard
  * the engine's own cleanup drains every monitor, verified per exact id

Safety contract: production GETs plus the engine's own headline monitors,
which its cleanup() deletes; per-id 404 verification; nothing else touched.

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

import nfs_v3                                               # noqa: E402
import vast_api_log                                         # noqa: E402
import vast_common                                          # noqa: E402

SUMMARY = []


def log(msg):
    print(msg, flush=True)


def verdict(name, ok, detail=""):
    line = "CHECK:%s %s %s" % (name, "PASS" if ok else "FAIL", detail)
    log(line)
    SUMMARY.append((name, ok, detail))


def render_frame():
    """One frame from the production renderer, captured off-terminal."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        nfs_v3.render_screen()
    return buf.getvalue()


def api_lines():
    path = vast_api_log.log_path()
    if not path:
        return []
    with open(path) as fh:
        return fh.readlines()


def call_count(lines):
    return sum(1 for l in lines
               if re.search(r"\b(GET|POST|DELETE|PUT|PATCH)\s+https?://", l))


def created_ids(lines):
    ids = []
    for line in lines:
        if re.search(r"POST\s+\S*/monitors/\s", line):
            m = re.search(r'body=\{"id":\s*(\d+)', line)
            if m:
                ids.append(int(m.group(1)))
    return ids


def main():
    if not (os.environ.get("VAST_TOKEN") or os.environ.get("VAST_PASSWORD")):
        print("ERROR: set VAST_TOKEN or VAST_PASSWORD in the environment.",
              file=sys.stderr)
        return 2
    vms = os.environ.get("OPSTAT_VMS", "var203.selab.vastdata.com")
    log("FR1 unavailable-state validation - %s"
        % time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    log("target %s; production nfs_v3 engine in-process" % vms)

    port = int(os.environ.get("OPSTAT_PORT", "443"))
    nfs_v3.init_config(SimpleNamespace(
        vms=vms, port=port, user=os.environ.get("OPSTAT_USER", "admin"),
        password=None, sample_average=None, refresh=5, csv=None,
        no_color=True, discover_metrics=False, log_api_calls=True,
        export_openmetrics=False, openmetrics_file=None,
    ))
    log("api log: %s" % vast_api_log.log_path())

    rc = 0
    try:
        nfs_v3.CLUSTER_ID, nfs_v3.CLUSTER_NAME = nfs_v3.get_current_cluster()
        log("cluster: %s (id %s)" % (nfs_v3.CLUSTER_NAME, nfs_v3.CLUSTER_ID))
        nfs_v3.create_headline_monitors()
        nfs_v3.fetch_monitor_query()
        rows = nfs_v3.LAST_ROWS or []
        busy = [r for r in rows if (r.get("ops_sec") or 0) > 0]
        verdict("nfs3.dashboard.healthy", bool(rows),
                "%d headline rows, %d with live traffic" % (len(rows), len(busy)))
        verdict("nfs3.workload.visible", bool(busy),
                "live NFSv3 operations visible in cluster telemetry"
                if busy else "no live rows - is nfs3-loadgen running?")
        dash = render_frame()
        verdict("nfs3.dashboard.footer", "[q] Quit" in dash,
                "footer present on the dashboard")

        # --- VIEW drill: honest unavailable state, at zero API cost --------
        mark = len(api_lines())
        nfs_v3.switch_drill_mode("view")
        entry_lines = api_lines()[mark:]
        entry_calls = call_count(entry_lines)
        entry_creates = created_ids(entry_lines)
        verdict("view.entry.zero_api_calls", entry_calls == 0,
                "%d API calls during VIEW entry (expected 0)" % entry_calls)
        verdict("view.entry.zero_monitors", not entry_creates,
                "monitors created during VIEW entry: %s"
                % (entry_creates or "none"))
        verdict("view.state.unavailable",
                nfs_v3.DRILL_MODE is None and nfs_v3.DRILL_ERROR is not None
                and nfs_v3.VIEW_UNAVAILABLE_MARKER in (nfs_v3.DRILL_ERROR or ""),
                "drill closed with the capability notice set")
        frame = render_frame()
        verdict("view.frame.notice", nfs_v3.VIEW_UNAVAILABLE_NOTICE in frame,
                "notice line rendered")
        verdict("view.frame.detail", nfs_v3.VIEW_UNAVAILABLE_DETAIL in frame,
                "cluster-level-telemetry line rendered")
        verdict("view.frame.not_an_error", "Error:" not in frame,
                "notice is not presented as an error")
        verdict("view.frame.footer", "[q] Quit" in frame,
                "footer present in the unavailable state")
        stray = [p for p in ("/kmacs/smb/opstat", "/kmacs/block",
                             "/csnow-db-203", "/sli/") if p in frame]
        verdict("view.frame.no_foreign_rows", not stray,
                "no other-protocol view rows presented as NFSv3%s"
                % ("" if not stray else ": %s leaked" % stray))
        save_to = os.environ.get("OPSTAT_FRAME_OUT")
        if save_to:
            with open(save_to, "w") as fh:
                fh.write(dash + "\n\n===== VIEW DRILL FRAME =====\n\n" + frame)
            log("frames saved: %s" % save_to)

        # --- exit returns to the dashboard ---------------------------------
        nfs_v3.exit_drill_mode()
        back = render_frame()
        verdict("view.exit.returns", nfs_v3.DRILL_ERROR is None
                and "[q] Quit" in back and nfs_v3.VIEW_UNAVAILABLE_NOTICE not in back,
                "exit cleared the notice and re-rendered the dashboard")
    except Exception as exc:                    # noqa: BLE001 - report, then clean up
        verdict("run.exception", False, repr(exc))
        rc = 1
    finally:
        all_created = created_ids(api_lines())
        nfs_v3.cleanup()
        leaked = []
        for mid in all_created:
            try:
                vast_common.request("GET", "/monitors/%d/" % mid)
                leaked.append(mid)
            except RuntimeError as exc:
                if "404" not in str(exc):
                    leaked.append(mid)
        verdict("cleanup.exact_ids", not leaked,
                "all %d session monitors confirmed gone by per-id GET"
                % len(all_created) if not leaked
                else "still present or unverifiable: %s" % leaked)
        vast_common.close_connection()

    log("\n=== RESULT SUMMARY ===")
    failed = [n for n, ok, _d in SUMMARY if not ok]
    for name, ok, detail in SUMMARY:
        log("CHECK:%-28s %-5s %s" % (name, "PASS" if ok else "FAIL", detail))
    log("RESULT: %s" % ("PASS" if not failed else "FAIL (%s)" % ", ".join(failed)))
    log("NOTE: keystroke dispatch is unit-test covered; this run exercises the "
        "production enter/exit/render path in-process, not a terminal.")
    return rc if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
