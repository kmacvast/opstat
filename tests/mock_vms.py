#!/usr/bin/env python3
"""In-process mock VAST VMS REST API for exercising the opstat TUI end to end.

Serves just enough of the VMS surface (``/clusters/``, ``/monitors/``,
``/cnodes/``, ``/views/``, ``/tenants/``, ``/volumes/``, ``/metrics/`` ...) over
HTTPS with a self-signed certificate so a real ``opstat`` process can be driven
against it. Every request is recorded, which makes the mock the measurement
instrument for API-call accounting as well as a functional test double.

Run standalone::

    python3 tests/mock_vms.py --port 8443

Or embed::

    with MockVMS() as vms:
        ...
        print(vms.counts())
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ---------------------------------------------------------------------------
# Synthetic cluster inventory
# ---------------------------------------------------------------------------
CLUSTERS = [
    {
        "id": 1,
        "name": "mock-cluster",
        "local": True,
        "sw_version": "5.4.3.1.14178074658457882785",
        "guid": "mock-guid",
    }
]

CNODES = [
    {"id": 100 + i, "name": f"cnode-{i:02d}", "hostname": f"cnode-{i:02d}.mock",
     "mgmt_ip": f"10.0.0.{10 + i}"}
    for i in range(12)
]

# Deliberately large so the drill ranking path has to chunk (32 per chunk).
VIEWS = [
    {"id": 1000 + i, "path": f"/view/{i:03d}", "name": f"view-{i:03d}",
     "title": f"view-{i:03d}"}
    for i in range(400)
]

TENANTS = [{"id": 10 + i, "name": f"tenant-{i}"} for i in range(6)]

VOLUMES = [
    {"id": 500 + i, "name": f"vol-{i:02d}", "subsystem_name": f"subsys-{i % 3}",
     "size": 1 << 40}
    for i in range(24)
]

VIPS = [{"id": 700 + i, "ip": f"10.1.0.{i + 1}", "name": f"vip-{i}"} for i in range(4)]


def _metric_value(prop, seed, t):
    """Deterministic-but-lively synthetic value for a monitor property."""
    h = 0
    for ch in prop:
        h = (h * 31 + ord(ch)) & 0xFFFF
    base = (h % 997) + 1
    wobble = 1.0 + 0.25 * math.sin((t + seed) / 7.0 + h)
    if "latency" in prop and "__avg" in prop:
        return round(base * 3.0 * wobble, 3)
    if "_bw" in prop or "bw__" in prop:
        return round(base * 5.0e6 * wobble, 1)
    if "__sum" in prop or "num_samples" in prop:
        # Cumulative counters must grow monotonically for delta-rate math.
        return round(base * 1000.0 + t * base * 3.0, 1)
    return round(base * 0.5 * wobble, 3)


class _State:
    def __init__(self):
        self.lock = threading.Lock()
        self.monitors = {}
        self.next_monitor_id = 9000
        self.calls = []          # [(monotonic_ts, method, path, status)]
        self.connections = 0     # TCP connections accepted (keep-alive metric)
        self.open_sockets = set()  # live sockets, severed on stop()
        self.reject_granularity_auto = False
        # Prop-name prefixes this "cluster build" does not export: they are
        # accepted at create time but omitted from query prop_lists, matching
        # how engines probe real VMS capability differences.
        self.unsupported_prop_prefixes = ()
        # When True, POST /monitors/ rejects prop_lists that mix metric
        # families (text before the first comma), for fallback-path testing.
        self.reject_mixed_families = False
        # Prop-name prefixes that make POST /monitors/ fail outright, for
        # engines whose fallback path reacts to a rejected create.
        self.reject_prop_prefixes = ()
        self.latency_s = 0.0
        self.t0 = time.time()


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    state: _State = None  # injected

    # -- plumbing ----------------------------------------------------------
    def setup(self):
        super().setup()
        with self.state.lock:
            self.state.connections += 1
            self.state.open_sockets.add(self.connection)

    def finish(self):
        with self.state.lock:
            self.state.open_sockets.discard(self.connection)
        super().finish()

    def log_message(self, *_args):  # silence stderr noise
        pass

    def _record(self, status):
        with self.state.lock:
            self.state.calls.append(
                (time.monotonic(), self.command, self.path, status)
            )

    def _send(self, payload, status=200):
        body = b"" if payload is None else json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)
        self._record(status)

    def _error(self, status, detail):
        self._send({"detail": detail}, status)

    def _read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return None
        try:
            return json.loads(self.rfile.read(length).decode())
        except ValueError:
            return None

    # -- routing -----------------------------------------------------------
    def do_GET(self):
        if self.state.latency_s:
            time.sleep(self.state.latency_s)
        path = urllib.parse.urlsplit(self.path).path
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)

        simple = {
            "/api/clusters/": CLUSTERS,
            "/api/cnodes/": CNODES,
            "/api/views/": VIEWS,
            "/api/tenants/": TENANTS,
            "/api/volumes/": VOLUMES,
            "/api/vips/": VIPS,
        }
        if path in simple:
            return self._send(simple[path])

        if path == "/api/metrics/":
            state_ops = (
                "open", "close", "lock", "locku", "sequence",
                "delegreturn", "exchange_id", "create_session",
            )
            return self._send({"data": [
                {"metric": "NfsMetrics,nfs_read_latency__rate"},
                {"metric": "SMBCommon,md_iops"},
                {"metric": "S3Common,rd_bw"},
            ] + [
                {"metric": f"NfsMetrics,nfs_{op}_latency__rate"}
                for op in state_ops
            ]})

        if path == "/api/openfilehandles/":
            return self._send({"results": [
                {"id": i, "path": f"/share/file{i}", "client_ip": "10.2.0.5"}
                for i in range(8)
            ]})

        if path == "/api/monitors/topn/":
            def block(prefix):
                return [{"title": f"{prefix}{i}", "value": 1000.0 - i * 37}
                        for i in range(10)]
            return self._send({"data": {
                "client": {"md_iops": block("10.2.0."), "iops": block("10.2.0."),
                           "bw": block("10.2.0."), "latency": block("10.2.0.")},
                "view": {"md_iops": block("/view/"), "iops": block("/view/"),
                         "bw": block("/view/")},
                "vip": {"iops": block("10.1.0."), "bw": block("10.1.0."),
                        "latency": block("10.1.0.")},
                "user": {"md_iops": block("user")},
            }})

        if path == "/api/clusters/list_smb_client_connections/":
            return self._send({"connections": [
                {"client_ip": (query.get("client_ip") or ["?"])[0],
                 "user": "mock", "share": "data"}
            ]})

        m = re.match(r"^/api/monitors/(\d+)/query/$", path)
        if m:
            return self._monitor_query(int(m.group(1)))

        return self._error(404, f"no mock route for {path}")

    def do_POST(self):
        if self.state.latency_s:
            time.sleep(self.state.latency_s)
        path = urllib.parse.urlsplit(self.path).path
        payload = self._read_body() or {}
        if path != "/api/monitors/":
            return self._error(404, f"no mock route for {path}")
        if self.state.reject_granularity_auto and payload.get("granularity") == "auto":
            return self._error(400, "Invalid granularity: auto")
        if self.state.reject_mixed_families:
            families = {str(p).split(",", 1)[0] for p in payload.get("prop_list") or []}
            if len(families) > 1:
                return self._error(400, "cannot mix metric families in one monitor")
        if self.state.reject_prop_prefixes:
            for p in payload.get("prop_list") or []:
                if str(p).startswith(tuple(self.state.reject_prop_prefixes)):
                    return self._error(400, f"unsupported metric: {p}")
        with self.state.lock:
            monitor_id = self.state.next_monitor_id
            self.state.next_monitor_id += 1
            self.state.monitors[monitor_id] = {
                "prop_list": list(payload.get("prop_list") or []),
                "object_ids": list(payload.get("object_ids") or []),
                "object_type": payload.get("object_type"),
            }
        return self._send({"id": monitor_id, "name": payload.get("name")}, 201)

    def do_DELETE(self):
        if self.state.latency_s:
            time.sleep(self.state.latency_s)
        path = urllib.parse.urlsplit(self.path).path
        m = re.match(r"^/api/monitors/(\d+)/$", path)
        if not m:
            return self._error(404, f"no mock route for {path}")
        monitor_id = int(m.group(1))
        with self.state.lock:
            existed = self.state.monitors.pop(monitor_id, None)
        if existed is None:
            return self._error(404, "monitor not found")
        return self._send({"deleted": monitor_id})

    # -- monitor query synthesis -------------------------------------------
    def _monitor_query(self, monitor_id):
        with self.state.lock:
            mon = self.state.monitors.get(monitor_id)
        if mon is None:
            return self._error(404, "monitor not found")

        props = [
            p for p in mon["prop_list"]
            if not str(p).startswith(tuple(self.state.unsupported_prop_prefixes))
        ] if self.state.unsupported_prop_prefixes else mon["prop_list"]
        object_ids = mon["object_ids"] or [None]
        batch = len(object_ids) > 1
        prop_list = ["timestamp"] + (["object_id"] if batch else []) + props

        now = time.time() - self.state.t0
        rows = []
        # Newest first, matching the VMS ordering the engines assume.
        for step in range(12):
            t = now - step * 5.0
            stamp = time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.state.t0 + t)
            )
            for oid in object_ids:
                seed = 0 if oid is None else (int(oid) if str(oid).isdigit() else 0)
                row = [stamp]
                if batch:
                    row.append(oid)
                row.extend(_metric_value(p, seed, t) for p in props)
                rows.append(row)
        return self._send({"prop_list": prop_list, "data": rows})


def _self_signed_cert(directory):
    """Return (cert_path, key_path), generating a throwaway pair if needed."""
    cert = os.path.join(directory, "mock-vms.pem")
    if os.path.exists(cert):
        return cert, cert
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
         "-keyout", cert, "-out", cert, "-days", "2",
         "-subj", "/CN=localhost"],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return cert, cert


class _QuietServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        # Broken pipes are expected when stop() severs keep-alive sockets.
        pass


class MockVMS:
    """Threaded HTTPS mock VMS. Use as a context manager."""

    def __init__(self, port=0, certdir=None):
        self.state = _State()
        self._certdir = certdir or tempfile.gettempdir()
        handler = type("BoundHandler", (_Handler,), {"state": self.state})
        self._server = _QuietServer(("127.0.0.1", port), handler)
        cert, key = _self_signed_cert(self._certdir)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, key)
        self._server.socket = ctx.wrap_socket(self._server.socket, server_side=True)
        self.port = self._server.server_address[1]
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        kwargs={"poll_interval": 0.05}, daemon=True)
        self._thread.start()
        return self

    def stop(self):
        self._server.shutdown()
        self._server.server_close()
        # Sever established keep-alive connections so a stopped mock actually
        # looks like an outage to a client holding a persistent socket.
        with self.state.lock:
            sockets = list(self.state.open_sockets)
            self.state.open_sockets.clear()
        for sock in sockets:
            # shutdown(), not close(): the handler thread holds makefile()
            # references, so close() alone defers the FIN and the client
            # would keep getting served after the "outage".
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            try:
                sock.close()
            except Exception:
                pass
        if self._thread:
            self._thread.join(timeout=5)

    def __enter__(self):
        return self.start()

    def __exit__(self, *_exc):
        self.stop()

    # -- measurement -------------------------------------------------------
    def calls(self):
        with self.state.lock:
            return list(self.state.calls)

    def reset_calls(self):
        with self.state.lock:
            self.state.calls.clear()

    def counts(self):
        """Return {"METHOD normalized-path": count} with ids collapsed."""
        out = {}
        for _ts, method, path, _status in self.calls():
            norm = re.sub(r"/\d+/", "/{id}/", urllib.parse.urlsplit(path).path)
            key = f"{method} {norm}"
            out[key] = out.get(key, 0) + 1
        return out

    def live_monitors(self):
        with self.state.lock:
            return dict(self.state.monitors)

    def connection_count(self):
        with self.state.lock:
            return self.state.connections


def main(argv=None):
    ap = argparse.ArgumentParser(description="Standalone mock VAST VMS API")
    ap.add_argument("--port", type=int, default=8443)
    args = ap.parse_args(argv)
    vms = MockVMS(port=args.port).start()
    print(f"mock VMS listening on https://127.0.0.1:{vms.port}/api", flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        vms.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
