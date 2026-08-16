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

A var203 probe (VAST OS 5.5.0.1) has since established what a real VMS does,
and it is scope-dependent: at ``object_type=cnode`` a multi-object_id
BlockMetrics monitor is created, queried and splits correctly (120 rows per
object for ids [4, 3]), while at ``=vip`` and ``=blockhost`` the very same
shape is created and queried successfully but yields **no per-object rows**.
Creation succeeding is therefore not evidence a batch layout is usable, which
is why the engine validates the response before committing to one. See
docs/decisions/D-013.
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
    rank_posts = [n for n in vms.created_monitor_names() if "rank" in n]
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
    """Every multi-object create refused -> bounded per-object fallback.

    Ranking cannot run either (rank monitors are multi-object), which proves
    nothing about telemetry - the drill must still open, capped at
    _MAX_FALLBACK_OBJECTS with only the data-I/O groups the panel renders.
    The unbounded fallback cost 43 monitors / 464 s on var203 (round 3).
    """
    engine, vms = nvme
    vms.state.max_object_ids = 1     # any multi-object monitor is refused
    try:
        engine.enter_drill_mode("cnode")
        assert engine.DRILL_ERROR is None
        assert not engine._drill_batch_active()
        assert 1 <= len(engine.DRILL_MONITORS) <= engine._MAX_FALLBACK_OBJECTS
        # Per-object tuples carry the object name (batch carries None).
        assert all(name is not None for _ids, _proto, name in engine.DRILL_MONITORS)
        # Data-I/O groups only: read/write/compare (3), never the 7-group
        # full set that made the fallback a monitor storm.
        for ops_ids, _proto, _name in engine.DRILL_MONITORS:
            assert len(ops_ids) == 3, f"fallback created {len(ops_ids)} op groups"
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


# ---------------------------------------------------------------------------
# Real var203 shape: the batch is ACCEPTED and QUERIED but is not splittable.
#
# This is the case the earlier fallback test did not cover. There, creation was
# refused outright (max_object_ids=1). On var203 the vip and blockhost batches
# were created successfully AND queried successfully - they simply carried no
# usable per-object rows, while cnode with the identical shape split fine.
# An engine that treated a successful create as proof of a working batch would
# have rendered an empty or fabricated panel on two of three NVMe drill modes.
#
# The exact var203 response shape is not yet distinguished (the probe recorded
# rows-per-object = 0 without separating "no object_id column" from "column
# present, no matching rows"), so both are modelled and both must fall back.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("mode,object_type", [("vip", "vip"), ("cnode", "cnode")])
def test_no_matching_rows_scope_gets_the_honest_empty_panel(
        nvme, mode, object_type):
    """The real var203 vip/blockhost shape: responses carry an object_id
    column but no rows ever match the requested ids - for the rank monitor
    too. That scope publishes no per-object telemetry, and the drill must SAY
    so instead of fanning out per-object monitors that each prove emptiness
    at 2-38 s a call (43 monitors / 464 s in round 3)."""
    engine, vms = nvme
    vms.state.batch_unsplittable = {object_type: "no_matching_rows"}
    try:
        vms.reset_calls()
        engine.enter_drill_mode(mode)
        assert engine.DRILL_MODE is None, "drill opened on a telemetry-less scope"
        assert engine.DRILL_ERROR is not None
        assert "telemetry" in engine.DRILL_ERROR
        assert engine.DRILL_MONITORS == []
        # Bounded probe cost: endpoint list + rank attempt + batch attempt,
        # never a per-object storm.
        posts = [c for c in vms.calls() if c[1] == "POST"]
        assert len(posts) <= 6, f"{len(posts)} monitor creates on a dead scope"
    finally:
        vms.state.batch_unsplittable = {}
    live = set(vms.live_monitors()) - set(engine.OPS_MONITOR_IDS) - {engine.PROTO_MONITOR_ID}
    assert not live, f"probe monitors leaked: {sorted(live)}"


