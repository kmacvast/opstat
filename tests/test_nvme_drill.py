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

import tui_layout
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
    # This is the FIRST cNode entry of the run, so the cold-entry wording
    # applies (NVMe entry ran ~2 min on var203). Asserting it exactly is
    # stricter than the previous "stand by" substring check.
    assert first_render[1] == (
        "Loading the cNODE drill-down, this can take 30+ seconds the first "
        "time..."), first_render[1]
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


def test_validator_contract_matches_the_product_notice():
    """The lab validator imports the marker from the product; the notice the
    product renders must actually contain it (round 4 crashed on a marker
    that was referenced but never defined)."""
    import nvme_tcp

    notice = nvme_tcp._no_telemetry_notice("vip")
    assert nvme_tcp.NO_TELEMETRY_MARKER in notice
    import importlib.util as _ilu
    spec = _ilu.spec_from_file_location(
        "run_var203_validation",
        "scripts/var203_validation/run_var203_validation.py")
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.NO_TELEMETRY_MARKER == nvme_tcp.NO_TELEMETRY_MARKER


def test_validator_scenario_exception_still_quits_the_session(monkeypatch):
    """Round 4: a NameError mid-scenario abandoned the opstat process, which
    ran headless for 24 minutes and leaked its headline monitors when the
    dying PTY finally killed it. Whatever the scenario raises, the session
    must be quit and its cleanup accounting must run."""
    import importlib.util as _ilu

    spec = _ilu.spec_from_file_location(
        "run_var203_validation",
        "scripts/var203_validation/run_var203_validation.py")
    val = _ilu.module_from_spec(spec)
    spec.loader.exec_module(val)

    events = []

    class _FakeSession:
        exit_code = None
        output = ""

        def start(self):
            events.append("start")
            return self

        def quit(self, *_a, **_k):
            events.append("quit")
            self.exit_code = 0
            return 0.0

    monkeypatch.setattr(val, "OpstatSession",
                        lambda *a, **k: _FakeSession())
    monkeypatch.setattr(val, "_scenario_nvme_body",
                        lambda *_a: (_ for _ in ()).throw(NameError("boom")))
    monkeypatch.setattr(val, "_cleanup_scenario",
                        lambda *_a, **_k: events.append("cleanup"))

    args = type("A", (), {"drain_budget": 1, "vms": "unused.invalid",
                          "user": "admin", "vms_port": 443})()
    with pytest.raises(NameError):
        val.scenario_nvme(args)
    assert "quit" in events, "exception path abandoned the opstat session"
    assert "cleanup" in events, "exception path skipped cleanup accounting"
    assert events.index("quit") < events.index("cleanup")


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


# ---------------------------------------------------------------------------
# Round-5B validator remediation. Round 5's only red result and its two
# misleading numbers were validator measurement defects, not product ones:
# the dead-scope notice was only checked after the full 420 s title wait
# ("421s", 93/98 entry calls of accumulated background polling), and manual
# refresh was judged by a fixed 6 s settle on a cluster where a single call
# runs 2-38 s. These tests pin the corrected measurements.
# ---------------------------------------------------------------------------
import time as _time


def _load_validator():
    import importlib.util as _ilu

    spec = _ilu.spec_from_file_location(
        "run_var203_validation",
        "scripts/var203_validation/run_var203_validation.py")
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _bare_session(val, output):
    sess = val.OpstatSession.__new__(val.OpstatSession)
    sess.output = output
    sess.started = _time.time()
    sess._drain = lambda budget: _time.sleep(min(budget, 0.02))
    return sess


def test_wait_for_any_since_title_wins():
    val = _load_validator()
    sess = _bare_session(val, "noise CNODE PATHS noise")
    needle, elapsed = sess.wait_for_any_since(
        ("CNODE PATHS", val.NO_TELEMETRY_MARKER), 0, 5)
    assert needle == "CNODE PATHS"
    assert elapsed is not None


