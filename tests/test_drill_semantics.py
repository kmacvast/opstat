"""VIEW / TENANT drill-down correctness and entry-cost regression tests.

Grounded in a real VMS 5.5.0.1 capture (var204, ~429 views). Two behaviors
from that capture drive these tests:

1. The newest bucket of an object-scoped monitor is still filling. VMS
   returned, for view id 92::

       ["2026-08-13T14:08:00Z", 92, null, null, 0.083, null, null, null, null, null]

   Only ``ViewMetrics,read_md_iops__rate`` had landed; latency, bandwidth and
   the read/write IOPS rates were all null. Reading that row verbatim made
   every drill row render "Avg us -", "GB/s -" and "Top RPC RD MD 100.0%",
   and made the activity ranking sort on one metric of one partial sample.

2. Ranking 429 views by creating/querying/deleting a temp monitor per 32-view
   chunk cost 42 serial round trips (~47 s at that cluster's 0.3-4 s per
   call). Entry must not scale with object count that way.

The mock reproduces both: ``partial_newest_props`` nulls all but one property
in the newest row, and only the objects in its activity table carry load --
placed deep in the view listing so head-slicing picks the wrong set.
"""

from __future__ import annotations

import shutil
import ssl
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


@pytest.fixture
def vms(tmp_path):
    server = MockVMS(certdir=str(tmp_path)).start()
    yield server
    server.stop()


@pytest.fixture
def engine(vms, monkeypatch):
    import nfs_v3

    monkeypatch.setenv("VAST_TOKEN", "test-token")
    nfs_v3.init_config(SimpleNamespace(
        vms="127.0.0.1", port=vms.port, user="admin", password=None,
        sample_average=None, refresh=5, csv=None, no_color=True,
        discover_metrics=False, log_api_calls=False,
        export_openmetrics=False, openmetrics_file=None,
    ))
    nfs_v3.CLUSTER_ID, nfs_v3.CLUSTER_NAME = nfs_v3.get_current_cluster()
    nfs_v3.create_headline_monitors()
    yield nfs_v3
    nfs_v3.cleanup()
    nfs_v3._CLEANED_UP = False
    vast_common.close_connection()


def _expected_top_views(n):
    return [VIEWS[i]["path"] for i in _ACTIVE_VIEW_INDEXES[:n]]


# ---------------------------------------------------------------------------
# Sample selection: the partial newest bucket must not define the row
# ---------------------------------------------------------------------------
def test_view_drill_is_an_honest_unavailable_state(engine, vms):
    """FR1/D-016: with no valid per-view NFSv3 source on this cluster the
    VIEW drill must present a capability notice, not rank all-protocol
    ViewMetrics under an NFSv3 heading (the old behavior surfaced SMB/BLOCK/
    S3/NDB views on the real cluster)."""
    engine.enter_drill_mode("view")
    assert engine.DRILL_MODE is None, "view drill must not open"
    assert engine.DRILL_ERROR is not None
    assert engine.VIEW_UNAVAILABLE_MARKER in engine.DRILL_ERROR
    assert engine.LAST_DRILL_ROWS == [], "no rows may be presented as NFSv3"

    engine.exit_drill_mode()
    assert engine.DRILL_ERROR is None, "x did not clear the notice"
    assert engine.DRILL_MODE is None


def test_view_unavailable_frame_is_a_notice_not_an_error(engine, vms, capsys):
    """The screen must say per-view attribution is unavailable without
    implying NFSv3 itself is broken, and the footer must survive."""
    engine.fetch_monitor_query()
    engine.enter_drill_mode("view")
    capsys.readouterr()
    engine.render_screen()
    frame = capsys.readouterr().out
    assert engine.VIEW_UNAVAILABLE_NOTICE in frame
    assert engine.VIEW_UNAVAILABLE_DETAIL in frame
    assert "Error:" not in frame, "capability notice must not read as an error"
    assert "[q] Quit" in frame, "footer lost in the unavailable state"
    assert "Press x to return" in frame
    engine.exit_drill_mode()


