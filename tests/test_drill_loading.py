"""Drill-entry loading interstitial and NFSv4 view-drill rebuild.

Entering a drill can block for seconds against a real VMS - ranking hundreds
of views, creating monitors, or scraping a 276 KB exporter endpoint. The
stand-by message has to reach the terminal *before* that work starts, or the
TUI simply looks hung. NFSv4.1 had no such message at all for its c/v/t
drills.
"""

from __future__ import annotations

import shutil
from types import SimpleNamespace

import pytest

import vast_drill
from tests.mock_vms import MockVMS

pytestmark = pytest.mark.skipif(
    shutil.which("openssl") is None,
    reason="openssl binary required to generate the mock VMS certificate",
)


# ---------------------------------------------------------------------------
# The shared helper
# ---------------------------------------------------------------------------
def test_status_is_painted_before_the_blocking_work_runs():
    """Ordering is the whole point: render must happen before work."""
    events = []

    def show_status(text):
        events.append(("status", text))

    def render():
        events.append(("render", None))

    def work():
        events.append(("work", None))
        return "done"

    result = vast_drill.with_loading_status(show_status, render, "view", work)
    assert result == "done"
    kinds = [kind for kind, _ in events]
    assert kinds == ["status", "render", "work", "status"], kinds
    assert events[0][1] == "Loading the VIEW drill-down, please stand by..."
    assert events[-1][1] is None, "status not cleared"


def test_status_is_cleared_even_when_the_work_raises():
    cleared = []

    def show_status(text):
        cleared.append(text)

    def boom():
        raise RuntimeError("VMS said no")

    with pytest.raises(RuntimeError):
        vast_drill.with_loading_status(show_status, lambda: None, "tenant", boom)
    assert cleared[-1] is None, "a failed drill left the status frame up"


@pytest.mark.parametrize("mode,expected", [
    ("view", "Loading the VIEW drill-down, please stand by..."),
    ("tenant", "Loading the TENANT drill-down, please stand by..."),
    ("cnode", "Loading the cNODE drill-down, please stand by..."),
    ("native", "Loading the NFSv4 telemetry view, please stand by..."),
    ("hosts", "Loading the NFSv4 hosts view, please stand by..."),
])
def test_every_drill_mode_has_a_loading_message(mode, expected):
    assert vast_drill.loading_message(mode) == expected


def test_unknown_mode_still_gets_a_message():
    assert "stand by" in vast_drill.loading_message("something-new")


# ---------------------------------------------------------------------------
# Cold-entry wording. A first monitor-backed drill entry ranks the whole
# object population (SMB view ranking measured 104 s before batching, 9 s
# after; NVMe entry ~2 min on var203), so the wait is named up front instead
# of reading as a hang. Re-entry reuses the rank cache and stays plain.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("mode,expected", [
    ("view", "Loading the VIEW drill-down, this can take 30+ seconds the first time..."),
    ("tenant", "Loading the TENANT drill-down, this can take 30+ seconds the first time..."),
    ("cnode", "Loading the cNODE drill-down, this can take 30+ seconds the first time..."),
    ("vip", "Loading the VIP drill-down, this can take 30+ seconds the first time..."),
    ("host", "Loading the HOST drill-down, this can take 30+ seconds the first time..."),
])
def test_first_cold_entry_names_the_expected_wait(mode, expected):
    assert vast_drill.loading_message(mode, first_time=True) == expected


def test_unknown_mode_keeps_its_name_in_the_cold_wording():
    """The drill-name substitution survives the first-time wording."""
    assert vast_drill.loading_message("blockhost", first_time=True) == (
        "Loading the BLOCKHOST drill-down, this can take 30+ seconds the "
        "first time...")


@pytest.mark.parametrize("mode", ["view", "tenant", "cnode", "something-new"])
def test_default_is_still_the_plain_stand_by_wording(mode):
    """Repeat entry, and every caller that does not opt in, is unchanged."""
    assert vast_drill.loading_message(mode).endswith("please stand by...")
    assert "30+ seconds" not in vast_drill.loading_message(mode)


