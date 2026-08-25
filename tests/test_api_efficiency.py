"""API-efficiency and TUI-loop regression tests.

These guard the performance-critical behaviors introduced by the TUI
performance refactor:

- one keep-alive connection is reused across REST calls (with a single
  retry on a stale socket),
- startup fetches /clusters/ exactly once per engine,
- the merged headline monitors keep per-refresh query counts at 1 for the
  NFS engines, with a working fallback to the historical split monitors,
- the main-loop wait helper sleeps instead of spinning,
- run-stat sample dedupe memory stays bounded.

The integration tests drive the real transport against tests/mock_vms.py
over TLS on loopback.
"""

from __future__ import annotations

import shutil
import ssl
import time
from types import SimpleNamespace

import pytest

import vast_common
from tests.mock_vms import MockVMS

pytestmark = pytest.mark.skipif(
    shutil.which("openssl") is None,
    reason="openssl binary required to generate the mock VMS certificate",
)


@pytest.fixture
def vms(tmp_path):
    server = MockVMS(certdir=str(tmp_path)).start()
    yield server
    server.stop()


@pytest.fixture
def transport(vms):
    headers = {
        "Authorization": "Bearer test-token",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "opstat-tests",
    }
    vast_common.configure_connection(
        f"https://127.0.0.1:{vms.port}/api",
        headers,
        ssl._create_unverified_context(),
    )
    yield vms
    vast_common.close_connection()