def test_wait_for_any_since_marker_wins_without_waiting_out_the_budget():
    """Round 5 reported '421s' for a VIP entry whose real work was one
    bounded probe: the notice was only checked after the 420 s title wait."""
    import nvme_tcp

    val = _load_validator()
    sess = _bare_session(
        val, "Loading the VIP drill-down, please stand by...\n"
        + nvme_tcp._no_telemetry_notice("vip"))
    t0 = _time.time()
    needle, elapsed = sess.wait_for_any_since(
        ("VIP PATHS", val.NO_TELEMETRY_MARKER), 0, 420)
    assert needle == val.NO_TELEMETRY_MARKER
    assert elapsed is not None
    assert _time.time() - t0 < 5, "marker match must not wait out the budget"


def test_wait_for_any_since_timeout_is_honest():
    val = _load_validator()
    sess = _bare_session(val, "neither state ever renders")
    t0 = _time.time()
    needle, elapsed = sess.wait_for_any_since(("AAA", "BBB"), 0, 0.3)
    assert needle is None and elapsed is None
    assert _time.time() - t0 < 5


def test_wait_for_any_since_ignores_output_before_the_offset():
    """A stale previous frame containing the marker must not satisfy a new
    wait - the same trap wait_for_since exists to close."""
    val = _load_validator()
    stale = "old frame: " + val.NO_TELEMETRY_MARKER + "\n"
    sess = _bare_session(val, stale + "new frame: still loading")
    needle, _elapsed = sess.wait_for_any_since(
        ("VIP PATHS", val.NO_TELEMETRY_MARKER), len(stale), 0.3)
    assert needle is None


class _ScriptedSession:
    """Stand-in for OpstatSession: output and API-log lines appear on send(),
    or on a timed release schedule, never from a real PTY."""

    def __init__(self, val, on_send):
        self._val = val
        self.output = ""
        self.started = _time.time()
        self.log_path = "<scripted>"
        self._lines = ["session start scripted"]
        self._pending = []          # (release_epoch, line)
        self._on_send = on_send

    def _release_due(self):
        now = _time.time()
        due = [line for at, line in self._pending if at <= now]
        self._pending = [(at, line) for at, line in self._pending if at > now]
        self._lines.extend(due)

    def _drain(self, budget):
        self._release_due()
        _time.sleep(min(budget, 0.02))

    def wait_for_since(self, needle, offset, budget):
        return self._val.OpstatSession.wait_for_since(self, needle, offset, budget)

    def wait_for_any_since(self, needles, offset, budget):
        return self._val.OpstatSession.wait_for_any_since(
            self, needles, offset, budget)

    def api_lines(self):
        self._release_due()
        return list(self._lines)

    def api_mark(self):
        return len(self.api_lines())

    def api_since(self, mark):
        return self.api_lines()[mark:]

    def send(self, keys, settle=0.0):
        handler = self._on_send.get(keys)
        if handler:
            handler(self)
        if settle:
            self._drain(settle)


def test_validator_dead_scope_stops_at_the_notice_not_the_budget():
    """A dead scope's verdict is knowable the moment the notice renders; the
    metrics must cover only the probe, not 420 s of background polling."""
    import nvme_tcp

    val = _load_validator()

    def on_i(sess):
        sess.output += "Loading the VIP drill-down, please stand by...\n"
        sess.output += nvme_tcp._no_telemetry_notice("vip") + "\n"
        sess._lines.append(
            '2026-08-16 12:00:01 POST https://h:443/api/monitors/ 900ms '
            'payload={"name": "adhoc_opstat_probe_vip_1"} '
            '-> HTTP 201 (14 bytes) body={"id": 7001}')
        sess._lines.append(
            '2026-08-16 12:00:02 GET https://h:443/api/monitors/7001/query/ '
            '800ms -> HTTP 200 (2 bytes) body={}')
        sess._lines.append(
            '2026-08-16 12:00:03 DELETE https://h:443/api/monitors/7001/ '
            '400ms -> HTTP 204 (0 bytes)')

    sess = _ScriptedSession(val, {"i": on_i, "x": lambda s: None})
    args = SimpleNamespace(key_budget=30, drill_budget=420, drill_settle=0.01)
    val.REPORT = val.Report()
    t0 = _time.time()
    out = val._drill_scenario(sess, "i", "vip", args)
    took = _time.time() - t0
    assert out["no_telemetry"] is True
    assert took < 30, "dead-scope verdict took %.1fs of validator dead time" % took
    results = {n: (s, d) for n, s, d in val.REPORT.results}
    assert results["nvme.vip.open"][0] == val.PASS
    assert results["nvme.vip.entry"][0] == val.PASS
    assert "3 calls, 1 creates" in results["nvme.vip.entry"][1], (
        "entry accounting must stop at the notice: %s"
        % results["nvme.vip.entry"][1])