@pytest.mark.parametrize("mode", ["native", "hosts", "views", "delegation"])
def test_fast_paths_never_claim_a_30_second_wait(mode):
    """The exporter scrapes cost 1.2-2.4 s and the delegation lookup 217-751
    ms on a real cluster - those callers must not pass first_time, and their
    wording must stay plain even if one ever did by mistake."""
    plain = vast_drill.loading_message(mode)
    assert "30+ seconds" not in plain
    import nfs_v41
    src = __import__("inspect").getsource(nfs_v41.enter_exporter_mode)
    assert "first_time" not in src, (
        "the exporter scrape must not use the cold-drill wording")


def test_begin_load_is_first_time_once_per_mode_per_run():
    """Per mode, not global, and not tied to whether the mode ranks: SMB's
    cNode drill head-slices its population and never stores a ranking, yet
    its first entry still pays monitor creation and the first query."""
    session = vast_drill.DrillSession(
        request_fn=lambda *a, **k: {}, create_monitor_fn=lambda *a, **k: 1,
        delete_monitor_fn=lambda *a, **k: None)
    assert session.begin_load("view") is True
    assert session.begin_load("view") is False
    assert session.begin_load("cnode") is True, "per mode, not global"
    session.reset()
    assert session.begin_load("view") is True, "a new run is cold again"


def test_begin_load_is_independent_of_the_rank_cache():
    session = vast_drill.DrillSession(
        request_fn=lambda *a, **k: {}, create_monitor_fn=lambda *a, **k: 1,
        delete_monitor_fn=lambda *a, **k: None)
    session._store("view", [{"id": 1}], [{"id": 1, "name": "v"}])
    assert session.begin_load("view") is True, (
        "a cached ranking is not proof the drill was ever entered")


def test_with_loading_status_passes_the_cold_wording_through():
    seen = []
    vast_drill.with_loading_status(
        seen.append, lambda: None, "view", lambda: None, first_time=True)
    assert seen[0] == (
        "Loading the VIEW drill-down, this can take 30+ seconds the first "
        "time...")
    assert seen[-1] is None


# ---------------------------------------------------------------------------
# Engine wiring: the frame really reaches the terminal first
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
    nfs_v41.fetch_monitor_query()
    yield nfs_v41, server
    nfs_v41.exit_exporter_mode()
    nfs_v41.exit_drill_mode()
    nfs_v41.cleanup()
    nfs_v41._CLEANED_UP = False
    server.stop()


def _capture_frames(module, monkeypatch):
    """Record every frame flushed, in order."""
    frames = []
    import vast_common

    monkeypatch.setattr(vast_common, "flush_frame", lambda text: frames.append(text))
    return frames


@pytest.mark.parametrize("mode,needle", [
    ("cnode", "Loading the cNODE drill-down"),
    ("tenant", "Loading the TENANT drill-down"),
])
def test_monitor_drills_paint_the_loading_frame_first(engine, monkeypatch,
                                                     mode, needle):
    """Regression: NFSv4.1 had no DRILL_STATUS, so c/v/t blocked in silence."""
    nfs_v41, server = engine
    frames = _capture_frames(nfs_v41, monkeypatch)
    first_api_at = []
    real_request = nfs_v41.api_request

    def watched(method, path, payload=None):
        first_api_at.append(len(frames))
        return real_request(method, path, payload)

    monkeypatch.setattr(nfs_v41, "api_request", watched)
    nfs_v41.switch_drill_mode(mode)

    assert frames, "no frame rendered at all"
    assert needle in frames[0], f"first frame was not the loading frame: {frames[0][:120]}"
    assert first_api_at and first_api_at[0] >= 1, (
        "an API call was issued before any frame reached the terminal")


