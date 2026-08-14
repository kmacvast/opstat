"""SMB / S3 drill-down port to vast_drill.DrillSession.

Regression tests for porting the SMB and S3 view/tenant/bucket drill mechanics
onto the shared vast_drill.DrillSession machinery already proven for the NFS
engines. Grounded in a real VMS 5.5.0.1 capture (var203): the pre-port engines
ranked view/bucket candidates with a serial chunked scan that cost 18 API calls
and ~100 s of "stand by" for 145 views, and re-queried the drill on every 5 s
tick with no throttle.

These mirror the NFSv3 / NFSv4.1 suites in test_drill_semantics.py and
test_drill_loading.py. The budget, throttle, rank-cache and batch-fallback
tests fail against the pre-port implementation; the ranking-correctness, VIP
and loading tests are preservation guards.
"""

from __future__ import annotations

import shutil
from types import SimpleNamespace

import pytest

import vast_common
import vast_drill
from tests.mock_vms import (
    _ACTIVE_TENANT_INDEXES, _ACTIVE_VIEW_INDEXES, TENANTS, VIEWS, MockVMS,
)

pytestmark = pytest.mark.skipif(
    shutil.which("openssl") is None,
    reason="openssl binary required to generate the mock VMS certificate",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def vms(tmp_path):
    server = MockVMS(certdir=str(tmp_path)).start()
    yield server
    server.stop()


def _args(vms, **overrides):
    base = dict(
        vms="127.0.0.1", port=vms.port, user="admin", password=None,
        sample_average=None, refresh=5, csv=None, no_color=True,
        discover_metrics=False, log_api_calls=False,
        export_openmetrics=False, openmetrics_file=None,
        clients=None, buckets=None, tenants=None, volumes=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture
def smb_engine(vms, monkeypatch):
    import smb

    monkeypatch.setenv("VAST_TOKEN", "test-token")
    smb.init_config(_args(vms))
    smb.CLUSTER_ID, smb.CLUSTER_NAME = smb.get_current_cluster()
    smb.create_headline_monitors()
    yield smb, vms
    smb.exit_drill_mode()
    smb.cleanup()
    smb._CLEANED_UP = False
    vast_common.close_connection()


@pytest.fixture
def s3_engine(vms, monkeypatch):
    import s3

    monkeypatch.setenv("VAST_TOKEN", "test-token")
    s3.init_config(_args(vms))
    s3.CLUSTER_ID, s3.CLUSTER_NAME = s3.get_current_cluster()
    s3.create_headline_monitors()
    yield s3, vms
    s3.exit_drill_mode()
    s3.cleanup()
    s3._CLEANED_UP = False
    vast_common.close_connection()


def _top_views(n):
    return [VIEWS[i]["path"] for i in _ACTIVE_VIEW_INDEXES[:n]]


def _headline_ids(smb):
    return {smb.HEADLINE_MONITOR_ID, smb.SMB_COMMAND_MONITOR_ID}


def _s3_headline_ids(s3):
    return {s3.HEADLINE_MONITOR_ID, s3.S3_METRICS_MONITOR_ID}


def _queries(counts):
    return sum(v for k, v in counts.items() if "query" in k)


# ===========================================================================
# SMB
# ===========================================================================
def test_smb_view_entry_is_a_handful_of_calls(smb_engine):
    """Pre-port: 429 views cost ~44 serial calls (14 chunks x POST/GET/DELETE)."""
    smb, vms = smb_engine
    assert len(VIEWS) > 400
    vms.reset_calls()
    smb.enter_drill_mode("view")
    total = sum(vms.counts().values())
    assert smb.DRILL_ERROR is None
    assert total <= 6, f"view drill entry cost {total} calls: {vms.counts()}"


def test_smb_tenant_entry_is_a_handful_of_calls(smb_engine):
    smb, vms = smb_engine
    vms.reset_calls()
    smb.enter_drill_mode("tenant")
    total = sum(vms.counts().values())
    assert smb.DRILL_ERROR is None
    assert total <= 6, f"tenant drill entry cost {total} calls: {vms.counts()}"


def test_smb_view_ranking_picks_busy_views_beyond_the_first_32(smb_engine):
    smb, _vms = smb_engine
    smb.enter_drill_mode("view")
    assert smb.DRILL_ERROR is None
    names = [o["name"] for o in smb.DRILL_OBJECTS]
    assert set(names) == set(_top_views(smb._MAX_DRILL_OBJECTS)), names
    assert names[0] != VIEWS[0]["path"], "still head-slicing /views/"


def test_smb_reentering_view_reuses_the_cached_ranking(smb_engine):
    smb, vms = smb_engine
    smb.enter_drill_mode("view")
    first = [o["name"] for o in smb.DRILL_OBJECTS]
    smb.exit_drill_mode()
    vms.reset_calls()
    smb.enter_drill_mode("view")
    second = [o["name"] for o in smb.DRILL_OBJECTS]
    calls = sum(vms.counts().values())
    assert second == first
    assert calls <= 2, f"re-entry re-ranked from scratch: {vms.counts()}"


def test_smb_drill_query_is_throttled_between_ticks(smb_engine):
    smb, vms = smb_engine
    smb.enter_drill_mode("view")
    smb.fetch_drill_query(force=True)
    vms.reset_calls()
    for _ in range(4):
        smb.poll_tick()
    assert _queries(vms.counts()) <= 1, (
        f"drill re-queried on every tick: {vms.counts()}")


def test_smb_manual_refresh_forces_a_drill_query(smb_engine):
    smb, vms = smb_engine
    smb.enter_drill_mode("view")
    smb.fetch_drill_query(force=True)
    vms.reset_calls()
    smb.manual_refresh()
    assert _queries(vms.counts()) >= 1, "space-bar refresh must bypass the throttle"


def test_smb_view_batch_monitor_falls_back_to_per_object(smb_engine):
    """A cluster that caps object_ids below the drill width must split the
    display monitor per object rather than failing the drill."""
    smb, vms = smb_engine
    vms.state.max_object_ids = 4       # < _MAX_DRILL_OBJECTS (8); topn still ranks
    smb.enter_drill_mode("view")
    assert smb.DRILL_ERROR is None
    assert len(smb.DRILL_MONITORS) == len(smb.DRILL_OBJECTS)
    assert not vast_drill.DrillSession.batch_active(None, smb.DRILL_MONITORS)
    smb.fetch_drill_query(force=True)
    assert len(smb.LAST_DRILL_ROWS) == len(smb.DRILL_OBJECTS)


def test_smb_view_uses_one_batched_monitor(smb_engine):
    smb, vms = smb_engine
    smb.enter_drill_mode("view")
    assert smb.DRILL_ERROR is None
    assert len(smb.DRILL_MONITORS) == 1, "view drill should batch into one monitor"
    vms.reset_calls()
    smb.fetch_drill_query(force=True)
    assert vms.counts() == {"GET /api/monitors/{id}/query/": 1}
    assert len(smb.LAST_DRILL_ROWS) == len(smb.DRILL_OBJECTS)


def test_smb_ranking_leaves_no_monitors_behind_on_success(smb_engine):
    smb, vms = smb_engine
    smb.enter_drill_mode("view")
    live = set(vms.live_monitors())
    drill_ids = {mid for mid, _n in smb.DRILL_MONITORS}
    assert live <= drill_ids | _headline_ids(smb), (
        f"ranking monitors leaked: {live - drill_ids - _headline_ids(smb)}")


def test_smb_ranking_cleans_up_on_query_error(smb_engine, monkeypatch):
    """A rank monitor whose query raises must still be deleted (finally path)."""
    smb, vms = smb_engine
    vms.state.topn_enabled = False     # force monitor-based ranking
    real = smb.api_request

    def flaky(method, path, payload=None):
        if method == "GET" and "/query/" in path:
            raise RuntimeError("VMS query failed mid-rank")
        return real(method, path, payload)

    monkeypatch.setattr(smb, "api_request", flaky)
    smb.DRILL._request = flaky
    smb.enter_drill_mode("view")
    live = set(vms.live_monitors())
    drill_ids = {mid for mid, _n in smb.DRILL_MONITORS}
    leaked = live - _headline_ids(smb) - drill_ids
    assert not leaked, f"rank monitors leaked on error: {leaked}"


# ===========================================================================
# S3 (bucket / tenant)
# ===========================================================================
def test_s3_bucket_entry_is_a_handful_of_calls(s3_engine):
    s3, vms = s3_engine
    vms.reset_calls()
    s3.enter_drill_mode("bucket")
    total = sum(vms.counts().values())
    assert s3.DRILL_ERROR is None
    assert total <= 6, f"bucket drill entry cost {total} calls: {vms.counts()}"


def test_s3_tenant_entry_is_a_handful_of_calls(s3_engine):
    s3, vms = s3_engine
    vms.reset_calls()
    s3.enter_drill_mode("tenant")
    total = sum(vms.counts().values())
    assert s3.DRILL_ERROR is None
    assert total <= 6, f"tenant drill entry cost {total} calls: {vms.counts()}"


def test_s3_bucket_ranking_picks_busy_views_beyond_the_first_32(s3_engine):
    s3, _vms = s3_engine
    s3.enter_drill_mode("bucket")
    assert s3.DRILL_ERROR is None
    names = [o["name"] for o in s3.DRILL_OBJECTS]
    assert set(names) == set(_top_views(s3._MAX_DRILL_OBJECTS)), names
    assert names[0] != VIEWS[0]["path"]


def test_s3_reentering_bucket_reuses_the_cached_ranking(s3_engine):
    s3, vms = s3_engine
    s3.enter_drill_mode("bucket")
    first = [o["name"] for o in s3.DRILL_OBJECTS]
    s3.exit_drill_mode()
    vms.reset_calls()
    s3.enter_drill_mode("bucket")
    second = [o["name"] for o in s3.DRILL_OBJECTS]
    assert second == first
    assert sum(vms.counts().values()) <= 2, f"re-ranked: {vms.counts()}"


def test_s3_bucket_query_is_throttled_between_ticks(s3_engine):
    s3, vms = s3_engine
    s3.enter_drill_mode("bucket")
    s3.fetch_drill_query(force=True)
    vms.reset_calls()
    for _ in range(4):
        s3.poll_tick()
    assert _queries(vms.counts()) <= 1, f"bucket re-queried every tick: {vms.counts()}"


def test_s3_manual_refresh_forces_a_drill_query(s3_engine):
    s3, vms = s3_engine
    s3.enter_drill_mode("bucket")
    s3.fetch_drill_query(force=True)
    vms.reset_calls()
    s3.manual_refresh()
    assert _queries(vms.counts()) >= 1


def test_s3_bucket_batch_monitor_falls_back_to_per_object(s3_engine):
    s3, vms = s3_engine
    vms.state.max_object_ids = 4
    s3.enter_drill_mode("bucket")
    assert s3.DRILL_ERROR is None
    assert len(s3.DRILL_MONITORS) == len(s3.DRILL_OBJECTS)
    s3.fetch_drill_query(force=True)
    assert len(s3.LAST_DRILL_ROWS) == len(s3.DRILL_OBJECTS)


def test_s3_bucket_uses_one_batched_monitor(s3_engine):
    s3, vms = s3_engine
    s3.enter_drill_mode("bucket")
    assert s3.DRILL_ERROR is None
    assert len(s3.DRILL_MONITORS) == 1
    vms.reset_calls()
    s3.fetch_drill_query(force=True)
    assert vms.counts() == {"GET /api/monitors/{id}/query/": 1}


def test_s3_ranking_leaves_no_monitors_behind_on_success(s3_engine):
    s3, vms = s3_engine
    s3.enter_drill_mode("bucket")
    live = set(vms.live_monitors())
    drill_ids = {mid for mid, _n in s3.DRILL_MONITORS}
    assert live <= drill_ids | _s3_headline_ids(s3)


# ===========================================================================
# S3 VIP: preserved exactly (topn ranking, 192.168 filtering, topn-only fallback)
# ===========================================================================
def test_s3_vip_ranks_via_topn(s3_engine):
    s3, vms = s3_engine
    vms.reset_calls()
    s3.enter_drill_mode("vip")
    assert s3.DRILL_ERROR is None
    topn = [p for _t, _m, p, _s in vms.calls() if "monitors/topn" in p]
    assert topn, "VIP ranking must consult /monitors/topn/"
    assert s3.DRILL_OBJECTS, "VIP drill produced no objects"


def test_s3_vip_filters_internal_192_168_addresses():
    import s3

    objs = [
        {"id": 1, "name": "192.168.5.5", "ip": "192.168.5.5"},
        {"id": 2, "name": "vip-public", "ip": "10.1.0.9"},
    ]
    entries = s3._vip_objects_for_drill(objs)
    names = [e["name"] for e in entries]
    assert not any(n.startswith("192.168.") for n in names), names
    assert any("10.1.0.9" in (e.get("name") or "") or e["id"] == 2 for e in entries)


def test_s3_vip_falls_back_to_topn_only_when_monitors_rejected(s3_engine):
    """When object_type=vip monitors are rejected, VIP must still show topn
    activity rather than erroring out (the documented fallback path)."""
    s3, vms = s3_engine
    vms.state.reject_object_types = ("vip",)
    s3.enter_drill_mode("vip")
    assert s3.DRILL_ERROR is None
    assert s3.DRILL_MONITORS == [], "expected topn-only VIP mode (no monitors)"
    assert s3.LAST_DRILL_ROWS, "topn-only VIP fallback produced no rows"


# ===========================================================================
# Loading interstitial before blocking drill work
# ===========================================================================
def _capture_frames(monkeypatch):
    frames = []
    monkeypatch.setattr(vast_common, "flush_frame", lambda text: frames.append(text))
    return frames


@pytest.mark.parametrize("mode,needle", [
    ("view", "Loading the VIEW drill-down"),
    ("tenant", "Loading the TENANT drill-down"),
])
def test_smb_drill_paints_loading_frame_first(smb_engine, monkeypatch, mode, needle):
    smb, _vms = smb_engine
    frames = _capture_frames(monkeypatch)
    first_api_at = []
    real = smb.api_request

    def watched(method, path, payload=None):
        first_api_at.append(len(frames))
        return real(method, path, payload)

    monkeypatch.setattr(smb, "api_request", watched)
    smb.DRILL._request = watched
    smb.switch_drill_mode(mode)
    assert frames, "no frame rendered"
    assert needle in frames[0], frames[0][:120]
    assert first_api_at and first_api_at[0] >= 1, "API call before any frame"


@pytest.mark.parametrize("mode,needle", [
    ("bucket", "Loading the BUCKET drill-down"),
    ("tenant", "Loading the TENANT drill-down"),
])
def test_s3_drill_paints_loading_frame_first(s3_engine, monkeypatch, mode, needle):
    s3, _vms = s3_engine
    frames = _capture_frames(monkeypatch)
    first_api_at = []
    real = s3.api_request

    def watched(method, path, payload=None):
        first_api_at.append(len(frames))
        return real(method, path, payload)

    monkeypatch.setattr(s3, "api_request", watched)
    s3.DRILL._request = watched
    s3.switch_drill_mode(mode)
    assert frames, "no frame rendered"
    assert needle in frames[0], frames[0][:120]
    assert first_api_at and first_api_at[0] >= 1


# ===========================================================================
# Startup interstitial before blocking initial metric collection
# ===========================================================================
@pytest.mark.parametrize("engine_name", ["smb", "s3"])
def test_startup_paints_gathering_frame_before_blocking_work(vms, monkeypatch, engine_name):
    import importlib

    mod = importlib.import_module(engine_name)
    monkeypatch.setenv("VAST_TOKEN", "test-token")
    mod.init_config(_args(vms))

    frames = _capture_frames(monkeypatch)
    first_api_at = []
    real = mod.api_request

    def watched(method, path, payload=None):
        first_api_at.append(len(frames))
        return real(method, path, payload)

    monkeypatch.setattr(mod, "api_request", watched)
    try:
        mod.initialize()
        assert frames, "no startup frame rendered"
        # 3-phase startup: the first frame names the host (cluster unknown yet),
        # and a later frame reaches the "gathering" phase.
        assert "Connecting to" in frames[0], frames[0][:120]
        assert any("Gathering initial metrics" in f for f in frames), (
            "the gathering-metrics phase never rendered")
        assert first_api_at and first_api_at[0] >= 1, (
            "an API call was issued before the startup status frame")
    finally:
        mod.cleanup()
        mod._CLEANED_UP = False
        vast_common.close_connection()
