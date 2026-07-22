"""S3 engine helper and scope tests (no live VMS)."""

import pytest

import s3


@pytest.fixture(autouse=True)
def _s3_state(reset_s3_globals):
    yield


def test_first_positive():
    assert s3._first_positive(None, 0, -1, "x", 3.5) == 3.5
    assert s3._first_positive(None, 0) is None


def test_common_fqn_and_headline_props():
    assert s3._common_fqn("rd_iops").startswith("ProtoMetrics,proto_name=S3Common,")
    props = s3.build_headline_monitor_props(s3._PROTO_S3_LEGACY)
    assert all(p.startswith(s3._PROTO_S3_LEGACY + ",") for p in props)
    assert any(p.endswith(",rd_iops") for p in props)
    assert any(p.endswith(",write_latency__avg") for p in props)


def test_s3_metric_fqns_and_props():
    fqns = s3.s3_metric_fqns("get_object")
    assert fqns == [
        "S3Metrics,get_object__rate",
        "S3Metrics,get_object__avg",
        "S3Metrics,get_object_latency__rate",
        "S3Metrics,get_object_latency__avg",
    ]
    props = s3.build_s3_metrics_props()
    assert "S3Metrics,get_object" in props
    assert "S3Metrics,put_object__rate" in props
    assert len(props) == len(set(props))


def test_configure_bucket_and_tenant_scope(ns):
    s3.configure_bucket_scope(ns(buckets=" a, ,b "))
    assert s3.BUCKET_SCOPED is True
    assert s3.BUCKET_NAMES == ["a", "b"]
    s3.configure_bucket_scope(ns(buckets=None))
    assert s3.BUCKET_SCOPED is False
    assert s3.BUCKET_NAMES == []

    s3.configure_tenant_scope(ns(tenants="default,prod"))
    assert s3.TENANT_SCOPED is True
    assert s3.TENANT_NAMES == ["default", "prod"]


def test_bucket_and_tenant_match_scope():
    s3.BUCKET_SCOPED = True
    s3.BUCKET_NAMES = ["logs"]
    assert s3._bucket_matches_scope({"path": "/logs", "id": 1}) is True
    assert s3._bucket_matches_scope({"title": "app-data", "id": 2}) is False
    s3.BUCKET_SCOPED = False
    assert s3._bucket_matches_scope({"title": "app-data", "id": 2}) is True

    s3.TENANT_SCOPED = True
    s3.TENANT_NAMES = ["Prod"]
    assert s3._tenant_matches_scope({"name": "prod", "id": 1}) is True
    assert s3._tenant_matches_scope({"name": "dev", "id": 2}) is False


def test_normalize_object_id():
    assert s3._normalize_object_id("12") == 12
    assert s3._normalize_object_id(12) == 12
    assert s3._normalize_object_id("abc") == "abc"
    assert s3._normalize_object_id(None) is None


def test_build_drill_prop_lists_do_not_mix_metric_classes():
    bucket_props = s3.build_drill_prop_list("bucket")
    assert all(p.startswith("ViewMetrics,") for p in bucket_props)
    assert not any(p.startswith("BucketViewMetrics,") for p in bucket_props)
    tenant_props = s3.build_drill_prop_list("tenant")
    assert all(p.startswith("TenantMetrics,") for p in tenant_props)
    cnode_props = s3.build_drill_prop_list("cnode")
    assert any("S3Common" in p or "proto_name=S3" in p for p in cnode_props)
    assert s3._is_batch_drill_mode("bucket") is True
    assert s3._is_batch_drill_mode("cnode") is False


def test_build_rows_from_results_component_mix():
    proto = s3._PROTO_S3_COMMON
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
    ]
    row = [
        "2026-07-08T12:00:00Z",
        100.0, 50.0, 25.0,
        10_000_000.0, 5_000_000.0,
        200.0, 300.0,
        4096.0, 8192.0,
        100.0, 15_000_000.0,
    ]
    snapshot, sample = s3.build_rows_from_results({"prop_list": prop_list, "data": [row]})
    assert sample.startswith("2026-07-08")
    assert snapshot["meta"]["total_iops"] == 175.0
    get_row = next(r for r in snapshot["data"] if r["key"] == "get")
    put_row = next(r for r in snapshot["data"] if r["key"] == "put")
    assert get_row["ops_sec"] == 100.0
    assert put_row["ops_sec"] == 50.0
    assert get_row["bw_mbs"] == 10.0
    assert s3.METRICS_SOURCE in ("S3Common", "S3")


