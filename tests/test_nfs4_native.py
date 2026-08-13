"""Native NFSv4 exporter drill: derivation, cost and failure behavior.

The VMS monitor API exposes no NFSv4.1 protocol-state counters; the
Prometheus exporter does, through Nfs4Metrics. Those counters are cumulative
gauges in microseconds, and the narrowest endpoint carrying the family costs
~276 KB and 1.2-2.4 s against a real cluster - so the drill is on demand,
throttled, and must never appear on the dashboard refresh path.
"""

from __future__ import annotations

import shutil
import time
from types import SimpleNamespace

import pytest

import nfs4_native
import vast_common
from tests.mock_vms import MockVMS

pytestmark = pytest.mark.skipif(
    shutil.which("openssl") is None,
    reason="openssl binary required to generate the mock VMS certificate",
)

ALL_OPS = nfs4_native.__dict__  # placeholder to keep linters quiet


def exposition(count_scale=1.0, latency_us=500.0, ops=("read", "write",
                                                       "sequence", "open"),
               connections=42, cnodes=(("1", "cnode-01"), ("2", "cnode-02"))):
    """Build a minimal Nfs4Metrics body with controllable totals."""
    lines = []
    for op in ops:
        base = f"vast_cluster_metrics_Nfs4Metrics_nfs4_{op}_req_latency"
        count = 1000.0 * count_scale
        lines.append(f'{base}_count{{cluster="c"}} {count}')
        lines.append(f'{base}_sum{{cluster="c"}} {count * latency_us}')
        for cid, host in cnodes:
            cbase = f"vast_cnode_metrics_Nfs4Metrics_nfs4_{op}_req_latency"
            ccount = count / len(cnodes)
            lines.append(
                f'{cbase}_count{{cluster="c",cnode_id="{cid}",'
                f'hostname="{host}"}} {ccount}')
            lines.append(
                f'{cbase}_sum{{cluster="c",cnode_id="{cid}",'
                f'hostname="{host}"}} {ccount * latency_us}')
    lines.append(
        'vast_cluster_metrics_Nfs4Metrics_nfs4_open_connections_cnt'
        f'{{cluster="c"}} {connections}')
    for cid, host in cnodes:
        lines.append(
            'vast_cnode_metrics_Nfs4Metrics_nfs4_open_connections_cnt'
            f'{{cluster="c",cnode_id="{cid}",hostname="{host}"}} 7')
    return "\n".join(lines) + "\n"


class FakeTransport:
    """Returns queued bodies, records paths, and can fail or stall."""

    def __init__(self, bodies):
        self.bodies = list(bodies)
        self.paths = []
        self.delay = 0.0

    def __call__(self, _method, path, *args, **kwargs):
        self.paths.append(path)
        if self.delay:
            time.sleep(self.delay)
        body = self.bodies.pop(0) if self.bodies else ""
        if isinstance(body, Exception):
            raise body
        return body


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------
def test_parses_cluster_cnode_and_connection_series():
    cluster, cnodes, connections = nfs4_native.parse_nfs4_exposition(exposition())
    assert set(cluster) == {"read", "write", "sequence", "open"}
    assert cluster["read"]["count"] == 1000.0
    assert cluster["read"]["sum"] == 500000.0
    assert set(cnodes) == {("1", "cnode-01"), ("2", "cnode-02")}
    assert connections["cluster"] == 42
    assert connections["cnodes"][("1", "cnode-01")] == 7


def test_parses_all_twentynine_exported_operations():
    from tests.mock_vms import NFS4_EXPORTER_OPS, _nfs4_exposition

    cluster, cnodes, connections = nfs4_native.parse_nfs4_exposition(
        _nfs4_exposition(10.0))
    assert set(cluster) == set(NFS4_EXPORTER_OPS)
    assert len(cluster) == 29
    for op in ("putfh", "getfh", "access", "savefh", "restorefh",
               "secinfo_no_name", "lookupp", "sequence", "test_stateid"):
        assert op in cluster, f"{op} not parsed"
    assert connections["cluster"] is not None
    assert cnodes, "no per-cNode series parsed"


