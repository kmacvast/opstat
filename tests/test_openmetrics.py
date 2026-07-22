"""OpenMetrics JSON Lines exporter tests."""

import json

import openmetrics


def test_configure_disabled_returns_none():
    assert openmetrics.configure(False, None, "s3", "vms") is None
    assert openmetrics.is_enabled() is False
    assert openmetrics.path() is None


def test_export_snapshot_s3_namespace(tmp_path):
    path = tmp_path / "s3.jsonl"
    assert openmetrics.configure(True, str(path), "s3", "vms1") == str(path)
    assert openmetrics.is_enabled() is True

    openmetrics.export_snapshot(
        "cluster-a",
        None,
        "cluster-a",
        [{
            "operation": "GET",
            "category": "data",
            "ops_sec": 42.0,
            "avg_us": 150.5,
            "bw_bytes_sec": 1_000_000.0,
            "io_bytes": 4096.0,
        }],
        sample="2026-07-08T14:30:00.000Z",
    )
    openmetrics.close()

    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 4
    names = {json.loads(line)["metric_name"] for line in lines}
    assert names == {
        "vast.s3.operations",
        "vast.s3.latency",
        "vast.s3.throughput",
        "vast.s3.io_size",
    }
    ops = next(json.loads(line) for line in lines if "operations" in line)
    assert ops["value"] == 42.0
    assert ops["unit"] == "ops/s"
    assert ops["metric_type"] == "gauge"
    assert ops["attributes"]["protocol"] == "s3"
    assert ops["attributes"]["operation"] == "GET"
    assert ops["attributes"]["cluster"] == "cluster-a"
    assert ops["attributes"]["vms"] == "vms1"
    assert ops["timestamp"] == "2026-07-08T14:30:00.000Z"


def test_export_skips_null_fields(tmp_path):
    path = tmp_path / "partial.jsonl"
    openmetrics.configure(True, str(path), "nfs3", "v")
    openmetrics.export_snapshot("c", None, "c", [{
        "operation": "READ",
        "category": "data",
        "ops_sec": 1.0,
        "avg_us": None,
        "bw_bytes_sec": None,
        "io_bytes": None,
    }])
    openmetrics.close()
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["metric_name"] == "vast.nfs3.operations"


def test_export_noop_when_disabled(tmp_path):
    path = tmp_path / "empty.jsonl"
    openmetrics.configure(False, str(path), "s3", "v")
    openmetrics.export_snapshot("c", None, "c", [{
        "operation": "GET", "category": "data",
        "ops_sec": 1.0, "avg_us": 1.0,
        "bw_bytes_sec": 1.0, "io_bytes": 1.0,
    }])
    assert not path.exists()


def test_export_drill_uses_bw_mbs_or_gbs(tmp_path):
    path = tmp_path / "drill.jsonl"
    openmetrics.configure(True, str(path), "s3", "v")
    openmetrics.export_drill("c", "bucket", [
        {"name": "b1", "total_ops": 10.0, "latency_us": 20.0, "bw_mbs": 2.0},
        {"name": "b2", "total_iops": 5.0, "latency_us": None, "bw_gbs": 0.001},
    ])
    openmetrics.close()
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    targets = {r["attributes"]["target_name"] for r in rows}
    assert targets == {"b1", "b2"}
    assert all(r["attributes"]["category"] == "drill" for r in rows)
    assert all(r["attributes"]["operation"] == "TOTAL" for r in rows)
    b1_tp = next(
        r for r in rows
        if r["attributes"]["target_name"] == "b1" and r["metric_name"].endswith(".throughput")
    )
    assert b1_tp["value"] == 2_000_000.0


def test_unit_conversions():
    assert openmetrics.mbps_to_bytes_sec(None) is None
    assert openmetrics.mbps_to_bytes_sec(1.5) == 1_500_000.0
    assert openmetrics.gbps_to_bytes_sec(None) is None
    assert openmetrics.gbps_to_bytes_sec(1.0) == 1_000_000_000.0


def test_iso_timestamp_strips_warming_suffix():
    ts = openmetrics._iso_timestamp("2026-07-08T14:30:00Z (warming up…)")
    assert ts.startswith("2026-07-08T14:30:00")
    assert ts.endswith("Z")
    assert openmetrics._iso_timestamp("-")  # falls back to now
    assert openmetrics._iso_timestamp(None)


def test_close_idempotent(tmp_path):
    openmetrics.configure(True, str(tmp_path / "x.jsonl"), "smb", "v")
    openmetrics.close()
    openmetrics.close()
    assert openmetrics.is_enabled() is False
