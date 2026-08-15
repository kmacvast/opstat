#!/usr/bin/env python3
"""Automated end-to-end var203 validation, driven from the Linux lab host.

Everything the mock cannot answer, in one unattended run: real startup and
shutdown UX, drill entry cost and cadence, ranking and cache behaviour,
navigation key handling, the Fabric/workload panel, and exact-id monitor
cleanup - plus the read-only probes from ``probe_var203.py``.

It drives ``opstat`` itself through a pseudo-terminal, so no human presses a
key. Timing is only meaningful when this runs *near* the cluster: the report
records the hostname so a run from a tethered laptop can be discounted.

Usage (Linux lab host, repository root):

    export VAST_PASSWORD=...      # or VAST_TOKEN; never on the command line
    python3 scripts/var203_validation/run_var203_validation.py

Then return ``/tmp/opstat-var203-validation.txt`` and the referenced API logs.

Safety contract (same as the probe):
  * targets --vms only, default var203.selab.vastdata.com; GETs plus temporary
    monitors that opstat itself owns and deletes
  * never modifies VMS configuration
  * exits every opstat session with a clean ``q`` and waits for the drain;
    SIGTERM only as a last resort after a grace period, SIGKILL never
  * learns the exact monitor ids each session created from that session's own
    API log, verifies each by per-id GET, and treats 404 as proof of deletion
  * never enumerates-and-deletes; other sessions' adhoc_opstat_* monitors on
    this shared cluster are read but never touched

Python 3.8+, stdlib only.
"""

import argparse
import errno
import json
import os
import re
import select
import signal
import socket
import subprocess
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

DEFAULT_VMS = "var203.selab.vastdata.com"
DEFAULT_USER = "admin"
RESULT_FILE = "/tmp/opstat-var203-validation.txt"

# Startup phase messages, in the order the engines must paint them.
STARTUP_PHASES = ("Connecting to", "Preparing metrics", "Gathering initial metrics")
CLEANUP_MARKER = "Cleaning up"

PASS, FAIL, UNVERIFIED = "PASS", "FAIL", "UNVERIFIED"

_ANSI = re.compile(r"\033\[[0-9;?]*[A-Za-z]")
_MONITOR_POST = re.compile(r'"id"\s*:\s*(\d+)')
_CALL_RE = re.compile(r"\b(GET|POST|DELETE|PUT|PATCH)\s+(\S+)")

# Panel titles the NVMe drill renders, per mode - the readiness signal that a
# drill actually opened. ("DRILL" appears in other engines' title bars but not
# in NVMe's, which was worth one wasted timeout to discover.)
DRILL_TITLES = {
    "cnode": "CNODE PATHS",
    "vip": "VIP PATHS",
    "host": "HOST INITIATORS",
}


class Report(object):
    """Accumulates lines for the result file and the verdict tallies."""

    def __init__(self):
        self.lines = []
        self.results = []          # (name, verdict, detail)

    def log(self, msg=""):
        print(msg, flush=True)
        self.lines.append(msg)

    def verdict(self, name, status, detail=""):
        self.results.append((name, status, detail))
        self.log("RESULT:%-28s %-10s %s" % (name, status, detail))

    def tally(self, status):
        return [n for n, s, _d in self.results if s == status]

    def write(self, path):
        with open(path, "w") as fh:
            fh.write("\n".join(self.lines) + "\n")


REPORT = Report()


def strip_ansi(text):
    return _ANSI.sub("", text)


# ---------------------------------------------------------------------------
# Prerequisites
# ---------------------------------------------------------------------------
def check_prerequisites(args):
    """Fail fast and loudly rather than half-running against a bad setup."""
    ok = True
    REPORT.log("=== prerequisites ===")

    REPORT.log("host              : %s" % socket.gethostname())
    REPORT.log("python            : %s" % sys.version.split()[0])
    if sys.version_info < (3, 8):
        REPORT.log("FAIL: Python 3.8+ required")
        ok = False

    if not os.path.isfile(os.path.join(_ROOT, "opstat")):
        REPORT.log("FAIL: opstat not found at repo root %s" % _ROOT)
        ok = False
    else:
        REPORT.log("opstat            : %s" % os.path.join(_ROOT, "opstat"))

    for cmd, label in (("rev-parse --abbrev-ref HEAD", "branch"),
                       ("rev-parse HEAD", "HEAD")):
        try:
            out = subprocess.check_output(
                ["git"] + cmd.split(), cwd=_ROOT,
                stderr=subprocess.STDOUT).decode().strip()
            REPORT.log("%-18s: %s" % (label, out))
        except (subprocess.CalledProcessError, OSError) as exc:
            REPORT.log("WARN: git %s failed: %s" % (cmd, exc))

    # Credential presence only - never the value, never its length.
    if os.environ.get("VAST_TOKEN"):
        REPORT.log("credential        : VAST_TOKEN present")
    elif os.environ.get("VAST_PASSWORD"):
        REPORT.log("credential        : VAST_PASSWORD present")
    else:
        REPORT.log("FAIL: set VAST_PASSWORD or VAST_TOKEN in the environment "
                   "(never on the command line)")
        ok = False

    try:
        addr = socket.gethostbyname(args.vms)
        REPORT.log("dns               : %s -> %s" % (args.vms, addr))
    except socket.gaierror as exc:
        REPORT.log("FAIL: cannot resolve %s: %s" % (args.vms, exc))
        return False
    try:
        sock = socket.create_connection((args.vms, args.vms_port), timeout=10)
        sock.close()
        REPORT.log("tcp               : %s:%d reachable" % (args.vms, args.vms_port))
    except (socket.error, OSError) as exc:
        REPORT.log("FAIL: cannot connect to %s:%d: %s" % (args.vms, args.vms_port, exc))
        ok = False

    return ok


