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
    meta = {"md_iops": 5.0, "total_iops": 25.0}
    rows = s3.build_opcode_breakdown_rows(data_rows, metadata_rows, meta, None)
    labels = {r["label"] for r in rows}
    assert "GET / READ" in labels
    assert "PUT / WRITE" in labels
    assert "METADATA (total)" in labels
    sources = {r["source"] for r in rows}
    assert "MEASURED" in sources
    assert "AGGREGATE" in sources


def test_opcode_rows_from_s3metrics_delta_rates():
    rate_fqn = "S3Metrics,get_object__rate"
    avg_fqn = "S3Metrics,get_object__avg"
    result = {
        "prop_list": ["timestamp", rate_fqn, avg_fqn],
        "data": [
            ["2026-07-08T12:00:10Z", 200.0, 150.0],
            ["2026-07-08T12:00:00Z", 100.0, 140.0],
        ],
    }
    rows = s3._build_opcode_rows_from_s3metrics(result)
    get_row = next(r for r in rows if r["cmd"] == "get_object")
    assert get_row["ops_sec"] == pytest.approx(10.0)
    assert get_row["source"] == "S3METRICS"


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
    get_pct, put_pct, md_pct = s3.s3_workload_mix(
        {"md_iops": 25.0},
        [
            {"key": "get", "ops_sec": 50.0},
            {"key": "put", "ops_sec": 25.0},
        ],
    )
    assert get_pct + put_pct + md_pct == pytest.approx(100.0)
    assert get_pct == pytest.approx(50.0)
    assert "GET-biased" in s3.classify_s3_workload(
        {"md_iops": 5.0},
        [
            {"key": "get", "ops_sec": 80.0, "avg_io_bytes": 4096},
            {"key": "put", "ops_sec": 10.0},
        ],
    )