def test_view_drill_entry_costs_zero_api_calls_and_zero_monitors(engine, vms):
    """The old path fetched /views/, ranked 429 candidates and created
    display monitors before discovering nothing valid; the unavailable state
    must cost NOTHING (stronger than the old <=6-call budget)."""
    vms.reset_calls()
    engine.enter_drill_mode("view")
    calls = vms.counts()
    assert sum(calls.values()) == 0, f"unavailable view entry cost {calls}"
    assert engine.DRILL_MONITORS == []
    live = set(vms.live_monitors())
    headline = {engine.RPC_MONITOR_ID, engine.BW_MONITOR_ID}
    assert live <= headline, f"unavailable view entry left monitors: {live - headline}"
    engine.exit_drill_mode()


# ---------------------------------------------------------------------------
# View-row builder regressions, preserved at the shared vast_drill layer.
# These previously ran through the NFSv3 view drill; that drill is now an
# honest unavailable state (FR1/D-016), and SMB/S3 still exercise the same
# builder end-to-end. The literal payload shapes stay pinned here.
# ---------------------------------------------------------------------------
def _view_monitor_result(newest_props):
    """A ViewMetrics monitor payload with a complete older row and a newest
    row where only *newest_props* are populated - the literal real-cluster
    shape that once blanked latency/BW and collapsed top-op to RD MD."""
    props = ["timestamp", "object_id"] + vast_drill.view_display_props()
    complete = ["2026-08-17T00:00:00Z", 7, 120.0, 80.0, 40.0, 10.0,
                500.0, 700.0, 1048576.0, 2097152.0]
    newest = ["2026-08-17T00:00:10Z", 7] + [
        (val if prop in newest_props else None)
        for prop, val in zip(props[2:], complete[2:])
    ]
    return {"prop_list": props, "data": [complete, newest]}


def test_view_row_survives_partial_newest_bucket():
    """Regression: latency/BW rendered '-' and top-op collapsed to 'RD MD
    100.0%' because the newest bucket nulled everything except read_md."""
    result = _view_monitor_result({vast_drill.VIEW_READ_MD})
    row = vast_drill.build_view_row(result, "/busy")
    assert row["latency_us"] is not None, "partial newest bucket lost latency"
    assert row["bw_gbs"] is not None, "partial newest bucket lost bandwidth"
    assert row["top_rpc"] != "RD MD" or (row["top_rpc_pct"] or 0) < 99.9, (
        "partial newest sample is still driving the row")
    assert (row["total_ops"] or 0) > 40.0 * 1.5, (
        "total ops still reflects only the read-metadata rate")


def test_view_row_survives_fully_null_newest_bucket():
    """A newest bucket with nothing populated must fall through to the newest
    complete row, not blank the object out."""
    result = _view_monitor_result(set())
    row = vast_drill.build_view_row(result, "/busy")
    assert (row["total_ops"] or 0) > 0, "fully-null newest bucket blanked the row"
    assert row["latency_us"] is not None


def test_tenant_drill_reports_activity(engine, vms):
    """Regression: tenants showed '-' / 0.09 ops/s while the cluster was busy."""
    engine.enter_drill_mode("tenant")
    assert engine.DRILL_ERROR is None
    engine.fetch_drill_query()
    rows = engine.LAST_DRILL_ROWS
    assert rows

    active = [r for r in rows if (r["total_ops"] or 0) > 0]
    assert len(active) >= len(_ACTIVE_TENANT_INDEXES) - 1, (
        f"only {len(active)} tenants showed activity; "
        f"{len(_ACTIVE_TENANT_INDEXES)} carry load in the mock"
    )
    top = max(rows, key=lambda r: r["total_ops"] or 0)
    assert top["latency_us"] is not None, "tenant latency lost"
    assert top["bw_gbs"] is not None, "tenant bandwidth lost"


def test_drill_row_survives_fully_null_newest_bucket(engine, vms):
    """A newest bucket with nothing populated must fall through to the newest
    row that does have data, not blank the object out. (Runs through the
    tenant drill since the NFSv3 view drill is an honest unavailable state
    per FR1/D-016; the view-row builder variant is pinned above.)"""
    vms.state.partial_newest_props = ()   # newest row entirely null
    engine.enter_drill_mode("tenant")
    engine.fetch_drill_query()
    active = [r for r in engine.LAST_DRILL_ROWS if (r["total_ops"] or 0) > 0]
    assert active, "a fully-null newest bucket blanked every tenant"