def report_loadgen_state():
    """Report the committed lab load generators; never start or install them.

    Starting a systemd unit needs privilege and changes machine state, so this
    only observes and prints what the operator would have to run.
    """
    REPORT.log("\n=== load generators ===")
    units_dir = os.path.join(_ROOT, "scripts", "systemd")
    if not os.path.isdir(units_dir):
        REPORT.log("no scripts/systemd in this checkout")
        return
    try:
        names = sorted(n for n in os.listdir(units_dir) if n.endswith(".service"))
    except OSError:
        names = []
    if not names:
        REPORT.log("no committed .service units found")
    for name in names:
        state = "unknown"
        try:
            state = subprocess.check_output(
                ["systemctl", "is-active", name],
                stderr=subprocess.STDOUT).decode().strip()
        except subprocess.CalledProcessError as exc:
            state = exc.output.decode().strip() or "inactive"
        except OSError:
            state = "systemctl unavailable"
        REPORT.log("  %-40s %s" % (name, state))
    REPORT.log("NVMe/block figures need block load running. This script does not")
    REPORT.log("start units (privileged, changes machine state). If block load is")
    REPORT.log("inactive, run the documented installer/start yourself first:")
    REPORT.log("  scripts/systemd/install-lab-loadgen-units.sh   (see scripts/README-systemd.md)")