def _headline_ids(n=8, base=100):
    return list(range(base, base + n))


def test_judge_manual_refresh_abort_restart_is_forced():
    """A headline pass restarted mid-flight is unforgeable by cadence: only
    queued input aborts _query_ops_monitors_interruptible."""
    val = _load_validator()
    H = _headline_ids()
    pre = [(1000.0 + i * 7.5, H[i]) for i in range(3)]     # burst in flight
    t_space = 1020.0
    post = [(1024.0, H[0]), (1030.0, H[1])]                # restart from H0
    verdict, detail = val.judge_manual_refresh(pre, post, t_space, H)
    assert verdict == val.PASS
    assert "aborted-and-restarted" in detail


def test_judge_manual_refresh_throttle_window_is_forced():
    """On a quiet log, a query issued inside the throttle window after the
    previous burst began cannot be a scheduled poll."""
    val = _load_validator()
    H = _headline_ids()
    pre = [(1000.0 + i * 0.1, H[i]) for i in range(8)]     # clean fast burst
    post = [(1005.0 + i * 0.1, H[i]) for i in range(8)]    # forced full pass
    verdict, detail = val.judge_manual_refresh(pre, post, 1004.0, H)
    assert verdict == val.PASS
    assert "window" in detail


def test_judge_manual_refresh_cadence_alone_does_not_pass():
    """Two COMPLETE passes with the full complement between repeats is what
    ordinary cadence produces; it must never be read as forced."""
    val = _load_validator()
    H = _headline_ids()
    drill = [(1010.0, 900), (1011.0, 901)]
    pre = [(1000.0 + i, H[i]) for i in range(8)] + drill
    post = [(1023.0 + i, H[i]) for i in range(8)]          # next scheduled pass
    verdict, detail = val.judge_manual_refresh(pre, post, 1020.0, H)
    assert verdict == val.UNVERIFIED
    assert "not evidence of a product defect" in detail


def test_judge_manual_refresh_back_to_back_complete_passes_do_not_pass():
    """Timer-aligned consecutive complete passes (no drill queries between,
    e.g. a skipped drill fetch) still must not read as forced."""
    val = _load_validator()
    H = _headline_ids()
    pre = [(1000.0 + i * 0.1, H[i]) for i in range(8)]
    post = [(1015.0 + i * 0.1, H[i]) for i in range(8)]    # exactly on cadence
    verdict, _detail = val.judge_manual_refresh(pre, post, 1010.0, H)
    assert verdict == val.UNVERIFIED


def test_judge_manual_refresh_inflight_completion_is_not_forced():
    """A line that lands after the keypress but was ISSUED before it is an
    in-flight cadence call, not evidence."""
    val = _load_validator()
    H = _headline_ids()
    pre = [(1000.0, H[0]), (1007.5, H[1])]
    post = [(1010.0, H[0])]                 # issued before t_space, landed after
    verdict, _detail = val.judge_manual_refresh(pre, post, 1030.0, H)
    assert verdict == val.UNVERIFIED


def test_judge_manual_refresh_no_activity_is_an_honest_fail():
    val = _load_validator()
    H = _headline_ids()
    verdict, detail = val.judge_manual_refresh(
        [(1000.0, H[0])], [], 1010.0, H)
    assert verdict == val.FAIL
    assert "no API activity" in detail


def _stamp(epoch):
    return _time.strftime("%Y-%m-%d %H:%M:%S", _time.localtime(epoch))


def _query_line(epoch_issue, monitor_id, dur_ms=100):
    return ("%s GET https://h:443/api/monitors/%d/query/ %dms "
            "-> HTTP 200 (2 bytes) body={}"
            % (_stamp(epoch_issue + dur_ms / 1000.0), monitor_id, dur_ms))