def test_ignores_unrelated_and_malformed_lines():
    body = (
        "# HELP something Not ours\n"
        "vast_other_metric{cluster=\"c\"} 5\n"
        "garbage line without a value\n"
        'vast_cluster_metrics_Nfs4Metrics_nfs4_read_req_latency_count{cluster="c"} NaN\n'
        'vast_cluster_metrics_Nfs4Metrics_nfs4_write_req_latency_count{cluster="c"} 12\n'
    )
    cluster, _cnodes, _conn = nfs4_native.parse_nfs4_exposition(body)
    assert "read" not in cluster, "NaN accepted as a reading"
    assert cluster["write"]["count"] == 12.0


# ---------------------------------------------------------------------------
# Warm-up and derivation
# ---------------------------------------------------------------------------
def test_first_scrape_is_warm_up_and_yields_no_rates():
    transport = FakeTransport([exposition()])
    collector = nfs4_native.Nfs4Collector(transport)
    assert collector.scrape() is True
    assert collector.warm is False
    assert collector.cluster_rows() == []
    assert collector.cnode_rows() == []
    assert collector.ops_per_compound() is None


def test_second_scrape_derives_rates_and_microsecond_latency(monkeypatch):
    transport = FakeTransport([
        exposition(count_scale=1.0, latency_us=500.0),
        exposition(count_scale=2.0, latency_us=500.0),
    ])
    collector = nfs4_native.Nfs4Collector(transport, min_interval=0)
    clock = [1000.0]
    monkeypatch.setattr(nfs4_native.time, "monotonic", lambda: clock[0])
    collector.scrape()
    clock[0] += 10.0
    collector.scrape()

    assert collector.warm is True
    rows = collector.rows_by_op()
    # count went 1000 -> 2000 over 10s.
    assert rows["read"]["ops_sec"] == pytest.approx(100.0)
    # sum delta / count delta = mean microseconds per operation.
    assert rows["read"]["avg_us"] == pytest.approx(500.0)
    assert rows["read"]["count_delta"] == pytest.approx(1000.0)


def test_shares_sum_to_one_hundred_percent(monkeypatch):
    transport = FakeTransport([exposition(1.0), exposition(3.0)])
    collector = nfs4_native.Nfs4Collector(transport, min_interval=0)
    clock = [0.0]
    monkeypatch.setattr(nfs4_native.time, "monotonic", lambda: clock[0])
    collector.scrape()
    clock[0] += 5.0
    collector.scrape()
    total = sum(r["share_pct"] for r in collector.cluster_rows())
    assert total == pytest.approx(100.0)


def test_zero_delta_operations_report_zero_not_missing(monkeypatch):
    """Zero session churn is information; it must not render as absent."""
    transport = FakeTransport([exposition(1.0), exposition(1.0)])
    collector = nfs4_native.Nfs4Collector(transport, min_interval=0)
    clock = [0.0]
    monkeypatch.setattr(nfs4_native.time, "monotonic", lambda: clock[0])
    collector.scrape()
    clock[0] += 10.0
    collector.scrape()
    rows = collector.rows_by_op()
    assert rows["sequence"]["ops_sec"] == 0.0
    assert rows["sequence"]["count_delta"] == 0.0
    # No operations completed, so there is no interval latency to report.
    assert rows["sequence"]["avg_us"] is None


def test_counter_reset_never_produces_a_negative_rate(monkeypatch):
    transport = FakeTransport([exposition(5.0), exposition(1.0)])
    collector = nfs4_native.Nfs4Collector(transport, min_interval=0)
    clock = [0.0]
    monkeypatch.setattr(nfs4_native.time, "monotonic", lambda: clock[0])
    collector.scrape()
    clock[0] += 10.0
    collector.scrape()
    for row in collector.cluster_rows():
        assert row["ops_sec"] >= 0.0, f"negative rate for {row['op']}"
    # A reset drops the affected series rather than inventing a value.
    assert collector.rows_by_op() == {}


def test_zero_or_negative_elapsed_is_rejected():
    assert nfs4_native._rate({"count": 1.0}, {"count": 2.0}, 0) is None
    assert nfs4_native._rate({"count": 1.0}, {"count": 2.0}, -5) is None
    assert nfs4_native._rate(None, {"count": 2.0}, 10) is None