# ---------------------------------------------------------------------------
# PTY session driver
# ---------------------------------------------------------------------------
class OpstatSession(object):
    """One opstat run driven through a PTY, with its own API log.

    Never SIGKILLs. ``q`` is the exit path; SIGTERM is a last resort and the
    PTY is held open afterwards so the monitor drain can finish and be seen.
    """

    def __init__(self, label, engine_args, vms, user, vms_port, timeout=240):
        self.label = label
        self.engine_args = engine_args
        self.vms, self.user, self.vms_port = vms, user, vms_port
        self.timeout = timeout
        self.log_path = None
        self.output = ""
        self.frames = []           # (elapsed_s, cumulative_output_len)
        self.started = None
        self.exit_code = None
        self.pid = None
        self._master = None
        self._proc = None

    # -- lifecycle ---------------------------------------------------------
    def start(self):
        import pty

        cmd = [sys.executable, os.path.join(_ROOT, "opstat")] + self.engine_args + [
            "--vms", self.vms, "--vms-port", str(self.vms_port),
            "--user", self.user, "--log-api-calls",
        ]
        master, slave = pty.openpty()
        # A wide, tall terminal so the footer is never truncated by geometry.
        try:
            import fcntl
            import struct
            import termios
            fcntl.ioctl(slave, termios.TIOCSWINSZ,
                        struct.pack("HHHH", 50, 200, 0, 0))
        except Exception:
            pass
        env = dict(os.environ)
        env["TERM"] = "xterm-256color"
        self.started = time.time()
        self._proc = subprocess.Popen(
            cmd, stdin=slave, stdout=slave, stderr=slave,
            cwd=_ROOT, env=env, close_fds=True, preexec_fn=os.setsid,
        )
        os.close(slave)
        self._master = master
        self.pid = self._proc.pid
        REPORT.log("  started %s pid=%d" % (self.label, self.pid))
        return self

    def _drain(self, budget):
        """Read whatever is available for up to *budget* seconds."""
        deadline = time.time() + budget
        while time.time() < deadline:
            remaining = max(0.0, deadline - time.time())
            try:
                ready, _w, _x = select.select([self._master], [], [], remaining)
            except (select.error, OSError):
                return
            if not ready:
                return
            try:
                chunk = os.read(self._master, 65536)
            except OSError as exc:
                if exc.errno in (errno.EIO, errno.EBADF):
                    return
                raise
            if not chunk:
                return
            self.output += chunk.decode("utf-8", "replace")
            self.frames.append((time.time() - self.started, len(self.output)))

    def wait_for(self, needle, budget):
        """Read until *needle* appears. Returns elapsed seconds, or None."""
        deadline = time.time() + budget
        while time.time() < deadline:
            if needle in strip_ansi(self.output):
                return time.time() - self.started
            self._drain(0.5)
        return needle in strip_ansi(self.output) and (time.time() - self.started) or None

    def wait_for_since(self, needle, offset, budget):
        """Like wait_for, but only matches output produced after *offset*.

        Matching the whole buffer read old frames as current state - a key
        was declared consumed because the PREVIOUS drill's panel contained
        the string being waited for.
        """
        deadline = time.time() + budget
        while time.time() < deadline:
            if needle in strip_ansi(self.output[offset:]):
                return time.time() - self.started
            self._drain(0.5)
        return (needle in strip_ansi(self.output[offset:])
                and (time.time() - self.started) or None)

    def send(self, keys, settle=0.0):
        os.write(self._master, keys.encode())
        if settle:
            self._drain(settle)

    def quit(self, drain_budget=120):
        """Clean ``q`` exit; wait for the monitor drain to finish."""
        t0 = time.time()
        try:
            self.send("q")
        except OSError:
            pass
        deadline = time.time() + drain_budget
        while time.time() < deadline:
            self._drain(1.0)
            if self._proc.poll() is not None:
                break
        if self._proc.poll() is None:
            REPORT.log("  %s did not exit on q; sending SIGTERM (never SIGKILL)"
                       % self.label)
            try:
                os.killpg(os.getpgid(self.pid), signal.SIGTERM)
            except OSError:
                pass
            # Hold the PTY open: closing it mid-drain is what truncated
            # cleanup historically.
            deadline = time.time() + drain_budget
            while time.time() < deadline and self._proc.poll() is None:
                self._drain(1.0)
        self._drain(2.0)
        self.exit_code = self._proc.poll()
        try:
            os.close(self._master)
        except OSError:
            pass
        return time.time() - t0

    # -- evidence ----------------------------------------------------------
    def find_api_log(self):
        """The per-pid API log this session wrote."""
        candidates = []
        for name in os.listdir("/tmp"):
            if name.startswith("opstat-api-") and str(self.pid) in name:
                candidates.append(os.path.join("/tmp", name))
        if not candidates:
            for name in os.listdir("/tmp"):
                if name.startswith("opstat-api-"):
                    path = os.path.join("/tmp", name)
                    if os.path.getmtime(path) >= self.started:
                        candidates.append(path)
        self.log_path = sorted(candidates, key=os.path.getmtime)[-1] if candidates else None
        return self.log_path

    def api_lines(self):
        if not self.log_path:
            return []
        try:
            with open(self.log_path) as fh:
                return fh.readlines()
        except IOError:
            return []

    def api_mark(self):
        """Current length of this session's API log, for before/after deltas.

        The log is appended live, so counting lines around an action gives the
        exact calls that action cost - the deterministic evidence, independent
        of the wall-clock this run may or may not be able to trust.
        """
        if not self.log_path:
            self.find_api_log()
        return len(self.api_lines())

    def api_since(self, mark):
        return self.api_lines()[mark:]


def parse_calls(lines):
    """(method, path) for each request recorded in an API log.

    The log records the full URL and folds the response onto the same line:
        2026-08-14 13:41:05 POST https://host:443/api/monitors/ 0ms \
            payload={...} -> HTTP 201 (58 bytes) body={"id": 9000, ...}
    so the method is followed by a URL, not a bare path.
    """
    calls = []
    for line in lines:
        m = _CALL_RE.search(line)
        if not m:
            continue
        method, url = m.group(1), m.group(2)
        path = url
        for marker in ("/api/", "://"):
            if marker in path:
                path = path.split(marker, 1)[1]
                if marker == "/api/":
                    path = "/" + path
                else:
                    path = "/" + path.split("/", 1)[1] if "/" in path else "/"
                break
        calls.append((method, path.split("?")[0]))
    return calls


def created_monitor_ids(lines):
    """Monitor ids created here, from the POST /monitors/ response body.

    Request and response share one line, so this is a single-line match -
    anchored on POST .../monitors/ so that an unrelated body carrying an "id"
    (e.g. GET /clusters/) can never be counted as a monitor this run created.
    """
    ids = []
    for line in lines:
        if not re.search(r"POST\s+\S*/monitors/\s", line):
            continue
        body = line.split("body=", 1)[1] if "body=" in line else ""
        m = _MONITOR_POST.search(body)
        if m:
            ids.append(int(m.group(1)))
    return ids


def deleted_monitor_ids(lines):
    out = []
    for line in lines:
        m = re.search(r"DELETE\s+\S*/monitors/(\d+)/", line)
        if m:
            out.append(int(m.group(1)))
    return out


# ---------------------------------------------------------------------------
# Per-id cleanup verification (never enumerate-and-delete)
# ---------------------------------------------------------------------------
_AUTH_READY = False


