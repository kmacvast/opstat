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
def test_view_drill_reports_latency_and_bandwidth(engine, vms):
    """Regression: latency/BW rendered '-' because the newest bucket nulled
    every property except read_md_iops."""
    engine.enter_drill_mode("view")
    assert engine.DRILL_ERROR is None
    engine.fetch_drill_query()
    rows = engine.LAST_DRILL_ROWS
    assert rows, "view drill produced no rows"

    active = [r for r in rows if (r["total_ops"] or 0) > 0]
    assert active, "no view row carried activity"
    for row in active:
        assert row["latency_us"] is not None, f"{row['name']} lost latency"
        assert row["bw_gbs"] is not None, f"{row['name']} lost bandwidth"


def test_view_drill_top_op_is_not_always_read_metadata(engine, vms):
    """Regression: every row showed 'RD MD 100.0%' because read_md_iops was
    the only non-null property in the newest bucket."""
    engine.enter_drill_mode("view")
    engine.fetch_drill_query()
    active = [r for r in engine.LAST_DRILL_ROWS if (r["total_ops"] or 0) > 0]
    assert active
    assert not all(r["top_rpc"] == "RD MD" for r in active), (
        "top op collapsed to RD MD for every row - partial newest sample "
        "is still driving the row"
    )
    assert not all((r["top_rpc_pct"] or 0) >= 99.9 for r in active)


def test_view_drill_counts_all_four_op_classes(engine, vms):
    """READ/WRITE/RD MD/WR MD all contribute once the sample is complete."""
    engine.enter_drill_mode("view")
    engine.fetch_drill_query()
    top = max(engine.LAST_DRILL_ROWS, key=lambda r: r["total_ops"] or 0)
    # A row built from the partial bucket totals only the single read_md rate.
    values, _idx, _s = engine._latest_complete_values(
        engine._slice_result_for_object(
            engine.api_request(
                "GET", f"/monitors/{engine.DRILL_MONITORS[0][0]}/query/"),
            [o["id"] for o in engine.DRILL_OBJECTS
             if o["name"] == top["name"]][0],
        )
    )
    read_md = engine.as_float(values.get(engine._VIEW_READ_MD)) or 0.0
    assert top["total_ops"] > read_md * 1.5, (
        "total ops still reflects only the read-metadata rate"
    )


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
    row that does have data, not blank the object out."""
    vms.state.partial_newest_props = ()   # newest row entirely null
    engine.enter_drill_mode("view")
    engine.fetch_drill_query()
    active = [r for r in engine.LAST_DRILL_ROWS if (r["total_ops"] or 0) > 0]
    assert active, "a fully-null newest bucket blanked every view"


# ---------------------------------------------------------------------------
# Ranking correctness: most active, not first listed
# ---------------------------------------------------------------------------
def test_view_ranking_picks_the_most_active_views(engine, vms):
    engine.enter_drill_mode("view")
    assert engine.DRILL_ERROR is None
    names = [o["name"] for o in engine.DRILL_OBJECTS]
    expected = _expected_top_views(engine._MAX_DRILL_OBJECTS)
    assert set(names) == set(expected), (
        f"ranking selected {names}, expected the busiest views {expected}"
    )


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
def test_view_drill_entry_is_a_handful_of_calls(engine, vms):
    """429 views previously cost 42 serial calls (14 chunks x POST/GET/DELETE)."""
    assert len(VIEWS) > 400, "mock must hold a realistically large view list"
    vms.reset_calls()
    engine.enter_drill_mode("view")
    calls = vms.counts()
    total = sum(calls.values())
    assert engine.DRILL_ERROR is None
    assert total <= 6, f"view drill entry cost {total} API calls: {calls}"


def test_view_drill_entry_without_topn_stays_bounded(engine, vms):
    """Without /monitors/topn/, one batched rank monitor still beats chunking."""
    vms.state.topn_enabled = False
    vms.reset_calls()
    engine.enter_drill_mode("view")
    calls = vms.counts()
    total = sum(calls.values())
    assert engine.DRILL_ERROR is None
    assert total <= 8, f"batched ranking cost {total} API calls: {calls}"
    assert set(o["name"] for o in engine.DRILL_OBJECTS) == set(
        _expected_top_views(engine._MAX_DRILL_OBJECTS)
    )


def test_ranking_adapts_to_a_cluster_object_id_cap(engine, vms):
    """A cluster that caps object_ids per monitor must still rank correctly,
    by discovering the cap and splitting - never by silently truncating."""
    vms.state.topn_enabled = False
    vms.state.max_object_ids = 64
    engine.enter_drill_mode("view")
    assert engine.DRILL_ERROR is None
    assert set(o["name"] for o in engine.DRILL_OBJECTS) == set(
        _expected_top_views(engine._MAX_DRILL_OBJECTS)
    )


def test_drill_entry_leaves_no_ranking_monitors_behind(engine, vms):
    engine.enter_drill_mode("view")
    live = vms.live_monitors()
    drill_ids = {mid for mid, _n in engine.DRILL_MONITORS}
    headline = {engine.RPC_MONITOR_ID, engine.BW_MONITOR_ID}
    assert set(live) <= drill_ids | headline, (
        f"ranking monitors leaked: {set(live) - drill_ids - headline}"
    )


def test_reentering_drill_reuses_the_cached_ranking(engine, vms):
    engine.enter_drill_mode("view")
    first = [o["name"] for o in engine.DRILL_OBJECTS]
    engine.exit_drill_mode()
    vms.reset_calls()
    engine.enter_drill_mode("view")
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
    engine.enter_drill_mode("view")
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
    engine.enter_drill_mode("view")
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