def test_ops_per_compound_excludes_sequence_from_the_numerator(monkeypatch):
    body_a = exposition(1.0, ops=("sequence", "read", "write"))
    body_b = exposition(2.0, ops=("sequence", "read", "write"))
    transport = FakeTransport([body_a, body_b])
    collector = nfs4_native.Nfs4Collector(transport, min_interval=0)
    clock = [0.0]
    monkeypatch.setattr(nfs4_native.time, "monotonic", lambda: clock[0])
    collector.scrape()
    clock[0] += 10.0
    collector.scrape()
    # read and write each match sequence's rate, so the ratio is exactly 2.
    assert collector.ops_per_compound() == pytest.approx(2.0)


def test_cnode_rows_come_from_the_same_scrape(monkeypatch):
    transport = FakeTransport([exposition(1.0), exposition(2.0)])
    collector = nfs4_native.Nfs4Collector(transport, min_interval=0)
    clock = [0.0]
    monkeypatch.setattr(nfs4_native.time, "monotonic", lambda: clock[0])
    collector.scrape()
    clock[0] += 10.0
    collector.scrape()
    rows = collector.cnode_rows()
    assert len(rows) == 2
    assert {r["hostname"] for r in rows} == {"cnode-01", "cnode-02"}
    assert all(r["total_ops"] > 0 for r in rows)
    assert all(r["connections"] == 7 for r in rows)
    # Two scrapes total: no extra request was made for cNode data.
    assert len(transport.paths) == 2


def test_ranking_orders_operations_by_rate(monkeypatch):
    first = exposition(1.0, ops=("read", "write", "open"))
    second = "\n".join([
        'vast_cluster_metrics_Nfs4Metrics_nfs4_read_req_latency_count{cluster="c"} 5000',
        'vast_cluster_metrics_Nfs4Metrics_nfs4_read_req_latency_sum{cluster="c"} 2500000',
        'vast_cluster_metrics_Nfs4Metrics_nfs4_write_req_latency_count{cluster="c"} 3000',
        'vast_cluster_metrics_Nfs4Metrics_nfs4_write_req_latency_sum{cluster="c"} 1500000',
        'vast_cluster_metrics_Nfs4Metrics_nfs4_open_req_latency_count{cluster="c"} 1200',
        'vast_cluster_metrics_Nfs4Metrics_nfs4_open_req_latency_sum{cluster="c"} 600000',
    ]) + "\n"
    transport = FakeTransport([first, second])
    collector = nfs4_native.Nfs4Collector(transport, min_interval=0)
    clock = [0.0]
    monkeypatch.setattr(nfs4_native.time, "monotonic", lambda: clock[0])
    collector.scrape()
    clock[0] += 10.0
    collector.scrape()
    assert [r["op"] for r in collector.ranked_rows()] == ["read", "write", "open"]


# ---------------------------------------------------------------------------
# Throttling and endpoint choice
# ---------------------------------------------------------------------------
def test_scrape_is_throttled_and_space_can_force_it(monkeypatch):
    transport = FakeTransport([exposition()] * 5)
    collector = nfs4_native.Nfs4Collector(transport, min_interval=30.0)
    clock = [0.0]
    monkeypatch.setattr(nfs4_native.time, "monotonic", lambda: clock[0])
    assert collector.scrape() is True          # first is always allowed
    clock[0] += 5.0
    assert collector.scrape() is False, "re-scraped inside the throttle window"
    assert collector.scrape(force=True) is True, "space could not force it"
    clock[0] += 31.0
    assert collector.scrape() is True
    assert len(transport.paths) == 3


def test_default_endpoint_is_basic_and_never_all():
    transport = FakeTransport([exposition()])
    collector = nfs4_native.Nfs4Collector(transport)
    assert collector.endpoint == "/prometheusmetrics/basic"
    collector.scrape()
    assert transport.paths == ["/prometheusmetrics/basic"]
    assert not any("all" in p for p in transport.paths)


# ---------------------------------------------------------------------------
# Failure behavior
# ---------------------------------------------------------------------------
def test_failed_scrape_preserves_the_last_good_sample(monkeypatch):
    transport = FakeTransport([
        exposition(1.0), exposition(2.0), RuntimeError("HTTP 503: unavailable"),
    ])
    collector = nfs4_native.Nfs4Collector(transport, min_interval=0)
    clock = [0.0]
    monkeypatch.setattr(nfs4_native.time, "monotonic", lambda: clock[0])
    collector.scrape()
    clock[0] += 10.0
    collector.scrape()
    good = collector.rows_by_op()["read"]["ops_sec"]

    clock[0] += 10.0
    assert collector.scrape() is False
    assert collector.error and "503" in collector.error
    assert collector.warm is True, "last good sample discarded on failure"
    assert collector.rows_by_op()["read"]["ops_sec"] == pytest.approx(good)