def _connect(args):
    """Configure opstat's own transport for the verification GETs."""
    global _AUTH_READY
    if _AUTH_READY:
        return
    import ssl

    import vast_common

    base = ("https://%s/api" % args.vms if args.vms_port == 443
            else "https://%s:%d/api" % (args.vms, args.vms_port))
    headers, _auth, _pw = vast_common.resolve_auth(
        args.user, args.vms, None, "opstat/var203-validation")
    vast_common.configure_connection(
        base, headers, ssl._create_unverified_context())
    _AUTH_READY = True


def verify_ids_gone(ids, args):
    """GET each id; 404 is proof of deletion. Returns the ids still present.

    Deliberately per-id: listing every monitor on this shared lab cluster would
    surface other sessions' adhoc_opstat_* monitors, which must never be
    touched or counted against this run.
    """
    if not ids:
        return []
    import vast_common

    try:
        _connect(args)
    except Exception as exc:
        REPORT.log("  WARN: cannot verify ids (auth/transport): %r" % exc)
        return list(ids)
    remaining = []
    for monitor_id in ids:
        try:
            vast_common.request("GET", "/monitors/%d/" % monitor_id)
            remaining.append(monitor_id)
        except RuntimeError as exc:
            if "404" not in str(exc):
                REPORT.log("  WARN: id %d check inconclusive: %s" % (monitor_id, exc))
                remaining.append(monitor_id)
    return remaining


# ---------------------------------------------------------------------------
# Scenario A: NVMe startup
# ---------------------------------------------------------------------------
def scenario_nvme(args):
    REPORT.log("\n=== A. NVMe startup + dashboard ===")
    session = OpstatSession(
        "nvme", ["--block", "--nvme-over-tcp"], args.vms, args.user, args.vms_port,
    ).start()

    phase_times = {}
    for phase in STARTUP_PHASES:
        elapsed = session.wait_for(phase, args.startup_budget)
        phase_times[phase] = elapsed
        REPORT.log("  %-26s %s" % (
            phase, ("%.2fs" % elapsed) if elapsed else "NOT SEEN"))

    # "BLOCK HEALTH & WORKLOAD" is the dashboard's actual panel title.
    # ("TOTAL IOPS" was assumed in the first run and never renders, so the
    # validator dead-waited a full budget after the dashboard was already up,
    # inflating 157 s to a reported 206 s.)
    dashboard = session.wait_for("BLOCK HEALTH & WORKLOAD", args.startup_budget)
    REPORT.log("  %-26s %s" % (
        "dashboard", ("%.2fs" % dashboard) if dashboard else "NOT SEEN"))

    # Per-call startup cost, from the session's own log: which requests the
    # 206 s actually went to. Durations are in the log lines ("1234ms").
    startup_lines = session.api_since(0)
    REPORT.log("  startup call durations:")
    for line in startup_lines:
        m = re.search(r"\b(GET|POST|DELETE)\s+https?://\S*?(/api/\S+?)\s+(\d+)ms", line)
        if m:
            REPORT.log("    %-8s %-44s %8sms"
                       % (m.group(1), m.group(2).replace("/api", "")[:44], m.group(3)))

    seen_in_order = [p for p in STARTUP_PHASES if phase_times.get(p)]
    ordered = seen_in_order == [p for p in STARTUP_PHASES if phase_times.get(p)] and \
        all(phase_times[a] <= phase_times[b]
            for a, b in zip(seen_in_order, seen_in_order[1:]))
    if len(seen_in_order) == len(STARTUP_PHASES) and ordered:
        REPORT.verdict("nvme.startup.phases", PASS,
                       "all three in order, dashboard at %s" %
                       (("%.2fs" % dashboard) if dashboard else "?"))
    elif seen_in_order:
        REPORT.verdict("nvme.startup.phases", FAIL,
                       "saw %d/3: %s" % (len(seen_in_order), seen_in_order))
    else:
        REPORT.verdict("nvme.startup.phases", FAIL, "no startup phases observed")

    frame = strip_ansi(session.output)
    REPORT.verdict("nvme.footer", PASS if "[q] Quit" in frame else FAIL,
                   "footer present in dashboard" if "[q] Quit" in frame
                   else "no [q] Quit in frame")

    # --- Fabric / workload panel -------------------------------------------
    REPORT.log("\n--- F. Fabric / workload panel (verbatim frame excerpt) ---")
    excerpt = _panel_excerpt(frame)
    for line in excerpt:
        REPORT.log("  | " + line)
    REPORT.verdict("fabric.captured", PASS if excerpt else UNVERIFIED,
                   "%d panel lines captured for manual %% verification" % len(excerpt))

    # --- Drills -------------------------------------------------------------
    drill_results = {}
    for key, mode in (("c", "cnode"), ("i", "vip"), ("h", "host")):
        drill_results[mode] = _drill_scenario(session, key, mode, args)

    # --- Navigation ---------------------------------------------------------
    _navigation_scenario(session, args)

    # --- Shutdown -----------------------------------------------------------
    REPORT.log("\n=== G. NVMe shutdown ===")
    shutdown_s = session.quit(args.drain_budget)
    tail = strip_ansi(session.output)
    cleanup_seen = CLEANUP_MARKER in tail
    REPORT.log("  shutdown wall-clock : %.2fs" % shutdown_s)
    REPORT.log("  exit code           : %s" % session.exit_code)
    REPORT.verdict("nvme.shutdown.frame", PASS if cleanup_seen else FAIL,
                   "'%s' shown before the drain" % CLEANUP_MARKER if cleanup_seen
                   else "no cleanup message")
    REPORT.verdict("nvme.shutdown.exit", PASS if session.exit_code == 0 else FAIL,
                   "exit=%s in %.2fs" % (session.exit_code, shutdown_s))

    _cleanup_scenario(session, args, "nvme")
    return drill_results


