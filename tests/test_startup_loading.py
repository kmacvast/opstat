"""Startup loading interstitial (FR-D / Phase 9), all five engines.

Every engine's main() blocks for ~30 s on a real cluster (auth, cluster
resolution, monitor creation, first query) before painting anything. The fix
paints a status frame before each blocking startup step via
vast_drill.with_startup_status, so the message visibly changes as startup
progresses. These tests assert the ordering (a render precedes each blocking
call) and that the status is always cleared afterwards, including on error.

Modelled on tests/test_drill_loading.py, which asserts literal event ordering.
"""

from __future__ import annotations

import importlib

import pytest

# (engine module, its monitor-creation function name)
ENGINES = [
    ("nfs_v3", "create_headline_monitors"),
    ("nfs_v41", "create_headline_monitors"),
    ("smb", "create_headline_monitors"),
    ("s3", "create_headline_monitors"),
    ("nvme_tcp", "create_cluster_monitors"),
]


def _prime(module, monkeypatch, events, monitor_fn, monitor_raises=False):
    monkeypatch.setattr(module, "VMS", "10.0.0.1", raising=False)
    monkeypatch.setattr(module, "PORT", 443, raising=False)

    def get_cluster():
        events.append(("call", "get_current_cluster"))
        return 1, "c1"

    def monitors(*a, **k):
        events.append(("call", monitor_fn))
        if monitor_raises:
            raise RuntimeError("VMS refused the monitor")

    def fetch(*a, **k):
        events.append(("call", "fetch_monitor_query"))

    def render():
        events.append(("render", module.STARTUP_STATUS))

    monkeypatch.setattr(module, "get_current_cluster", get_cluster)
    monkeypatch.setattr(module, monitor_fn, monitors)
    monkeypatch.setattr(module, "fetch_monitor_query", fetch)
    monkeypatch.setattr(module, "render_screen", render)
    for optional in ("_capture_cluster_os", "ensure_csv_file"):
        if hasattr(module, optional):
            monkeypatch.setattr(module, optional, lambda *a, **k: None)
    if hasattr(module, "configure_volume_scope"):
        monkeypatch.setattr(module, "configure_volume_scope", lambda *a, **k: None)


@pytest.mark.parametrize("engine_name,monitor_fn", ENGINES)
def test_startup_paints_a_frame_before_each_blocking_step(engine_name, monitor_fn, monkeypatch):
    module = importlib.import_module(engine_name)
    events = []
    _prime(module, monkeypatch, events, monitor_fn)

    module.initialize()

    # The blocking calls happen in order. NVMe deliberately does NOT block
    # startup on the first query cycle: on var203 that cycle is ~80 s of
    # serial queries and awaiting it pushed first paint to 166 s with keys
    # dead. Its "Gathering" status persists on the rendered waiting frame
    # and poll_tick clears it when the first cycle lands.
    calls = [d for k, d in events if k == "call"]
    if engine_name == "nvme_tcp":
        assert calls == ["get_current_cluster", monitor_fn], calls
    else:
        assert calls == ["get_current_cluster", monitor_fn, "fetch_monitor_query"], calls
    # ...and every one of them is immediately preceded by a rendered frame whose
    # status was already set (the user sees what the process is waiting on).
    for i, (kind, _detail) in enumerate(events):
        if kind == "call":
            assert i > 0 and events[i - 1][0] == "render", (
                "no frame rendered before %s" % _detail)
            assert events[i - 1][1], "status not set before %s" % _detail
    # The first frame names the host (cluster name is unknown that early).
    assert events[0] == ("render", None) or "Connecting" in (events[0][1] or ""), events[0]
    if engine_name == "nvme_tcp":
        # The dashboard appears with the Gathering status still visible on
        # the footer-owning waiting frame; the first poll cycle clears it.
        assert module.STARTUP_STATUS is not None
        assert "Gathering" in module.STARTUP_STATUS
        assert events[-1][0] == "render", "no frame rendered with the Gathering status"
        module.STARTUP_STATUS = None
    else:
        # Status is cleared once startup finishes.
        assert module.STARTUP_STATUS is None


@pytest.mark.parametrize("engine_name,monitor_fn", ENGINES)
def test_startup_status_cleared_even_when_a_step_raises(engine_name, monitor_fn, monkeypatch):
    module = importlib.import_module(engine_name)
    events = []
    _prime(module, monkeypatch, events, monitor_fn, monitor_raises=True)

    with pytest.raises(RuntimeError):
        module.initialize()
    assert module.STARTUP_STATUS is None, "startup status left behind after an error"


@pytest.mark.parametrize("engine_name", [e for e, _ in ENGINES])
def test_first_startup_message_names_the_host_not_the_cluster(engine_name, monkeypatch):
    """CLUSTER_NAME is None until get_current_cluster() returns, so the first
    message must reference the VMS host:port, never the cluster."""
    module = importlib.import_module(engine_name)
    events = []
    _prime(module, monkeypatch, events, dict(ENGINES)[engine_name])
    module.initialize()
    first_status = next(d for k, d in events if k == "render" and d)
    assert "10.0.0.1:443" in first_status, first_status
    assert "Connecting" in first_status, first_status
