"""NVMe drill ranking and batching (mock-VMS backed).

The pre-ranking NVMe drill head-sliced the first ``_MAX_DRILL_OBJECTS`` (8)
objects the endpoint listed and created ~8 monitors per object - so on the
mock's 12 cNodes, the busiest cNode (planted at index 10) was never even
considered, and a cnode drill entry cost 65 API calls. These tests fail
against that behavior.

Ranking scores candidates by differencing the cumulative BlockMetrics
read_req/write_req counters over the rank monitor's own time series - the
same semantics the headline extraction uses (rate_from_timeseries). topn is
deliberately not used: it has no protocol label (docs/decisions/D-007), so it
would rank NVMe candidates by all-protocol traffic.

Whether a real VMS accepts multi-object_id BlockMetrics/ProtoMetrics monitors
at cnode/vip/blockhost scope is NOT proven by these tests - the mock models
the documented batch shape (object_id column). The engine validates
splittability at entry and falls back to the per-object layout, and the
work-laptop validation package carries the real-cluster probe.
"""

from __future__ import annotations

import shutil
from types import SimpleNamespace

import pytest

import vast_common
from tests.mock_vms import CNODES, VIPS, _ACTIVE_BLOCK_INDEXES_CNODE, MockVMS

pytestmark = pytest.mark.skipif(
    shutil.which("openssl") is None,
    reason="openssl binary required to generate the mock VMS certificate",
)


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
        volumes=None, volume=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture
def nvme(vms, monkeypatch):
    import nvme_tcp

    monkeypatch.setenv("VAST_TOKEN", "test-token")
    nvme_tcp.init_config(_args(vms))
    nvme_tcp.CLUSTER_ID, nvme_tcp.CLUSTER_NAME = nvme_tcp.get_current_cluster()
    nvme_tcp.create_cluster_monitors()
    yield nvme_tcp, vms
    nvme_tcp.exit_drill_mode()
    nvme_tcp.cleanup()
    nvme_tcp._CLEANED_UP = False
    vast_common.close_connection()


HOT_CNODE = CNODES[_ACTIVE_BLOCK_INDEXES_CNODE[0]]


# ---------------------------------------------------------------------------
# Ranking: activity, never API order
# ---------------------------------------------------------------------------
def test_cnode_drill_selects_the_busy_cnode_beyond_index_8(nvme):
    """The mock plants the busiest cNode at index 10 of 12. The head-slicing
    implementation took objects [:8] by API order and could never show it."""
    engine, vms = nvme
    engine.enter_drill_mode("cnode")
    assert engine.DRILL_ERROR is None
    names = [obj["name"] for obj in engine.DRILL_OBJECTS]
    assert HOT_CNODE["name"] in names, (
        f"busy cNode {HOT_CNODE['name']} (index 10) not selected: {names}")
    # And it ranks first, because it is the most active.
    assert names[0] == HOT_CNODE["name"]


def test_cnode_drill_caps_the_panel_at_max_objects(nvme):
    engine, _vms = nvme
    engine.enter_drill_mode("cnode")
    assert len(engine.DRILL_OBJECTS) <= engine._MAX_DRILL_OBJECTS


def test_vip_drill_ranks_by_activity(nvme):
    engine, _vms = nvme
    engine.enter_drill_mode("vip")
    assert engine.DRILL_ERROR is None
    names = [obj["name"] for obj in engine.DRILL_OBJECTS]
    hot_vip = VIPS[3]
    assert names[0] in (hot_vip["ip"], hot_vip["name"]), names


def test_rank_cache_spares_re_entry(nvme):
    """Re-entering a drill inside the cache TTL must create no rank monitors."""
    engine, vms = nvme
    engine.enter_drill_mode("cnode")
    engine.exit_drill_mode()
    vms.reset_calls()
    engine.enter_drill_mode("cnode")
    rank_posts = [c for c in vms.calls()
                  if c[1] == "POST" and "rank" in str(c[3])]
    assert rank_posts == [], "re-entry inside the TTL re-ranked from scratch"