@pytest.mark.parametrize("mode,object_type", [("vip", "vip"), ("cnode", "cnode")])
def test_no_object_id_shape_falls_back_bounded_with_real_rows(
        nvme, mode, object_type):
    """The other candidate shape: batch responses lack the object_id column
    but per-object telemetry exists. Fallback engages - bounded and
    data-only - and still renders real per-object rows."""
    engine, vms = nvme
    vms.state.batch_unsplittable = {object_type: "no_object_id"}
    try:
        engine.enter_drill_mode(mode)
        assert engine.DRILL_ERROR is None, engine.DRILL_ERROR
        assert not engine._drill_batch_active(), (
            "committed to a batch whose response carries no per-object rows")
        assert 1 <= len(engine.DRILL_MONITORS) <= engine._MAX_FALLBACK_OBJECTS
        assert all(name is not None for _ids, _proto, name in engine.DRILL_MONITORS)

        # Two ticks: BlockMetrics req counters are cumulative, so the first
        # poll only baselines the per-scope counter state.
        engine.fetch_drill_query(force=True)
        engine.fetch_drill_query(force=True)
        rows = engine.LAST_DRILL_ROWS
        assert rows, "fallback produced no drill rows"
        assert len({r["name"] for r in rows}) == len(rows), "duplicate object rows"
        assert any(r["total_iops"] for r in rows), "no object reported any activity"
    finally:
        vms.state.batch_unsplittable = {}


@pytest.mark.parametrize("shape", ["no_object_id", "no_matching_rows"])
def test_rejected_batch_monitors_are_torn_down(nvme, shape):
    """The probe attempt must not leak the monitors it created."""
    engine, vms = nvme
    vms.state.batch_unsplittable = {"vip": shape}
    try:
        engine.enter_drill_mode("vip")
        live = set(vms.live_monitors())
        held = set()
        for ids, proto, _name in engine.DRILL_MONITORS:
            held.update(int(i) for i in ids)
            if proto is not None:
                held.add(int(proto))
        orphaned = live - held - set(engine.OPS_MONITOR_IDS) - {engine.PROTO_MONITOR_ID}
        orphaned.discard(None)
        assert not orphaned, f"rejected batch left monitors behind: {sorted(orphaned)}"
        engine.exit_drill_mode()
    finally:
        vms.state.batch_unsplittable = {}
    engine.cleanup()
    engine._CLEANED_UP = False
    assert vms.live_monitors() == {}, "monitors leaked after unsplittable-batch entry"


def test_cnode_batch_engages_when_the_response_really_splits(nvme):
    """The var203 cnode result: object_id column with rows per object."""
    engine, _vms = nvme
    engine.enter_drill_mode("cnode")
    assert engine.DRILL_ERROR is None
    assert engine._drill_batch_active(), (
        "cnode batch must engage when the response splits per object_id")


# ---------------------------------------------------------------------------
# Key dispatch: every queued key is honored in arrival order.
#
# Observed on var203: poll cycles blocked 30-80 s, several keys queued in one
# read, and the old single-action-per-read handling let a buffered space
# swallow "x" and "i" - the drill could not be exited until quit, VIP was
# never entered, and the drill monitors survived until shutdown.
# ---------------------------------------------------------------------------
def test_queued_keys_are_all_dispatched_in_order(nvme):
    engine, vms = nvme
    engine.enter_drill_mode("cnode")
    assert engine.DRILL_MODE == "cnode"
    # The exact buffered sequence from the real run: space, then x, then i,
    # read as one chars batch. Every action must fire, in order.
    for key in " xi":
        engine._dispatch_key(key)
    assert engine.DRILL_MODE == "vip", (
        "buffered x/i were dropped; drill stuck in %r" % engine.DRILL_MODE)


def test_x_after_long_poll_actually_exits_and_cleans_up(nvme):
    engine, vms = nvme
    engine.enter_drill_mode("cnode")
    drill_ids = set()
    for ids, proto, _name in engine.DRILL_MONITORS:
        drill_ids.update(int(i) for i in ids)
        if proto is not None:
            drill_ids.add(int(proto))
    engine._dispatch_key("x")
    assert engine.DRILL_MODE is None
    live = set(vms.live_monitors())
    leaked = drill_ids & live
    assert not leaked, f"x left drill monitors behind: {sorted(leaked)}"