def test_opcode_breakdown_uses_s3common_proxies_when_metrics_absent():
    s3.S3_METRICS_EXPORTED = False
    data_rows = [
        {"key": "get", "label": "GET", "ops_sec": 10.0, "avg_us": 100.0,
         "bw_mbs": 1.0, "avg_io_bytes": 1024.0, "pct": 50.0},
        {"key": "put", "label": "PUT", "ops_sec": 10.0, "avg_us": 200.0,
         "bw_mbs": 2.0, "avg_io_bytes": 2048.0, "pct": 50.0},
    ]
    metadata_rows = [
        {"key": "md_total", "label": "METADATA", "ops_sec": 5.0,
         "avg_us": None, "bw_mbs": None, "avg_io_bytes": None, "pct": 100.0},
    ]
    meta = {
        "md_iops": 5.0, "rd_md_iops": 3.0, "wr_md_iops": 2.0, "total_iops": 25.0,
    }
    rows = s3.build_opcode_breakdown_rows(data_rows, metadata_rows, meta, None)
    labels = {r["label"] for r in rows}
    assert labels == {"GET", "PUT", "DELETE", "LIST"}
    sources = {r["source"] for r in rows}
    assert "MEASURED" in sources
    assert "AGGREGATE" in sources
    assert next(r for r in rows if r["label"] == "DELETE")["ops_sec"] == 2.0
    assert next(r for r in rows if r["label"] == "LIST")["ops_sec"] == 3.0


def test_opcode_rows_from_s3metrics_uses_instant_rate_not_cumulative():
    # Instantaneous __rate must be used as-is (not delta, not bare counter).
    rate_fqn = "S3Metrics,get_object__rate"
    counter_fqn = "S3Metrics,get_object"
    result = {
        "prop_list": ["timestamp", rate_fqn, counter_fqn],
        "data": [
            ["2026-07-08T12:00:10Z", 832.0, 5_232_374.0],
            ["2026-07-08T12:00:00Z", 800.0, 5_230_000.0],
        ],
    }
    rows = s3._build_opcode_rows_from_s3metrics(result)
    get_row = next(r for r in rows if r["cmd"] == "get_object")
    assert get_row["ops_sec"] == pytest.approx(832.0)
    assert get_row["source"] == "S3METRICS"


def test_opcode_rows_from_s3metrics_deltas_bare_counter():
    counter_fqn = "S3Metrics,get_object"
    result = {
        "prop_list": ["timestamp", counter_fqn],
        "data": [
            ["2026-07-08T12:00:10Z", 200.0],
            ["2026-07-08T12:00:00Z", 100.0],
        ],
    }
    rows = s3._build_opcode_rows_from_s3metrics(result)
    get_row = next(r for r in rows if r["cmd"] == "get_object")
    assert get_row["ops_sec"] == pytest.approx(10.0)


def test_opcode_rejects_cumulative_looking_s3metrics():
    s3.S3_METRICS_EXPORTED = True
    native_result = {
        "prop_list": ["timestamp", "S3Metrics,get_object"],
        "data": [["2026-07-08T12:00:00Z", 5_232_374.0]],  # single sample => no delta
    }
    data_rows = [
        {"key": "get", "label": "GET", "ops_sec": 832.0, "avg_us": 811.0,
         "bw_mbs": 564.0, "avg_io_bytes": 1024.0, "pct": 50.0},
        {"key": "put", "label": "PUT", "ops_sec": 790.0, "avg_us": 2770.0,
         "bw_mbs": 358.0, "avg_io_bytes": 2048.0, "pct": 50.0},
    ]
    meta = {"md_iops": 519.0, "total_iops": 2141.0}
    # With only a bare counter and one sample, ops are None; force a bad rate path:
    # inject fake native rates via monkeypatch of builder.
    bad_native = [
        {"label": "GET", "ops_sec": 5_232_374.0, "avg_us": None,
         "bw_mbs": None, "avg_io_bytes": None, "source": "S3METRICS", "hint": False,
         "category": "data", "cmd": "get_object"},
        {"label": "PUT", "ops_sec": 4_331_549.0, "avg_us": None,
         "bw_mbs": None, "avg_io_bytes": None, "source": "S3METRICS", "hint": False,
         "category": "data", "cmd": "put_object"},
    ]
    assert s3._s3metrics_looks_like_cumulative(bad_native, meta) is True
    # Full builder should fall back to MEASURED S3Common proxies.
    import types
    # Simulate exported path with absurd rates by temporarily patching builder.
    original = s3._build_opcode_rows_from_s3metrics
    s3._build_opcode_rows_from_s3metrics = lambda _r: bad_native
    try:
        rows = s3.build_opcode_breakdown_rows(data_rows, [], meta, native_result)
    finally:
        s3._build_opcode_rows_from_s3metrics = original
    sources = {r["source"] for r in rows}
    assert "MEASURED" in sources
    assert "S3METRICS" not in sources
    get_row = next(r for r in rows if "GET" in r["label"])
    assert get_row["ops_sec"] == pytest.approx(832.0)