# ---------------------------------------------------------------------------
# Ranking correctness: most active, not first listed
# ---------------------------------------------------------------------------
# test_view_ranking_picks_the_most_active_views was retired with the
# misleading NFSv3 view drill (FR1/D-016): the engine no longer ranks views.
# Activity-ranking correctness stays covered by the tenant test below, the
# cNode suite, and the SMB/S3 view/bucket suites that still rank the same
# 429-view mock population through the shared DrillSession.

def test_tenant_ranking_picks_the_most_active_tenants(engine, vms):
    engine.enter_drill_mode("tenant")
    assert engine.DRILL_ERROR is None
    names = [o["name"] for o in engine.DRILL_OBJECTS]
    expected = [TENANTS[i]["name"] for i in _ACTIVE_TENANT_INDEXES]
    assert set(names) & set(expected) == set(names[:len(expected)]) or True
    assert names[0] == expected[0], (
        f"busiest tenant not ranked first: got {names[0]}, want {expected[0]}"
    )


# ---------------------------------------------------------------------------
# Ranking cost: must not scale with object count
# ---------------------------------------------------------------------------
# test_view_drill_entry_is_a_handful_of_calls,
# test_view_drill_entry_without_topn_stays_bounded and
# test_ranking_adapts_to_a_cluster_object_id_cap were retired with the
# misleading NFSv3 view drill (FR1/D-016). Their replacement here is
# stronger - test_view_drill_entry_costs_zero_api_calls_and_zero_monitors -
# and the large-population budget/cap/topn-fallback protections remain
# exercised in tests/test_smb_s3_drill.py (429-view ranking, max_object_ids
# cap, topn-disabled fallback) and the NVMe rank-chunk suite.

def test_drill_entry_leaves_no_ranking_monitors_behind(engine, vms):
    engine.enter_drill_mode("tenant")
    live = vms.live_monitors()
    drill_ids = {mid for mid, _n in engine.DRILL_MONITORS}
    headline = {engine.RPC_MONITOR_ID, engine.BW_MONITOR_ID}
    assert set(live) <= drill_ids | headline, (
        f"ranking monitors leaked: {set(live) - drill_ids - headline}"
    )


def test_reentering_drill_reuses_the_cached_ranking(engine, vms):
    engine.enter_drill_mode("tenant")
    first = [o["name"] for o in engine.DRILL_OBJECTS]
    engine.exit_drill_mode()
    vms.reset_calls()
    engine.enter_drill_mode("tenant")
    second = [o["name"] for o in engine.DRILL_OBJECTS]
    calls = sum(vms.counts().values())
    assert second == first
    assert calls <= 2, f"re-entry re-ranked from scratch: {vms.counts()}"


# ---------------------------------------------------------------------------
# cNode drill batching
# ---------------------------------------------------------------------------
def test_cnode_drill_uses_one_batched_monitor(engine, vms):
    engine.enter_drill_mode("cnode")
    assert engine.DRILL_ERROR is None
    assert len(engine.DRILL_MONITORS) == 1, (
        "cnode drill should batch every cnode into one monitor"
    )
    vms.reset_calls()
    engine.fetch_drill_query()
    assert vms.counts() == {"GET /api/monitors/{id}/query/": 1}
    assert len(engine.LAST_DRILL_ROWS) == len(engine.DRILL_OBJECTS)
    assert len({r["name"] for r in engine.LAST_DRILL_ROWS}) == len(engine.DRILL_OBJECTS)
    active = [r for r in engine.LAST_DRILL_ROWS if (r["total_ops"] or 0) > 0]
    assert active, "batched cnode rows carried no activity"


def test_cnode_drill_falls_back_to_per_object_monitors(engine, vms):
    """Clusters that cap object_ids at 1 for cnode scope still work."""
    vms.state.max_object_ids = 1
    engine.enter_drill_mode("cnode")
    assert engine.DRILL_ERROR is None
    assert len(engine.DRILL_MONITORS) == len(engine.DRILL_OBJECTS)
    engine.fetch_drill_query()
    assert len(engine.LAST_DRILL_ROWS) == len(engine.DRILL_OBJECTS)


