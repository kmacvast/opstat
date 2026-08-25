#!/usr/bin/env python3
"""Shared lifecycle, signal, and rendering helpers for opstat engines.

Centralizes the cross-cutting concerns that were previously copy-pasted into
each protocol engine: VMS monitor teardown tracking, signal/atexit wiring
(including SIGHUP), local-cluster selection, and flicker-free frame flushing.
"""

import atexit
import base64
import getpass
import http.client
import json
import os
import re
import select
import shutil
import signal
import sys
import threading
import time
import urllib.parse

try:
    import termios
    import tty
    _TERMIOS_OK = True
except ImportError:  # non-POSIX (e.g. Windows); keyboard features degrade gracefully
    termios = tty = None
    _TERMIOS_OK = False

import vast_api_log

# ---------------------------------------------------------------------------
# REST transport
# ---------------------------------------------------------------------------
# A single persistent HTTPS connection with keep-alive. The previous
# urllib-based transport opened (and TLS-handshook) a brand-new TCP connection
# for every API call, which dominated per-call latency and multiplied load on
# the VMS. Engines are single-threaded, but the connection is guarded by a
# lock so future background pollers cannot interleave requests.
_BASE_URL = None
_HOST = None
_PORT = None
_BASE_PATH = ""
_HEADERS = None
_SSL_CTX = None
_TIMEOUT = 60
_CONN = None
_CONN_LOCK = threading.Lock()


def configure_connection(base_url, headers, ssl_ctx, timeout=60):
    """Store the VMS connection context used by :func:`request`."""
    global _BASE_URL, _HOST, _PORT, _BASE_PATH, _HEADERS, _SSL_CTX, _TIMEOUT
    _BASE_URL = base_url
    parsed = urllib.parse.urlsplit(base_url)
    _HOST = parsed.hostname
    _PORT = parsed.port or 443
    _BASE_PATH = parsed.path.rstrip("/")
    _HEADERS = headers
    _SSL_CTX = ssl_ctx
    _TIMEOUT = timeout
    close_connection()


def _get_connection():
    """Return the persistent HTTPS connection, creating it on first use."""
    global _CONN
    if _CONN is None:
        _CONN = http.client.HTTPSConnection(
            _HOST, _PORT, timeout=_TIMEOUT, context=_SSL_CTX,
        )
    return _CONN


def close_connection():
    """Drop the persistent connection (reconnects lazily on next request)."""
    global _CONN
    if _CONN is not None:
        try:
            _CONN.close()
        except Exception:
            pass
        _CONN = None


def _send_once(method, path, data, base=None):
    """One request/response on the persistent connection. Returns (status, body)."""
    conn = _get_connection()
    prefix = _BASE_PATH if base is None else base
    conn.request(method, f"{prefix}{path}", body=data, headers=_HEADERS)
    resp = conn.getresponse()
    body = resp.read().decode(errors="replace")
    return resp.status, body


def request_text(method, path, payload=None, root=False):
    """Issue an authenticated VMS request and return the raw response body.

    ``root=True`` addresses the server root rather than the API base path, for
    resources such as the Swagger UI that live outside ``/api``. Used by
    discovery for endpoints that do not return JSON - the Prometheus exporter
    serves ``text/plain``, which :func:`request` would fail to parse.
    """
    base = "" if root else _BASE_PATH
    url = f"https://{_HOST}:{_PORT}{base}{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    started = time.monotonic()
    with _CONN_LOCK:
        try:
            reused = _CONN is not None
            try:
                status, body = _send_once(method, path, data, base=base)
            except (http.client.HTTPException, ConnectionError, BrokenPipeError, OSError):
                # Stale keep-alive socket: retry exactly once on a new
                # connection, but only if the failed attempt was on a reused
                # one (a fresh-connection failure is a real outage).
                if not reused:
                    raise
                close_connection()
                status, body = _send_once(method, path, data, base=base)
        except Exception as e:
            close_connection()
            elapsed_ms = (time.monotonic() - started) * 1000
            vast_api_log.log_call(method, url, payload, None, None, e, elapsed_ms)
            raise RuntimeError(f"{method} {url} failed: {e}") from e

    elapsed_ms = (time.monotonic() - started) * 1000
    if status >= 400:
        err = f"HTTP {status}: {body}"
        vast_api_log.log_call(method, url, payload, status, body, err, elapsed_ms)
        raise RuntimeError(f"{method} {url} failed: {err}")
    vast_api_log.log_call(method, url, payload, status, body, None, elapsed_ms)
    return body