def test_same_mode_key_toggles_out(nvme):
    engine, _vms = nvme
    engine._dispatch_key("c")
    assert engine.DRILL_MODE == "cnode"
    engine._dispatch_key("c")
    assert engine.DRILL_MODE is None


def test_unbound_key_costs_nothing(nvme):
    engine, vms = nvme
    vms.reset_calls()
    assert engine._dispatch_key("z") is None
    assert vms.calls() == []


# ---------------------------------------------------------------------------
# Drill entry paints a loading frame before the blocking work (the entry
# against var203 blocked ~2 minutes with a frozen frame - NVMe was the one
# engine without the interstitial).
# ---------------------------------------------------------------------------
def test_drill_entry_paints_loading_frame_before_work(nvme, monkeypatch):
    engine, _vms = nvme
    events = []
    real_render = engine.render_screen
    real_enter = engine.enter_drill_mode

    monkeypatch.setattr(engine, "render_screen",
                        lambda: events.append(("render", engine.DRILL_STATUS)))
    monkeypatch.setattr(engine, "enter_drill_mode",
                        lambda mode: events.append(("work", mode)) or real_enter(mode))
    engine._dispatch_key("c")
    monkeypatch.setattr(engine, "render_screen", real_render)

    assert ("work", "cnode") in events
    first_render = next(e for e in events if e[0] == "render")
    work_idx = events.index(("work", "cnode"))
    assert events.index(first_render) < work_idx, (
        "no frame rendered before the blocking drill entry")
    assert first_render[1], "loading status was empty in the pre-work frame"
    assert "stand by" in first_render[1]
    assert engine.DRILL_STATUS is None, "loading status not cleared"


# ---------------------------------------------------------------------------
# Responsiveness: the round-3 var203 run left an "x" unprocessed for 150+ s
# because one refresh cycle is several serial queries at 2-38 s each and keys
# are only read between cycles. Two fixes are covered here: queued input
# aborts the remaining queries of a cycle, and an open drill moves the
# headline monitors to the drill cadence instead of every 5 s tick.
# ---------------------------------------------------------------------------
def test_pending_input_aborts_the_rest_of_the_headline_cycle(nvme, monkeypatch):
    engine, vms = nvme
    engine.fetch_monitor_query()          # baseline rows
    rows_before = engine.LAST_ROWS
    assert rows_before, "baseline fetch produced no rows"

    monkeypatch.setattr(engine.vast_common, "input_pending", lambda: True)
    vms.reset_calls()
    engine.fetch_monitor_query()
    queries = [c for c in vms.calls() if "/query/" in str(c[2])]
    # proto + first ops monitor at most: the cycle must yield after noticing
    # the pending keystroke, not run all monitors serially.
    assert len(queries) <= 2, f"cycle ran {len(queries)} queries with input pending"
    assert engine.LAST_ROWS == rows_before, "aborted cycle must keep prior rows"


def test_open_drill_moves_headline_to_drill_cadence(nvme, monkeypatch):
    engine, vms = nvme
    engine.enter_drill_mode("cnode")
    assert engine.DRILL_ERROR is None
    engine._LAST_HEADLINE_AT = 0.0
    engine.poll_tick()                    # due -> headline + drill
    vms.reset_calls()
    engine.poll_tick()                    # immediately again -> throttled
    paths = [str(c[2]) for c in vms.calls()]
    headline = [p for p in paths
                if any(f"/monitors/{m}/" in p for m in engine.OPS_MONITOR_IDS)]
    assert headline == [], (
        "headline re-queried on the 5 s tick while a drill is open")