def test_format_latency_ms_always_milliseconds():
    text, raw = s3.format_latency_ms(811)
    assert text == "0.81 ms"
    assert raw == 811
    text, raw = s3.format_latency_ms(2770)
    assert text == "2.77 ms"
    assert s3.format_latency_ms(0) == ("-", None)


def test_vip_hides_192_168_addresses():
    assert s3._is_192_168_ip("192.168.1.10") is True
    assert s3._is_192_168_ip("10.1.1.5") is False
    assert s3._vip_display_name({"ip": "192.168.1.10", "vippool": "s3-pool", "id": 7}) == "s3-pool"
    assert s3._vip_display_name({"ip": "10.50.1.2", "id": 8}) == "10.50.1.2"
    entries = s3._vip_objects_for_drill([
        {"id": 1, "ip": "192.168.0.5", "vippool": "front"},
        {"id": 2, "ip": "192.168.0.6"},  # only internal IP
        {"id": 3, "ip": "10.0.0.9", "name": "ext-vip"},
    ])
    names = {e["name"] for e in entries}
    assert "front" in names
    assert "ext-vip" in names
    assert "192.168.0.6" not in names
    assert not any(n.startswith("192.168.") for n in names)


def test_vip_topn_ignores_latency_as_ops():
    """Regression: latency read/write (µs) must not become GET/s PUT/s."""
    s3.VIP_TOPN = {
        "data": {
            "vip": {
                "iops": [
                    {"title": "172.200.202.1", "total": 5.79, "read": 5.04, "write": 0.75},
                ],
                "md_iops": [
                    {"title": "172.200.202.1", "total": 2.0, "read": 1.5, "write": 0.5},
                ],
                "bw": [
                    {"title": "172.200.202.1", "total": 4.94, "read": 0.5, "write": 4.4},
                ],
                "latency": [
                    {"title": "172.200.202.1", "total": 14933.0, "read": 4019.32, "write": 68046.22},
                ],
            }
        }
    }
    activity = {r["title"]: r for r in s3._vip_topn_activity_rows()}
    row = activity["172.200.202.1"]
    assert row["read"] == pytest.approx(5.04)
    assert row["write"] == pytest.approx(0.75)
    assert row["md_read"] == pytest.approx(1.5)
    assert row["md_write"] == pytest.approx(0.5)
    assert row["bw_mbs"] == pytest.approx(4.94)
    assert row["latency_us"] == pytest.approx(14933.0)
    # Must not pick latency write=68046 as PUT ops.
    assert row["write"] < 100

    drill = {r["name"]: r for r in s3._build_vip_rows_from_topn()}
    d = drill["172.200.202.1"]
    assert d["get_ops"] == pytest.approx(5.04)
    assert d["put_ops"] == pytest.approx(0.75)
    assert d["list_ops"] == pytest.approx(1.5)
    assert d["delete_ops"] == pytest.approx(0.5)
    assert d["bw_mbs"] == pytest.approx(4.94)
    assert d["latency_us"] == pytest.approx(14933.0)
    assert d["top_rpc"] == "GET"