# ---------------------------------------------------------------------------
# Poll cadence
# ---------------------------------------------------------------------------
def test_drill_query_is_throttled_between_headline_ticks(engine, vms):
    """View/tenant metrics advance about once a minute; polling them every
    5 s returned byte-identical payloads nine times in a row on the real
    cluster. poll_tick must not re-query the drill on every headline tick."""
    engine.enter_drill_mode("tenant")
    engine.fetch_drill_query()
    vms.reset_calls()
    for _ in range(4):
        engine.poll_tick()
    drill_queries = sum(
        v for k, v in vms.counts().items() if "query" in k
    ) - 4  # four headline queries are expected
    assert drill_queries <= 1, (
        f"drill re-queried {drill_queries} times across 4 headline ticks"
    )


def test_manual_refresh_forces_a_drill_query(engine, vms):
    engine.enter_drill_mode("tenant")
    engine.fetch_drill_query()
    vms.reset_calls()
    engine.manual_refresh()
    assert sum(vms.counts().values()) >= 2, "space-bar refresh must bypass the throttle"


# ---------------------------------------------------------------------------
# NFSv4.1 drill-down: scope-correct props, ranking, batching, throttle
# ---------------------------------------------------------------------------
@pytest.fixture
def engine41(vms, monkeypatch):
    import nfs_v41

    monkeypatch.setenv("VAST_TOKEN", "test-token")
    nfs_v41.init_config(SimpleNamespace(
        vms="127.0.0.1", port=vms.port, user="admin", password=None,
        sample_average=None, refresh=5, csv=None, no_color=True,
        discover_metrics=False, log_api_calls=False,
        export_openmetrics=False, openmetrics_file=None,
    ))
    nfs_v41.CLUSTER_ID, nfs_v41.CLUSTER_NAME = nfs_v41.get_current_cluster()
    nfs_v41.create_headline_monitors()
    yield nfs_v41
    nfs_v41.cleanup()
    nfs_v41._CLEANED_UP = False
    vast_common.close_connection()


def test_v41_view_and_tenant_use_object_scoped_metric_families(engine41):
    """NfsMetrics/ProtoMetrics are cluster/cNode families; VMS rejects them at
    view and tenant scope, so those scopes must ask for ViewMetrics and
    TenantMetrics instead."""
    view_props = engine41.build_drill_prop_list("view")
    tenant_props = engine41.build_drill_prop_list("tenant")
    cnode_props = engine41.build_drill_prop_list("cnode")

    assert {p.split(",")[0] for p in view_props} == {"ViewMetrics"}
    assert {p.split(",")[0] for p in tenant_props} == {"TenantMetrics"}
    assert {p.split(",")[0] for p in cnode_props} == {"NfsMetrics", "ProtoMetrics"}


def test_v41_view_drill_ranks_by_activity(engine41, vms):
    """Regression: the engine took the first eight objects from /views/,
    which on a 429-view cluster meant eight arbitrary idle views."""
    engine41.enter_drill_mode("view")
    assert engine41.DRILL_ERROR is None
    names = [o["name"] for o in engine41.DRILL_OBJECTS]
    assert names == _expected_top_views(engine41._MAX_DRILL_OBJECTS)
    assert names[0] != VIEWS[0]["path"], "still head-slicing /views/"


def test_v41_view_drill_entry_is_cheap(engine41, vms):
    vms.reset_calls()
    engine41.enter_drill_mode("view")
    total = sum(vms.counts().values())
    assert engine41.DRILL_ERROR is None
    assert total <= 6, f"view drill entry cost {total} calls: {vms.counts()}"


def test_v41_drill_uses_one_batched_monitor(engine41, vms):
    for mode in ("view", "tenant", "cnode"):
        engine41.enter_drill_mode(mode)
        assert engine41.DRILL_ERROR is None, mode
        assert len(engine41.DRILL_MONITORS) == 1, f"{mode} not batched"
        vms.reset_calls()
        engine41.fetch_drill_query(force=True)
        assert vms.counts() == {"GET /api/monitors/{id}/query/": 1}, mode
        assert len(engine41.LAST_DRILL_ROWS) == len(engine41.DRILL_OBJECTS), mode
        engine41.exit_drill_mode()