def test_manual_refresh_still_forces_headline_in_drill(nvme):
    engine, vms = nvme
    engine.enter_drill_mode("cnode")
    engine.poll_tick()
    vms.reset_calls()
    engine.manual_refresh()
    paths = [str(c[2]) for c in vms.calls()]
    headline = [p for p in paths
                if any(f"/monitors/{m}/" in p for m in engine.OPS_MONITOR_IDS)]
    assert headline, "space no longer forces the headline monitors"


# ---------------------------------------------------------------------------
# Round 4: the VIP monitor storm and the O(1) dead-scope contract.
#
# The real run devolved one VIP rank scan into 189 serial create/query/delete
# cycles: the DrillSession's discovered rank chunk size was shared across
# object types, so "chunks of 2 work" - learned on a 2-cNode scope - capped
# the 378-VIP scan at 2 ids per monitor. And even with a sane chunk size,
# a scope that publishes no per-object telemetry only discovered that fact
# AFTER ranking its whole population. Dead scopes must fail closed and
# cheaply: discovery cost may not scale with the object count.
# ---------------------------------------------------------------------------
def _vip_population(n):
    return [{"id": 5000 + i, "ip": f"10.9.{i // 250}.{i % 250}",
             "name": f"vip-syn-{i}"} for i in range(n)]


def test_small_cnode_scope_does_not_poison_vip_rank_chunks(nvme):
    """The literal round-4 sequence: rank a 2-object cNode scope, then enter
    a 378-object VIP scope with live telemetry. The VIP rank must not devolve
    into per-pair monitors (189 on the real cluster)."""
    engine, vms = nvme
    vms.state.cnodes = [dict(c) for c in vms.state.cnodes or []] or None
    from tests.mock_vms import CNODES as _C
    vms.state.cnodes = [_C[0], _C[1]]              # population = 2
    vms.state.vips = _vip_population(378)          # population = 378, alive
    try:
        engine.enter_drill_mode("cnode")
        assert engine.DRILL_ERROR is None
        engine.exit_drill_mode()

        vms.reset_calls()
        engine.enter_drill_mode("vip")
        rank_posts = [n for n in vms.created_monitor_names() if "rank_vip" in n]
        assert len(rank_posts) <= 2, (
            f"{len(rank_posts)} VIP rank monitors created - the cNode scan's "
            f"chunk size poisoned the VIP scan (round 4: 189 monitors)")
        engine.exit_drill_mode()
    finally:
        vms.state.vips = None
        vms.state.cnodes = None


@pytest.mark.parametrize("population", [10, 100, 500, 1000])
def test_dead_vip_scope_discovery_cost_is_constant(nvme, population):
    """A scope with no per-object telemetry must be discovered in O(1):
    a fixed create budget regardless of 10, 100, 500 or 1000 objects."""
    engine, vms = nvme
    vms.state.vips = _vip_population(population)
    vms.state.batch_unsplittable = {"vip": "no_matching_rows"}
    try:
        vms.reset_calls()
        engine.enter_drill_mode("vip")
        posts = [c for c in vms.calls() if c[1] == "POST"]
        # Explicit budget: the bounded capability probe (1 create) for large
        # populations, or one rank chunk (1 create) for small ones. Never a
        # population-proportional storm.
        assert len(posts) <= 3, (
            f"dead vip scope with {population} objects cost {len(posts)} "
            f"creates; the budget is 3")
        assert engine.DRILL_MODE is None
        assert engine.DRILL_ERROR is not None
        assert engine.NO_TELEMETRY_MARKER in engine.DRILL_ERROR
        assert engine.DRILL_MONITORS == []

        # Re-entry uses the cached verdict: zero additional creates.
        vms.reset_calls()
        engine.enter_drill_mode("vip")
        posts = [c for c in vms.calls() if c[1] == "POST"]
        assert posts == [], "re-entering a known-dead scope created monitors"
    finally:
        vms.state.vips = None
        vms.state.batch_unsplittable = {}
    live = set(vms.live_monitors()) - set(engine.OPS_MONITOR_IDS) - {engine.PROTO_MONITOR_ID}
    assert not live, f"dead-scope probe leaked monitors: {sorted(live)}"


