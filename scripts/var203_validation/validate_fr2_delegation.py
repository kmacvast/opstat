#!/usr/bin/env python3
"""FR2 real-VMS validation: the NFSv4.1 delegation diagnostic, end to end.

Drives the PRODUCTION nfs_v41 engine in-process against the real cluster -
init_config, cluster resolution, headline monitors, then the actual prompt
dispatch path (_dispatch_key, character by character, exactly as the terminal
would deliver keys), the actual tenant resolution, the actual GET-only lookup
and the actual render function. Only raw terminal keystroke *capture* is not
driven (covered by the engine's unit tests); every key still flows through
the production dispatch code.

Validates on the cluster (D-008, owner-approved FR2 product decisions):
  * the NFSv4.1 dashboard is healthy with live telemetry (nfs41-loadgen)
  * [d] opens the path prompt; the footer survives it
  * typing a path containing "q" does not quit (prompt-aware quit guard)
  * a REAL workload file (derived, never hard-coded) returns live delegation
    records carrying the six proven fields; IDs render as the dim line
  * a valid path with no delegation renders the honest empty state
  * a nonexistent path under a real view renders invalid via ILLEGAL_PATH
  * lookup cost is bounded (<= one /views/ + two delegation GETs); the view
    inventory is cached across lookups; space re-queries exactly once
  * the normal refresh path performs ZERO delegation API calls
  * the API log carries no non-GET request to nfs4_delegs (hard gate)
  * x exits; engine cleanup drains every monitor, verified per exact id

Safety contract: production GETs plus the engine's own headline monitors,
which its cleanup() deletes; per-id 404 verification; nothing else touched.
The nfs4_delegs DELETE sibling is never invoked (test-enforced in the repo,
log-verified here).

Python 3.8+, stdlib only.
"""

import argparse
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

import nfs_v41                                              # noqa: E402
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
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        nfs_v41.render_screen()
    return buf.getvalue()


def api_lines():
    path = vast_api_log.log_path()
    if not path:
        return []
    with open(path) as fh:
        return fh.readlines()


def deleg_calls(lines):
    return [l for l in lines if "nfs4_delegs" in l]


def non_get_deleg_calls(lines):
    return [l for l in deleg_calls(lines)
            if not re.search(r"\bGET\s+https?://", l)]


def views_calls(lines):
    return [l for l in lines if re.search(r"GET\s+\S*/views/\s", l)]


def created_ids(lines):
    ids = []
    for line in lines:
        if re.search(r"POST\s+\S*/monitors/\s", line):
            m = re.search(r'body=\{"id":\s*(\d+)', line)
            if m:
                ids.append(int(m.group(1)))
    return ids


def server_path_for(client_path, mountpoint, export_path):
    """Translate a client mount path to the full VAST namespace path."""
    rel = os.path.relpath(client_path, mountpoint)
    if rel == ".":
        return export_path
    return export_path.rstrip("/") + "/" + rel


def vips_contain_ip(payload, ip):
    """Whole-payload walk: does the cluster's own VIP inventory carry *ip*?"""
    if isinstance(payload, dict):
        return any(vips_contain_ip(v, ip) for v in payload.values())
    if isinstance(payload, list):
        return any(vips_contain_ip(v, ip) for v in payload)
    return isinstance(payload, str) and ip in payload


def submit_path(path):
    """Drive the REAL prompt flow: open, type each character, Enter."""
    nfs_v41._dispatch_key("d")
    for ch in path:
        nfs_v41._dispatch_key(ch)
    nfs_v41._dispatch_key("\r")
    return nfs_v41.DELEG_RESULT