@pytest.mark.parametrize("mode,needle", [
    ("native", "Loading the NFSv4 telemetry view"),
    ("hosts", "Loading the NFSv4 hosts view"),
    ("view", "Loading the VIEW drill-down"),
])
def test_exporter_drills_paint_the_loading_frame_first(engine, monkeypatch,
                                                      mode, needle):
    nfs_v41, server = engine
    frames = _capture_frames(nfs_v41, monkeypatch)
    scraped_at = []
    real_text = nfs_v41.vast_common.request_text

    def watched(method, path, *args, **kwargs):
        if "prometheus" in path:
            scraped_at.append(len(frames))
        return real_text(method, path, *args, **kwargs)

    monkeypatch.setattr(nfs_v41.vast_common, "request_text", watched)
    # The collectors captured the original callable at construction time.
    nfs_v41.NFS4._request_text = watched
    nfs_v41.HOSTVIEW._request_text = watched

    nfs_v41.enter_exporter_mode(mode)
    assert frames, "no frame rendered at all"
    assert needle in frames[0], f"first frame was not the loading frame: {frames[0][:120]}"
    assert scraped_at and scraped_at[0] >= 1, (
        "the exporter was scraped before any frame reached the terminal")


def test_loading_status_is_cleared_after_entry(engine):
    nfs_v41, _server = engine
    nfs_v41.switch_drill_mode("cnode")
    assert nfs_v41.DRILL_STATUS is None
    nfs_v41.enter_exporter_mode("native")
    assert nfs_v41.EXPORTER_STATUS is None


# ---------------------------------------------------------------------------
# The rebuilt NFSv4 VIEW drill
# ---------------------------------------------------------------------------
def test_view_drill_uses_the_exporter_not_viewmetrics(engine):
    """ViewMetrics reported no NFSv4 activity on a live cluster while
    Nfs4Metrics measured ~1553 SEQUENCE/s; the drill now reads host_view."""
    nfs_v41, server = engine
    server.reset_calls()
    nfs_v41.enter_exporter_mode("view")
    paths = [p for _t, _m, p, _s in server.calls()]
    assert "/api/prometheusmetrics/host_view" in paths
    assert not any("/monitors/" in p and "POST" for p in paths if "monitors" in p), (
        "still creating ViewMetrics monitors for the view drill")
    assert nfs_v41.EXPORTER_MODE == "view"


def test_view_drill_aggregates_hosts_into_views(engine):
    nfs_v41, _server = engine
    nfs_v41.enter_exporter_mode("view")
    import nfs4_native

    rows = nfs4_native.aggregate_by_path(nfs_v41.HOSTVIEW.rows)
    assert rows, "no view rows aggregated"
    # Every row is NFS4 only, and carries the host count it came from.
    assert all(r["client_count"] >= 1 for r in rows)
    assert rows == sorted(rows, key=lambda r: (-(r["iops"] or 0.0), r["path"]))


def test_view_and_hosts_drills_share_one_scrape(engine):
    """Both read host_view, so switching between them inside the throttle
    window must not cost a second request."""
    nfs_v41, server = engine
    nfs_v41.enter_exporter_mode("hosts")
    server.reset_calls()
    nfs_v41.enter_exporter_mode("view")
    scrapes = [p for _t, _m, p, _s in server.calls() if "prometheus" in p]
    assert scrapes == [], f"re-scraped when switching drills: {scrapes}"


def test_aggregate_by_path_weights_latency_by_iops():
    import nfs4_native

    rows = [
        {"ip": "a", "path": "/v", "tenant": "t", "iops": 40.0, "read_iops": 0.0,
         "write_iops": 0.0, "md_iops": 0.0, "bw": 0.0, "read_bw": 0.0,
         "write_bw": 0.0, "latency_us": 800.0},
        {"ip": "b", "path": "/v", "tenant": "t", "iops": 10.0, "read_iops": 0.0,
         "write_iops": 0.0, "md_iops": 0.0, "bw": 0.0, "read_bw": 0.0,
         "write_bw": 0.0, "latency_us": 300.0},
    ]
    row = nfs4_native.aggregate_by_path(rows)[0]
    assert row["client_count"] == 2
    assert row["iops"] == pytest.approx(50.0)
    # (800*40 + 300*10) / 50
    assert row["latency_us"] == pytest.approx(700.0)