def test_manual_refresh_check_survives_slow_line_arrival():
    """The evidence line lands well after any fixed settle would have given
    up (round 5's 6 s window read a working refresh as 'no effect'); the
    bounded log poll must still find it and PASS."""
    val = _load_validator()
    H = _headline_ids()
    now = _time.time()

    def on_space(sess):
        # Forced full pass, issued right after the keypress, but the log
        # lines only materialize 1.5 s later (slow completion).
        at = _time.time()
        for i, mid in enumerate(H):
            sess._pending.append(
                (at + 1.5, _query_line(at + 0.2 + i * 0.01, mid)))

    sess = _ScriptedSession(val, {" ": on_space})
    # Pre-context: one clean completed burst just before the check begins.
    for i, mid in enumerate(H):
        sess._lines.append(_query_line(now - 2.0 + i * 0.01, mid))
    args = SimpleNamespace(refresh_deadline=15.0)
    val.REPORT = val.Report()
    verdict = val._manual_refresh_check(sess, "cnode", args, H, attempts=1)
    assert verdict == val.PASS
    results = {n: (s, d) for n, s, d in val.REPORT.results}
    assert results["nvme.cnode.manual_refresh"][0] == val.PASS


def test_manual_refresh_check_times_out_to_an_honest_fail():
    """No API reaction at all within the bounded deadline is the only state
    that may report FAIL 'no effect'."""
    val = _load_validator()
    H = _headline_ids()
    sess = _ScriptedSession(val, {" ": lambda s: None})
    args = SimpleNamespace(refresh_deadline=1.0)
    val.REPORT = val.Report()
    verdict = val._manual_refresh_check(sess, "cnode", args, H, attempts=1)
    assert verdict == val.FAIL
    results = {n: (s, d) for n, s, d in val.REPORT.results}
    assert "no API activity" in results["nvme.cnode.manual_refresh"][1]


def test_manual_refresh_check_failure_still_quits_the_session(monkeypatch):
    """If the refresh check itself blows up mid-scenario, the session must
    still be quit and its cleanup accounting must run - same lifecycle
    guarantee the round-4 NameError violated."""
    val = _load_validator()
    events = []

    class _FakeSession:
        exit_code = None
        output = ""

        def start(self):
            events.append("start")
            return self

        def api_mark(self):
            raise RuntimeError("api log unreadable")

        def quit(self, *_a, **_k):
            events.append("quit")
            self.exit_code = 0
            return 0.0

    monkeypatch.setattr(val, "OpstatSession", lambda *a, **k: _FakeSession())
    monkeypatch.setattr(
        val, "_scenario_nvme_body",
        lambda session, args: val._manual_refresh_check(
            session, "cnode", args, [1], attempts=1))
    monkeypatch.setattr(val, "_cleanup_scenario",
                        lambda *_a, **_k: events.append("cleanup"))
    args = SimpleNamespace(drain_budget=1, vms="unused.invalid",
                           user="admin", vms_port=443, refresh_deadline=1.0)
    with pytest.raises(RuntimeError):
        val.scenario_nvme(args)
    assert "quit" in events, "exception path abandoned the opstat session"
    assert "cleanup" in events, "exception path skipped cleanup accounting"
    assert events.index("quit") < events.index("cleanup")


# ---------------------------------------------------------------------------
# Capability notice vs real error (manual-testing follow-up).
#
# A scope that publishes no per-object rows is a CAPABILITY-UNAVAILABLE
# state, not a failure: the VMS accepts the monitor and answers the query
# (D-013 measured vip and blockhost at 0 rows per object while cnode
# returned 120). It used to render as "Error: ..." in bright red, which read
# as "opstat broke". NFSv3 already set the precedent for the honest form -
# these mirror tests/test_drill_semantics.py's NFSv3 pair.
# ---------------------------------------------------------------------------
def _live_dashboard(engine):
    """Populate headline rows so frames take the normal (not waiting) path."""
    engine.poll_tick()
    assert engine.LAST_ROWS, "fixture assumption: headline rows expected"


