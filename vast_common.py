#!/usr/bin/env python3
"""Shared lifecycle, signal, and rendering helpers for opstat engines.

Centralizes the cross-cutting concerns that were previously copy-pasted into
each protocol engine: VMS monitor teardown tracking, signal/atexit wiring
(including SIGHUP), local-cluster selection, and flicker-free frame flushing.
"""

import atexit
import base64
import getpass
import json
import os
import re
import select
import signal
import sys
import time
import urllib.error
import urllib.request

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
_BASE_URL = None
_HEADERS = None
_SSL_CTX = None
_TIMEOUT = 60


def configure_connection(base_url, headers, ssl_ctx, timeout=60):
    """Store the VMS connection context used by :func:`request`."""
    global _BASE_URL, _HEADERS, _SSL_CTX, _TIMEOUT
    _BASE_URL = base_url
    _HEADERS = headers
    _SSL_CTX = ssl_ctx
    _TIMEOUT = timeout


def request(method, path, payload=None):
    """Issue an authenticated VMS REST request; log every call via vast_api_log.

    Raises RuntimeError on any HTTP or transport error (never leaks the raw
    urllib exception type to callers).
    """
    url = f"{_BASE_URL}{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=_HEADERS, method=method)
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, context=_SSL_CTX, timeout=_TIMEOUT) as resp:
            body = resp.read().decode()
            elapsed_ms = (time.monotonic() - started) * 1000
            vast_api_log.log_call(method, url, payload, resp.status, body, None, elapsed_ms)
            return json.loads(body) if body else None
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        elapsed_ms = (time.monotonic() - started) * 1000
        err = f"HTTP {e.code}: {body}"
        vast_api_log.log_call(method, url, payload, e.code, body, err, elapsed_ms)
        raise RuntimeError(f"{method} {url} failed: {err}") from e
    except Exception as e:
        elapsed_ms = (time.monotonic() - started) * 1000
        vast_api_log.log_call(method, url, payload, None, None, e, elapsed_ms)
        raise RuntimeError(f"{method} {url} failed: {e}") from e


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


def get_current_cluster(request_fn):
    """Return (cluster_id, cluster_name) for the active/local cluster.

    Read-only. ``request_fn`` is the engine's ``api_request`` so unit tests that
    patch it continue to intercept the call.
    """
    data = request_fn("GET", "/clusters/")
    clusters = normalize_list_response(data)
    if not clusters:
        raise RuntimeError(f"No clusters returned from /clusters/: {data}")
    cluster = select_local_cluster(clusters)
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

    Read-only and defensive: the OS label is a cosmetic header adornment, so any
    failure (network, missing field) degrades to None rather than raising.
    """
    try:
        data = request_fn("GET", "/clusters/")
        cluster = select_local_cluster(normalize_list_response(data))
        return os_release_from_cluster(cluster)
    except Exception:
        return None


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


def drain_monitors(delete_fn):
    """Delete every still-registered monitor using engine-supplied delete_fn."""
    for monitor_id in list(_CREATED_MONITORS):
        delete_fn(monitor_id)
        _CREATED_MONITORS.discard(monitor_id)


def record_failed_delete(monitor_id, detail):
    """Note a DELETE that failed for a non-404 reason, for exit reporting."""
    _FAILED_DELETES.append((monitor_id, detail))


def failed_deletes():
    """Return list of (monitor_id, detail) for deletes that truly failed."""
    return list(_FAILED_DELETES)


def reset_registry():
    """Clear registry + failure log (used between sessions and in tests)."""
    global _POLL_FAILURES
    _CREATED_MONITORS.clear()
    _FAILED_DELETES.clear()
    _POLL_FAILURES = 0


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


def keyboard_enabled():
    """Return True when cbreak keyboard polling is active."""
    return _TERM_ENABLED


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


def clear_screen():
    """Clear the screen and home the cursor (used at startup/teardown)."""
    sys.stdout.write("\033[2J\033[H")
    sys.stdout.flush()