def test_aggregate_by_path_handles_missing_latency():
    import nfs4_native

    rows = [{"ip": "a", "path": "/v", "tenant": "t", "iops": 5.0,
             "read_iops": None, "write_iops": None, "md_iops": None,
             "bw": None, "read_bw": None, "write_bw": None,
             "latency_us": None}]
    row = nfs4_native.aggregate_by_path(rows)[0]
    assert row["latency_us"] is None
    assert row["iops"] == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# Cold vs warm entry through the real engines. Reported from manual testing
# against SMB, whose [v]/[c]/[t] entries were the slow ones.
# ---------------------------------------------------------------------------
@pytest.fixture
def smb_engine(tmp_path, monkeypatch):
    server = MockVMS(certdir=str(tmp_path)).start()
    import smb

    monkeypatch.setenv("VAST_TOKEN", "test-token")
    smb.init_config(SimpleNamespace(
        vms="127.0.0.1", port=server.port, user="admin", password=None,
        sample_average=None, refresh=5, csv=None, no_color=True,
        discover_metrics=False, log_api_calls=False,
        export_openmetrics=False, openmetrics_file=None,
    ))
    smb.CLUSTER_ID, smb.CLUSTER_NAME = smb.get_current_cluster()
    smb.create_headline_monitors()
    yield smb, server
    smb.exit_drill_mode()
    smb.cleanup()
    smb._CLEANED_UP = False
    server.stop()


@pytest.mark.parametrize("mode", ["view", "tenant", "cnode"])
def test_smb_cold_entry_warns_then_repeat_entry_does_not(smb_engine,
                                                         monkeypatch, mode):
    smb, _server = smb_engine
    frames = _capture_frames(smb, monkeypatch)
    smb.switch_drill_mode(mode)
    assert frames, "no frame rendered at all"
    assert "30+ seconds the first time" in frames[0], (
        "cold %s entry did not name the expected wait: %r"
        % (mode, frames[0][:160]))

    del frames[:]
    smb.switch_drill_mode(mode)
    assert frames, "no frame rendered on re-entry"
    assert "30+ seconds" not in frames[0], (
        "a warm re-entry must not repeat the first-time warning: %r"
        % frames[0][:160])
    assert "please stand by" in frames[0]


def test_nfs41_exporter_drill_keeps_the_plain_wording(engine, monkeypatch):
    """[v] is an exporter scrape in NFSv4.1 even though it names the same
    "view" mode the monitor-backed engines use - it must stay plain."""
    nfs_v41, _server = engine
    frames = _capture_frames(nfs_v41, monkeypatch)
    nfs_v41.enter_exporter_mode("view")
    assert frames and "Loading the VIEW drill-down" in frames[0]
    assert "30+ seconds" not in frames[0]


def test_nfs3_view_unavailable_never_claims_a_wait(tmp_path, monkeypatch):
    """[v] on a cluster with no per-view NFSv3 source renders the capability
    notice instantly at zero API cost (D-016), so its loading frame must not
    promise a 30-second wait - not even on the first entry of the run."""
    server = MockVMS(certdir=str(tmp_path)).start()
    import nfs_v3

    monkeypatch.setenv("VAST_TOKEN", "test-token")
    nfs_v3.init_config(SimpleNamespace(
        vms="127.0.0.1", port=server.port, user="admin", password=None,
        sample_average=None, refresh=5, csv=None, no_color=True,
        discover_metrics=False, log_api_calls=False,
        export_openmetrics=False, openmetrics_file=None,
    ))
    try:
        assert nfs_v3.view_attribution_source() is None, "fixture assumption"
        statuses = []
        monkeypatch.setattr(nfs_v3, "render_screen",
                            lambda: statuses.append(nfs_v3.DRILL_STATUS))
        nfs_v3.switch_drill_mode("view")
        assert statuses, "no frame rendered"
        assert "30+ seconds" not in (statuses[0] or ""), statuses[0]
        assert "please stand by" in (statuses[0] or "")
        # A drill that does real work still gets the cold wording.
        del statuses[:]
        nfs_v3.switch_drill_mode("cnode")
        assert "30+ seconds the first time" in (statuses[0] or ""), statuses[0]
    finally:
        nfs_v3.cleanup()
        nfs_v3._CLEANED_UP = False
        server.stop()
