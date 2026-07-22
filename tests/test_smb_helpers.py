"""SMB engine helper and client-scope tests (no live VMS)."""

import pytest

import smb


@pytest.fixture(autouse=True)
def _smb_state(reset_smb_globals):
    yield


def test_smb_metric_fqn_and_command_props():
    assert smb.smb_metric_fqn("read", "rate") == "SmbMetrics,smb_read_latency__rate"
    props = smb.smb_command_props()
    assert "SmbMetrics,smb_write_latency__avg" in props
    assert len(props) == len(smb.SMB_CMD_CANDIDATES) * 2


def test_build_headline_includes_interop():
    props = smb.build_headline_monitor_props()
    assert any(p.endswith(",rd_iops") for p in props)
    for interop in smb._INTEROP_METRICS:
        assert interop in props


def test_configure_client_scope_accepts_ip_and_hostname(ns, capsys):
    smb.configure_client_scope(ns(clients="10.1.1.5,client-host,bad host!!"))
    assert smb.CLIENT_SCOPED is True
    assert "10.1.1.5" in smb.CLIENT_IPS
    assert "client-host" in smb.CLIENT_IPS
    assert "bad host!!" not in smb.CLIENT_IPS
    err = capsys.readouterr().err
    assert "malformed" in err.lower()

    smb.configure_client_scope(ns(clients=None))
    assert smb.CLIENT_SCOPED is False
    assert smb.CLIENT_IPS == []


def test_client_matches_scope_and_parse_topn_ip():
    assert smb._parse_topn_ip("172.200.14.253 [default]") == "172.200.14.253"
    smb.CLIENT_SCOPED = True
    smb.CLIENT_IPS = ["10.0.0.1"]
    assert smb._client_matches_scope("10.0.0.1 [t]") is True
    assert smb._client_matches_scope("10.0.0.2 [t]") is False
    smb.CLIENT_SCOPED = False
    assert smb._client_matches_scope("10.0.0.2 [t]") is True


def test_filter_smb_metrics():
    catalog = [
        "ProtoMetrics,proto_name=SMBCommon,rd_iops",
        "ProtoMetrics,proto_name=NFSCommon,rd_iops",
        {"name": "SmbMetrics,smb_read_latency__rate"},
        42,
    ]
    hits = smb.filter_smb_metrics(catalog)
    assert len(hits) == 2


def test_build_rows_from_results_component_total():
    proto = smb._PROTO_SMB_COMMON
    prop_list = [
        "timestamp",
        f"{proto},rd_iops",
        f"{proto},wr_iops",
        f"{proto},md_iops",
        f"{proto},rd_bw",
        f"{proto},wr_bw",
        f"{proto},read_latency__avg",
        f"{proto},write_latency__avg",
        f"{proto},read_size__avg",
        f"{proto},write_size__avg",
        f"{proto},iops",
        f"{proto},bw",
        f"{proto},rd_md_iops",
        f"{proto},wr_md_iops",
        f"{proto},notify_counter",
    ]
    row = [
        "2026-07-08T12:00:00Z",
        40.0, 20.0, 10.0,
        4_000_000.0, 2_000_000.0,
        100.0, 200.0,
        1024.0, 2048.0,
        40.0, 6_000_000.0,
        6.0, 4.0,
        0,
    ]
    snapshot, _sample = smb.build_rows_from_results({"prop_list": prop_list, "data": [row]})
    assert snapshot["meta"]["total_iops"] == 70.0
    read = next(r for r in snapshot["data"] if r["key"] == "read")
    write = next(r for r in snapshot["data"] if r["key"] == "write")
    assert read["ops_sec"] == 40.0
    assert write["ops_sec"] == 20.0


def test_smb_health_and_mix():
    assert smb.smb_health_label(0, None)[0] == "IDLE"
    assert smb.smb_health_label(100, 500)[0] == "HEALTHY"
    md_pct, read_pct, write_pct = smb.smb_workload_mix(
        {"md_iops": 20.0},
        [
            {"key": "read", "ops_sec": 60.0},
            {"key": "write", "ops_sec": 20.0},
        ],
    )
    assert md_pct + read_pct + write_pct == pytest.approx(100.0)
