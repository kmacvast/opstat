#!/usr/bin/env python3
"""Optional VMS REST API call logging for opstat (enabled via --log-api-calls)."""

import atexit
import json
import os
from datetime import datetime

_LOG_ENABLED = False
_LOG_PATH = None
_LOG_FILE = None
_MAX_BODY_CHARS = 2048


def configure(enabled, protocol, vms, port):
    """Open the API log file under /tmp when enabled."""
    global _LOG_ENABLED, _LOG_PATH, _LOG_FILE
    close()
    _LOG_ENABLED = bool(enabled)
    if not _LOG_ENABLED:
        return None
    safe_vms = "".join(c if c.isalnum() or c in ".-_" else "_" for c in str(vms))
    _LOG_PATH = os.path.join(
        "/tmp",
        f"opstat-api-{protocol}-{safe_vms}-{port}-{os.getpid()}.log",
    )
    # Create private (0600) so response bodies aren't world-readable in /tmp.
    fd = os.open(_LOG_PATH, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
    _LOG_FILE = os.fdopen(fd, "a", encoding="utf-8")
    atexit.register(close)
    _write_line(
        f"session start protocol={protocol} vms={vms} port={port} pid={os.getpid()}"
    )
    return _LOG_PATH


def log_path():
    """Return the active log file path, or None if logging is disabled."""
    return _LOG_PATH if _LOG_ENABLED else None


def close():
    """Close the log file handle."""
    global _LOG_FILE, _LOG_ENABLED, _LOG_PATH
    if _LOG_FILE is not None:
        try:
            _write_line("session end")
            _LOG_FILE.close()
        except Exception:
            pass
    _LOG_FILE = None
    _LOG_ENABLED = False
    _LOG_PATH = None


def _write_line(text):
    if _LOG_FILE is None:
        return
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _LOG_FILE.write(f"{stamp} {text}\n")
    _LOG_FILE.flush()


def _truncate(text):
    if text is None:
        return None
    if len(text) <= _MAX_BODY_CHARS:
        return text
    return f"{text[:_MAX_BODY_CHARS]}…({len(text)} bytes total)"


def _payload_summary(payload):
    if payload is None:
        return None
    try:
        return _truncate(json.dumps(payload, sort_keys=True))
    except (TypeError, ValueError):
        return _truncate(str(payload))


def log_call(method, url, payload, status_code, body_text, error, elapsed_ms):
    """Append one API request/response record."""
    if not _LOG_ENABLED:
        return
    parts = [method, url, f"{elapsed_ms:.0f}ms"]
    if payload is not None:
        summary = _payload_summary(payload)
        if summary:
            parts.append(f"payload={summary}")
    if error is not None:
        parts.append(f"ERROR {_truncate(str(error))}")
    elif status_code is not None:
        size = len(body_text) if body_text else 0
        parts.append(f"-> HTTP {status_code} ({size} bytes)")
        if body_text:
            parts.append(f"body={_truncate(body_text)}")
    _write_line(" ".join(parts))