def _nvme_frame(engine, columns=120):
    """One composed frame from the production renderer, ANSI stripped."""
    import io as _io
    import os as _os
    import shutil as _sh
    import sys as _sys

    import tui_layout as _tl

    real_size = _sh.get_terminal_size
    buf, real_stdout = _io.StringIO(), _sys.stdout
    _sh.get_terminal_size = lambda fallback=(80, 24): _os.terminal_size(
        (columns, 40))
    _sys.stdout = buf
    try:
        engine._render_frame()
    finally:
        _sys.stdout = real_stdout
        _sh.get_terminal_size = real_size
    return _tl.strip_ansi(buf.getvalue())


def _dead_scope_frame(engine, vms, key, object_type, population=None,
                      columns=120):
    """Drive a scope the cluster answers but cannot attribute per object."""
    vms.state.batch_unsplittable = {object_type: "no_matching_rows"}
    if population is not None:
        vms.state.vips = _vip_population(population)
    try:
        _live_dashboard(engine)
        engine._dispatch_key(key)
        return _nvme_frame(engine, columns=columns)
    finally:
        vms.state.vips = None
        vms.state.batch_unsplittable = {}


def test_vip_no_telemetry_renders_as_a_capability_notice(nvme, capsys):
    engine, vms = nvme
    frame = _dead_scope_frame(engine, vms, "i", "vip", population=50)
    capsys.readouterr()

    assert "Per-VIP block telemetry is not available from this cluster." in frame
    assert "Cluster-level block telemetry remains available." in frame
    assert "Press x to return to cluster view" in frame
    assert "Error:" not in frame, (
        "a proven capability-unavailable scope must not read as a failure")
    # Implementation mechanics stay in the code, not on the operator's screen.
    assert "monitor responses carry no rows" not in frame
    assert "capability probe and rank scan agree" not in frame


def test_host_no_telemetry_gets_the_same_capability_treatment(nvme, capsys):
    """blockhost reaches the identical proven state (D-013) and must get the
    identical treatment, named the way the drill already names it."""
    engine, vms = nvme
    frame = _dead_scope_frame(engine, vms, "h", "blockhost")
    capsys.readouterr()

    assert engine.NO_TELEMETRY_MARKER in engine.DRILL_ERROR, (
        "fixture assumption: the host scope must reach the no-telemetry state")
    assert ("Per-host initiator block telemetry is not available from this "
            "cluster.") in frame
    assert "Cluster-level block telemetry remains available." in frame
    assert "Error:" not in frame
    assert "blockhost" not in frame, (
        "the notice must use the drill's own name, not the object_type")


def test_capability_notice_keeps_the_footer_and_x_returns(nvme, capsys):
    engine, vms = nvme
    frame = _dead_scope_frame(engine, vms, "i", "vip", population=50)
    capsys.readouterr()
    assert "[q]" in frame and "[x]" in frame, "footer lost on the notice frame"

    outcome = engine._dispatch_key("x")
    capsys.readouterr()
    assert outcome == "refresh"
    assert engine.DRILL_ERROR is None, "x did not clear the notice state"
    back = _nvme_frame(engine)
    capsys.readouterr()
    assert engine.NO_TELEMETRY_MARKER not in back, "notice survived the exit"
    assert "[q]" in back


@pytest.mark.parametrize("columns", [120, 100, 80, 60, 40])
def test_capability_notice_fits_narrow_terminals(nvme, capsys, columns):
    """The notice uses the existing box/truncation machinery, so its own
    lines never exceed the terminal and the footer survives.

    Scoped to the lines this notice owns on purpose: the NVMe header meta
    line ("Cluster ... VMS ... Refresh") has never been truncated and can
    exceed a very narrow terminal on any frame, drill or dashboard. That is
    pre-existing and out of scope here.
    """
    engine, vms = nvme
    frame = _dead_scope_frame(engine, vms, "i", "vip", population=50,
                              columns=columns)
    capsys.readouterr()
    assert "[q]" in frame, f"footer vanished at {columns} columns"
    notice_lines = [
        line for line in frame.split("\n")
        if engine.NO_TELEMETRY_MARKER in line
        or engine.NO_TELEMETRY_DETAIL in line
        or "Press x to return" in line
    ]
    assert notice_lines, f"notice not rendered at {columns} columns"
    for line in notice_lines:
        assert tui_layout.display_width(line) <= columns, (
            f"notice line wider than terminal at {columns} cols: {line!r}")