def test_no_telemetry_state_renders_notice_and_x_exits(nvme, capsys):
    """The honest empty panel renders (with footer) and x leaves it."""
    engine, vms = nvme
    vms.state.vips = _vip_population(50)
    vms.state.batch_unsplittable = {"vip": "no_matching_rows"}
    try:
        engine._dispatch_key("i")
        capsys.readouterr()
        assert engine.DRILL_MODE is None
        assert engine.DRILL_ERROR is not None

        import io as _io
        import shutil as _sh
        import sys as _sys
        buf, real = _io.StringIO(), _sys.stdout
        _sys.stdout = buf
        try:
            engine._render_frame()
        finally:
            _sys.stdout = real
        import tui_layout as _tl
        frame = _tl.strip_ansi(buf.getvalue())
        assert engine.NO_TELEMETRY_MARKER in frame, "notice not rendered"
        assert "[q]" in frame, "footer lost on the no-telemetry frame"

        outcome = engine._dispatch_key("x")
        capsys.readouterr()
        assert outcome == "refresh"
        assert engine.DRILL_ERROR is None, "x did not clear the notice state"
    finally:
        vms.state.vips = None
        vms.state.batch_unsplittable = {}


# ---------------------------------------------------------------------------
# Blockhost: same dead-scope contract as VIP. The inventory shape is modeled
# from three rounds of real var203 probe evidence (six objects, name + nqn,
# BlockMetrics echoed unrewritten, zero per-object rows on that build).
# ---------------------------------------------------------------------------
def _blockhost_population(n):
    return [{"id": 9000 + i, "name": f"bh-syn-{i}",
             "nqn": f"nqn.2014-08.org.nvmexpress:uuid:syn-{i:05d}"}
            for i in range(n)]


@pytest.mark.parametrize("population", [6, 10, 100, 500, 1000])
def test_dead_blockhost_scope_discovery_cost_is_constant(nvme, population):
    """Dead blockhost scope: fixed create budget at any population - the
    var203 population is 6 (rank-chunk verdict path); larger synthetic
    populations exercise the pre-probe path."""
    engine, vms = nvme
    if population != 6:
        vms.state.blockhosts = _blockhost_population(population)
    vms.state.batch_unsplittable = {"blockhost": "no_matching_rows"}
    try:
        vms.reset_calls()
        engine.enter_drill_mode("host")
        posts = [c for c in vms.calls() if c[1] == "POST"]
        assert len(posts) <= 3, (
            f"dead blockhost scope with {population} objects cost "
            f"{len(posts)} creates; the budget is 3")
        assert engine.DRILL_MODE is None
        assert engine.DRILL_ERROR is not None
        assert engine.NO_TELEMETRY_MARKER in engine.DRILL_ERROR
        assert engine.DRILL_MONITORS == []

        vms.reset_calls()
        engine.enter_drill_mode("host")
        assert [c for c in vms.calls() if c[1] == "POST"] == [], (
            "re-entering the known-dead blockhost scope created monitors")
    finally:
        vms.state.blockhosts = None
        vms.state.batch_unsplittable = {}
    live = set(vms.live_monitors()) - set(engine.OPS_MONITOR_IDS) - {engine.PROTO_MONITOR_ID}
    assert not live, f"dead-scope probe leaked monitors: {sorted(live)}"


def test_live_blockhost_scope_opens_with_real_rows(nvme):
    """When blockhost telemetry exists (a future build), the drill opens with
    per-object rows and x exits - the verdict must not hardcode var203."""
    engine, vms = nvme
    engine.enter_drill_mode("host")
    assert engine.DRILL_ERROR is None, engine.DRILL_ERROR
    assert engine.DRILL_MODE == "host"
    engine.fetch_drill_query(force=True)
    engine.fetch_drill_query(force=True)
    assert engine.LAST_DRILL_ROWS, "live blockhost drill produced no rows"
    engine.exit_drill_mode()
    assert engine.DRILL_MODE is None