def request(method, path, payload=None):
    """Issue an authenticated VMS REST request; log every call via vast_api_log.

    Reuses one keep-alive connection across calls; a request that fails on a
    previously-used connection (e.g. the server idled it out between refresh
    ticks) is retried once on a fresh connection. Raises RuntimeError on any
    HTTP or transport error (never leaks the raw http.client exception type).
    """
    body = request_text(method, path, payload)
    if not body:
        return None
    try:
        return json.loads(body)
    except ValueError as e:
        raise RuntimeError(
            f"{method} {_BASE_URL}{path} returned non-JSON body: {str(e)[:80]}"
        ) from e


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------
def resolve_auth(user, vms, cli_password, user_agent):
    """Resolve VMS auth headers once, identically for every engine.

    VAST_TOKEN (Bearer) wins and is checked before any password acquisition,
    so token users are never prompted for a password that would be ignored.
    Otherwise: --password, then VAST_PASSWORD, then an interactive prompt.

    Returns (headers, basic_auth_b64, password); the last two are None in
    token mode.
    """
    token = os.environ.get("VAST_TOKEN")
    if token:
        headers = {"Authorization": f"Bearer {token}"}
        auth = password = None
    else:
        password = cli_password or os.environ.get("VAST_PASSWORD")
        if not password:
            try:
                password = getpass.getpass(f"Password for {user}@{vms}: ")
            except KeyboardInterrupt:
                print()
                raise SystemExit(1)
        auth = base64.b64encode(f"{user}:{password}".encode()).decode()
        headers = {"Authorization": f"Basic {auth}"}
    headers.update({
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": user_agent,
    })
    return headers, auth, password


def resolve_object_name(obj, fields):
    """Resolve a drill-down object's display name from candidate fields.

    Falls back to the object id. The cluster root/default view has path ``/``;
    label it ``/ (default)`` so it is not mistaken for a blank/unnamed row.
    """
    name = None
    for field in fields:
        val = obj.get(field)
        if val:
            name = str(val)
            break
    if name is None:
        name = str(obj.get("id", "?"))
    return "/ (default)" if name == "/" else name