def test_a_genuine_drill_error_still_renders_as_an_error(nvme, capsys):
    """Only the proven NO_TELEMETRY_MARKER state is reclassified. An API
    failure, a bad mode, a refused monitor - all still read as errors."""
    engine, vms = nvme
    _live_dashboard(engine)
    engine.enter_drill_mode("not-a-mode")
    frame = _nvme_frame(engine)
    capsys.readouterr()
    assert "Error:" in frame, "a real failure must still read as an error"
    assert engine.NO_TELEMETRY_MARKER not in frame
    assert "Cluster-level block telemetry remains available." not in frame
    engine.exit_drill_mode()


def test_api_failure_on_drill_entry_still_renders_as_an_error(nvme,
                                                              monkeypatch,
                                                              capsys):
    engine, vms = nvme
    _live_dashboard(engine)
    real_request = engine.api_request

    def boom(method, path, payload=None):
        if path == "/vips/":
            raise RuntimeError("GET /vips/ failed: HTTP 500: server on fire")
        return real_request(method, path, payload)

    monkeypatch.setattr(engine, "api_request", boom)
    engine._dispatch_key("i")
    frame = _nvme_frame(engine)
    capsys.readouterr()
    assert "Error:" in frame
    assert "Cannot fetch vip objects" in frame
    assert engine.NO_TELEMETRY_MARKER not in frame
    engine.exit_drill_mode()


def test_rendering_the_notice_costs_no_calls_and_no_monitors(nvme, capsys):
    """Re-rendering a cached dead-scope verdict is free: the classification
    is presentation only and must not re-probe."""
    engine, vms = nvme
    vms.state.vips = _vip_population(50)
    vms.state.batch_unsplittable = {"vip": "no_matching_rows"}
    try:
        _live_dashboard(engine)
        engine._dispatch_key("i")
        capsys.readouterr()
        before = set(vms.live_monitors())
        vms.reset_calls()
        for _ in range(3):
            _nvme_frame(engine)
        capsys.readouterr()
        assert vms.calls() == [], "rendering the notice issued API calls"
        assert set(vms.live_monitors()) == before, (
            "rendering the notice created or deleted monitors")

        # And re-entering the known-dead scope stays free (bounded probe
        # behaviour unchanged by the presentation fix).
        vms.reset_calls()
        engine._dispatch_key("i")
        capsys.readouterr()
        posts = [call for call in vms.calls() if call[1] == "POST"]
        assert posts == [], "re-entry re-probed a scope already proven dead"
    finally:
        vms.state.vips = None
        vms.state.batch_unsplittable = {}


def test_notice_and_detail_are_shared_constants():
    """One source of truth: the lab validator imports the marker, so the
    rendered notice must carry it and the detail line must be a constant
    rather than a string duplicated at each render site."""
    import nvme_tcp

    for mode, label in (("vip", "VIP"), ("host", "host initiator"),
                        ("cnode", "cNode")):
        notice = nvme_tcp._no_telemetry_notice(mode)
        assert nvme_tcp.NO_TELEMETRY_MARKER in notice
        assert notice.startswith("Per-%s " % label)
    assert nvme_tcp.NO_TELEMETRY_DETAIL == (
        "Cluster-level block telemetry remains available.")


def test_capability_notice_is_the_same_before_headline_rows_arrive(nvme,
                                                                   capsys):
    """The frame takes a different path when no headline rows exist yet; the
    capability notice must read identically there - same wording, no
    red-error treatment, footer intact."""
    engine, vms = nvme
    vms.state.vips = _vip_population(50)
    vms.state.batch_unsplittable = {"vip": "no_matching_rows"}
    try:
        assert engine.LAST_ROWS == [], "fixture assumption: no rows yet"
        engine._dispatch_key("i")
        frame = _nvme_frame(engine)
        capsys.readouterr()
        assert "Per-VIP block telemetry is not available from this cluster." in frame
        assert "Cluster-level block telemetry remains available." in frame
        assert "Press x to return to cluster view" in frame
        assert "Error:" not in frame
        assert "[q]" in frame
    finally:
        vms.state.vips = None
        vms.state.batch_unsplittable = {}