def _panel_excerpt(frame):
    """Lines around the workload/fabric panel, for manual %-verification."""
    lines = [l.rstrip() for l in frame.split("\n")]
    keys = ("READ", "WRITE", "DEALLOCATE", "WRITE ZEROES", "FABRIC", "ADMIN",
            "Workload", "WORKLOAD", "TOTAL IOPS", "Reclaim", "RECLAIM")
    hits = [i for i, l in enumerate(lines) if any(k in l for k in keys)]
    if not hits:
        return []
    lo, hi = max(0, min(hits) - 2), min(len(lines), max(hits) + 3)
    return [l for l in lines[lo:hi] if l.strip()]


def _drill_scenario(session, key, mode, args):
    REPORT.log("\n=== NVMe %s drill (key '%s') ===" % (mode.upper(), key))
    title = DRILL_TITLES[mode]
    api_mark = session.api_mark()
    consumed_at = len(session.output)
    t0 = time.time()
    session.send(key)
    # Two-stage readiness. First: proof the keypress was CONSUMED - the drill
    # paints a "please stand by" loading frame before its blocking work, and
    # keys are only read between poll cycles (30-80 s each on var203). Then:
    # the panel itself, which legitimately took ~2 minutes of ranking + batch
    # creation on the first lab run - the old fixed 120 s deadline expired
    # while opstat was doing exactly what it should.
    consumed = session.wait_for_since("stand by", consumed_at, args.key_budget) \
        or session.wait_for_since(title, consumed_at, args.key_budget)
    if not consumed:
        REPORT.log("  WARN: no loading frame within %ss of '%s'"
                   % (args.key_budget, key))
    opened = session.wait_for_since(title, consumed_at, args.drill_budget)
    # Snapshot the log the instant the panel renders: anything after this is
    # ordinary polling during the settle window, not part of drill entry.
    entry_s = time.time() - t0
    entry_lines = session.api_since(api_mark)
    entry_calls = parse_calls(entry_lines)
    session._drain(args.drill_settle)
    if not opened and NO_TELEMETRY_MARKER in strip_ansi(session.output[consumed_at:]):
        # A scope that publishes no per-object telemetry renders an explicit
        # notice instead of fanning out monitors (round-3 remediation). On
        # builds like var203's 5.4.6 that IS the correct vip/blockhost
        # outcome: a bounded probe, an honest panel, no monitor storm.
        posts = [c for c in entry_calls if c[0] == "POST"]
        REPORT.verdict("nvme.%s.open" % mode, PASS,
                       "honest no-telemetry notice rendered (%d creates, %.0fs)"
                       % (len(posts), entry_s))
        REPORT.verdict("nvme.%s.entry" % mode,
                       PASS if len(posts) <= 8 else FAIL,
                       "%d calls, %d creates - bounded probe, no fan-out"
                       % (len(entry_calls), len(posts)))
        session.send("x", settle=2.0)
        return {"names": [], "entry_s": entry_s, "no_telemetry": True}
    if not opened:
        REPORT.verdict("nvme.%s.open" % mode, FAIL,
                       "panel '%s' never rendered within %ss"
                       % (title, args.drill_budget))

    # Rows come from the LAST repaint only - the tail of the whole buffer
    # still contains the previous mode's panels, which is how stale cNode
    # rows were attributed to the VIP and HOST windows in the first lab run.
    names = _drill_names(_last_frame(session)) if opened else []
    layout = _infer_layout(entry_lines)
    REPORT.log("  entry wall-clock    : %.2fs  (to panel render)" % entry_s)
    REPORT.log("  entry API calls     : %d  (keypress -> panel rendered)"
               % len(entry_calls))
    REPORT.log("  monitors created    : %d" % len(created_monitor_ids(entry_lines)))
    REPORT.log("  layout              : %s" % layout)
    REPORT.log("  rows observed       : %s" % (names or "none parsed"))
    _call_breakdown(entry_calls, indent="    ")

    # Steady-state cadence: watch without touching anything.
    cadence_mark = session.api_mark()
    session._drain(args.cadence_window)
    cadence_calls = parse_calls(session.api_since(cadence_mark))
    queries = [c for c in cadence_calls if c[1].endswith("/query/")]
    REPORT.log("  in %ds idle          : %d calls, %d queries"
               % (args.cadence_window, len(cadence_calls), len(queries)))

    # Manual refresh must force a query immediately.
    mark = len(session.output)
    refresh_mark = session.api_mark()
    session.send(" ", settle=args.refresh_settle)
    forced_calls = parse_calls(session.api_since(refresh_mark))
    forced_q = [c for c in forced_calls if c[1].endswith("/query/")]
    forced = len(session.output) > mark or forced_q
    REPORT.verdict("nvme.%s.manual_refresh" % mode, PASS if forced else FAIL,
                   "space forced %d queries (%d calls)" % (len(forced_q), len(forced_calls))
                   if forced_q else
                   ("repaint only, no forced query" if forced else "no effect"))

    exited = _exit_drill(session, title, args)
    REPORT.verdict("nvme.%s.exit_x" % mode, PASS if exited else FAIL,
                   "x returned to the dashboard" if exited
                   else "still in drill after x (waited %ss)" % args.key_budget)
    REPORT.verdict("nvme.%s.entry" % mode,
                   PASS if opened else FAIL,
                   "%d calls, %s layout, %d rows, %.2fs"
                   % (len(entry_calls), layout, len(names), entry_s))
    return {"names": names, "entry_s": entry_s, "entry_calls": len(entry_calls),
            "layout": layout}