def normalize_list_response(data):
    """Normalize VMS list endpoints (list, or {results|data|objects: [...]}) to a list."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("results", "data", "objects"):
            if isinstance(data.get(key), list):
                return data[key]
    return []


# Cached local-cluster record from the most recent get_current_cluster()
# call, so the follow-up OS-version lookup does not repeat GET /clusters/.
_LAST_CLUSTER_RECORD = None


def get_current_cluster(request_fn):
    """Return (cluster_id, cluster_name) for the active/local cluster.

    Read-only. ``request_fn`` is the engine's ``api_request`` so unit tests that
    patch it continue to intercept the call.
    """
    global _LAST_CLUSTER_RECORD
    data = request_fn("GET", "/clusters/")
    clusters = normalize_list_response(data)
    if not clusters:
        raise RuntimeError(f"No clusters returned from /clusters/: {data}")
    cluster = select_local_cluster(clusters)
    _LAST_CLUSTER_RECORD = cluster
    cluster_id = cluster.get("id")
    cluster_name = (
        cluster.get("name") or cluster.get("cluster_name")
        or cluster.get("mgmt_name") or cluster.get("guid") or "unknown"
    )
    if cluster_id is None:
        raise RuntimeError(f"Cluster record did not include id: {cluster}")
    return cluster_id, cluster_name


# VMS cluster records expose the running VAST OS build under one of these keys,
# depending on cluster version. Ordered by preference.
_OS_VERSION_KEYS = (
    "sw_version", "os_version", "sw_version_str", "release", "version", "build",
)


def os_release_from_cluster(cluster):
    """Return the first non-empty OS version field from a cluster dict, or None."""
    if not isinstance(cluster, dict):
        return None
    for key in _OS_VERSION_KEYS:
        val = cluster.get(key)
        if val:
            return str(val)
    return None


def get_current_cluster_os(request_fn):
    """Best-effort local-cluster VAST OS version string, or None.

    Reuses the cluster record cached by the immediately-preceding
    :func:`get_current_cluster` call (every engine calls them back to back),
    so the second GET /clusters/ round trip is skipped. Read-only and
    defensive: the OS label is a cosmetic header adornment, so any failure
    (network, missing field) degrades to None rather than raising.
    """
    if _LAST_CLUSTER_RECORD is not None:
        return os_release_from_cluster(_LAST_CLUSTER_RECORD)
    try:
        data = request_fn("GET", "/clusters/")
        cluster = select_local_cluster(normalize_list_response(data))
        return os_release_from_cluster(cluster)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Metric catalog
# ---------------------------------------------------------------------------
def collect_metric_names(obj):
    """Recursively gather every string in a catalog response (schema-agnostic).

    VMS builds disagree about the shape of /metrics/ (a bare list, {results:
    [...]}, dicts keyed by family, entries with a "metric" or "name" field),
    so walk the whole structure rather than guessing a schema.
    """
    names = set()
    stack = [obj]
    while stack:
        node = stack.pop()
        if isinstance(node, str):
            names.add(node)
        elif isinstance(node, dict):
            stack.extend(node.values())
        elif isinstance(node, (list, tuple)):
            stack.extend(node)
    return names


def fetch_metric_catalog(request_fn, max_pages=20):
    """Return the set of metric names VMS advertises, following pagination.

    Read-only and best-effort: returns an empty set when the catalog cannot
    be read, so callers can fall back to probing monitors directly.
    """
    names = set()
    path = "/metrics/"
    for _page in range(max_pages):
        try:
            payload = request_fn("GET", path)
        except RuntimeError:
            break
        if payload is None:
            break
        names |= collect_metric_names(payload)
        nxt = payload.get("next") if isinstance(payload, dict) else None
        if not nxt or not isinstance(nxt, str):
            break
        # VMS returns either an absolute URL or a path; keep only the path
        # so the shared transport's base URL still applies.
        marker = "/api"
        path = nxt[nxt.index(marker) + len(marker):] if marker in nxt else nxt
    return filter_metric_names(names)


def filter_metric_names(names):
    """Keep only strings shaped like metric FQNs.

    The recursive walk also picks up descriptions, URLs and enum values.
    A metric identifier always carries a ``family,`` prefix and never
    contains whitespace, which is enough to separate them from prose that
    happens to contain a comma.
    """
    return {
        n for n in names
        if "," in n and not n.startswith(("http", "/")) and not any(
            ch.isspace() for ch in n
        )
    }


def metric_family(name):
    """The family prefix of a metric FQN (text before the first comma)."""
    return str(name).split(",", 1)[0]


# ---------------------------------------------------------------------------
# Monitor sample selection
# ---------------------------------------------------------------------------
# Columns that are always populated and so carry no information about how
# complete a sample row is.
_NON_METRIC_COLUMNS = ("timestamp", "object_id")


def metric_column_indexes(prop_idx):
    """Indexes of the real metric columns in a monitor query result."""
    return [idx for name, idx in prop_idx.items() if name not in _NON_METRIC_COLUMNS]


def latest_complete_row(data, metric_indexes):
    """Return the newest row carrying the most populated metrics, or None.

    VMS publishes the newest bucket of a monitor while it is still filling.
    On VAST OS 5.5.0.1 a cluster monitor's newest row had 2 of 46 metrics
    populated, and a ViewMetrics row had exactly one. Taking ``data[0]``
    verbatim therefore reports missing latency/bandwidth and a workload mix
    skewed to whichever counter happened to land first. Rows arrive newest
    first, so scanning in order and keeping the strictly-better score yields
    the freshest usable sample, with every value from one instant.
    """
    if not data:
        return None
    if not metric_indexes:
        return data[0]
    best, best_score = data[0], -1
    complete = len(metric_indexes)
    for row in data:
        score = sum(
            1 for idx in metric_indexes if idx < len(row) and row[idx] is not None
        )
        if score > best_score:
            best, best_score = row, score
            if score == complete:
                break
    return best


def latest_complete_values(data, prop_idx, prop_names=None):
    """Return (values_by_prop_name, sample_timestamp) for the newest usable row.

    ``prop_names`` restricts which columns decide "most populated". This
    matters whenever one monitor carries several metric families: on a real
    cluster the newest cNode bucket had *only* the two NFSCommon bandwidth
    props filled in, while the 44 NfsMetrics props landed in older buckets.
    Scoring across everything therefore picked an NfsMetrics-rich row whose
    bandwidth columns were null, and bandwidth rendered as "-". Each consumer
    scores against the props it actually reads, so every family gets the
    newest row that carries *it*.
    """
    if not data:
        return {}, "-"
    if prop_names:
        indexes = [prop_idx[n] for n in prop_names if n in prop_idx]
    else:
        indexes = metric_column_indexes(prop_idx)
    row = latest_complete_row(data, indexes)
    if row is None:
        return {}, "-"
    values = {name: row[idx] for name, idx in prop_idx.items() if idx < len(row)}
    return values, (row[0] if row else "-")


def bounding_samples(data, *indexes):
    """Newest and oldest rows where every column in *indexes* is populated.

    Cumulative-counter rates need two real readings; anchoring on
    ``data[0]``/``data[-1]`` silently yields None (rendered "-") or a zero
    rate whenever the newest bucket has not filled that column yet.
    """
    usable = []
    for row in data:
        for idx in indexes:
            if idx >= len(row) or row[idx] is None:
                break
        else:
            usable.append(row)
    if len(usable) < 2:
        return None, None
    return usable[0], usable[-1]     # VMS orders newest first


# ---------------------------------------------------------------------------
# Monitor scaffolding (create / delete)
# ---------------------------------------------------------------------------
def create_monitor_raw(request_fn, name, prop_list, object_type, object_ids,
                       *, time_frame, no_aggregation=False):
    """Create one VMS monitor and register it for guaranteed teardown.

    Data-altering (POST /monitors/). When ``no_aggregation`` is False, tries a
    ``granularity=auto`` payload first and retries without it on clusters that
    reject that granularity.
    """
    base_payload = {
        "name": name,
        "object_type": object_type,
        "object_ids": object_ids,
        "time_frame": time_frame,
        "prop_list": prop_list,
    }
    if not no_aggregation:
        base_payload["aggregation"] = "avg"
        base_payload["query_aggregation"] = "avg"

    if no_aggregation:
        result = request_fn("POST", "/monitors/", base_payload)
    else:
        payload = {**base_payload, "granularity": "auto"}
        try:
            result = request_fn("POST", "/monitors/", payload)
        except RuntimeError as e:
            msg = str(e)
            if "Invalid granularity: auto" not in msg and "no such granularity auto" not in msg:
                raise
            result = request_fn("POST", "/monitors/", base_payload)

    monitor_id = result.get("id") if isinstance(result, dict) else None
    if not monitor_id:
        raise RuntimeError(f"Monitor create did not return id for {name}: {result}")
    return register_monitor(monitor_id)


def delete_monitor(request_fn, monitor_id):
    """Delete a monitor (Data-altering); track real (non-404) failures for exit."""
    if monitor_id is None:
        return
    try:
        request_fn("DELETE", f"/monitors/{monitor_id}/")
    except RuntimeError as e:
        if "HTTP 404" not in str(e):
            record_failed_delete(monitor_id, str(e)[:80])
    except Exception as e:  # pragma: no cover - request() already wraps to RuntimeError
        record_failed_delete(monitor_id, str(e)[:80])
    finally:
        forget_monitor(monitor_id)


# ---------------------------------------------------------------------------
# Monitor lifecycle registry
# ---------------------------------------------------------------------------
# Every monitor created via a protocol engine is registered here the instant
# the VMS returns an id. Teardown drains this set, so a partially-created
# monitor group (or an unexpected exit path) can never orphan monitors.
_CREATED_MONITORS = set()
_FAILED_DELETES = []


def register_monitor(monitor_id):
    """Record a freshly-created monitor id; returns it for call-site chaining."""
    if monitor_id is not None:
        _CREATED_MONITORS.add(monitor_id)
    return monitor_id


def forget_monitor(monitor_id):
    """Drop a monitor id from the registry (after it has been deleted)."""
    _CREATED_MONITORS.discard(monitor_id)


def emit_stderr(message):
    """Best-effort stderr line: NEVER raises.

    Cleanup output goes to a terminal that may already be gone - round 4
    leaked the whole headline monitor set because the shutdown banner's
    write to a dead PTY raised EIO and killed cleanup() before the drain
    ran, on the signal path and the atexit retry alike. Rendering is
    best-effort; monitor deletion is mandatory.
    """
    try:
        print(message, file=sys.stderr, flush=True)
    except Exception:
        pass


def drain_monitors(delete_fn):
    """Delete every still-registered monitor using engine-supplied delete_fn.

    The drain is a slow synchronous DELETE loop (~1 s per monitor on a real
    cluster). Termination signals are blocked for its duration so a SIGTERM,
    a second SIGINT, or a PTY hang-up SIGHUP arriving mid-drain cannot re-enter
    the signal handler and ``sys.exit`` out of the loop, orphaning the monitors
    it had not yet reached. Any such signal is deferred and delivered once the
    mask is restored, so a clean shutdown still happens — after every monitor is
    gone. The previous mask is always restored, so this is safe to call from
    tests that keep running.
    """
    block = getattr(signal, "pthread_sigmask", None)
    term_sigs = [getattr(signal, n) for n in ("SIGINT", "SIGTERM", "SIGHUP")
                 if hasattr(signal, n)]
    previous_mask = None
    if block is not None and term_sigs:
        try:
            previous_mask = block(signal.SIG_BLOCK, term_sigs)
        except (ValueError, OSError):
            previous_mask = None
    try:
        # Continue through EVERY owned monitor even when one delete raises
        # (engine delete wrappers normally record-and-swallow, but the drain
        # must not depend on that), and show truthful progress on a long
        # drain: the round-3 var203 shutdown was draining 206 monitors at
        # ~2 s each - the observer gave up at 362 s with 9 to go, reading a
        # working drain as a hang. Real counts only, no fake progress.
        pending = list(_CREATED_MONITORS)
        total = len(pending)
        for done, monitor_id in enumerate(pending, 1):
            try:
                delete_fn(monitor_id)
            except Exception as exc:              # noqa: BLE001 - report, keep draining
                record_failed_delete(monitor_id, str(exc)[:80])
            _CREATED_MONITORS.discard(monitor_id)
            if total >= 20 and (done % 10 == 0 or done == total):
                emit_stderr("  ... %d/%d monitors removed" % (done, total))
    finally:
        if previous_mask is not None:
            try:
                block(signal.SIG_SETMASK, previous_mask)
            except (ValueError, OSError):
                pass


def pending_monitor_count():
    """How many session monitors are still registered (awaiting teardown)."""
    return len(_CREATED_MONITORS)


def cleanup_message(count):
    """Truthful shutdown banner for the monitor drain (no fake progress)."""
    noun = "monitor" if count == 1 else "monitors"
    return "Cleaning up %d temporary %s, please stand by..." % (count, noun)


def record_failed_delete(monitor_id, detail):
    """Note a DELETE that failed for a non-404 reason, for exit reporting."""
    _FAILED_DELETES.append((monitor_id, detail))


def failed_deletes():
    """Return list of (monitor_id, detail) for deletes that truly failed."""
    return list(_FAILED_DELETES)


def reset_registry():
    """Clear registry + failure log (used between sessions and in tests)."""
    global _POLL_FAILURES, _LAST_CLUSTER_RECORD
    _CREATED_MONITORS.clear()
    _FAILED_DELETES.clear()
    _POLL_FAILURES = 0
    _LAST_CLUSTER_RECORD = None
    close_connection()


# ---------------------------------------------------------------------------
# Cluster selection
# ---------------------------------------------------------------------------
def select_local_cluster(clusters):
    """Pick the local/current cluster by explicit boolean fields.

    Avoids the fragile ``'"local": true' in json.dumps(...)`` string match by
    reading the fields directly. Falls back to the first cluster.
    """
    if not clusters:
        return None
    for cluster in clusters:
        if not isinstance(cluster, dict):
            continue
        for key in ("local", "is_local", "current"):
            if cluster.get(key) is True:
                return cluster
    return clusters[0]


# ---------------------------------------------------------------------------
# Signal + atexit wiring
# ---------------------------------------------------------------------------
def install_signal_handlers(handler):
    """Route SIGINT, SIGTERM, and SIGHUP to *handler* where supported."""
    for name in ("SIGINT", "SIGTERM", "SIGHUP"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, handler)
        except (ValueError, OSError):
            # SIGHUP unavailable on some platforms; non-main-thread guard.
            pass


def register_atexit(cleanup_fn):
    """Register *cleanup_fn* as an interpreter-exit backstop."""
    atexit.register(cleanup_fn)


# ---------------------------------------------------------------------------
# Frame rendering
# ---------------------------------------------------------------------------
def flush_frame(text):
    """Write one composed frame with a single syscall.

    Homes the cursor (no full-screen erase, so there is no blank interval) and
    appends ``\\033[K`` (erase-to-end-of-line) after every line so a shorter new
    line never leaves stale characters from the previous frame on the right.
    A trailing ``\\033[J`` then clears any rows below a now-shorter frame. This
    removes both the right-side ghosting and the screen tearing that a
    ``\\033[2J`` + many per-line prints would cause.
    """
    framed = "\033[K\n".join(text.split("\n"))
    sys.stdout.write("\033[H" + framed + "\033[K\033[J")
    sys.stdout.flush()


# ---------------------------------------------------------------------------
# Poll-failure tolerance
# ---------------------------------------------------------------------------
# A transient VMS/network error (blip, VMS restart, expired session) must not
# kill a long-running dashboard. Give up only after this many consecutive
# failed refresh ticks; at the default 5s refresh this is ~2.5 minutes.
MAX_CONSECUTIVE_POLL_FAILURES = 30
_POLL_FAILURES = 0


def guarded_poll(fetch_fn, render_fn):
    """Run one poll+render tick, tolerating transient failures.

    On failure: redraws the last good data via *render_fn* (engine renderers
    compose from module state, which a failed fetch leaves untouched), writes
    a one-line retry notice below the frame (the next successful redraw's
    ``\\033[J`` clears it), and returns False. Re-raises only after
    MAX_CONSECUTIVE_POLL_FAILURES consecutive failures, so callers' existing
    error paths still report a persistent outage. Returns True on success.
    """
    global _POLL_FAILURES
    try:
        fetch_fn()
        render_fn()
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        _POLL_FAILURES += 1
        if _POLL_FAILURES >= MAX_CONSECUTIVE_POLL_FAILURES:
            raise
        try:
            render_fn()
        except Exception:
            pass
        _write_poll_error(exc, _POLL_FAILURES)
        return False
    _POLL_FAILURES = 0
    return True


def _write_poll_error(exc, failures):
    """Show a single yellow retry line below the current frame."""
    msg = str(exc).replace("\n", " ")
    if len(msg) > 140:
        msg = msg[:137] + "..."
    sys.stdout.write(
        f"\n\033[K\033[33mpoll failed ({failures}/{MAX_CONSECUTIVE_POLL_FAILURES}), "
        f"retrying in next cycle: {msg}\033[0m"
    )
    sys.stdout.flush()


# ---------------------------------------------------------------------------
# Terminal / keyboard I/O (cbreak-mode single-key polling)
# ---------------------------------------------------------------------------
_TERM_ORIGINAL = None
_TERM_ENABLED = False


def setup_keyboard():
    """Put stdin into cbreak mode for non-blocking key polling; no-op off a tty."""
    global _TERM_ORIGINAL, _TERM_ENABLED
    if not _TERMIOS_OK or not sys.stdin.isatty():
        _TERM_ENABLED = False
        return False
    fd = sys.stdin.fileno()
    _TERM_ORIGINAL = termios.tcgetattr(fd)
    tty.setcbreak(fd)
    _TERM_ENABLED = True
    return True


def restore_terminal():
    """Restore original terminal settings saved by :func:`setup_keyboard`."""
    global _TERM_ORIGINAL, _TERM_ENABLED
    if _TERM_ORIGINAL is not None and _TERMIOS_OK and sys.stdin.isatty():
        try:
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, _TERM_ORIGINAL)
        except Exception:
            pass
    _TERM_ORIGINAL = None
    _TERM_ENABLED = False


# ESC-initiated terminal input: CSI (arrows, Home/End, F5+), SS3 (F1-F4), and
# Alt-modified chords. The trailing alternatives also swallow a sequence cut
# off at the end of a read so its tail bytes cannot masquerade as plain keys.
_ESC_SEQ_RE = re.compile(
    r"\x1b(?:\[[0-9;:<=>?]*[ -/]*[@-~]?|O.?|[^\[O])?"
)


def strip_escape_sequences(text):
    """Drop ANSI escape sequences, returning only plain keypresses.

    Engines bind printable keys (plus Ctrl-C) via substring checks, so without
    this the final byte of e.g. right-arrow (``ESC [ C``) would satisfy
    ``"c" in chars`` and trigger a drill-mode switch — a data-altering VMS
    monitor create — from a stray arrow key.
    """
    return _ESC_SEQ_RE.sub("", text)


def check_keypress():
    """Return any buffered plain keypresses (non-blocking), or '' when none/inactive.

    Drains everything currently buffered in one call so a multi-byte escape
    sequence is never split across polls, then strips escape sequences.
    """
    if not _TERM_ENABLED:
        return ""
    fd = sys.stdin.fileno()
    chunks = []
    while True:
        try:
            readable, _w, _e = select.select([fd], [], [], 0)
        except Exception:
            break
        if not readable:
            break
        try:
            data = os.read(fd, 1024)
        except Exception:
            break
        if not data:
            break
        chunks.append(data)
    if not chunks:
        return ""
    return strip_escape_sequences(b"".join(chunks).decode(errors="ignore"))


def input_pending():
    """True when stdin has unread keystrokes (non-blocking, zero timeout).

    Lets a long serial fetch cycle notice queued input BETWEEN API calls and
    yield back to the main loop early: on var203 one refresh cycle is several
    monitor queries at 2-38 s each, and a keypress used to wait for the whole
    cycle - an ``x`` went unprocessed for 150+ s in the round-3 lab run.
    False when keyboard polling is inactive (piped stdin, tests, non-POSIX),
    so fetch loops behave exactly as before in those environments.
    """
    if not _TERM_ENABLED:
        return False
    try:
        readable, _w, _e = select.select([sys.stdin.fileno()], [], [], 0)
        return bool(readable)
    except Exception:
        return False


def wait_for_input(timeout):
    """Block until stdin has data or *timeout* seconds elapse; True on input.

    Replaces the engines' previous ``time.sleep(0.05)`` spin loop: keys wake
    the loop instantly instead of after up to 50 ms, and an idle dashboard
    makes zero wakeups between refresh ticks instead of twenty per second.
    Falls back to a plain sleep when keyboard polling is inactive (piped
    stdin, non-POSIX). Signal handlers still run during the wait: SIGINT and
    SIGTERM raise out of ``select`` via the engines' handlers.
    """
    if timeout <= 0:
        return False
    if not _TERM_ENABLED:
        time.sleep(timeout)
        return False
    try:
        readable, _w, _e = select.select([sys.stdin.fileno()], [], [], timeout)
        return bool(readable)
    except Exception:
        time.sleep(min(timeout, 0.05))
        return False


def terminal_width(fallback, cap):
    """Visible columns for a rendered frame, capped at *cap*.

    Asks the terminal itself, NOT the environment. ``shutil.get_terminal_size``
    honours ``COLUMNS`` before the ioctl, and an exported stale COLUMNS then
    pins the width permanently: measured in a real pty, with COLUMNS=200 and
    the terminal resized 120 -> 80 -> 40, opstat kept emitting 120-column
    frames indefinitely, wrapping and corrupting the display. With COLUMNS
    unset the same sequence converged within one refresh tick.

    Only stdout is consulted, and ``sys.__stdout__`` first: ``render_screen``
    swaps ``sys.stdout`` for a StringIO while composing, so the real stream
    has to be reachable. stderr is deliberately NOT consulted - with the
    frame piped to a file and stderr still on a terminal, the terminal's
    width is not the output's width.

    Falls back to ``shutil`` when stdout is not a terminal: piped output and
    the render tests land there, and honouring COLUMNS is correct for a pipe.
    """
    for stream in (sys.__stdout__, sys.stdout):
        try:
            descriptor = stream.fileno()
        # AttributeError: sys.__stdout__ can be None (pythonw, frozen apps).
        # ValueError / io.UnsupportedOperation: StringIO or a closed file -
        # taken on EVERY frame, because render_screen swaps sys.stdout.
        except (AttributeError, ValueError, OSError):
            continue
        try:
            if not os.isatty(descriptor):
                continue
            columns = os.get_terminal_size(descriptor).columns
        except OSError:
            continue
        if columns > 0:
            return min(columns, cap)
    # A 0x0 winsize (script(1), some CI pty wrappers) reports 0 columns.
    # Python >= 3.11 coalesces that to the fallback; 3.8 - this project's
    # floor - does NOT, and returned a frame width of 0.
    columns = shutil.get_terminal_size((fallback, 40)).columns or fallback
    return min(columns, cap)


def clear_screen():
    """Clear the screen and home the cursor (used at startup/teardown)."""
    sys.stdout.write("\033[2J\033[H")
    sys.stdout.flush()
