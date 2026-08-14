"""NVMe-oTCP (BLOCK) engine unit tests.

Covers the FR-C fabric-percentage correction with literal values, and (added
in later phases) latency-unit conversions and navigation. NVMe previously had
no dedicated unit coverage; this file is the start of it.
"""

from __future__ import annotations

import pytest

import nvme_tcp


def _rows(**ops):
    """Build workload rows in the shape block_workload_mix expects."""
    return [{"key": k, "ops_sec": v} for k, v in ops.items()]


# ---------------------------------------------------------------------------
# FR-C: Fabric activity must NOT be in the primary workload-mix denominator.
# ---------------------------------------------------------------------------
def test_workload_mix_read_only():
    read, write, reclaim, fabric = nvme_tcp.block_workload_mix(_rows(read=100))
    assert read == pytest.approx(100.0)
    assert write == 0.0 and reclaim == 0.0


def test_workload_mix_write_only():
    read, write, reclaim, fabric = nvme_tcp.block_workload_mix(_rows(write=100))
    assert write == pytest.approx(100.0)
    assert read == 0.0


def test_workload_mix_mixed_read_write():
    read, write, reclaim, fabric = nvme_tcp.block_workload_mix(_rows(read=75, write=25))
    assert read == pytest.approx(75.0)
    assert write == pytest.approx(25.0)


def test_workload_mix_large_fabric_does_not_distort_read_write():
    """The defect: fabric in the denominator made an 80/20 read/write workload
    render as 16%/4%. Fabric must be excluded from the workload denominator."""
    read, write, reclaim, fabric = nvme_tcp.block_workload_mix(
        _rows(read=80, write=20, handle_request=400))
    assert read == pytest.approx(80.0), "fabric distorted the read percentage"
    assert write == pytest.approx(20.0)
    assert reclaim == 0.0
    # Fabric is still quantified, separately, as a share of all activity.
    assert fabric == pytest.approx(400 / 500 * 100)


def test_workload_mix_reclaim_counted_as_workload_not_fabric():
    read, write, reclaim, fabric = nvme_tcp.block_workload_mix(
        _rows(read=50, write=25, unmap=25))
    assert read == pytest.approx(50.0)
    assert write == pytest.approx(25.0)
    assert reclaim == pytest.approx(25.0)
    assert fabric == 0.0


def test_workload_mix_zero_workload_with_fabric_activity():
    """Zero real workload + fabric traffic must not invent a workload mix, but
    fabric must still register as activity."""
    read, write, reclaim, fabric = nvme_tcp.block_workload_mix(
        _rows(handle_request=30, transport_free=20))
    assert read == 0.0 and write == 0.0 and reclaim == 0.0
    assert fabric == pytest.approx(100.0)


def test_workload_mix_idle_is_all_zero():
    assert nvme_tcp.block_workload_mix(_rows()) == (0.0, 0.0, 0.0, 0.0)


# ---------------------------------------------------------------------------
# Drill re-poll throttle + forced manual refresh (Phase C, deterministic part)
# ---------------------------------------------------------------------------
def _prime_drill(monkeypatch):
    monkeypatch.setattr(nvme_tcp, "DRILL_MODE", "cnode", raising=False)
    monkeypatch.setattr(nvme_tcp, "DRILL_MONITORS", [(["m1"], None, "cn1")], raising=False)
    monkeypatch.setattr(nvme_tcp, "DRILL_OBJECTS", [{"id": 1, "name": "cn1"}], raising=False)
    monkeypatch.setattr(nvme_tcp, "LAST_DRILL_ROWS", [], raising=False)
    monkeypatch.setattr(nvme_tcp, "_DRILL_LAST_QUERY_AT", 0.0, raising=False)
    calls = []
    monkeypatch.setattr(nvme_tcp, "query_ops_monitors",
                        lambda ids: calls.append(1) or ["r"])
    monkeypatch.setattr(nvme_tcp, "build_rows_from_results",
                        lambda *a, **k: ([{"key": "read", "ops_sec": 5.0, "avg_us": 100.0}], None))
    return calls


def test_nvme_drill_query_is_throttled_between_ticks(monkeypatch):
    calls = _prime_drill(monkeypatch)
    nvme_tcp.fetch_drill_query(force=True)      # entry: forced -> queries
    assert len(calls) == 1
    assert nvme_tcp.LAST_DRILL_ROWS, "drill produced no rows"
    for _ in range(4):                           # rapid ticks within the window
        nvme_tcp.fetch_drill_query()
    assert len(calls) == 1, "drill re-queried on every tick instead of throttling"


def test_nvme_manual_refresh_forces_a_drill_query(monkeypatch):
    calls = _prime_drill(monkeypatch)
    nvme_tcp.fetch_drill_query(force=True)
    assert len(calls) == 1
    nvme_tcp.fetch_drill_query()                 # throttled
    assert len(calls) == 1
    monkeypatch.setattr(nvme_tcp, "fetch_monitor_query", lambda *a, **k: None)
    nvme_tcp.manual_refresh()                    # space bar -> forces
    assert len(calls) == 2