def _exit_drill(session, title, args):
    """Send x and wait until the LAST frame is no longer the drill panel.

    Key consumption can take a full poll cycle, so a fixed 2 s settle read
    "still in drill" for an x that simply had not been processed yet.
    """
    session.send("x")
    deadline = time.time() + args.key_budget
    while time.time() < deadline:
        session._drain(2.0)
        frame = _last_frame(session)
        if title not in frame and "PERFORMANCE INSIGHTS" in frame:
            return True
    return title not in _last_frame(session)


def _infer_layout(lines):
    """batch vs per-object, from the monitor NAMES this entry created.

    The batch layout creates ``adhoc_opstat_<mode>_batch_<n>``; the fallback
    creates ``adhoc_opstat_<mode>_<object_id>``. The name lives in the POST
    payload, not the path (every create posts to ``/monitors/``), so this
    reads the raw log line. Counting monitors instead would be ambiguous,
    because the count varies with the number of op groups.
    """
    created = [l for l in lines if re.search(r"POST\s+\S*/monitors/\s", l)]
    if not created:
        return "no monitors created"
    names = []
    for line in created:
        m = re.search(r'"name":\s*"([^"]+)"', line)
        if m:
            names.append(m.group(1))
    if any("_batch_" in n for n in names):
        return "batch"
    if any("_rank_" in n or "rank_" in n for n in names) and len(names) == 1:
        return "rank only"
    return "per-object"


def _last_frame(session):
    """The most recent full repaint in the PTY buffer.

    The buffer accumulates every repaint; parsing its tail attributed stale
    cNode rows to the VIP and HOST windows in the first lab run. Frames start
    at the title bar, so the last title-bar occurrence bounds current screen
    state.
    """
    text = strip_ansi(session.output)
    idx = text.rfind("VAST NVMe-oTCP")
    return text[idx:] if idx >= 0 else text


def _drill_names(frame):
    """Object names from the rendered drill table (box-drawing tolerant)."""
    names = []
    for line in frame.split("\n"):
        line = strip_ansi(line).strip().strip("│|").strip()
        if not line or line.startswith(("─", "-", "=")):
            continue
        m = re.match(r"^([A-Za-z0-9][\w.:/-]{2,48}?)\s{2,}[\d.,-]", line)
        if m and m.group(1).upper() not in (
                "READ", "WRITE", "TOTAL", "IOPS", "CNODE", "VIP"):
            names.append(m.group(1))
    # The capture window can span more than one repaint; keep first-seen order
    # (which is rank order) without repeating an object per frame.
    seen, ordered = set(), []
    for name in names:
        if name not in seen:
            seen.add(name)
            ordered.append(name)
    return ordered[:12]


