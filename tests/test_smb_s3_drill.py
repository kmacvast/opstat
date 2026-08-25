"""SMB / S3 drill-down: protocol-attribution correctness (FR14).

REWRITTEN 2026-08-25 with the drills themselves. The suite this replaces
pinned SMB view/tenant and S3 bucket/tenant drills built on the monitor API's
ViewMetrics/TenantMetrics families - which real-VMS evidence (D-016) proved
carry no protocol discriminator. On a mixed cluster the SMB VIEW drill ranked
and displayed an NVMe view (/kmacs/block) and an NFS view
(/bgolliher/nfs-source) under "VAST SMB ... | VIEW DRILL"; that lab defect is
reproduced literally in the mock's host_view exposition and pinned here.

The new contract, per FR14 owner decisions:

- SMB view/tenant ride /prometheusmetrics/host_view filtered to
  protocol=SMB2, first-party validated on var203/5.4.6: one throttled
  scrape, zero monitors, nothing on the 5 s refresh path (D-004/D-005),
  and ONLY SMB2-attributed rows on screen.
- S3 bucket/tenant render an honest capability notice (zero API cost) until
  a first-party S3 host_view correlation validates a protocol-scoped
  rebuild - never all-protocol rows relabelled as GET/s / PUT/s.
- S3 vip keeps its protocol-scoped S3Common monitors; a measured all-zero
  result renders as real zeros (D-009), and /monitors/topn/ - which carries
  no protocol label (D-007) - never substitutes for it. On builds that
  reject vip monitors the drill shows a capability notice, not topn rows.
"""

from __future__ import annotations

import shutil
from types import SimpleNamespace

import pytest

import vast_common
import vast_drill
from tests.mock_vms import MockVMS