def test_empty_or_unrelated_body_is_reported_not_crashed():
    collector = nfs4_native.Nfs4Collector(FakeTransport(["", ]))
    assert collector.scrape() is False
    assert collector.error and "no Nfs4Metrics" in collector.error

    collector2 = nfs4_native.Nfs4Collector(FakeTransport(["<html>oops</html>"]))
    assert collector2.scrape() is False
    assert collector2.error


# ---------------------------------------------------------------------------
# Client / view attribution
# ---------------------------------------------------------------------------
HOST_VIEW_BODY = """# HELP vast_host_view_iops VMS host-view iops
# TYPE vast_host_view_iops gauge
vast_host_view_iops{ip="10.0.0.1",path="/a",protocol="NFS4",tenant="t1"} 40
vast_host_view_iops{ip="10.0.0.2",path="/b",protocol="NFS4",tenant="t1"} 90
vast_host_view_iops{ip="10.0.0.3",path="/c",protocol="NFS3",tenant="t1"} 500
vast_host_view_iops{ip="10.0.0.4",path="/d",protocol="SMB",tenant="t2"} 800
vast_host_view_read_iops{ip="10.0.0.2",path="/b",protocol="NFS4",tenant="t1"} 60
vast_host_view_write_iops{ip="10.0.0.2",path="/b",protocol="NFS4",tenant="t1"} 30
vast_host_view_bw{ip="10.0.0.2",path="/b",protocol="NFS4",tenant="t1"} 1048576
vast_host_view_latency{ip="10.0.0.2",path="/b",protocol="NFS4",tenant="t1"} 812
"""


def test_host_view_filters_strictly_to_nfs4():
    rows = nfs4_native.parse_host_view(HOST_VIEW_BODY, protocol="NFS4")
    assert {r["ip"] for r in rows} == {"10.0.0.1", "10.0.0.2"}
    assert all(r["ip"] not in ("10.0.0.3", "10.0.0.4") for r in rows)


def test_host_view_ranks_by_activity_not_api_order():
    rows = nfs4_native.parse_host_view(HOST_VIEW_BODY, protocol="NFS4")
    assert [r["ip"] for r in rows] == ["10.0.0.2", "10.0.0.1"]


def test_host_view_carries_every_attribution_field():
    rows = nfs4_native.parse_host_view(HOST_VIEW_BODY, protocol="NFS4")
    busiest = rows[0]
    assert busiest["path"] == "/b"
    assert busiest["tenant"] == "t1"
    assert busiest["read_iops"] == 60.0
    assert busiest["write_iops"] == 30.0
    assert busiest["bw"] == 1048576.0
    assert busiest["latency_us"] == 812.0


def test_host_view_collector_needs_no_warm_up_and_throttles(monkeypatch):
    transport = FakeTransport([HOST_VIEW_BODY] * 3)
    collector = nfs4_native.HostViewCollector(transport, min_interval=30.0)
    clock = [0.0]
    monkeypatch.setattr(nfs4_native.time, "monotonic", lambda: clock[0])
    assert collector.scrape() is True
    assert collector.rows, "gauges should be usable from one scrape"
    clock[0] += 5.0
    assert collector.scrape() is False
    assert collector.scrape(force=True) is True
    assert transport.paths == ["/prometheusmetrics/host_view"] * 2


def test_host_view_failure_keeps_previous_rows():
    transport = FakeTransport([HOST_VIEW_BODY, RuntimeError("HTTP 500")])
    collector = nfs4_native.HostViewCollector(transport, min_interval=0)
    collector.scrape()
    before = len(collector.rows)
    assert collector.scrape() is False
    assert collector.error and "500" in collector.error
    assert len(collector.rows) == before


