"""NFSv4.1 telemetry discovery and evidence-gated panel tests.

The NFSv4.1 dashboard fell back to NFSv3-shaped metadata proxy panels
whenever ``probe_available_state_ops()`` found nothing, and that probe
matched exactly one metric spelling (``nfs_{op}_latency``). If a build names
its v4.1 counters differently the panel silently disappears and the
dashboard stops being v4.1-specific at all - with no way to tell whether the
cluster lacks the telemetry or opstat simply failed to recognise it.

These tests cover the catalog reader, the concept sweep, the live property
probe, and the rule that a panel renders only for operations the cluster
actually returned.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

import vast_common
from tests import mock_vms as mock_vms_module
from tests.mock_vms import MockVMS

pytestmark = pytest.mark.skipif(
    shutil.which("openssl") is None,
    reason="openssl binary required to generate the mock VMS certificate",
)

PNFS_CATALOG = [
    f"NfsMetrics,nfs_{op}_latency__{kind}"
    for op in ("layoutget", "layoutreturn", "layoutcommit", "getdeviceinfo")
    for kind in ("rate", "avg")
]


@pytest.fixture
def vms(tmp_path):
    server = MockVMS(certdir=str(tmp_path)).start()
    yield server
    server.stop()


def _init(vms, monkeypatch):
    import nfs_v41

    monkeypatch.setenv("VAST_TOKEN", "test-token")
    nfs_v41.init_config(SimpleNamespace(
        vms="127.0.0.1", port=vms.port, user="admin", password=None,
        sample_average=None, refresh=5, csv=None, no_color=True,
        discover_metrics=False, log_api_calls=False,
        export_openmetrics=False, openmetrics_file=None,
    ))
    nfs_v41.CLUSTER_ID, nfs_v41.CLUSTER_NAME = nfs_v41.get_current_cluster()
    return nfs_v41


# ---------------------------------------------------------------------------
# Catalog reader
# ---------------------------------------------------------------------------
def test_catalog_reader_returns_metric_fqns_only():
    payload = {"data": [
        {"metric": "NfsMetrics,nfs_open_latency__rate"},
        {"metric": "ProtoMetrics,proto_name=NFS4Common,rd_iops"},
        {"note": "a free-text description, not a metric"},
        {"link": "https://vms/api/metrics/"},
    ]}
    names = vast_common.collect_metric_names(payload)
    assert "a free-text description, not a metric" in names
    # Prose containing a comma must not be mistaken for a metric FQN.
    assert vast_common.filter_metric_names(names) == {
        "NfsMetrics,nfs_open_latency__rate",
        "ProtoMetrics,proto_name=NFS4Common,rd_iops",
    }


def test_catalog_reader_follows_pagination():
    pages = {
        "/metrics/": {"data": [{"metric": "A,one"}],
                      "next": "https://vms/api/metrics/?page=2"},
        "/metrics/?page=2": {"data": [{"metric": "A,two"}]},
    }
    seen = []

    def fake_request(_method, path, payload=None):
        seen.append(path)
        return pages[path]

    names = vast_common.fetch_metric_catalog(fake_request)
    assert names == {"A,one", "A,two"}
    assert seen == ["/metrics/", "/metrics/?page=2"]


def test_catalog_reader_survives_an_unreadable_catalog():
    def boom(_method, _path, payload=None):
        raise RuntimeError("HTTP 403")

    assert vast_common.fetch_metric_catalog(boom) == set()


def test_metric_family_splits_on_the_first_comma():
    assert vast_common.metric_family(
        "ProtoMetrics,proto_name=NFS4Common,rd_iops") == "ProtoMetrics"
    assert vast_common.metric_family("NfsMetrics,nfs_open_latency__rate") == "NfsMetrics"


# ---------------------------------------------------------------------------
# Op detection across metric spellings
# ---------------------------------------------------------------------------
def test_op_detection_accepts_the_nfs_and_nfs4_prefixes():
    """Regression: matching only ``nfs_{op}_latency`` meant a build naming its
    counters differently reported zero v4.1 ops and the panel vanished."""
    import nfs_v41

    for name in ("NfsMetrics,nfs_open_latency__rate",
                 "NfsMetrics,nfs_open_latency__avg",
                 "NfsMetrics,nfs4_open_latency__rate",
                 "NfsMetrics,nfs_open"):
        assert nfs_v41._catalog_exports_op({name}, "open"), name


def test_op_detection_rejects_nfsv3_and_interop_counters():
    """Real VAST 5.5.0.1 catalog entries that must NOT count as NFSv4 ops."""
    import nfs_v41

    assert not nfs_v41._catalog_exports_op(
        {"NfsMetrics,nfs3_open_file_handle_cnt",
         "NfsMetrics,nfs3_open_file_handle_ram_cache_hit"}, "open")
    assert not nfs_v41._catalog_exports_op(
        {"NfsMetrics,nfs3_smb_interop_handles_closed"}, "close")


def test_op_detection_does_not_confuse_similar_op_names():
    import nfs_v41

    catalog = {"NfsMetrics,nfs_release_lockowner_latency__rate"}
    assert nfs_v41._catalog_exports_op(catalog, "release_lockowner")
    assert not nfs_v41._catalog_exports_op(catalog, "layoutget")
    # nfs_open_ must not swallow nfs_open_downgrade_
    assert not nfs_v41._catalog_exports_op(
        {"NfsMetrics,nfs_open_downgrade_latency__rate"}, "open")


def test_concept_scan_does_not_match_substrings_inside_words():
    """Regression: 'lock' matched every BlockMetrics name through 'b-lock';
    431 of 434 hits on a real catalog were that noise."""
    import nfs_v41

    noise = {
        "BlockMetrics,access_cache_hit_rate__avg",
        "ProtoMetrics,proto_name=BlockCommon,rd_bw",
        "BlockMetrics,write_zeroes_latency__max",
    }
    real = {"NfsMetrics,nfs_lock_latency__rate"}
    hits = nfs_v41.concept_scan(noise | real)
    assert hits.get("lock") == ["NfsMetrics,nfs_lock_latency__rate"]


def test_concept_scan_still_matches_token_prefixes():
    import nfs_v41

    names = {
        "NfsMetrics,nfs_delegreturn_latency__rate",
        "NfsMetrics,nfs_layoutget_latency__rate",
        "ProtoMetrics,proto_name=NFS4Common,rd_iops",
    }
    hits = nfs_v41.concept_scan(names)
    assert hits["deleg"] == ["NfsMetrics,nfs_delegreturn_latency__rate"]
    assert hits["layout"] == ["NfsMetrics,nfs_layoutget_latency__rate"]
    assert hits["nfs4"] == ["ProtoMetrics,proto_name=NFS4Common,rd_iops"]


def test_concept_scan_groups_catalog_names_by_keyword():
    import nfs_v41

    names = {
        "NfsMetrics,nfs_sequence_latency__rate",
        "NfsMetrics,nfs_layoutget_latency__rate",
        "NfsMetrics,nfs_read_latency__rate",
    }
    hits = nfs_v41.concept_scan(names)
    assert hits["sequence"] == ["NfsMetrics,nfs_sequence_latency__rate"]
    assert hits["layout"] == ["NfsMetrics,nfs_layoutget_latency__rate"]
    assert "read" not in hits          # not an NFSv4.1 concept keyword


# ---------------------------------------------------------------------------
# Live property probe
# ---------------------------------------------------------------------------
def test_prop_probe_separates_supported_from_rejected(vms, monkeypatch):
    nfs_v41 = _init(vms, monkeypatch)
    vms.state.unsupported_prop_prefixes = ("NfsMetrics,nfs_layoutget",)
    supported, rejected = nfs_v41.probe_prop_support([
        "NfsMetrics,nfs_open_latency__rate",
        "NfsMetrics,nfs_layoutget_latency__rate",
    ])
    assert supported == ["NfsMetrics,nfs_open_latency__rate"]
    assert rejected == ["NfsMetrics,nfs_layoutget_latency__rate"]
    assert vms.live_monitors() == {}, "probe monitors must not survive"


# ---------------------------------------------------------------------------
# Evidence-gated panels
# ---------------------------------------------------------------------------
def test_pnfs_panel_absent_when_cluster_exports_no_layout_metrics(vms, monkeypatch):
    nfs_v41 = _init(vms, monkeypatch)
    nfs_v41.create_headline_monitors()
    assert nfs_v41.STATE_OPS_AVAILABLE, "state ops should be detected"
    assert nfs_v41.PNFS_OPS_AVAILABLE == []
    nfs_v41.fetch_monitor_query()
    assert nfs_v41.LAST_ROWS.get("pnfs") == []
    nfs_v41.cleanup()
    nfs_v41._CLEANED_UP = False


def test_pnfs_panel_appears_when_cluster_exports_layout_metrics(vms, monkeypatch):
    vms.state.catalog = set(mock_vms_module.DEFAULT_CATALOG) | set(PNFS_CATALOG)
    nfs_v41 = _init(vms, monkeypatch)
    nfs_v41.create_headline_monitors()
    labels = [label for _op, label in nfs_v41.PNFS_OPS_AVAILABLE]
    assert "LAYOUTGET" in labels and "LAYOUTCOMMIT" in labels
    nfs_v41.fetch_monitor_query()
    assert len(nfs_v41.LAST_ROWS["pnfs"]) == len(nfs_v41.PNFS_OPS_AVAILABLE)
    nfs_v41.cleanup()
    nfs_v41._CLEANED_UP = False


def test_pnfs_metrics_cost_no_extra_query_per_refresh(vms, monkeypatch):
    vms.state.catalog = set(mock_vms_module.DEFAULT_CATALOG) | set(PNFS_CATALOG)
    nfs_v41 = _init(vms, monkeypatch)
    nfs_v41.create_headline_monitors()
    vms.reset_calls()
    nfs_v41.fetch_monitor_query()
    assert vms.counts() == {"GET /api/monitors/{id}/query/": 1}
    nfs_v41.cleanup()
    nfs_v41._CLEANED_UP = False


def test_panels_never_show_ops_the_cluster_did_not_return(vms, monkeypatch):
    """A build that advertises an op in the catalog but does not return it in
    a monitor's prop_list must not get a row - that would be a fabricated
    metric."""
    vms.state.catalog = set(mock_vms_module.DEFAULT_CATALOG) | set(PNFS_CATALOG)
    vms.state.unsupported_prop_prefixes = ("NfsMetrics,nfs_layout",)
    nfs_v41 = _init(vms, monkeypatch)
    nfs_v41.create_headline_monitors()
    labels = [label for _op, label in nfs_v41.PNFS_OPS_AVAILABLE]
    assert "LAYOUTGET" not in labels
    assert "GETDEVICEINFO" in labels, "unaffected op should still be available"
    nfs_v41.cleanup()
    nfs_v41._CLEANED_UP = False


# ---------------------------------------------------------------------------
# NFS4Common statistical surface
# ---------------------------------------------------------------------------
def test_distribution_panel_appears_when_max_and_std_are_returned(vms, monkeypatch):
    nfs_v41 = _init(vms, monkeypatch)
    nfs_v41.create_headline_monitors()
    bases = [base for base, _label, _kind in nfs_v41.DISTRIBUTION_AVAILABLE]
    assert bases == ["read_latency", "write_latency", "read_size", "write_size"]
    nfs_v41.fetch_monitor_query()
    rows = nfs_v41.LAST_ROWS["distribution"]
    assert len(rows) == 4
    for row in rows:
        assert row["avg"] is not None and row["max"] is not None
    nfs_v41.cleanup()
    nfs_v41._CLEANED_UP = False


def test_distribution_panel_absent_when_only_avg_is_exported(vms, monkeypatch):
    """A build publishing only __avg must not get a distribution panel."""
    vms.state.unsupported_prop_prefixes = tuple(
        f"ProtoMetrics,proto_name=NFS4Common,{base}__{s}"
        for base in ("read_latency", "write_latency", "read_size", "write_size")
        for s in ("max", "std")
    )
    nfs_v41 = _init(vms, monkeypatch)
    nfs_v41.create_headline_monitors()
    assert nfs_v41.DISTRIBUTION_AVAILABLE == []
    nfs_v41.fetch_monitor_query()
    assert nfs_v41.LAST_ROWS["distribution"] == []
    nfs_v41.cleanup()
    nfs_v41._CLEANED_UP = False


def test_distribution_costs_no_extra_query_per_refresh(vms, monkeypatch):
    nfs_v41 = _init(vms, monkeypatch)
    nfs_v41.create_headline_monitors()
    vms.reset_calls()
    nfs_v41.fetch_monitor_query()
    assert vms.counts() == {"GET /api/monitors/{id}/query/": 1}
    nfs_v41.cleanup()
    nfs_v41._CLEANED_UP = False


# ---------------------------------------------------------------------------
# A build with no NFSv4.1 telemetry at all - VAST OS 5.5.0.1's real shape
# ---------------------------------------------------------------------------
REAL_5501_CATALOG = (
    # NFSv3 op counters and the NFS4Common/NFSCommon aggregates, which is all
    # the real var204 catalog carried for NFS. No session/sequence/exchange/
    # open/lock/deleg/layout counters exist.
    [f"NfsMetrics,nfs_{op}_latency__{k}"
     for op in ("read", "write", "getattr", "lookup", "access", "commit")
     for k in ("rate", "avg")]
    + ["NfsMetrics,nfs3_open_file_handle_cnt",
       "NfsMetrics,nfs3_smb_interop_handles_closed"]
    + [f"ProtoMetrics,proto_name=NFS4Common,{m}"
       for m in ("rd_iops", "wr_iops", "rd_bw", "wr_bw", "md_iops",
                 "rd_md_iops", "wr_md_iops", "iops", "latency",
                 "read_latency__avg", "write_latency__avg")]
    + ["ProtoMetrics,proto_name=NFSCommon,rd_bw",
       "ProtoMetrics,proto_name=NFSCommon,wr_bw"]
    + [f"BlockMetrics,{m}" for m in ("read_req", "write_req", "unmap_req")]
)


def test_build_without_v41_telemetry_reports_none_available(vms, monkeypatch):
    """The real VAST OS 5.5.0.1 case: no v4.1 state counters exist at all."""
    vms.state.catalog = set(REAL_5501_CATALOG)
    vms.state.unsupported_prop_prefixes = (
        "NfsMetrics,nfs_open", "NfsMetrics,nfs_close", "NfsMetrics,nfs_lock",
        "NfsMetrics,nfs_sequence", "NfsMetrics,nfs_exchange",
        "NfsMetrics,nfs_create_session", "NfsMetrics,nfs_destroy_session",
        "NfsMetrics,nfs_reclaim", "NfsMetrics,nfs_deleg",
        "NfsMetrics,nfs_layout", "NfsMetrics,nfs_getdevice",
        "NfsMetrics,nfs_release_lockowner",
    )
    nfs_v41 = _init(vms, monkeypatch)
    nfs_v41.create_headline_monitors()
    assert nfs_v41.STATE_OPS_AVAILABLE == []
    assert nfs_v41.PNFS_OPS_AVAILABLE == []
    nfs_v41.fetch_monitor_query()
    assert nfs_v41.LAST_ROWS["state"] == []
    assert nfs_v41.LAST_ROWS["pnfs"] == []
    # The data path still works and the fallback panel still has content.
    assert nfs_v41.LAST_ROWS["data"]
    assert nfs_v41.LAST_ROWS["stateful"]
    nfs_v41.cleanup()
    nfs_v41._CLEANED_UP = False


def test_block_metrics_never_leak_into_the_v41_concept_report():
    """The real catalog's 382 BlockMetrics names must not be reported as
    NFSv4.1 lock telemetry."""
    import nfs_v41

    hits = nfs_v41.concept_scan(set(REAL_5501_CATALOG))
    assert "lock" not in hits
    assert "session" not in hits and "layout" not in hits
    assert hits["nfs4"], "NFS4Common aggregates should still be reported"


# ---------------------------------------------------------------------------
# The --discover-metrics report
# ---------------------------------------------------------------------------
def _run_discovery(vms, probe_interval="1"):
    return subprocess.run(
        [sys.executable, "opstat", "--nfs", "--version=4.1",
         "--vms", "127.0.0.1", "--vms-port", str(vms.port),
         "--discover-metrics", "--no-color"],
        capture_output=True, text=True, timeout=180,
        env={"VAST_TOKEN": "test-token", "PATH": "/usr/bin:/bin",
             "OPSTAT_NFS4_PROBE_INTERVAL": probe_interval},
    )


def test_discovery_reports_every_section_and_leaks_nothing(vms):
    result = _run_discovery(vms)
    assert result.returncode == 0, result.stderr[-2000:]
    out = result.stdout
    for section in ("1. Metric catalog", "2. NFSv4.1 concept scan",
                    "3. NFSv4.1 operation probe", "4. Families in use today",
                    "5. Object scopes", "6. NFS4Common statistical surface",
                    "7. NFS family inventory",
                    "8. VMS observability API inventory",
                    "9. Prometheus/OpenMetrics endpoints",
                    "10. NFS-related REST resources",
                    "11. Client/host/user activity sources",
                    "12. Top-N / analytics capabilities",
                    "13. Candidate supporting data for NFSv4.1",
                    "15. Nfs4Metrics (Prometheus) interrogation",
                    "16. host_view / user_view / vip_view attribution",
                    "17. NFSv4 delegation REST endpoint",
                    "18. Summary"):
        assert section in out, f"missing section {section}"
    assert "Full report written to" in out
    assert vms.live_monitors() == {}, "discovery leaked monitors"


def test_discovery_names_queryable_and_missing_operations(vms):
    out = _run_discovery(vms).stdout
    # The mock exports state/session ops but no pNFS ones.
    assert "SEQUENCE       QUERYABLE" in out
    assert "OPEN           QUERYABLE" in out
    assert "LAYOUTGET      not exported" in out


def test_discovery_reports_pnfs_when_present(vms):
    vms.state.catalog = set(mock_vms_module.DEFAULT_CATALOG) | set(PNFS_CATALOG)
    out = _run_discovery(vms).stdout
    assert "LAYOUTGET      QUERYABLE" in out


def test_discovery_survives_an_unreadable_catalog(vms):
    vms.state.catalog = set()
    result = _run_discovery(vms)
    assert result.returncode == 0, result.stderr[-2000:]
    assert "unreadable or empty" in result.stdout
    assert vms.live_monitors() == {}


def test_discovery_reports_object_scope_support(vms):
    out = _run_discovery(vms).stdout
    assert "view     /views/" in out
    assert "object_id=yes" in out


# ---------------------------------------------------------------------------
# Alternative observability surfaces
# ---------------------------------------------------------------------------
def test_openapi_definition_is_located_and_inventoried(vms):
    out = _run_discovery(vms).stdout
    assert "definition: /api/openapi.json" in out
    assert "endpoint(s)" in out


def test_discovery_survives_a_cluster_without_openapi(vms):
    vms.state.openapi_enabled = False
    result = _run_discovery(vms)
    assert result.returncode == 0, result.stderr[-1500:]
    assert "OpenAPI definition not retrievable" in result.stdout
    assert "9. Prometheus/OpenMetrics endpoints" in result.stdout


def test_prometheus_endpoints_are_probed_and_parsed(vms):
    out = _run_discovery(vms).stdout
    assert "/prometheusmetrics/" in out
    assert "NFS/protocol-relevant exporter metrics:" in out


def test_discovery_survives_a_cluster_without_prometheus(vms):
    vms.state.prometheus = {}
    result = _run_discovery(vms)
    assert result.returncode == 0, result.stderr[-1500:]
    assert "13. Candidate supporting data" in result.stdout


def test_prometheus_parser_reads_help_type_and_labels():
    import vast_discovery

    body = (
        "# HELP vast_view_nfs_iops Per-view NFS operations per second\n"
        "# TYPE vast_view_nfs_iops gauge\n"
        'vast_view_nfs_iops{view="/a",tenant="t1"} 44.2\n'
        'vast_view_nfs_iops{view="/b",tenant="t2"} 12.0\n'
        "# HELP vast_total_bytes Lifetime bytes\n"
        "# TYPE vast_total_bytes counter\n"
        "vast_total_bytes 99\n"
    )
    metrics = vast_discovery.parse_prometheus(body)
    assert metrics["vast_view_nfs_iops"]["type"] == "gauge"
    assert metrics["vast_view_nfs_iops"]["help"].startswith("Per-view NFS")
    assert metrics["vast_view_nfs_iops"]["labels"] == {"view", "tenant"}
    assert metrics["vast_view_nfs_iops"]["samples"] == 2
    assert metrics["vast_total_bytes"]["type"] == "counter"


def test_prometheus_parser_handles_commas_inside_label_values():
    """Regression: VAST emits protocols="['NFS4', 'SMB']". Splitting the label
    block on commas shredded that into bogus label names such as "'SMB'" and
    made the real label set unreadable."""
    import vast_discovery

    body = (
        "# HELP vast_view_logical_capacity View Logical Capacity\n"
        "# TYPE vast_view_logical_capacity gauge\n"
        'vast_view_logical_capacity{cluster="c",path="/v",'
        "protocols=\"['NFS4', 'SMB']\",tenant_name=\"t\"} 1024\n"
    )
    meta = vast_discovery.parse_prometheus(body)["vast_view_logical_capacity"]
    assert meta["labels"] == {"cluster", "path", "protocols", "tenant_name"}
    assert meta["label_values"]["protocols"] == {"['NFS4', 'SMB']"}


def test_prometheus_parser_records_label_values_for_attribution():
    """A protocol label is only useful once we know it carries NFS values."""
    import vast_discovery

    body = (
        "# TYPE vast_host_view_iops gauge\n"
        'vast_host_view_iops{ip="10.9.0.1",protocol="NFS4",path="/a"} 44.2\n'
        'vast_host_view_iops{ip="10.9.0.2",protocol="SMB",path="/b"} 3.0\n'
    )
    meta = vast_discovery.parse_prometheus(body)["vast_host_view_iops"]
    assert meta["label_values"]["protocol"] == {"NFS4", "SMB"}
    assert meta["label_values"]["ip"] == {"10.9.0.1", "10.9.0.2"}
    assert meta["samples"] == 2


def test_prometheus_label_values_are_capped():
    import vast_discovery

    body = "# TYPE m gauge\n" + "".join(
        f'm{{ip="10.0.0.{i}"}} 1\n' for i in range(50))
    meta = vast_discovery.parse_prometheus(body)["m"]
    assert len(meta["label_values"]["ip"]) <= vast_discovery._MAX_LABEL_VALUES
    assert meta["samples"] == 50


def test_topn_envelope_is_described_by_its_contents():
    """Regression: /monitors/topn/ wraps results in {data,next,previous,
    timestamp}; reporting those four envelope keys made every top-N probe look
    like one record and hid whether it returned anything at all."""
    import vast_discovery

    payload = {
        "data": {"client": {"iops": [{"title": "10.0.0.1", "value": 5.0},
                                     {"title": "10.0.0.2", "value": 3.0}]}},
        "next": None, "previous": None, "timestamp": "2026-08-13T00:00:00Z",
    }
    count, fields = vast_discovery.describe_payload(payload)
    assert count == 2, "envelope reported instead of contents"
    assert "client.title" in fields and "client.value" in fields


def test_prometheus_parser_tolerates_junk():
    import vast_discovery

    assert vast_discovery.parse_prometheus("") == {}
    assert vast_discovery.parse_prometheus("<html>not prometheus</html>") == {}


def test_series_classification_uses_values_not_names():
    import vast_discovery

    # Monitor rows arrive newest-first, so a lifetime counter descends.
    assert vast_discovery.classify_series([300, 200, 100]) == "cumulative"
    assert vast_discovery.classify_series([100, 200, 300]) == "cumulative"
    assert vast_discovery.classify_series([5, 9, 2, 7]) == "rate/gauge"
    assert vast_discovery.classify_series([4, 4, 4]) == "constant"
    assert vast_discovery.classify_series([None, None]) == "no data"
    assert vast_discovery.classify_series([7]) == "single sample"


def test_openapi_endpoint_matching_groups_by_keyword():
    import vast_discovery
    from tests.mock_vms import OPENAPI_SPEC

    endpoints = vast_discovery.openapi_endpoints(OPENAPI_SPEC)
    assert any(path == "/views/" for path, _m, _s in endpoints)
    hits = vast_discovery.match_endpoints(endpoints, ("nfs", "topn"))
    assert any("nfs_client_connections" in p for p, _m, _s in hits["nfs"])
    assert hits["topn"]


def test_describe_payload_handles_every_list_shape():
    import vast_discovery

    assert vast_discovery.describe_payload([{"a": 1, "b": 2}]) == (1, ["a", "b"])
    assert vast_discovery.describe_payload({"results": [{"c": 1}]}) == (1, ["c"])
    assert vast_discovery.describe_payload({"x": 1})[0] == 1


def test_discovery_is_read_only_and_leaves_no_monitors(vms):
    """Every temporary monitor must be gone, and nothing but GET/POST-monitor
    /DELETE-monitor may be issued."""
    result = _run_discovery(vms)
    assert result.returncode == 0, result.stderr[-1500:]
    assert vms.live_monitors() == {}

    mutating = [
        (method, path) for _ts, method, path, _status in vms.calls()
        if method != "GET" and not path.startswith("/api/monitors")
    ]
    assert mutating == [], f"discovery issued non-monitor writes: {mutating}"

    posts = sum(1 for _t, m, p, _s in vms.calls()
                if m == "POST" and p == "/api/monitors/")
    deletes = sum(1 for _t, m, p, _s in vms.calls() if m == "DELETE")
    assert deletes == posts, f"{posts} monitors created, {deletes} deleted"


def test_discovery_reports_client_activity_and_topn_scopes(vms):
    out = _run_discovery(vms).stdout
    assert "NFS client connections" in out
    assert "topn object_type=view" in out
    assert "topn object_type=client" in out


def test_candidate_block_renders_every_required_field():
    import vast_discovery

    block = "\n".join(vast_discovery.candidate_block(
        api_path="/api/x", source="s", scope="cluster", provides="p",
        read_only="yes", queried="yes", opstat_use="u", caveats="c"))
    for field in ("API path:", "Data source:", "Scope:", "What it provides:",
                  "Read-only:", "Successfully queried:", "Potential opstat use:",
                  "Caveats:"):
        assert field in block


# ---------------------------------------------------------------------------
# Nfs4Metrics: the family the monitor API does not expose
# ---------------------------------------------------------------------------
def test_counter_delta_maths_derives_rate_and_latency():
    import vast_discovery

    first = {("m_count", frozenset({("cluster", "c")}.items() if False else
                                   [("cluster", "c")])): 1000.0,
             ("m_sum", frozenset([("cluster", "c")])): 500000.0}
    second = {("m_count", frozenset([("cluster", "c")])): 1100.0,
              ("m_sum", frozenset([("cluster", "c")])): 560000.0}
    rows = vast_discovery.counter_deltas(first, second, elapsed=10.0)
    by_name = {r["metric"]: r for r in rows}
    assert by_name["m_count"]["delta"] == 100.0
    assert by_name["m_count"]["per_sec"] == pytest.approx(10.0)
    latency = vast_discovery.derive_latency(
        by_name["m_count"]["delta"], by_name["m_sum"]["delta"])
    assert latency == pytest.approx(600.0)


def test_counter_behavior_classification():
    import vast_discovery

    assert "cumulative" in vast_discovery.summarize_counter_behavior(
        [{"delta": 5.0}, {"delta": 2.0}])
    assert "non-monotonic" in vast_discovery.summarize_counter_behavior(
        [{"delta": 5.0}, {"delta": -1.0}])
    assert "static" in vast_discovery.summarize_counter_behavior([{"delta": 0.0}])
    assert "no comparable" in vast_discovery.summarize_counter_behavior([])


def test_latency_unit_inference_against_a_known_reference():
    import vast_discovery

    # Reference is microseconds; a derived value of the same magnitude is µs.
    assert vast_discovery.infer_time_unit(2000.0, 2000.0)[0] == "microseconds"
    assert vast_discovery.infer_time_unit(2_000_000.0, 2000.0)[0] == "nanoseconds"
    assert vast_discovery.infer_time_unit(2.0, 2000.0)[0] == "milliseconds"
    assert vast_discovery.infer_time_unit(None, 2000.0)[0] is None


def test_sample_values_keys_by_metric_and_labels():
    import vast_discovery

    body = (
        '# TYPE m gauge\n'
        'm{cnode_id="1",hostname="a"} 10\n'
        'm{cnode_id="2",hostname="b"} 20\n'
    )
    samples = vast_discovery.sample_values(body)
    assert len(samples) == 2, "per-cNode series collapsed into one key"
    assert set(samples.values()) == {10.0, 20.0}


def test_discovery_finds_nfs4metrics_and_proves_counter_semantics(vms, monkeypatch):
    monkeypatch.setenv("OPSTAT_NFS4_PROBE_INTERVAL", "1")
    out = _run_discovery(vms).stdout
    assert "15. Nfs4Metrics (Prometheus) interrogation" in out
    assert "Nfs4Metrics" in out
    assert "behavior: cumulative" in out, "counter semantics not established"
    # Session and state operations the monitor API never exposed.
    for op in ("sequence", "exchange_id", "create_session", "open",
               "close", "free_stateid", "test_stateid"):
        assert op in out, f"{op} missing from the per-operation table"
    assert "latency-unit inference:" in out


def test_discovery_reports_nfs4_cnode_scope(vms, monkeypatch):
    monkeypatch.setenv("OPSTAT_NFS4_PROBE_INTERVAL", "1")
    out = _run_discovery(vms).stdout
    assert "cNode-scope Nfs4Metrics series:" in out
    count = int(out.split("cNode-scope Nfs4Metrics series:")[1].split("\n")[0])
    assert count > 0, "no per-cNode NFSv4 series found"


def test_discovery_prefers_the_narrowest_endpoint_carrying_nfs4metrics(vms, monkeypatch):
    monkeypatch.setenv("OPSTAT_NFS4_PROBE_INTERVAL", "1")
    out = _run_discovery(vms).stdout
    assert "narrowest endpoint carrying Nfs4Metrics:" in out
    # /all is the widest endpoint and must not be the chosen one when a
    # narrower path carries the same family.
    chosen = out.split("narrowest endpoint carrying Nfs4Metrics:")[1].split()[0]
    assert not chosen.endswith("/all"), f"chose the widest endpoint: {chosen}"


def test_attribution_reports_protocol_values_and_cardinality(vms, monkeypatch):
    monkeypatch.setenv("OPSTAT_NFS4_PROBE_INTERVAL", "1")
    out = _run_discovery(vms).stdout
    assert "16. host_view / user_view / vip_view attribution" in out
    assert "protocol=NFS4" in out, "NFS4 attribution not established"
    assert "series" in out


def test_delegation_endpoint_probed_read_only(vms, monkeypatch):
    """The endpoint requires file_path ("['__root__->file_path: field
    required']"), so it answers per-file rather than listing delegations.
    Discovery must pass a real NFS4 view path taken from host_view."""
    monkeypatch.setenv("OPSTAT_NFS4_PROBE_INTERVAL", "1")
    result = _run_discovery(vms)
    assert "17. NFSv4 delegation REST endpoint" in result.stdout
    assert "record(s)" in result.stdout
    delegs = [p for _t, m, p, _s in vms.calls() if "nfs4_delegs" in p]
    assert delegs, "delegation endpoint was never probed"
    assert any("file_path=" in p for p in delegs), (
        "probed without the required file_path parameter")
    # The sibling DELETE endpoint must never be called.
    deletes = [p for _t, m, p, _s in vms.calls()
               if m == "DELETE" and "nfs4_deleg" in p]
    assert deletes == [], f"discovery called a delete endpoint: {deletes}"


def test_discovery_handles_a_cluster_without_nfs4metrics(vms, monkeypatch):
    vms.state.nfs4_exporter = False
    vms.state.delegations_enabled = False
    monkeypatch.setenv("OPSTAT_NFS4_PROBE_INTERVAL", "1")
    result = _run_discovery(vms)
    assert result.returncode == 0, result.stderr[-1500:]
    assert "no exporter path carries Nfs4Metrics" in result.stdout
    assert "18. Summary" in result.stdout
    assert vms.live_monitors() == {}


# ---------------------------------------------------------------------------
# Idle-cluster interrogation: the state var204 was actually in
# ---------------------------------------------------------------------------
def test_idle_counters_are_still_recognised_as_cumulative():
    """A zero delta is ambiguous; magnitude is not. The real cluster held
    nfs4_sequence_req_latency_count at 12,941,555 across both scrapes - an
    instantaneous rate would have read ~0."""
    import vast_discovery

    idle_counter = [{"delta": 0.0, "first": 12941555.0, "second": 12941555.0}]
    verdict = vast_discovery.summarize_counter_behavior(idle_counter)
    assert "idle but cumulative" in verdict
    assert "Re-run under load" in verdict

    idle_rate = [{"delta": 0.0, "first": 0.0, "second": 0.0}]
    assert "near zero" in vast_discovery.summarize_counter_behavior(idle_rate)


def test_lifetime_mean_works_when_deltas_are_zero():
    import vast_discovery

    # 12.9M SEQUENCE ops totalling 14.5e9 microseconds -> ~1125 us each.
    assert vast_discovery.lifetime_mean(12941555.0, 14_559_249_375.0) == pytest.approx(
        1125.0, rel=1e-3)
    assert vast_discovery.lifetime_mean(0.0, 5.0) is None
    assert vast_discovery.lifetime_mean(10.0, None) is None


def test_probe_readonly_keeps_the_whole_error_message():
    """Truncating at 110 chars hid which parameter an endpoint wanted."""
    import vast_discovery

    long_detail = "HTTP 400: " + ("x" * 400)

    def boom(_method, _path, payload=None):
        raise RuntimeError(long_detail)

    info = vast_discovery.probe_readonly(boom, "/tenants/1/nfs4_delegs/")
    assert info["ok"] is False
    assert info["detail"].endswith("x" * 20), "error detail was truncated"


def test_nested_payload_reports_every_dimension():
    """Top-N returns several dimensions; a 12-field cap hid the later ones."""
    import vast_discovery

    payload = {"data": {
        dim: {"iops": [{"title": f"{dim}-1", "read": 1, "write": 2,
                        "total": 3, "scan": 0}]}
        for dim in ("client", "cnode", "user", "view", "vip")
    }}
    _count, fields = vast_discovery.describe_payload(payload)
    dimensions = {f.split(".")[0] for f in fields}
    assert dimensions == {"client", "cnode", "user", "view", "vip"}


def test_idle_cluster_discovery_reports_lifetime_latency(vms, monkeypatch):
    """End to end: frozen counters must still yield a units verdict."""
    import tests.mock_vms as mm

    live = mm._nfs4_exposition
    monkeypatch.setattr(mm, "_nfs4_exposition", lambda elapsed: live(0.0))
    out = _run_discovery(vms).stdout
    assert "idle but cumulative" in out
    assert "lifetime mean" in out
    assert "latency-unit inference: microseconds" in out


def test_exporter_paths_are_not_probed_as_json(vms):
    """Prometheus endpoints serve text/plain; probing them with the JSON
    client produced a bare 'Expecting value' in the REST section."""
    out = _run_discovery(vms).stdout
    rest_section = out.split("[ 10.")[1].split("[ 11.")[0]
    assert "prometheus" not in rest_section.lower()


def test_discovery_enumerates_every_nfs4_operation_not_a_curated_list(vms, monkeypatch):
    """Regression: a hardcoded 14-op probe list omitted putfh/getfh/access -
    the most frequent operations in any NFSv4 compound - so no reasoning
    about compound composition was supported by the data."""
    monkeypatch.setenv("OPSTAT_NFS4_PROBE_INTERVAL", "1")
    out = _run_discovery(vms).stdout
    for op in ("putfh", "getfh", "access", "savefh", "restorefh",
               "secinfo_no_name", "lookupp", "putrootfh"):
        assert f"    {op:<18}" in out, f"{op} missing from the per-op table"
    header = [l for l in out.split("\n") if "per-operation (cluster scope" in l]
    assert header and "ops)" in header[0]


def test_cluster_and_cnode_counters_are_reconciled(vms, monkeypatch):
    monkeypatch.setenv("OPSTAT_NFS4_PROBE_INTERVAL", "1")
    out = _run_discovery(vms).stdout
    assert "cluster vs sum-of-cNodes (delta counts):" in out
    assert "variance" in out
    assert "no operation carried a non-zero value" not in out


def test_reconciliation_falls_back_to_lifetime_totals_when_idle(vms, monkeypatch):
    """An idle window produced an empty heading and no evidence at all;
    lifetime totals still show whether cNode counters account for the
    cluster figure."""
    import tests.mock_vms as mm

    live = mm._nfs4_exposition
    monkeypatch.setattr(mm, "_nfs4_exposition", lambda elapsed: live(0.0))
    monkeypatch.setenv("OPSTAT_NFS4_PROBE_INTERVAL", "1")
    out = _run_discovery(vms).stdout
    assert "cluster vs sum-of-cNodes (lifetime counts):" in out
    assert "no operation carried a non-zero value" not in out
    assert "ops per compound" in out, "compound ratio vanished when idle"


def test_compound_ratio_is_labelled_as_derived(vms, monkeypatch):
    """VMS publishes no compound counter; the figure is inferred from
    SEQUENCE and must not read as native telemetry."""
    monkeypatch.setenv("OPSTAT_NFS4_PROBE_INTERVAL", "1")
    out = _run_discovery(vms).stdout
    assert "DERIVED RATIO (not a native metric)" in out
    assert "ops per compound" in out


def test_delegation_record_schema_is_reported(vms, monkeypatch):
    """The response wraps records in "delegate_info" beside pagination keys;
    describing the wrapper hid the delegation schema."""
    monkeypatch.setenv("OPSTAT_NFS4_PROBE_INTERVAL", "1")
    _run_discovery(vms)
    import vast_discovery

    payload = {"delegate_info": [{"client_ip": "10.9.0.1", "stateid": "0x01",
                                  "deleg_type": "READ", "path": "/v"}],
               "delegate_info_count_total": 1,
               "xeystore_pagination": None}
    count, fields = vast_discovery.describe_payload(payload)
    assert count == 1
    assert "client_ip" in fields and "deleg_type" in fields and "stateid" in fields


def test_units_reference_falls_back_when_nfs4common_is_null(vms, monkeypatch):
    """NFS4Common data counters read zero on some clusters, leaving the
    microsecond reference null. Other known-microsecond sources must be
    tried before giving up on proving units."""
    vms.state.unsupported_prop_prefixes = (
        "ProtoMetrics,proto_name=NFS4Common,read_latency",
        "ProtoMetrics,proto_name=NFS4Common,write_latency",
    )
    monkeypatch.setenv("OPSTAT_NFS4_PROBE_INTERVAL", "1")
    out = _run_discovery(vms).stdout
    assert "microsecond reference:" in out
    assert "none available" not in out.split("microsecond reference:")[1][:60], (
        "gave up instead of trying NFSCommon / NfsMetrics")