pytestmark = pytest.mark.skipif(
    shutil.which("openssl") is None,
    reason="openssl binary required to generate the mock VMS certificate",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def vms(tmp_path):
    server = MockVMS(certdir=str(tmp_path)).start()
    yield server
    server.stop()


def _args(vms, **overrides):
    base = dict(
        vms="127.0.0.1", port=vms.port, user="admin", password=None,
        sample_average=None, refresh=5, csv=None, no_color=True,
        discover_metrics=False, log_api_calls=False,
        export_openmetrics=False, openmetrics_file=None,
        clients=None, buckets=None, tenants=None, volumes=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture
def smb_engine(vms, monkeypatch):
    import smb

    monkeypatch.setenv("VAST_TOKEN", "test-token")
    smb.init_config(_args(vms))
    smb.CLUSTER_ID, smb.CLUSTER_NAME = smb.get_current_cluster()
    smb.create_headline_monitors()
    smb.fetch_monitor_query()
    yield smb, vms
    smb.exit_hostview_mode()
    smb.exit_drill_mode()
    smb.cleanup()
    smb._CLEANED_UP = False
    vast_common.close_connection()


@pytest.fixture
def s3_engine(vms, monkeypatch):
    import s3

    monkeypatch.setenv("VAST_TOKEN", "test-token")
    s3.init_config(_args(vms))
    s3.CLUSTER_ID, s3.CLUSTER_NAME = s3.get_current_cluster()
    s3.create_headline_monitors()
    yield s3, vms
    s3.exit_drill_mode()
    s3.cleanup()
    s3._CLEANED_UP = False
    vast_common.close_connection()


def _headline_ids(smb):
    return {smb.HEADLINE_MONITOR_ID, smb.SMB_COMMAND_MONITOR_ID}


def _queries(counts):
    return sum(v for k, v in counts.items() if "query" in k)


def _scrapes(vms):
    return sum(1 for _t, _m, p, _s in vms.calls()
               if "prometheusmetrics/host_view" in p)


def _frame(module):
    import contextlib
    import io

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        module._render_frame()
    return buf.getvalue()


# ===========================================================================
# SMB view/tenant: host_view protocol=SMB2, and nothing else
# ===========================================================================
def test_smb_view_is_one_scrape_and_zero_monitors(smb_engine):
    """Entry costs one host_view GET; no monitor is created or leaked."""
    smb, vms = smb_engine
    before = set(vms.live_monitors())
    vms.reset_calls()
    smb.enter_hostview_mode("view")
    assert smb.HV_MODE == "view"
    assert _scrapes(vms) == 1, vms.counts()
    assert sum(vms.counts().values()) == 1, (
        "view entry must cost exactly the scrape: %s" % vms.counts())
    assert set(vms.live_monitors()) == before, "exporter drill created monitors"


def test_smb_view_shows_only_smb2_attributed_rows(smb_engine):
    """THE lab defect, reproduced and pinned (FR14).

    The mock's host_view carries the literal contamination observed on
    var203: /kmacs/block under protocol=BLOCK and /bgolliher/nfs-source
    under protocol=NFS3, both busier than the SMB2 rows. A protocol-scoped
    drill must never surface another protocol's activity.
    """
    smb, _vms = smb_engine
    smb.enter_hostview_mode("view")
    frame = _frame(smb)
    assert "/kmacs/smb/opstat" in frame, "the SMB2-attributed view is missing"
    assert "opstattest" in frame, "the SMB-native share identity is missing"
    assert "/kmacs/block" not in frame, (
        "an NVMe (BLOCK) view rendered inside the SMB VIEW drill - the "
        "2026-08-25 lab defect is back")
    assert "/bgolliher/nfs-source" not in frame, (
        "an NFS view rendered inside the SMB VIEW drill")
    assert "/view/317" not in frame, "an NFS4-attributed view leaked in"
    assert "[x] Exit drill" in frame, (
        "navigation footer missing from the host_view drill frame")


def test_smb_tenant_shows_only_smb2_tenants(smb_engine):
    """Tenant aggregation inherits the SMB2 filter from the shared parse."""
    smb, _vms = smb_engine
    smb.enter_hostview_mode("tenant")
    frame = _frame(smb)
    assert "tenant-0" in frame, "the SMB2-attributed tenant is missing"
    assert "tenant-9" not in frame, (
        "a BLOCK-only tenant rendered inside the SMB TENANT drill")
    assert "tenant-8" not in frame, (
        "an NFS3-only tenant rendered inside the SMB TENANT drill")


def test_smb_view_aggregates_every_client_of_a_share(smb_engine):
    """The per-view figure must SUM the share's clients, not show one.

    host_view emits one series per client IP x path, so a rebuild that
    forgot to aggregate would still render a plausible-looking row. The mock
    puts two clients on /kmacs/smb/opstat; the row must carry both.
    """
    import nfs4_native

    smb, _vms = smb_engine
    smb.enter_hostview_mode("view")
    rows = {r["path"]: r for r in nfs4_native.aggregate_by_path(smb.HOSTVIEW.rows)}
    row = rows["/kmacs/smb/opstat"]
    per_client = [r["iops"] for r in smb.HOSTVIEW.rows
                  if r["path"] == "/kmacs/smb/opstat"]
    assert len(per_client) == 2, "the mock should carry two SMB2 clients here"
    assert row["client_count"] == 2, row
    assert row["iops"] == pytest.approx(sum(per_client)), (
        "the view row must sum its clients, got %r from %r"
        % (row["iops"], per_client))
    assert row["share"] == "opstattest", (
        "the share column must carry THIS row's share, got %r" % row["share"])


def test_smb_tenant_sums_across_clients_and_paths(smb_engine):
    """Same for tenants: the figure spans every SMB2 client and path of that
    tenant, and excludes every other protocol's contribution."""
    import nfs4_native

    smb, _vms = smb_engine
    smb.enter_hostview_mode("tenant")
    rows = {r["tenant"]: r for r in nfs4_native.aggregate_by_tenant(smb.HOSTVIEW.rows)}
    row = rows["tenant-0"]
    contributing = [r["iops"] for r in smb.HOSTVIEW.rows if r["tenant"] == "tenant-0"]
    assert len(contributing) >= 2, "expected multiple SMB2 series for tenant-0"
    assert row["iops"] == pytest.approx(sum(contributing))
    assert row["client_count"] >= 2, row
    # Every row feeding the aggregate came through the SMB2 filter.
    assert all(r["share"] in ("opstattest", "othershare", "")
               for r in smb.HOSTVIEW.rows), "a non-SMB2 row reached the collector"


def test_smb_tenant_reuses_the_view_scrape(smb_engine):
    """View and tenant share one collector; switching inside the throttle
    window must not pay for a second scrape."""
    smb, vms = smb_engine
    smb.enter_hostview_mode("view")
    vms.reset_calls()
    smb.enter_hostview_mode("tenant")
    assert smb.HV_MODE == "tenant"
    assert _scrapes(vms) == 0, "tenant re-scraped inside the throttle window"


def test_smb_hostview_never_rides_the_refresh_tick(smb_engine):
    """D-004: /prometheusmetrics/* stays off the 5 s path. Four poll ticks
    inside the throttle window must scrape zero times; space forces one."""
    smb, vms = smb_engine
    smb.enter_hostview_mode("view")
    vms.reset_calls()
    for _ in range(4):
        smb.poll_tick()
    assert _scrapes(vms) == 0, (
        "the dashboard tick scraped the exporter: %s" % vms.counts())
    smb.manual_refresh()
    assert _scrapes(vms) == 1, "space-bar refresh must bypass the throttle"


def test_smb_view_provenance_names_the_real_source(smb_engine):
    """The header's source token must describe the active panel: host_view
    data under a "source SMBCommon" claim is a provenance lie (FR14)."""
    smb, _vms = smb_engine
    smb.enter_hostview_mode("view")
    frame = _frame(smb)
    assert "source host_view/SMB2" in frame, frame[:200]
    assert "source SMBCommon" not in frame
    smb.exit_hostview_mode()
    frame = _frame(smb)
    assert "source SMBCommon" in frame, "headline provenance regressed"


def test_smb_hostview_x_returns_to_dashboard(smb_engine):
    smb, _vms = smb_engine
    smb.enter_hostview_mode("view")
    assert smb._dispatch_key("x") == "refresh"
    assert smb.HV_MODE is None
    frame = _frame(smb)
    assert smb.HEALTH_PANEL_TITLE in frame


def test_smb_cnode_drill_still_uses_monitors_and_cleans_up(smb_engine):
    """The one monitor-backed drill left keeps its lifecycle honest."""
    smb, vms = smb_engine
    smb.enter_drill_mode("cnode")
    assert smb.DRILL_ERROR is None
    assert smb.DRILL_MONITORS, "cnode drill should create monitors"
    smb.exit_drill_mode()
    assert set(vms.live_monitors()) <= _headline_ids(smb), (
        "cnode drill monitors leaked")


# ===========================================================================
# S3 bucket/tenant: honest capability notice until a protocol-scoped source
# is first-party validated
# ===========================================================================
@pytest.mark.parametrize("mode,marker_attr", [
    ("bucket", "BUCKET_UNAVAILABLE_MARKER"),
    ("tenant", "TENANT_UNAVAILABLE_MARKER"),
])
def test_s3_scope_notice_costs_nothing(s3_engine, mode, marker_attr):
    """Zero API calls, zero monitors - the D-016 pattern: no inventory fetch
    and no ranking for a question this cluster cannot answer honestly."""
    s3, vms = s3_engine
    before = set(vms.live_monitors())
    vms.reset_calls()
    s3.enter_drill_mode(mode)
    assert s3.DRILL_MODE is None
    assert getattr(s3, marker_attr) in (s3.DRILL_ERROR or ""), s3.DRILL_ERROR
    assert sum(vms.counts().values()) == 0, (
        "the capability notice must cost zero API calls: %s" % vms.counts())
    assert set(vms.live_monitors()) == before


@pytest.mark.parametrize("mode", ["bucket", "tenant"])
def test_s3_scope_notice_renders_with_footer(s3_engine, mode):
    s3, _vms = s3_engine
    s3.enter_drill_mode(mode)
    frame = _frame(s3)
    assert "attribution is not available" in frame
    assert "Cluster-level S3 telemetry remains available." in frame
    assert "[x] Exit drill" in frame or "[q] Quit" in frame, (
        "navigation footer missing from the notice frame")
    assert "GET/s" not in frame, (
        "REST-verb columns rendered without a protocol-scoped source")


def test_s3_bucket_never_renders_foreign_views(s3_engine):
    """The bucket drill previously ranked all-protocol ViewMetrics and
    relabelled it as REST verbs; with the notice in place, no view path from
    the all-protocol inventory may appear."""
    s3, _vms = s3_engine
    s3.enter_drill_mode("bucket")
    frame = _frame(s3)
    assert "/kmacs/block" not in frame
    assert "/bgolliher/nfs-source" not in frame


# ===========================================================================
# S3 vip: measured zeros are data; topn never substitutes
# ===========================================================================
def test_s3_vip_measured_zero_is_preserved_and_topn_not_consulted(
        s3_engine, monkeypatch):
    """D-009 + D-007: an all-zero S3Common result renders as real zeros; the
    old behavior silently swapped in protocol-unattributable topn rows."""
    s3, vms = s3_engine
    s3.enter_drill_mode("vip")
    assert s3.DRILL_ERROR is None
    assert s3.DRILL_MONITORS, "expected monitor-backed vip mode on the mock"

    def all_zero_row(mode, result, obj_name):
        return {"name": obj_name, "total_ops": 0.0, "latency_us": None,
                "bw_mbs": None, "get_ops": 0.0, "put_ops": 0.0,
                "del_ops": 0.0, "list_ops": 0.0, "top_rpc": "-",
                "top_rpc_pct": 0.0}

    monkeypatch.setattr(s3, "_build_drill_row", all_zero_row)
    vms.reset_calls()
    s3.fetch_drill_query(force=True)
    assert s3.LAST_DRILL_ROWS, "zero rows must still be rows"
    assert all((r["total_ops"] or 0.0) == 0.0 for r in s3.LAST_DRILL_ROWS)
    topn = [p for _t, _m, p, _s in vms.calls() if "monitors/topn" in p]
    assert not topn, (
        "a measured zero was 'rescued' with protocol-unattributable topn "
        "rows: %s" % topn)


def test_s3_vip_rejected_monitors_mean_notice_not_topn(s3_engine):
    """On builds that refuse vip-scope monitors there is no protocol-scoped
    source, so the drill says so - it does not dress topn rows as S3."""
    s3, vms = s3_engine
    vms.state.reject_object_types = ("vip",)
    s3.enter_drill_mode("vip")
    assert s3.DRILL_MODE is None
    assert s3.VIP_UNAVAILABLE_MARKER in (s3.DRILL_ERROR or ""), s3.DRILL_ERROR
    assert s3.LAST_DRILL_ROWS == []
    frame = _frame(s3)
    assert "attribution is not available" in frame


def test_s3_vip_ranks_via_topn(s3_engine):
    """Candidate ORDERING may consult topn (rank by activity, never API
    order); what is DISPLAYED stays S3Common per vip."""
    s3, vms = s3_engine
    vms.reset_calls()
    s3.enter_drill_mode("vip")
    assert s3.DRILL_ERROR is None
    topn = [p for _t, _m, p, _s in vms.calls() if "monitors/topn" in p]
    assert topn, "VIP ranking must consult /monitors/topn/"
    assert s3.DRILL_OBJECTS, "VIP drill produced no objects"


def test_s3_vip_filters_internal_192_168_addresses():
    import s3

    objs = [
        {"id": 1, "name": "192.168.5.5", "ip": "192.168.5.5"},
        {"id": 2, "name": "vip-public", "ip": "10.1.0.9"},
    ]
    entries = s3._vip_objects_for_drill(objs)
    names = [e["name"] for e in entries]
    assert not any(n.startswith("192.168.") for n in names), names
    assert any("10.1.0.9" in (e.get("name") or "") or e["id"] == 2 for e in entries)


# ===========================================================================
# Loading interstitial before blocking drill work
# ===========================================================================
def _capture_frames(monkeypatch):
    frames = []
    monkeypatch.setattr(vast_common, "flush_frame", lambda text: frames.append(text))
    return frames


@pytest.mark.parametrize("mode,needle", [
    ("view", "Loading the VIEW drill-down"),
    ("tenant", "Loading the TENANT drill-down"),
])
def test_smb_hostview_paints_loading_frame_first(smb_engine, monkeypatch, mode, needle):
    """The scrape can cost seconds; a status frame paints before it - with
    the PLAIN wording, because exporter scrapes are far under the cold
    monitor-entry threshold (same rule as the NFSv4.1 exporter drills)."""
    smb, _vms = smb_engine
    frames = _capture_frames(monkeypatch)
    smb.enter_hostview_mode(mode)
    assert frames, "no frame rendered"
    assert needle in frames[0], frames[0][:120]
    assert "30+ seconds" not in frames[0], (
        "the cold monitor-entry warning is false for a one-scrape drill")


@pytest.mark.parametrize("mode,needle", [
    ("bucket", "Loading the BUCKET drill-down"),
    ("tenant", "Loading the TENANT drill-down"),
])
def test_s3_notice_paints_loading_frame_without_cold_warning(
        s3_engine, monkeypatch, mode, needle):
    """The notice is instant; promising a 30+ second wait would be false."""
    s3, _vms = s3_engine
    frames = _capture_frames(monkeypatch)
    s3.switch_drill_mode(mode)
    assert frames, "no frame rendered"
    assert needle in frames[0], frames[0][:120]
    assert "30+ seconds" not in frames[0]


# ===========================================================================
# Startup interstitial before blocking initial metric collection
# ===========================================================================
@pytest.mark.parametrize("engine_name", ["smb", "s3"])
def test_startup_paints_gathering_frame_before_blocking_work(vms, monkeypatch, engine_name):
    import importlib

    mod = importlib.import_module(engine_name)
    monkeypatch.setenv("VAST_TOKEN", "test-token")
    mod.init_config(_args(vms))

    frames = _capture_frames(monkeypatch)
    first_api_at = []
    real = mod.api_request

    def watched(method, path, payload=None):
        first_api_at.append(len(frames))
        return real(method, path, payload)

    monkeypatch.setattr(mod, "api_request", watched)
    try:
        mod.initialize()
        assert frames, "no startup frame rendered"
        # 3-phase startup: the first frame names the host (cluster unknown yet),
        # and a later frame reaches the "gathering" phase.
        assert "Connecting to" in frames[0], frames[0][:120]
        assert any("Gathering initial metrics" in f for f in frames), (
            "the gathering-metrics phase never rendered")
        assert first_api_at and first_api_at[0] >= 1, (
            "an API call was issued before the startup status frame")
    finally:
        mod.cleanup()
        mod._CLEANED_UP = False
        vast_common.close_connection()