def _engine_args(vms, **overrides):
    base = dict(
        vms="127.0.0.1", port=vms.port, user="admin", password=None,
        sample_average=None, refresh=5, csv=None, no_color=True,
        discover_metrics=False, log_api_calls=False,
        export_openmetrics=False, openmetrics_file=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


# ---------------------------------------------------------------------------
# Transport: keep-alive, retry, error mapping
# ---------------------------------------------------------------------------
def test_requests_reuse_one_connection(transport):
    vms = transport
    for _ in range(10):
        vast_common.request("GET", "/clusters/")
    assert len(vms.calls()) == 10
    assert vms.connection_count() == 1


def test_stale_connection_retries_once(transport, monkeypatch):
    vms = transport
    vast_common.request("GET", "/clusters/")  # establish the connection

    real_send = vast_common._send_once
    failures = {"n": 0}

    def flaky_send(method, path, data, base=None):
        if failures["n"] == 0:
            failures["n"] += 1
            raise BrokenPipeError("server idled out the keep-alive socket")
        return real_send(method, path, data, base=base)

    monkeypatch.setattr(vast_common, "_send_once", flaky_send)
    result = vast_common.request("GET", "/clusters/")
    assert result[0]["name"] == "mock-cluster"
    assert failures["n"] == 1


def test_fresh_connection_failure_is_not_retried(transport, monkeypatch):
    calls = {"n": 0}

    def always_fail(method, path, data, base=None):
        calls["n"] += 1
        raise ConnectionError("VMS unreachable")

    vast_common.close_connection()  # ensure no reused socket
    monkeypatch.setattr(vast_common, "_send_once", always_fail)
    with pytest.raises(RuntimeError):
        vast_common.request("GET", "/clusters/")
    assert calls["n"] == 1


def test_http_error_maps_to_runtime_error(transport):
    with pytest.raises(RuntimeError) as exc:
        vast_common.request("GET", "/no/such/route/")
    assert "HTTP 404" in str(exc.value)


# ---------------------------------------------------------------------------
# Startup call dedupe
# ---------------------------------------------------------------------------
def test_cluster_and_os_resolved_with_one_request():
    calls = []

    def fake_request(method, path, payload=None):
        calls.append((method, path))
        return [{"id": 7, "name": "c1", "local": True, "sw_version": "5.3.0.1"}]

    cluster_id, name = vast_common.get_current_cluster(fake_request)
    os_version = vast_common.get_current_cluster_os(fake_request)
    assert (cluster_id, name) == (7, "c1")
    assert os_version == "5.3.0.1"
    assert calls == [("GET", "/clusters/")]


def test_cluster_os_still_fetches_without_cached_record():
    calls = []

    def fake_request(method, path, payload=None):
        calls.append(path)
        return [{"id": 1, "name": "c", "local": True, "os_version": "5.2"}]

    vast_common.reset_registry()  # clears the cached record
    assert vast_common.get_current_cluster_os(fake_request) == "5.2"
    assert calls == ["/clusters/"]


# ---------------------------------------------------------------------------
# NFS v3: merged headline monitor
# ---------------------------------------------------------------------------
def _init_nfs_v3(vms, monkeypatch):
    import nfs_v3

    monkeypatch.setenv("VAST_TOKEN", "test-token")
    nfs_v3.init_config(_engine_args(vms))
    nfs_v3.CLUSTER_ID, nfs_v3.CLUSTER_NAME = nfs_v3.get_current_cluster()
    return nfs_v3


def test_nfs_v3_startup_and_refresh_call_budget(vms, monkeypatch):
    nfs_v3 = _init_nfs_v3(vms, monkeypatch)
    nfs_v3.create_headline_monitors()
    assert nfs_v3.RPC_MONITOR_ID == nfs_v3.BW_MONITOR_ID  # merged

    startup = vms.counts()
    assert startup.get("GET /api/clusters/") == 1
    assert startup.get("POST /api/monitors/") == 1

    vms.reset_calls()
    nfs_v3.fetch_monitor_query()
    per_refresh = vms.counts()
    assert per_refresh == {"GET /api/monitors/{id}/query/": 1}
    assert nfs_v3.LAST_ROWS, "merged monitor produced no rows"
    read = next(r for r in nfs_v3.LAST_ROWS if r["label"] == "READ")
    assert read["bw_gbs"] is not None, "BW props missing from merged fetch"

    nfs_v3.cleanup()
    assert vms.live_monitors() == {}, "monitors leaked after cleanup"
    nfs_v3._CLEANED_UP = False  # reset module flag for other tests


def test_nfs_v3_falls_back_to_split_monitors(vms, monkeypatch):
    vms.state.reject_mixed_families = True
    nfs_v3 = _init_nfs_v3(vms, monkeypatch)
    nfs_v3.create_headline_monitors()
    assert nfs_v3.RPC_MONITOR_ID != nfs_v3.BW_MONITOR_ID
    assert nfs_v3.RPC_MONITOR_ID is not None
    assert nfs_v3.BW_MONITOR_ID is not None

    vms.reset_calls()
    nfs_v3.fetch_monitor_query()
    assert vms.counts() == {"GET /api/monitors/{id}/query/": 2}

    nfs_v3.cleanup()
    assert vms.live_monitors() == {}
    nfs_v3._CLEANED_UP = False


def test_nfs_v3_falls_back_when_bw_family_not_exported(vms, monkeypatch):
    vms.state.unsupported_prop_prefixes = ("ProtoMetrics,",)
    nfs_v3 = _init_nfs_v3(vms, monkeypatch)
    nfs_v3.create_headline_monitors()
    # BW family missing from the merged probe -> split layout.
    assert nfs_v3.RPC_MONITOR_ID != nfs_v3.BW_MONITOR_ID
    nfs_v3.cleanup()
    nfs_v3._CLEANED_UP = False


# ---------------------------------------------------------------------------
# NFS v4.1: merged headline monitor
# ---------------------------------------------------------------------------
def _init_nfs_v41(vms, monkeypatch):
    import nfs_v41

    monkeypatch.setenv("VAST_TOKEN", "test-token")
    nfs_v41.init_config(_engine_args(vms))
    nfs_v41.CLUSTER_ID, nfs_v41.CLUSTER_NAME = nfs_v41.get_current_cluster()
    return nfs_v41


def test_nfs_v41_merged_monitor_single_query_per_refresh(vms, monkeypatch):
    nfs_v41 = _init_nfs_v41(vms, monkeypatch)
    nfs_v41.create_headline_monitors()
    assert nfs_v41.DATA_MONITOR_ID == nfs_v41.SUPPLEMENT_MONITOR_ID
    assert nfs_v41.DATA_MONITOR_ID == nfs_v41.BW_MONITOR_ID
    assert nfs_v41.DATA_MONITOR_ID == nfs_v41.META_MONITOR_ID
    assert nfs_v41.STATE_MONITOR_ID == nfs_v41.DATA_MONITOR_ID
    assert nfs_v41.STATE_OPS_AVAILABLE

    vms.reset_calls()
    nfs_v41.fetch_monitor_query()
    assert vms.counts() == {"GET /api/monitors/{id}/query/": 1}
    assert nfs_v41.LAST_ROWS.get("data"), "merged monitor produced no data rows"

    nfs_v41.cleanup()
    assert vms.live_monitors() == {}
    nfs_v41._CLEANED_UP = False


def test_nfs_v41_falls_back_to_split_monitors(vms, monkeypatch):
    vms.state.reject_mixed_families = True
    nfs_v41 = _init_nfs_v41(vms, monkeypatch)
    nfs_v41.create_headline_monitors()
    ids = {
        nfs_v41.DATA_MONITOR_ID, nfs_v41.SUPPLEMENT_MONITOR_ID,
        nfs_v41.BW_MONITOR_ID, nfs_v41.META_MONITOR_ID,
    }
    assert len(ids) == 4, "split fallback should create four distinct monitors"
    assert None not in ids

    vms.reset_calls()
    nfs_v41.fetch_monitor_query()
    counts = vms.counts()
    # 4 split monitors + optional state monitor
    expected = 4 + (1 if nfs_v41.STATE_MONITOR_ID else 0)
    assert counts == {"GET /api/monitors/{id}/query/": expected}

    nfs_v41.cleanup()
    assert vms.live_monitors() == {}
    nfs_v41._CLEANED_UP = False


def test_nfs_v41_drill_mode_tracks_and_queries_monitors(vms, monkeypatch):
    """Regression: enter_drill_mode() dropped its monitors into a function
    local (missing ``global DRILL_MONITORS``), so the drill panel never
    received data and the created monitors leaked until process exit."""
    nfs_v41 = _init_nfs_v41(vms, monkeypatch)
    nfs_v41.create_headline_monitors()

    # cnode: the one monitor-backed NFSv4.1 drill since FR14 ([v]/[t] are
    # exporter-backed and create no monitors at all).
    nfs_v41.enter_drill_mode("cnode")
    assert nfs_v41.DRILL_MODE == "cnode"
    assert nfs_v41.DRILL_ERROR is None
    assert nfs_v41.DRILL_MONITORS, "drill monitors must be tracked module-wide"

    nfs_v41.fetch_drill_query(force=True)
    # One batch monitor now covers every drill object.
    assert len(nfs_v41.LAST_DRILL_ROWS) == len(nfs_v41.DRILL_OBJECTS)

    drill_ids = {mid for mid, _name in nfs_v41.DRILL_MONITORS}
    nfs_v41.exit_drill_mode()
    live = set(vms.live_monitors())
    assert not (drill_ids & live), "exit_drill_mode must delete drill monitors"

    nfs_v41.cleanup()
    assert vms.live_monitors() == {}
    nfs_v41._CLEANED_UP = False


def test_nfs_v41_merges_without_state_when_state_unsupported(vms, monkeypatch):
    vms.state.unsupported_prop_prefixes = ("NfsMetrics,nfs_open",)
    nfs_v41 = _init_nfs_v41(vms, monkeypatch)
    # Only some state candidates unsupported: metric catalog trims them.
    nfs_v41.create_headline_monitors()
    assert nfs_v41.DATA_MONITOR_ID == nfs_v41.BW_MONITOR_ID  # still merged
    nfs_v41.cleanup()
    nfs_v41._CLEANED_UP = False


# ---------------------------------------------------------------------------
# SMB / S3: merged headline + probe monitor
# ---------------------------------------------------------------------------
def test_smb_merged_monitor_single_query_per_refresh(vms, monkeypatch, reset_smb_globals):
    import smb

    monkeypatch.setenv("VAST_TOKEN", "test-token")
    smb.init_config(_engine_args(vms, clients=None))
    smb.CLUSTER_ID, smb.CLUSTER_NAME = smb.get_current_cluster()
    smb.create_headline_monitors()
    assert smb.HEADLINE_MONITOR_ID is not None
    assert smb.SMB_COMMAND_MONITOR_ID == smb.HEADLINE_MONITOR_ID
    assert smb.SMB_PER_COMMAND_EXPORTED is True

    vms.reset_calls()
    smb.fetch_monitor_query()
    counts = vms.counts()
    assert counts.get("GET /api/monitors/{id}/query/") == 1
    assert smb.LAST_ROWS.get("data")

    smb.cleanup()
    assert vms.live_monitors() == {}
    smb._CLEANED_UP = False


def test_smb_falls_back_to_split_monitors(vms, monkeypatch, reset_smb_globals):
    """Clusters that 400 on SmbMetrics props in a monitor: the merged create
    fails, the split headline succeeds, and the command probe disables
    per-opcode rows gracefully."""
    import smb

    vms.state.reject_prop_prefixes = ("SmbMetrics,",)
    monkeypatch.setenv("VAST_TOKEN", "test-token")
    smb.init_config(_engine_args(vms, clients=None))
    smb.CLUSTER_ID, smb.CLUSTER_NAME = smb.get_current_cluster()
    smb.create_headline_monitors()
    assert smb.HEADLINE_MONITOR_ID is not None
    assert smb.SMB_COMMAND_MONITOR_ID is None
    assert smb.SMB_PER_COMMAND_EXPORTED is False

    vms.reset_calls()
    smb.fetch_monitor_query()
    assert vms.counts().get("GET /api/monitors/{id}/query/") == 1
    smb.cleanup()
    assert vms.live_monitors() == {}
    smb._CLEANED_UP = False


def test_smb_probe_disabled_when_smbmetrics_not_exported(vms, monkeypatch, reset_smb_globals):
    import smb

    vms.state.unsupported_prop_prefixes = ("SmbMetrics,",)
    monkeypatch.setenv("VAST_TOKEN", "test-token")
    smb.init_config(_engine_args(vms, clients=None))
    smb.CLUSTER_ID, smb.CLUSTER_NAME = smb.get_current_cluster()
    smb.create_headline_monitors()
    assert smb.HEADLINE_MONITOR_ID is not None
    assert smb.SMB_COMMAND_MONITOR_ID is None
    assert smb.SMB_PER_COMMAND_EXPORTED is False
    smb.cleanup()
    smb._CLEANED_UP = False


def test_s3_merged_monitor_single_query_per_refresh(vms, monkeypatch, reset_s3_globals):
    import s3

    monkeypatch.setenv("VAST_TOKEN", "test-token")
    s3.init_config(_engine_args(vms, buckets=None, tenants=None))
    s3.CLUSTER_ID, s3.CLUSTER_NAME = s3.get_current_cluster()
    s3.create_headline_monitors()
    assert s3.HEADLINE_MONITOR_ID is not None
    assert s3.S3_METRICS_MONITOR_ID == s3.HEADLINE_MONITOR_ID
    assert s3.S3_METRICS_EXPORTED is True
    assert s3.METRICS_SOURCE == "S3Common"

    vms.reset_calls()
    s3.fetch_monitor_query()
    counts = vms.counts()
    assert counts.get("GET /api/monitors/{id}/query/") == 1
    assert s3.LAST_ROWS.get("data")

    s3.cleanup()
    assert vms.live_monitors() == {}
    s3._CLEANED_UP = False


def test_s3_falls_back_when_s3metrics_not_exported(vms, monkeypatch, reset_s3_globals):
    import s3

    vms.state.unsupported_prop_prefixes = ("S3Metrics,",)
    monkeypatch.setenv("VAST_TOKEN", "test-token")
    s3.init_config(_engine_args(vms, buckets=None, tenants=None))
    s3.CLUSTER_ID, s3.CLUSTER_NAME = s3.get_current_cluster()
    s3.create_headline_monitors()
    assert s3.HEADLINE_MONITOR_ID is not None
    assert s3.S3_METRICS_MONITOR_ID is None
    assert s3.S3_METRICS_EXPORTED is False
    s3.cleanup()
    s3._CLEANED_UP = False


# ---------------------------------------------------------------------------
# Main-loop wait helper
# ---------------------------------------------------------------------------
def test_wait_for_input_sleeps_off_tty(monkeypatch):
    monkeypatch.setattr(vast_common, "_TERM_ENABLED", False)
    start = time.monotonic()
    woke = vast_common.wait_for_input(0.05)
    elapsed = time.monotonic() - start
    assert woke is False
    assert elapsed >= 0.04


def test_wait_for_input_zero_timeout_returns_immediately():
    start = time.monotonic()
    assert vast_common.wait_for_input(0) is False
    assert vast_common.wait_for_input(-1) is False
    assert time.monotonic() - start < 0.02


def test_wait_for_input_wakes_on_input(monkeypatch):
    import os
    import sys

    r_fd, w_fd = os.pipe()
    monkeypatch.setattr(vast_common, "_TERM_ENABLED", True)

    class FakeStdin:
        def fileno(self):
            return r_fd

    monkeypatch.setattr(sys, "stdin", FakeStdin())
    os.write(w_fd, b"q")
    start = time.monotonic()
    woke = vast_common.wait_for_input(1.0)
    elapsed = time.monotonic() - start
    os.close(r_fd)
    os.close(w_fd)
    assert woke is True
    assert elapsed < 0.5, "input should wake the wait immediately"


# ---------------------------------------------------------------------------
# Bounded run-stat dedupe
# ---------------------------------------------------------------------------
def test_remember_sample_dedupes_and_stays_bounded():
    import nfs_v3

    seen = {}
    assert nfs_v3._remember_sample(seen, "a") is True
    assert nfs_v3._remember_sample(seen, "a") is False
    for i in range(nfs_v3._MAX_SEEN_SAMPLE_IDS * 2):
        nfs_v3._remember_sample(seen, f"id-{i}")
    assert len(seen) <= nfs_v3._MAX_SEEN_SAMPLE_IDS
    # Recent ids are retained; ancient ones were evicted.
    assert nfs_v3._remember_sample(seen, f"id-{nfs_v3._MAX_SEEN_SAMPLE_IDS * 2 - 1}") is False
    assert nfs_v3._remember_sample(seen, "a") is True