# ---------------------------------------------------------------------------
# FR-A navigation standardization (NVMe footer)
# ---------------------------------------------------------------------------
def test_nvme_help_bar_uses_canonical_controls(monkeypatch):
    import io
    import sys
    import tui_layout

    tui_layout.set_color(False)
    monkeypatch.setattr(nvme_tcp, "_COLOR", False, raising=False)
    buf, real = io.StringIO(), sys.stdout
    sys.stdout = buf
    try:
        nvme_tcp._render_help_bar(120)
    finally:
        sys.stdout = real
    bar = tui_layout.strip_ansi(buf.getvalue())
    # VIP standardizes on [i], never [v]; exit is [x], never [p]; space refresh.
    assert "[i] VIP" in bar
    assert "[x] Exit drill" in bar
    assert "[space] Refresh" in bar
    assert "[q] Quit" in bar
    assert "[v]" not in bar, "VIP must not be bound to v"
    assert "[p]" not in bar, "exit-drill must standardize on x, not p"
    # Common controls precede NVMe-specific ones (Host, Reset stats).
    assert bar.index("[q] Quit") < bar.index("[c] cNode") < bar.index("[i] VIP") < bar.index("[x] Exit drill")
    assert bar.index("[x] Exit drill") < bar.index("[h] Host")


# ---------------------------------------------------------------------------
# FR-B: latency unit handling (literal values)
# ---------------------------------------------------------------------------
def test_combined_latency_header_does_not_collapse_sub_10us_to_zero(monkeypatch):
    """The header used to hand-format `avg_us/1000` as '%.2f ms', so a real
    6 us combined latency displayed as '0.00 ms' - a measurement rendered as
    absence (FR-B). It now routes through the shared auto-scaling formatter."""
    import io
    import sys
    import tui_layout

    tui_layout.set_color(False)
    monkeypatch.setattr(nvme_tcp, "_COLOR", False, raising=False)
    monkeypatch.setattr(nvme_tcp, "VOLUME_SCOPED", False, raising=False)
    rows = [
        {"key": "read", "label": "READ", "category": "data", "ops_sec": 100.0,
         "avg_us": 6.0, "bw_mbs": 10.0, "avg_io_bytes": 4096.0},
        {"key": "write", "label": "WRITE", "category": "data", "ops_sec": 50.0,
         "avg_us": 6.0, "bw_mbs": 5.0, "avg_io_bytes": 4096.0},
    ]
    buf, real = io.StringIO(), sys.stdout
    sys.stdout = buf
    try:
        nvme_tcp._render_health_panel(rows, 160)
    finally:
        sys.stdout = real
    frame = tui_layout.strip_ansi(buf.getvalue())
    assert "0.00 ms" not in frame, "sub-10us combined latency rendered as zero"
    assert "6.00" in frame, "the real 6 us value is not visible"


def test_op_row_latency_is_never_unit_converted():
    """BlockMetrics/VolumeMetrics *_latency__avg lands in avg_us verbatim -
    the monitor-API microsecond convention (consistent with D-003). Any
    accidental conversion here would be a silent 1000x error. Uses a
    rate-based op so the passthrough is observable on a single poll
    (counter-based ops gate latency on a two-poll ops delta)."""
    rows = [{"key": "handle_request", "label": "HANDLE REQ", "category": "fabric",
             "ops_fqn": "BlockMetrics,handle_request_latency__rate",
             "avg_fqn": "BlockMetrics,handle_request_latency__avg",
             "ops_raw": 12.5, "lat_raw": 541.4, "sample": "s",
             "series_data": None, "ops_sec": None, "avg_us": None,
             "bw_mbs": None, "avg_io_bytes": None}]
    nvme_tcp.apply_op_rates(rows, poll_time=100.0, scope="cluster")
    assert rows[0]["avg_us"] == 541.4, "latency value must pass through unconverted"
    assert rows[0]["ops_sec"] == 12.5


def test_counter_op_latency_needs_ops_evidence_first():
    """A counter-based op cannot show a rate (or a latency) from one poll -
    the first sample is a baseline, not a measurement. Latency appears only
    once an ops delta proves traffic; zero-vs-unavailable stays honest."""
    nvme_tcp.PREV_COUNTER_STATE.clear()
    rows = [{"key": "read", "label": "READ", "category": "data",
             "ops_fqn": "BlockMetrics,read_req",
             "avg_fqn": "BlockMetrics,read_latency__avg",
             "ops_raw": 1000.0, "lat_raw": 541.4, "sample": "s",
             "series_data": None, "ops_sec": None, "avg_us": None,
             "bw_mbs": None, "avg_io_bytes": None}]
    nvme_tcp.apply_op_rates(rows, poll_time=100.0, scope="cluster")
    assert rows[0]["ops_sec"] is None
    assert rows[0]["avg_us"] is None