def build_parser():
    """CLI contract, factored so the lab script can be held to it in tests."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--vms", default="var204.selab.vastdata.com")
    parser.add_argument("--port", type=int, default=443)
    parser.add_argument("--user", default="admin")
    parser.add_argument("--mountpoint", required=True,
                        help="client-side NFSv4.1 mountpoint")
    parser.add_argument("--export-path", required=True,
                        help="full VAST namespace path the mount exports")
    parser.add_argument("--mount-server", required=True,
                        help="NFS server IP of the mount; must belong to the "
                             "target VMS (cross-cluster runs are invalid)")
    parser.add_argument("--client-files", required=True,
                        help="newline- or comma-separated REAL client file "
                             "paths (from find_nfs41_candidates.py)")
    parser.add_argument("--frame-out", default=None,
                        help="write captured production frames here")
    return parser


def main():
    args = build_parser().parse_args()
    if not (os.environ.get("VAST_TOKEN") or os.environ.get("VAST_PASSWORD")):
        print("ERROR: set VAST_TOKEN or VAST_PASSWORD in the environment.",
              file=sys.stderr)
        return 2

    client_files = [p.strip() for chunk in args.client_files.split("\n")
                    for p in chunk.split(",") if p.strip()]
    if not client_files:
        print("ERROR: --client-files resolved to an empty list.",
              file=sys.stderr)
        return 2

    log("FR2 delegation-diagnostic validation - %s"
        % time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    log("target %s; production nfs_v41 engine in-process" % args.vms)
    log("candidate client files: %d" % len(client_files))

    nfs_v41.init_config(SimpleNamespace(
        vms=args.vms, port=args.port, user=args.user,
        password=None, sample_average=None, refresh=5, csv=None,
        no_color=True, discover_metrics=False, log_api_calls=True,
        export_openmetrics=False, openmetrics_file=None,
    ))
    log("api log: %s" % vast_api_log.log_path())

    rc = 0
    frames = []
    try:
        # --- preflight: the mount must belong to THIS cluster --------------
        vips = vast_common.request("GET", "/vips/")
        owns = vips_contain_ip(vips, args.mount_server)
        verdict("preflight.mount_vms_consistent", owns,
                "mount server %s %s in %s VIP inventory"
                % (args.mount_server,
                   "found" if owns else "NOT FOUND", args.vms))
        if not owns:
            raise RuntimeError(
                "mount belongs to a different cluster; run is invalid")

        nfs_v41.CLUSTER_ID, nfs_v41.CLUSTER_NAME = nfs_v41.get_current_cluster()
        log("cluster: %s (id %s)" % (nfs_v41.CLUSTER_NAME, nfs_v41.CLUSTER_ID))
        nfs_v41.create_headline_monitors()
        nfs_v41.poll_tick()
        dash = render_frame()
        frames.append(("dashboard", dash))
        verdict("nfs41.dashboard.footer", "[q] Quit" in dash,
                "footer present on the dashboard")
        verdict("nfs41.footer.advertises_d", "[d] Delegation" in dash,
                "the [d] Delegation control is discoverable")

        # --- prompt behavior through the production dispatch path ----------
        nfs_v41._dispatch_key("d")
        prompt_frame = render_frame()
        frames.append(("prompt", prompt_frame))
        verdict("prompt.opens", nfs_v41.DELEG_PROMPT == "",
                "[d] opened the path prompt")
        verdict("prompt.frame.footer", "[q] Quit" in prompt_frame,
                "footer survives the prompt")
        for ch in "/q":
            nfs_v41._dispatch_key(ch)
        verdict("prompt.q_is_text", nfs_v41.DELEG_PROMPT == "/q"
                and not nfs_v41._should_quit("q"),
                "typing q inside the prompt neither quits nor navigates")
        nfs_v41._dispatch_key("\x7f")
        nfs_v41._dispatch_key("\x7f")
        nfs_v41._dispatch_key("\x7f")
        verdict("prompt.cancel", nfs_v41.DELEG_PROMPT is None,
                "backspace-on-empty cancelled the prompt")

        # --- live lookup on REAL workload files ----------------------------
        mark = len(api_lines())
        live_result, live_path, attempts = None, None, 0
        for client_path in client_files:
            server_path = server_path_for(
                client_path, args.mountpoint, args.export_path)
            log("lookup: %s -> %s" % (client_path, server_path))
            attempts += 1
            result = submit_path(server_path)
            if result and result["state"] == "live":
                live_result, live_path = result, server_path
                break
        lookup_lines = api_lines()[mark:]
        verdict("deleg.live.records", live_result is not None,
                "live delegation records for %s" % live_path if live_result
                else "no candidate file returned a live delegation - is "
                     "nfs41-loadgen holding files open?")
        if live_result:
            rec = live_result["records"][0]
            missing = [k for k in (
                "delegation_type", "delegation_client_ip", "vip_addr",
                "revoke_in_progress", "client_id", "delegation_stateid")
                if rec.get(k) in (None, "-")]
            verdict("deleg.live.six_fields", not missing,
                    "all proven fields populated" if not missing
                    else "missing on the live record: %s" % missing)
            verdict("deleg.live.tenant_recorded",
                    bool(live_result.get("tenant")),
                    "answered by tenant %r" % live_result.get("tenant"))
            live_frame = render_frame()
            frames.append(("live", live_frame))
            verdict("deleg.live.frame",
                    "Delegation" in live_frame
                    and str(rec["delegation_client_ip"]) in live_frame
                    and "client_id" in live_frame,
                    "primary fields plus the dim id line rendered")
            verdict("deleg.live.frame.footer", "[q] Quit" in live_frame,
                    "footer survives the live result")

        # --- lookup cost bounds --------------------------------------------
        lookup_delegs = deleg_calls(lookup_lines)
        verdict("cost.lookup_bounded",
                len(views_calls(lookup_lines)) <= 1
                and len(lookup_delegs) <= 2 * attempts,
                "%d /views/ fetch(es), %d delegation GET(s) across %d "
                "lookup(s) (budget: 1 views + 2 GETs per lookup)"
                % (len(views_calls(lookup_lines)), len(lookup_delegs),
                   attempts))

        # --- empty state: the export root is valid and idle ----------------
        empty_result = submit_path(args.export_path)
        empty_frame = render_frame()
        frames.append(("empty-or-live-root", empty_frame))
        verdict("deleg.valid_root.answers",
                empty_result is not None
                and empty_result["state"] in ("live", "empty"),
                "state=%r for the export root (valid path)"
                % (empty_result or {}).get("state"))
        if empty_result and empty_result["state"] == "empty":
            verdict("deleg.empty.honest",
                    "No active NFSv4.1 delegation" in empty_frame,
                    "empty is information, not an error")

        # --- invalid path: ILLEGAL_PATH through the production path --------
        bogus = args.export_path.rstrip("/") + "/opstat-fr2-no-such-file.dat"
        invalid_result = submit_path(bogus)
        invalid_frame = render_frame()
        frames.append(("invalid", invalid_frame))
        verdict("deleg.invalid.state",
                invalid_result is not None
                and invalid_result["state"] == "invalid",
                "state=%r for a nonexistent path"
                % (invalid_result or {}).get("state"))
        verdict("deleg.invalid.frame.footer", "[q] Quit" in invalid_frame,
                "footer survives the invalid state")

        # --- views cache + space re-query ----------------------------------
        mark = len(api_lines())
        submit_path(args.export_path)
        cached_lines = api_lines()[mark:]
        verdict("cost.views_cached", len(views_calls(cached_lines)) == 0,
                "repeat lookup fetched /views/ %d time(s) (expected 0)"
                % len(views_calls(cached_lines)))
        mark = len(api_lines())
        nfs_v41._dispatch_key(" ")
        requery_lines = api_lines()[mark:]
        verdict("cost.space_requeries_once",
                len(deleg_calls(requery_lines)) == 1
                and len(requery_lines) == len(deleg_calls(requery_lines)),
                "space issued %d delegation GET(s), %d other call(s)"
                % (len(deleg_calls(requery_lines)),
                   len(requery_lines) - len(deleg_calls(requery_lines))))

        # --- refresh path stays delegation-free -----------------------------
        nfs_v41._dispatch_key("x")
        verdict("deleg.x_exits", nfs_v41.DELEG_RESULT is None,
                "x dismissed the result")
        mark = len(api_lines())
        for _ in range(3):
            nfs_v41.poll_tick()
        refresh_lines = api_lines()[mark:]
        verdict("cost.refresh_zero_deleg",
                not deleg_calls(refresh_lines),
                "%d delegation call(s) across 3 poll ticks (expected 0)"
                % len(deleg_calls(refresh_lines)))
        back = render_frame()
        frames.append(("dashboard-after", back))
        verdict("deleg.exit.returns", "[q] Quit" in back
                and "DELEGATION LOOKUP" not in back,
                "dashboard restored after exit")
    except Exception as exc:                    # noqa: BLE001 - report, then clean up
        verdict("run.exception", False, repr(exc))
        rc = 1
    finally:
        # --- hard safety gate: the log must show GET-only delegation use ---
        all_lines = api_lines()
        bad = non_get_deleg_calls(all_lines)
        verdict("safety.get_only", not bad,
                "every nfs4_delegs call in the API log is a GET (%d total)"
                % len(deleg_calls(all_lines)) if not bad
                else "NON-GET DELEGATION CALLS: %s" % bad[:3])
        all_created = created_ids(all_lines)
        nfs_v41.cleanup()
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
        if frames and args.frame_out:
            with open(args.frame_out, "w") as fh:
                for name, frame in frames:
                    fh.write("===== %s =====\n%s\n\n" % (name, frame))
            log("frames saved: %s" % args.frame_out)

    log("\n=== RESULT SUMMARY ===")
    failed = [n for n, ok, _d in SUMMARY if not ok]
    for name, ok, detail in SUMMARY:
        log("CHECK:%-32s %-5s %s" % (name, "PASS" if ok else "FAIL", detail))
    log("RESULT: %s" % ("PASS" if not failed else "FAIL (%s)" % ", ".join(failed)))
    log("NOTE: keystroke capture is unit-test covered; every key above flowed "
        "through the production _dispatch_key path in-process.")
    return rc if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