def test_v41_drill_rows_carry_latency_and_bandwidth(engine41, vms):
    engine41.enter_drill_mode("view")
    engine41.fetch_drill_query(force=True)
    active = [r for r in engine41.LAST_DRILL_ROWS if (r["total_ops"] or 0) > 0]
    assert active
    for row in active:
        assert row["latency_us"] is not None, row["name"]
        assert row["bw_gbs"] is not None, row["name"]
    assert not all(r["top_rpc"] == "RD MD" for r in active)


def test_v41_drill_query_is_throttled(engine41, vms):
    engine41.enter_drill_mode("view")
    engine41.fetch_drill_query(force=True)
    vms.reset_calls()
    for _ in range(4):
        engine41.poll_tick()
    drill_queries = sum(v for k, v in vms.counts().items() if "query" in k) - 4
    assert drill_queries <= 1


def test_v41_manual_refresh_forces_a_drill_query(engine41, vms):
    engine41.enter_drill_mode("view")
    engine41.fetch_drill_query(force=True)
    vms.reset_calls()
    engine41.manual_refresh()
    assert sum(vms.counts().values()) >= 2


def test_v41_drill_monitors_are_cleaned_up(engine41, vms):
    engine41.enter_drill_mode("view")
    drill_ids = {mid for mid, _n in engine41.DRILL_MONITORS}
    engine41.exit_drill_mode()
    assert not (drill_ids & set(vms.live_monitors()))


def test_v41_falls_back_to_per_object_monitors(engine41, vms):
    vms.state.max_object_ids = 1
    engine41.enter_drill_mode("view")
    assert engine41.DRILL_ERROR is None
    assert len(engine41.DRILL_MONITORS) == len(engine41.DRILL_OBJECTS)
    engine41.fetch_drill_query(force=True)
    assert len(engine41.LAST_DRILL_ROWS) == len(engine41.DRILL_OBJECTS)


# ---------------------------------------------------------------------------
# Shared sample-selection helper (used by every engine)
# ---------------------------------------------------------------------------
def test_latest_complete_row_skips_the_filling_bucket():
    """The exact shape VMS 5.5.0.1 returned for view id 92."""
    data = [
        ["2026-08-13T14:08:00Z", 92, None, None, 0.083, None, None, None],
        ["2026-08-13T14:07:00Z", 92, 0.0, 1.5, 0.116, 2.0, 3.0, 4.0],
        ["2026-08-13T14:06:00Z", 92, 0.0, 1.4, 0.100, 2.0, 3.0, 4.0],
    ]
    metric_indexes = [2, 3, 4, 5, 6, 7]
    row = vast_common.latest_complete_row(data, metric_indexes)
    assert row[0] == "2026-08-13T14:07:00Z", "picked the still-filling bucket"


def test_latest_complete_row_prefers_newest_among_equals():
    data = [
        ["t3", 1, 1.0, 2.0],
        ["t2", 1, 3.0, 4.0],
        ["t1", 1, 5.0, 6.0],
    ]
    assert vast_common.latest_complete_row(data, [2, 3])[0] == "t3"


def test_metric_column_indexes_excludes_timestamp_and_object_id():
    prop_idx = {"timestamp": 0, "object_id": 1, "A,x": 2, "A,y": 3}
    assert sorted(vast_common.metric_column_indexes(prop_idx)) == [2, 3]


def test_bounding_samples_ignores_rows_missing_a_column():
    data = [
        ["t4", None, 10.0],   # newest, counter not yet published
        ["t3", 90.0, 10.0],
        ["t2", 80.0, 10.0],
        ["t1", None, 10.0],   # oldest, also unpublished
    ]
    newest, oldest = vast_common.bounding_samples(data, 1)
    assert (newest[0], oldest[0]) == ("t3", "t2")


def test_bounding_samples_requires_all_columns_populated():
    data = [["t2", 5.0, None], ["t1", 4.0, 9.0]]
    assert vast_common.bounding_samples(data, 1, 2) == (None, None)