# ---------------------------------------------------------------------------
# Batching: entry budget and per-refresh budget
# ---------------------------------------------------------------------------
def test_cnode_drill_entry_call_budget(nvme):
    """Entry: 1 object list + rank (POST/GET/DELETE) + one POST per op group
    + proto POST + 1 validation GET. The per-object layout cost 65 calls."""
    engine, vms = nvme
    vms.reset_calls()
    engine.enter_drill_mode("cnode")
    assert engine.DRILL_ERROR is None
    n = len(vms.calls())
    assert n <= 15, f"cnode drill entry took {n} calls; the batch budget is <=15"


def test_drill_refresh_queries_one_per_group_not_per_object(nvme):
    engine, vms = nvme
    engine.enter_drill_mode("cnode")
    assert engine._drill_batch_active(), "batch layout expected against the mock"
    group_count = len(engine.DRILL_MONITORS[0][0]) + 1   # + proto monitor
    vms.reset_calls()
    engine.fetch_drill_query(force=True)
    queries = [c for c in vms.calls() if "/query/" in str(c[2])]
    assert len(queries) == group_count, (
        f"{len(queries)} queries for {len(engine.DRILL_OBJECTS)} objects; "
        f"expected one per monitor group ({group_count})")


def test_batch_rows_are_per_object_and_ranked(nvme):
    engine, _vms = nvme
    engine.enter_drill_mode("cnode")
    engine.fetch_drill_query(force=True)
    # Two ticks so counter deltas exist for every object.
    engine.fetch_drill_query(force=True)
    names = [r["name"] for r in engine.LAST_DRILL_ROWS]
    assert names, "batch drill produced no rows"
    assert HOT_CNODE["name"] in names
    iops = [r["total_iops"] or 0 for r in engine.LAST_DRILL_ROWS]
    assert iops == sorted(iops, reverse=True), "rows not sorted by activity"


# ---------------------------------------------------------------------------
# Fallback: a cluster that rejects the batch keeps the per-object layout
# ---------------------------------------------------------------------------
def test_batch_rejection_falls_back_to_per_object_monitors(nvme):
    engine, vms = nvme
    vms.state.max_object_ids = 1     # any multi-object monitor is refused
    try:
        engine.enter_drill_mode("cnode")
        assert engine.DRILL_ERROR is None
        assert not engine._drill_batch_active()
        assert len(engine.DRILL_MONITORS) >= 1
        # Per-object tuples carry the object name (batch carries None).
        assert all(name is not None for _ids, _proto, name in engine.DRILL_MONITORS)
        engine.fetch_drill_query(force=True)
    finally:
        vms.state.max_object_ids = None


def test_unrankable_cluster_still_opens_the_drill(nvme):
    """Every rank-monitor create refused -> unranked-but-stable candidates."""
    engine, vms = nvme
    vms.state.reject_object_types = ("cnode",)
    try:
        engine.enter_drill_mode("cnode")
        # Object-scoped monitors are all refused, so no monitors could be
        # created either way; the drill reports the error rather than opening
        # on fabricated data.
        assert engine.DRILL_MODE is None
        assert engine.DRILL_ERROR is not None
    finally:
        vms.state.reject_object_types = ()


# ---------------------------------------------------------------------------
# Cleanup: no layout may leak monitors
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("constrained", [False, True])
def test_drill_cleanup_leaves_no_monitors(nvme, constrained):
    engine, vms = nvme
    if constrained:
        vms.state.max_object_ids = 1
    try:
        engine.enter_drill_mode("cnode")
        engine.fetch_drill_query(force=True)
        engine.exit_drill_mode()
    finally:
        vms.state.max_object_ids = None
    engine.cleanup()
    engine._CLEANED_UP = False
    assert vms.live_monitors() == {}, "monitors leaked after drill cleanup"