def _navigation_scenario(session, args):
    """Old bindings must not navigate; new ones must. Never presses q."""
    REPORT.log("\n=== E. Navigation bindings ===")
    frame = strip_ansi(session.output)
    for key, label in (("i", "VIP"), ("x", "Exit drill"), ("space", "Refresh")):
        token = "[%s] %s" % (key, label)
        REPORT.verdict("nav.legend.%s" % key, PASS if token in frame else FAIL,
                       "'%s' in footer" % token if token in frame
                       else "'%s' not found in footer" % token)
    REPORT.verdict("nav.legend.no_v_vip", FAIL if "[v] VIP" in frame else PASS,
                   "[v] VIP absent" if "[v] VIP" not in frame else "[v] VIP still advertised")
    REPORT.verdict("nav.legend.no_p_exit", FAIL if "[p]" in frame else PASS,
                   "[p] absent" if "[p]" not in frame else "[p] still advertised")

    # An unbound key produces NO repaint at all, so "nothing new appeared"
    # cannot be read as "the key did nothing" - force a repaint with space and
    # inspect the resulting frame instead.
    def current_frame():
        mark = len(session.output)
        session.send(" ", settle=args.refresh_settle)
        return strip_ansi(session.output[mark:]) or strip_ansi(session.output[-6000:])

    # 'p' must not exit a drill (retired NVMe binding).
    mark = len(session.output)
    session.send("c")
    session.wait_for_since(DRILL_TITLES["cnode"], mark, args.drill_budget)
    session.send("p", settle=2.0)
    current_frame()                       # force a repaint (p alone paints nothing)
    still_in = DRILL_TITLES["cnode"] in _last_frame(session)
    REPORT.verdict("nav.p_does_not_exit", PASS if still_in else FAIL,
                   "p left the cNode drill open" if still_in
                   else "p exited the drill - retired binding is still live")
    _exit_drill(session, DRILL_TITLES["cnode"], args)

    # 'v' must not open a VIP drill on NVMe (retired binding; v means View).
    session.send("v", settle=2.0)
    current_frame()
    opened_vip = DRILL_TITLES["vip"] in _last_frame(session)
    REPORT.verdict("nav.v_is_not_vip", FAIL if opened_vip else PASS,
                   "v opened the VIP drill - retired binding is still live"
                   if opened_vip else "v did not open VIP")
    if opened_vip:
        _exit_drill(session, DRILL_TITLES["vip"], args)


def _cleanup_scenario(session, args, label):
    REPORT.log("\n=== %s cleanup accounting ===" % label)
    path = session.find_api_log()
    REPORT.log("  api log             : %s" % (path or "NOT FOUND"))
    if not path:
        REPORT.verdict("%s.cleanup" % label, UNVERIFIED, "no API log to account from")
        return
    lines = session.api_lines()
    calls = parse_calls(lines)
    created = created_monitor_ids(lines)
    deleted = deleted_monitor_ids(lines)
    REPORT.log("  total API calls     : %d" % len(calls))
    REPORT.log("  ids created         : %s" % sorted(set(created)))
    REPORT.log("  ids deleted         : %s" % sorted(set(deleted)))
    remaining = verify_ids_gone(sorted(set(created) - set(deleted)), args)
    REPORT.log("  ids still present   : %s" % (remaining or "NONE"))
    REPORT.verdict("%s.cleanup" % label, PASS if not remaining else FAIL,
                   "all %d session monitors deleted (per-id GET, 404=gone)"
                   % len(set(created)) if not remaining
                   else "STILL PRESENT: %s" % remaining)
    REPORT.log("  whole-session call breakdown:")
    _call_breakdown(calls, indent="    ")


def _call_breakdown(calls, indent="    "):
    if not calls:
        return
    buckets = {}
    for method, path in calls:
        key = "%s %s" % (method, re.sub(r"/\d+/", "/<id>/", path).split("?")[0])
        buckets[key] = buckets.get(key, 0) + 1
    for key in sorted(buckets, key=lambda k: -buckets[k]):
        REPORT.log("%s%-46s %d" % (indent, key, buckets[key]))


# ---------------------------------------------------------------------------
# Short startup/nav/clean-q checks for the other engines
# ---------------------------------------------------------------------------
def scenario_other(args, label, engine_args):
    REPORT.log("\n=== other protocol: %s ===" % label)
    try:
        session = OpstatSession(label, engine_args, args.vms, args.user,
                                args.vms_port).start()
    except OSError as exc:
        REPORT.verdict("%s.startup" % label, UNVERIFIED, "could not start: %s" % exc)
        return
    seen = []
    for phase in STARTUP_PHASES:
        if session.wait_for(phase, args.startup_budget):
            seen.append(phase)
    frame = strip_ansi(session.output)
    footer = "[q] Quit" in frame
    REPORT.verdict("%s.startup.phases" % label,
                   PASS if len(seen) == 3 else (FAIL if seen else UNVERIFIED),
                   "%d/3 phases seen" % len(seen))
    REPORT.verdict("%s.footer" % label, PASS if footer else FAIL,
                   "footer present" if footer else "no footer")
    if footer:
        for line in frame.split("\n"):
            if "[q] Quit" in line:
                REPORT.log("  footer: %s" % line.strip()[:180])
                break
    session.quit(args.drain_budget)
    REPORT.verdict("%s.exit" % label, PASS if session.exit_code == 0 else FAIL,
                   "exit=%s" % session.exit_code)
    _cleanup_scenario(session, args, label)