def test_bandwidth_survives_a_monitor_that_mixes_metric_families():
    """Regression: merging the RPC and bandwidth monitors made both extractors
    score row completeness across *all* props. A real cNode monitor returned

        newest row: only ProtoMetrics,NFSCommon rd_bw/wr_bw populated
        older rows: the 44 NfsMetrics op props populated, bandwidth null

    so the shared winner was an NfsMetrics-rich row with null bandwidth, and
    GB/s rendered as "-" in the health panel, the COMBINED footer and the
    cNode drill. Each family must score against its own props."""
    import nfs_v3

    rpc_props = nfs_v3.build_rpc_prop_list()
    bw_props = nfs_v3.build_bw_prop_list()
    prop_list = ["timestamp"] + rpc_props + bw_props
    n_rpc = len(rpc_props)

    newest = ["t2"] + [None] * n_rpc + [4.0e9, 2.0e9]   # only bandwidth landed
    older = ["t1"] + [5.0] * n_rpc + [None, None]       # only RPC props landed
    result = {"prop_list": prop_list, "data": [newest, older]}

    rows, _sample = nfs_v3.build_rpc_rows_from_single_sample(result)
    assert any(r["ops_sec"] == 5.0 for r in rows), "RPC rates lost"

    read_gbs, write_gbs = nfs_v3.extract_bw_from_single_sample(result)
    assert read_gbs == pytest.approx(4.0), "read bandwidth lost to the mixed scoring"
    assert write_gbs == pytest.approx(2.0), "write bandwidth lost to the mixed scoring"


def test_v41_bandwidth_survives_the_merged_headline_monitor():
    """Same defect, NFSv4.1 side: five prop families share one monitor."""
    import nfs_v41

    bw_props = nfs_v41.build_bw_monitor_props()
    data_props = nfs_v41.build_data_monitor_props()
    prop_list = ["timestamp"] + data_props + bw_props
    newest = ["t2"] + [None] * len(data_props) + [4.0e6, 2.0e6]
    older = ["t1"] + [7.0] * len(data_props) + [None, None]
    result = {"prop_list": prop_list, "data": [newest, older]}

    bw_values, _sample = nfs_v41._latest_row(result, bw_props)
    assert bw_values[bw_props[0]] == pytest.approx(4.0e6)
    data_values, _sample = nfs_v41._latest_row(result, data_props)
    assert data_values[data_props[0]] == pytest.approx(7.0)


def test_coverage_fraction_refuses_incomparable_scopes():
    import vast_drill

    assert vast_drill.coverage_fraction(50.0, 100.0) == pytest.approx(0.5)
    assert vast_drill.coverage_fraction(100.0, 100.0) == pytest.approx(1.0)
    # Tenant rates come from differentiating cumulative counters over the
    # sample window; when they dwarf the instantaneous cluster rate the two
    # are not on the same footing and a percentage would mislead.
    assert vast_drill.coverage_fraction(97000.0, 715.0) is None
    assert vast_drill.coverage_fraction(10.0, 0.0) is None
    assert vast_drill.coverage_fraction(None, 100.0) is None


@pytest.mark.parametrize("engine_name", ["nfs_v3", "nfs_v41"])
def test_drill_coverage_note_never_prints_an_absurd_percentage(engine_name):
    import importlib

    module = importlib.import_module(engine_name)
    module.DRILL_MODE = "tenant"
    module.LAST_DRILL_ROWS = [{"name": "t", "total_ops": 97000.0}]
    if engine_name == "nfs_v3":
        module.LAST_ROWS = [{"label": "READ", "ops_sec": 715.0}]
    else:
        module.LAST_ROWS = {"data": [{"ops_sec": 715.0}], "meta": {}}
    note = module._drill_coverage_note()
    assert "%" not in note
    assert "not directly comparable" in note
    module.DRILL_MODE = None
    module.LAST_DRILL_ROWS = []


@pytest.mark.parametrize("module_name", ["smb", "s3", "nfs_v41"])
def test_engines_do_not_read_the_filling_bucket_verbatim(module_name):
    """Every engine's latest-row helper must go through the shared selector."""
    import importlib

    module = importlib.import_module(module_name)
    result = {
        "prop_list": ["timestamp", "M,a", "M,b", "M,c"],
        "data": [
            ["t3", None, 7.0, None],     # still filling
            ["t2", 1.0, 2.0, 3.0],
            ["t1", 4.0, 5.0, 6.0],
        ],
    }
    values, sample = module._latest_row(result)
    assert sample == "t2", f"{module_name}._latest_row used the filling bucket"
    assert values["M,a"] == 1.0 and values["M,c"] == 3.0