# ---------------------------------------------------------------------------
# Engine integration: cost and isolation from the refresh path
# ---------------------------------------------------------------------------
@pytest.fixture
def engine(tmp_path, monkeypatch):
    server = MockVMS(certdir=str(tmp_path)).start()
    import nfs_v41

    monkeypatch.setenv("VAST_TOKEN", "test-token")
    nfs_v41.init_config(SimpleNamespace(
        vms="127.0.0.1", port=server.port, user="admin", password=None,
        sample_average=None, refresh=5, csv=None, no_color=True,
        discover_metrics=False, log_api_calls=False,
        export_openmetrics=False, openmetrics_file=None,
    ))
    nfs_v41.CLUSTER_ID, nfs_v41.CLUSTER_NAME = nfs_v41.get_current_cluster()
    nfs_v41.create_headline_monitors()
    yield nfs_v41, server
    nfs_v41.exit_exporter_mode()
    nfs_v41.cleanup()
    nfs_v41._CLEANED_UP = False
    vast_common.close_connection()
    server.stop()


def test_cluster_refresh_never_scrapes_the_exporter(engine):
    """The 5s dashboard tick must not touch /prometheusmetrics/*."""
    nfs_v41, server = engine
    server.reset_calls()
    for _ in range(5):
        nfs_v41.poll_tick()
    paths = [p for _t, _m, p, _s in server.calls()]
    assert not any("prometheus" in p for p in paths), (
        f"exporter scraped on the refresh path: {paths}")
    assert server.counts() == {"GET /api/monitors/{id}/query/": 5}


def test_entering_the_native_drill_scrapes_basic_once(engine):
    nfs_v41, server = engine
    server.reset_calls()
    nfs_v41.enter_exporter_mode("native")
    paths = [p for _t, _m, p, _s in server.calls() if "prometheus" in p]
    assert paths == ["/api/prometheusmetrics/basic"]
    assert nfs_v41.EXPORTER_MODE == "native"
    assert nfs_v41.EXPORTER_STATUS is None, "status left on screen after scrape"
    assert not any("/all" in p for p in paths)


def test_idle_native_drill_is_throttled(engine, monkeypatch):
    nfs_v41, server = engine
    nfs_v41.enter_exporter_mode("native")
    server.reset_calls()
    for _ in range(6):
        nfs_v41.poll_tick()
    scrapes = [p for _t, _m, p, _s in server.calls() if "prometheus" in p]
    assert scrapes == [], f"re-scraped inside the throttle window: {scrapes}"


def test_space_forces_a_native_rescrape(engine):
    nfs_v41, server = engine
    nfs_v41.enter_exporter_mode("native")
    server.reset_calls()
    nfs_v41.manual_refresh()
    scrapes = [p for _t, _m, p, _s in server.calls() if "prometheus" in p]
    assert scrapes == ["/api/prometheusmetrics/basic"]


def test_hosts_drill_uses_host_view_only(engine):
    nfs_v41, server = engine
    server.reset_calls()
    nfs_v41.enter_exporter_mode("hosts")
    paths = [p for _t, _m, p, _s in server.calls() if "prometheus" in p]
    assert paths == ["/api/prometheusmetrics/host_view"]
    assert nfs_v41.HOSTVIEW.rows or nfs_v41.HOSTVIEW.error is None


def test_exporter_drill_leaves_no_monitors_behind(engine):
    nfs_v41, server = engine
    before = set(server.live_monitors())
    nfs_v41.enter_exporter_mode("native")
    nfs_v41.enter_exporter_mode("hosts")
    nfs_v41.exit_exporter_mode()
    assert set(server.live_monitors()) == before


def test_exporter_drill_never_calls_the_delete_delegation_endpoint(engine):
    nfs_v41, server = engine
    nfs_v41.enter_exporter_mode("native")
    nfs_v41.enter_exporter_mode("hosts")
    for _ in range(3):
        nfs_v41.poll_tick()
    bad = [p for _t, m, p, _s in server.calls()
           if m == "DELETE" and "nfs4_deleg" in p]
    assert bad == []


def test_cnode_open_connections_are_reported(monkeypatch):
    """The exporter publishes the connection gauge per cNode as well as per
    cluster; the column must carry it rather than always showing '-'."""
    from tests.mock_vms import _nfs4_exposition

    transport = FakeTransport([_nfs4_exposition(0.0), _nfs4_exposition(10.0)])
    collector = nfs4_native.Nfs4Collector(transport, min_interval=0)
    clock = [0.0]
    monkeypatch.setattr(nfs4_native.time, "monotonic", lambda: clock[0])
    collector.scrape()
    clock[0] += 10.0
    collector.scrape()
    rows = collector.cnode_rows()
    assert rows
    assert all(r["connections"] is not None for r in rows), (
        "per-cNode open connections not parsed")
    assert collector.connections["cluster"] is not None