def test_create_headline_monitor_falls_back_to_legacy(monkeypatch):
    calls = []

    def fake_create(name_suffix, prop_list):
        calls.append((name_suffix, prop_list[0]))
        if name_suffix == "headline":
            raise RuntimeError("S3Common missing")
        return 55

    monkeypatch.setattr(s3, "create_monitor", fake_create)
    mid = s3.create_headline_monitor()
    assert mid == 55
    assert s3.METRICS_SOURCE == "S3"
    assert s3._PROTO_ACTIVE == s3._PROTO_S3_LEGACY
    assert len(calls) == 2


def test_health_and_workload_classifiers():
    assert s3.s3_health_label(0, None)[0] == "IDLE"
    assert s3.s3_health_label(1000, 20_000)[0] == "HOT"
    assert s3.s3_health_label(100, 100)[0] == "ACTIVE"
    get_pct, put_pct, delete_pct, list_pct = s3.s3_workload_mix(
        {"md_iops": 25.0, "rd_md_iops": 15.0, "wr_md_iops": 10.0},
        [
            {"key": "get", "ops_sec": 50.0},
            {"key": "put", "ops_sec": 25.0},
        ],
    )
    assert get_pct + put_pct + delete_pct + list_pct == pytest.approx(100.0)
    assert get_pct == pytest.approx(50.0)
    assert delete_pct == pytest.approx(10.0)
    assert list_pct == pytest.approx(15.0)
    assert "GET-biased" in s3.classify_s3_workload(
        {"md_iops": 5.0, "rd_md_iops": 3.0, "wr_md_iops": 2.0},
        [
            {"key": "get", "ops_sec": 80.0, "avg_io_bytes": 4096},
            {"key": "put", "ops_sec": 10.0},
        ],
    )


def test_drill_rest_fields_use_s3_call_names_and_bw_mbs():
    row = s3._drill_rest_fields(100, 50, 10, 20, 1500.0, 12.5, "bucket-a")
    assert row["get_ops"] == 100
    assert row["put_ops"] == 50
    assert row["delete_ops"] == 10
    assert row["list_ops"] == 20
    assert row["bw_mbs"] == 12.5
    assert row["top_rpc"] == "GET"
    assert "bw_gbs" not in row
    assert row["top_rpc"] not in ("RD MD", "WR MD")


def test_view_bw_and_latency_unit_conversion():
    # ViewMetrics bw is already MB/s (not ProtoMetrics bytes/s).
    assert s3._view_bw_to_mbs(9.65) == pytest.approx(9.65)
    assert s3._view_bw_to_mbs(40_900_845) == pytest.approx(40.900845)
    assert s3._view_bw_to_mbs(0) is None
    # View/Tenant latency averages are nanoseconds → microseconds.
    assert s3._ns_avg_to_us(3_866_140.55) == pytest.approx(3866.14055)
    assert s3._ns_avg_to_us(0) is None
    # Display path: µs → ms (3866 µs → 3.87 ms), not thousands of ms.
    lat_us = s3._ns_avg_to_us(3_866_140.55)
    assert (lat_us / 1000.0) == pytest.approx(3.866, rel=1e-3)


def test_bucket_drill_row_units_from_viewmetrics():
    prop_list = [
        "timestamp", "object_id",
        "ViewMetrics,read_iops__rate", "ViewMetrics,write_iops__rate",
        "ViewMetrics,read_md_iops__rate", "ViewMetrics,write_md_iops__rate",
        "ViewMetrics,read_latency__avg", "ViewMetrics,write_latency__avg",
        "ViewMetrics,read_md_latency__avg", "ViewMetrics,write_md_latency__avg",
        "ViewMetrics,read_bw__rate", "ViewMetrics,write_bw__rate",
    ]
    # Mirrors live /bench-2b sample shape (bw already MB/s, lat in ns).
    row = [
        "2026-07-22T22:35:24Z", 258,
        9.65, 0.0,
        9.73, 0.0,
        3_866_140.55, 0.0,
        0.0, 0.0,
        9.65, 0.0,
    ]
    result = {"prop_list": prop_list, "data": [row]}
    drill = s3._build_bucket_drill_row(result, "/bench-2b")
    assert drill["get_ops"] == pytest.approx(9.65)
    assert drill["list_ops"] == pytest.approx(9.73)
    assert drill["bw_mbs"] == pytest.approx(9.65)
    assert drill["latency_us"] == pytest.approx(3866.14055)
    assert (drill["latency_us"] / 1000.0) < 10.0  # ~3.9 ms, not ~3866 ms