# ---------------------------------------------------------------------------
# Probe integration
# ---------------------------------------------------------------------------
def run_probe(args):
    REPORT.log("\n=== read-only probes (probe_var203.py) ===")
    probe = os.path.join(_HERE, "probe_var203.py")
    if not os.path.isfile(probe):
        REPORT.verdict("probe", UNVERIFIED, "probe_var203.py not found")
        return
    out_path = "/tmp/opstat-var203-probe.txt"
    try:
        with open(out_path, "w") as fh:
            rc = subprocess.call(
                [sys.executable, probe, "--vms", args.vms, "--user", args.user,
                 "--port", str(args.vms_port)],
                cwd=_ROOT, stdout=fh, stderr=subprocess.STDOUT)
    except OSError as exc:
        REPORT.verdict("probe", UNVERIFIED, "could not run: %s" % exc)
        return
    REPORT.log("  probe output        : %s (rc=%d)" % (out_path, rc))
    try:
        with open(out_path) as fh:
            for line in fh:
                if line.startswith("PROBE:"):
                    REPORT.log("  " + line.rstrip())
    except IOError:
        pass
    REPORT.verdict("probe.run", PASS if rc == 0 else FAIL, "see %s" % out_path)


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--vms", default=DEFAULT_VMS)
    ap.add_argument("--vms-port", type=int, default=443)
    ap.add_argument("--user", default=DEFAULT_USER)
    ap.add_argument("--startup-budget", type=int, default=120,
                    help="seconds to wait for each startup phase")
    ap.add_argument("--drill-budget", type=int, default=420,
                    help="seconds to wait for a drill panel; a real var203 entry legitimately ran ~2 minutes")
    ap.add_argument("--drill-settle", type=float, default=8.0)
    ap.add_argument("--cadence-window", type=int, default=45)
    ap.add_argument("--refresh-settle", type=float, default=6.0)
    ap.add_argument("--key-budget", type=int, default=150,
                    help="seconds to wait for a keypress to be consumed; keys are read between poll cycles, which ran 30-80 s on var203")
    ap.add_argument("--drain-budget", type=int, default=180)
    ap.add_argument("--skip-probe", action="store_true")
    ap.add_argument("--skip-others", action="store_true",
                    help="NVMe only; skip the SMB/S3/NFS startup checks")
    ap.add_argument("--result", default=RESULT_FILE)
    args = ap.parse_args()

    if args.vms != DEFAULT_VMS:
        REPORT.log("NOTE: target overridden to %s" % args.vms)

    start = time.strftime("%Y-%m-%dT%H:%M:%S")
    REPORT.log("opstat var203 automated validation")
    REPORT.log("started %s" % start)

    if not check_prerequisites(args):
        REPORT.log("\nPrerequisites failed; nothing was run against the cluster.")
        REPORT.write(args.result)
        return 2

    report_loadgen_state()

    if not args.skip_probe:
        run_probe(args)

    try:
        scenario_nvme(args)
    except Exception as exc:                      # keep going; report honestly
        REPORT.verdict("nvme", FAIL, "scenario raised: %r" % exc)

    if not args.skip_others:
        for label, engine_args in (
            ("smb", ["--smb"]),
            ("s3", ["--s3"]),
            ("nfs_v3", ["--nfs", "--version=3.0"]),
            ("nfs_v41", ["--nfs", "--version=4.1"]),
        ):
            try:
                scenario_other(args, label, engine_args)
            except Exception as exc:
                REPORT.verdict("%s" % label, UNVERIFIED, "scenario raised: %r" % exc)

    end = time.strftime("%Y-%m-%dT%H:%M:%S")
    _summary(args, start, end)
    REPORT.write(args.result)
    print("\nwrote %s" % args.result)
    return 0


def _summary(args, start, end):
    REPORT.log("\n" + "=" * 70)
    REPORT.log("VAR203 AUTOMATED VALIDATION SUMMARY")
    REPORT.log("=" * 70)
    REPORT.log("Host running validation: %s" % socket.gethostname())
    try:
        branch = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=_ROOT).decode().strip()
        head = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=_ROOT).decode().strip()
    except (subprocess.CalledProcessError, OSError):
        branch = head = "unknown"
    REPORT.log("Branch: %s" % branch)
    REPORT.log("HEAD: %s" % head)
    REPORT.log("Target VMS: %s" % args.vms)
    REPORT.log("Start: %s" % start)
    REPORT.log("End: %s" % end)
    REPORT.log("")
    for name, status, detail in REPORT.results:
        REPORT.log("%-34s %-11s %s" % (name, status, detail))
    REPORT.log("")
    REPORT.log("PASS: %s" % ", ".join(REPORT.tally(PASS)) or "none")
    REPORT.log("FAIL: %s" % ", ".join(REPORT.tally(FAIL)) or "none")
    REPORT.log("UNVERIFIED: %s" % ", ".join(REPORT.tally(UNVERIFIED)) or "none")
    REPORT.log("")
    REPORT.log("Wall-clock is only meaningful when this ran near the cluster.")
    REPORT.log("FILES TO RETURN:")
    REPORT.log("  %s" % args.result)
    REPORT.log("  /tmp/opstat-var203-probe.txt")
    REPORT.log("  /tmp/opstat-api-*.log  (the pid-scoped logs referenced above)")


if __name__ == "__main__":
    sys.exit(main())
