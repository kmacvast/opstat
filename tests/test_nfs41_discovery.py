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
def _run_discovery(vms):
    return subprocess.run(
        [sys.executable, "opstat", "--nfs", "--version=4.1",
         "--vms", "127.0.0.1", "--vms-port", str(vms.port),
         "--discover-metrics", "--no-color"],
        capture_output=True, text=True, timeout=180,
        env={"VAST_TOKEN": "test-token", "PATH": "/usr/bin:/bin"},
    )


def test_discovery_reports_every_section_and_leaks_nothing(vms):
    result = _run_discovery(vms)
    assert result.returncode == 0, result.stderr[-2000:]
    out = result.stdout
    for section in ("1. Metric catalog", "2. NFSv4.1 concept scan",
                    "3. NFSv4.1 operation probe", "4. Families in use today",
                    "5. Object scopes", "6. NFS4Common statistical surface",
                    "7. NFS family inventory", "8. Summary"):
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
