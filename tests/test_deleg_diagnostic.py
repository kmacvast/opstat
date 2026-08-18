"""NFSv4.1 delegation diagnostic (FR2) - full behavior matrix.

Grounded in the decisive var204 / VAST 5.5.0.1 capture (2026-08-18):

- ``GET /tenants/{id}/nfs4_delegs/?file_path=<full namespace path>`` is the
  entire wire contract. Without ``file_path`` the endpoint returns HTTP 400
  ``__root__->file_path: field required``; a nonexistent path returns HTTP
  400 ``get_handle_by_path returned an error :
  GetHandleByPathCode.ILLEGAL_PATH``; a valid path answers with the
  ``delegate_info`` wrapper - an empty list for a file no client holds a
  delegation on (which is information, not an error).
- Live records carry exactly six fields (five real WRITE delegations were
  captured): client_id, delegation_client_ip, delegation_stateid,
  delegation_type, revoke_in_progress, vip_addr.
- The endpoint's DELETE sibling revokes live delegations (D-008). The only
  operation this feature may ever perform is the GET above.

The owner-approved product decisions these tests enforce: [d] opens a
one-line full-path prompt (leading /, no guessing, no completion); while the
prompt is open every printable key is path text - typing q must NOT quit and
d/x/v/h must NOT navigate; tenant resolution comes from the view that owns
the namespace (exact view: no fallback; root-only prefix: at most ONE bounded
fallback; never spray tenants); [space] on a result is a manual re-query
only - the normal refresh path performs zero delegation API calls; IDs render
as one dim secondary line under the four primary fields.
"""

from __future__ import annotations

import inspect
import io
import shutil
import sys
from types import SimpleNamespace

import pytest

import tui_layout
import vast_common
import vast_drill
from tests.mock_vms import MockVMS

pytestmark = pytest.mark.skipif(
    shutil.which("openssl") is None,
    reason="openssl binary required to generate the mock VMS certificate",
)


# The proven six-field record, verbatim shape from the var204 capture.
def proven_record(i=0, deleg_type="WRITE", revoke=False):
    return {
        "client_id": 4000 + i,
        "delegation_client_ip": f"172.200.204.{50 + i}",
        "delegation_stateid": 7000 + i,
        "delegation_type": deleg_type,
        "revoke_in_progress": revoke,
        "vip_addr": "172.200.204.6",
    }


@pytest.fixture
def vms(tmp_path):
    server = MockVMS(certdir=str(tmp_path)).start()
    yield server
    server.stop()


@pytest.fixture
def engine41(vms, monkeypatch):
    import nfs_v41

    monkeypatch.setenv("VAST_TOKEN", "test-token")
    nfs_v41.init_config(SimpleNamespace(
        vms="127.0.0.1", port=vms.port, user="admin", password=None,
        sample_average=None, refresh=5, csv=None, no_color=True,
        discover_metrics=False, log_api_calls=False,
        export_openmetrics=False, openmetrics_file=None,
    ))
    nfs_v41.CLUSTER_ID, nfs_v41.CLUSTER_NAME = nfs_v41.get_current_cluster()
    nfs_v41.create_headline_monitors()
    yield nfs_v41
    nfs_v41.cleanup()
    nfs_v41._CLEANED_UP = False
    vast_common.close_connection()


def submit_path(engine, path):
    """Drive the real prompt flow: open, type, Enter."""
    engine._dispatch_key("d")
    for ch in path:
        engine._dispatch_key(ch)
    engine._dispatch_key("\r")


def render_frame(module, columns=200, lines=40):
    """Capture one composed frame from the engine's _render_frame()."""
    import os

    real_size = shutil.get_terminal_size
    buf, real_stdout = io.StringIO(), sys.stdout
    shutil.get_terminal_size = lambda fallback=(80, 24): os.terminal_size(
        (columns, lines))
    sys.stdout = buf
    try:
        module._render_frame()
    finally:
        sys.stdout = real_stdout
        shutil.get_terminal_size = real_size
    return tui_layout.strip_ansi(buf.getvalue())


@pytest.fixture
def v41(monkeypatch):
    """nfs_v41 primed for render-only tests, no VMS needed."""
    import nfs4_native
    import nfs_v41

    monkeypatch.setattr(
        nfs_v41, "NFS4", nfs4_native.Nfs4Collector(lambda *a, **k: ""))
    monkeypatch.setattr(
        nfs_v41, "HOSTVIEW", nfs4_native.HostViewCollector(lambda *a, **k: ""))
    tui_layout.set_color(False)
    nfs_v41._COLOR = False
    nfs_v41.VMS, nfs_v41.PORT = "10.0.0.1", 443
    nfs_v41.CLUSTER_NAME, nfs_v41.CLUSTER_OS = "c1", "5.5.0.1"
    nfs_v41.REFRESH_SECONDS = 5
    for name in ("DELEG_PROMPT", "DELEG_RESULT", "DELEG_STATUS"):
        monkeypatch.setattr(nfs_v41, name, None)
    monkeypatch.setattr(nfs_v41, "DRILL_MODE", None)
    monkeypatch.setattr(nfs_v41, "DRILL_STATUS", None)
    monkeypatch.setattr(nfs_v41, "EXPORTER_MODE", None)
    monkeypatch.setattr(nfs_v41, "EXPORTER_STATUS", None)
    monkeypatch.setattr(nfs_v41, "STARTUP_STATUS", None)
    return nfs_v41


def deleg_result(state, **kw):
    base = {"path": "/kmacs/nfstest/f.dat", "state": state, "records": [],
            "count": 0, "truncated": False, "tenant": "default",
            "queried_at": "12:00:00", "message": "m"}
    base.update(kw)
    return base


# ---------------------------------------------------------------------------
# PATH INPUT: the prompt owns every key while open
# ---------------------------------------------------------------------------
def test_d_opens_the_path_prompt(v41):
    assert v41._dispatch_key("d") == "refresh"
    assert v41.DELEG_PROMPT == ""


def test_typing_q_inside_the_prompt_is_path_text_not_quit(v41):
    """The main loop's quit check fires before dispatch; with the prompt open
    it must let q through as path text. Typing a path containing q (or Q)
    once quit opstat outright - the exact hazard the owner called out."""
    v41.DELEG_PROMPT = "/kmacs/"
    assert v41._should_quit("q") is False
    assert v41._should_quit("Q") is False
    v41._dispatch_key("q")
    assert v41.DELEG_PROMPT == "/kmacs/q"
    # Prompt closed again: q quits as always.
    v41.DELEG_PROMPT = None
    assert v41._should_quit("q") is True


def test_ctrl_c_always_quits_even_inside_the_prompt(v41):
    v41.DELEG_PROMPT = "/kmacs/"
    assert v41._should_quit("\x03") is True


def test_navigation_keys_inside_the_prompt_are_path_text(v41, monkeypatch):
    """d, x, v, h, c, t, o, l, n, 4 and space are all legal path characters
    while the prompt is open - none may navigate, drill, or re-sort."""
    called = []
    for fn in ("switch_drill_mode", "enter_exporter_mode", "exit_drill_mode",
               "exit_exporter_mode", "manual_refresh"):
        monkeypatch.setattr(v41, fn, lambda *a, _f=fn: called.append(_f))
    v41.DELEG_PROMPT = ""
    for ch in "dxvhctoln4 ":
        v41._dispatch_key(ch)
    assert called == []
    assert v41.DELEG_PROMPT == "dxvhctoln4 "


def test_backspace_edits_and_backspace_on_empty_cancels(v41):
    v41.DELEG_PROMPT = "/ab"
    v41._dispatch_key("\x7f")
    assert v41.DELEG_PROMPT == "/a"
    v41.DELEG_PROMPT = ""
    v41._dispatch_key("\x7f")
    assert v41.DELEG_PROMPT is None, "backspace on an empty line cancels"


def test_enter_on_empty_line_cancels_with_zero_api_calls(v41, monkeypatch):
    monkeypatch.setattr(v41, "api_request", lambda *a, **k: pytest.fail(
        "cancel must not issue any API call"))
    v41.DELEG_PROMPT = ""
    v41._dispatch_key("\r")
    assert v41.DELEG_PROMPT is None
    assert v41.DELEG_RESULT is None


def test_ctrl_u_clears_the_line(v41):
    v41.DELEG_PROMPT = "/some/typo"
    v41._dispatch_key("\x15")
    assert v41.DELEG_PROMPT == ""


def test_relative_path_is_rejected_locally_with_zero_api_calls(v41, monkeypatch):
    """V1 accepts only the full VAST namespace path (leading /). No
    prepending, no guessing - and no API call is spent finding out."""
    monkeypatch.setattr(v41, "api_request", lambda *a, **k: pytest.fail(
        "local validation must not issue any API call"))
    v41.DELEG_PROMPT = "kmacs/nfstest/f.dat"
    v41._dispatch_key("\r")
    assert v41.DELEG_RESULT["state"] == "invalid_input"
    assert "start with /" in v41.DELEG_RESULT["message"]


def test_bare_escape_never_reaches_dispatch():
    """Documented deviation from the design mockup: Esc cannot cancel the
    prompt because the terminal layer strips escape sequences before
    dispatch (arrow-key safety). Cancel is Enter/Backspace on empty."""
    assert vast_common.strip_escape_sequences("\x1b") == ""
    assert vast_common.strip_escape_sequences("\x1b[A/a") == "/a"


# ---------------------------------------------------------------------------
# TENANT RESOLUTION: the namespace's owning view supplies the tenant
# ---------------------------------------------------------------------------
def fake_view(id, path, tenant_id, tenant_name, protocols=("NFS4",)):
    return {"id": id, "path": path, "tenant_id": tenant_id,
            "tenant_name": tenant_name, "protocols": list(protocols)}


def test_exact_view_match_wins_and_forbids_fallback(v41, monkeypatch):
    """Rule A: an exact matching NFS view is authoritative - its tenant is
    queried and an ILLEGAL_PATH answer is final (no second tenant)."""
    monkeypatch.setattr(v41, "_DELEG_VIEWS", [
        fake_view(1, "/", 9, "other"),
        fake_view(755, "/kmacs/nfstest", 1, "default"),
    ])
    tenants, allow_fallback, note = v41._deleg_resolve_tenants("/kmacs/nfstest")
    assert [t[0] for t in tenants] == [1]
    assert allow_fallback is False
    assert note is None


def test_deepest_prefix_view_wins_without_fallback(v41, monkeypatch):
    """A specific (non-root) prefix view is treated as authoritative too:
    the file lives inside that view's namespace or nowhere."""
    monkeypatch.setattr(v41, "_DELEG_VIEWS", [
        fake_view(1, "/", 9, "other"),
        fake_view(2, "/kmacs", 5, "mid"),
        fake_view(755, "/kmacs/nfstest", 1, "default"),
    ])
    tenants, allow_fallback, _ = v41._deleg_resolve_tenants(
        "/kmacs/nfstest/meta_stress/dir_4/f.dat")
    assert [t[0] for t in tenants] == [1], "longest prefix must win"
    assert allow_fallback is False


def test_root_only_match_allows_one_bounded_fallback(v41, monkeypatch):
    """Rule B: when only root views match, at most ONE fallback tenant may be
    tried on ILLEGAL_PATH - never a spray across the tenant list."""
    monkeypatch.setattr(v41, "_DELEG_VIEWS", [
        fake_view(1, "/", 1, "default"),
        fake_view(2, "/", 2, "second"),
    ])
    tenants, allow_fallback, _ = v41._deleg_resolve_tenants("/elsewhere/f")
    assert [t[0] for t in tenants] == [1, 2]
    assert allow_fallback is True


def test_ambiguous_ownership_stops_honestly(v41, monkeypatch):
    """More distinct candidate tenants than the cap is honest ambiguity:
    no query is made at all rather than guessing."""
    monkeypatch.setattr(v41, "_DELEG_VIEWS", [
        fake_view(i, "/", i, f"t{i}") for i in range(1, 6)
    ])
    tenants, allow_fallback, note = v41._deleg_resolve_tenants("/x/f")
    assert tenants == []
    assert "ambiguous" in note


def test_non_nfs_views_never_own_a_delegation_namespace(v41, monkeypatch):
    monkeypatch.setattr(v41, "_DELEG_VIEWS", [
        fake_view(1, "/kmacs/nfstest", 7, "smbonly", protocols=("SMB",)),
    ])
    tenants, _, note = v41._deleg_resolve_tenants("/kmacs/nfstest/f")
    assert tenants == []
    assert "no NFS-capable view" in note


def test_candidate_view_ordering_exact_then_longest_prefix():
    views = [
        fake_view(1, "/", 1, "a"),
        fake_view(2, "/kmacs", 2, "b"),
        fake_view(3, "/kmacs/nfstest", 3, "c"),
    ]
    cands = vast_drill.namespace_candidate_views(views, "/kmacs/nfstest")
    assert [(v["id"], kind) for v, kind in cands] == [
        (3, "exact"), (2, "prefix"), (1, "prefix")]


def test_rule_a_illegal_path_is_final_after_exactly_one_get(v41, monkeypatch):
    calls = []

    def fake_api(method, path, payload=None):
        calls.append((method, path))
        raise RuntimeError("GET url failed: HTTP 400: get_handle_by_path "
                           "returned an error : "
                           "GetHandleByPathCode.ILLEGAL_PATH")

    monkeypatch.setattr(v41, "_DELEG_VIEWS", [
        fake_view(755, "/kmacs/nfstest", 1, "default"),
        fake_view(1, "/", 2, "second"),
    ])
    monkeypatch.setattr(v41, "api_request", fake_api)
    v41._deleg_query("/kmacs/nfstest/gone.dat")
    assert v41.DELEG_RESULT["state"] == "invalid"
    assert len(calls) == 1, "exact-view ILLEGAL_PATH must not fall back"


def test_rule_b_fallback_is_bounded_to_one_extra_tenant(v41, monkeypatch):
    calls = []

    def fake_api(method, path, payload=None):
        calls.append((method, path))
        if "/tenants/1/" in path:
            raise RuntimeError("GET url failed: HTTP 400: get_handle_by_path"
                               " returned an error : "
                               "GetHandleByPathCode.ILLEGAL_PATH")
        return {"delegate_info": [proven_record()],
                "delegate_info_count_total": 1,
                "xeystore_pagination": False,
                "xeystore_pagination_next_client_id": 1}

    monkeypatch.setattr(v41, "_DELEG_VIEWS", [
        fake_view(1, "/", 1, "default"),
        fake_view(2, "/", 2, "second"),
    ])
    monkeypatch.setattr(v41, "api_request", fake_api)
    v41._deleg_query("/somewhere/f.dat")
    assert v41.DELEG_RESULT["state"] == "live"
    assert v41.DELEG_RESULT["tenant"] == "second", (
        "the tenant that answered must be recorded")
    assert len(calls) == 2, "exactly one bounded fallback"


# ---------------------------------------------------------------------------
# RESULT STATES against the wire-faithful mock
# ---------------------------------------------------------------------------
def test_live_delegations_render_the_proven_fields(engine41, vms):
    vms.state.deleg_records["/view/300/f.dat"] = [
        proven_record(0), proven_record(1, revoke=True)]
    vms.state.deleg_valid_paths.add("/view/300/f.dat")
    submit_path(engine41, "/view/300/f.dat")
    r = engine41.DELEG_RESULT
    assert r["state"] == "live"
    assert r["count"] == 2 and len(r["records"]) == 2
    rec = r["records"][0]
    assert rec["delegation_type"] == "WRITE"
    assert rec["delegation_client_ip"] == "172.200.204.50"
    assert rec["vip_addr"] == "172.200.204.6"
    assert rec["revoke_in_progress"] is False
    assert rec["client_id"] == 4000 and rec["delegation_stateid"] == 7000


def test_valid_path_with_no_delegation_is_empty_not_error(engine41, vms):
    """delegate_info: [] on a valid path means no client holds a delegation -
    information, never an error and never conflated with invalid."""
    submit_path(engine41, "/view/301")
    assert engine41.DELEG_RESULT["state"] == "empty"
    assert engine41.DELEG_RESULT["count"] == 0


def test_nonexistent_path_is_invalid_via_illegal_path(engine41, vms):
    """A path under a real view that does not exist on the cluster comes
    back as the literal ILLEGAL_PATH 400 and renders as invalid."""
    submit_path(engine41, "/view/300/no-such.dat")
    assert engine41.DELEG_RESULT["state"] == "invalid"


def test_path_outside_every_view_never_reaches_the_wire(engine41, vms):
    """No NFS-capable view owns the path: the diagnostic reports that
    honestly without spending a delegation query on it."""
    vms.reset_calls()
    submit_path(engine41, "/no/such/path.dat")
    assert engine41.DELEG_RESULT["state"] == "ambiguous"
    delegs = [p for _t, _m, p, _s in vms.calls() if "nfs4_deleg" in p]
    assert delegs == []


def test_missing_endpoint_is_an_unavailable_state(engine41, vms):
    vms.state.delegations_enabled = False
    submit_path(engine41, "/view/300")
    assert engine41.DELEG_RESULT["state"] == "unavailable"


def test_malformed_response_is_reported_not_rendered(v41, monkeypatch):
    monkeypatch.setattr(v41, "_DELEG_VIEWS",
                        [fake_view(755, "/kmacs/nfstest", 1, "default")])
    monkeypatch.setattr(v41, "api_request",
                        lambda *a, **k: {"unexpected": True})
    v41._deleg_query("/kmacs/nfstest/f")
    assert v41.DELEG_RESULT["state"] == "malformed"


def test_result_rows_are_bounded_with_an_honest_truncation_notice(engine41, vms):
    vms.state.deleg_records["/view/302/big"] = [proven_record(i)
                                                for i in range(12)]
    submit_path(engine41, "/view/302/big")
    r = engine41.DELEG_RESULT
    assert len(r["records"]) == engine41._DELEG_MAX_ROWS
    assert r["count"] == 12
    assert r["truncated"] is True


def test_server_side_pagination_flag_marks_truncation(engine41, vms):
    vms.state.deleg_records["/view/303/f"] = [proven_record()]
    vms.state.deleg_pagination = True
    submit_path(engine41, "/view/303/f")
    assert engine41.DELEG_RESULT["truncated"] is True


def test_record_missing_a_field_renders_dash_not_invention(v41, monkeypatch):
    """Evidence-gating: a field the cluster did not send renders '-';
    nothing is fabricated to make the record look complete."""
    partial = {"delegation_type": "WRITE", "client_id": 1}
    monkeypatch.setattr(v41, "_DELEG_VIEWS",
                        [fake_view(755, "/k", 1, "default")])
    monkeypatch.setattr(v41, "api_request", lambda *a, **k: {
        "delegate_info": [partial], "delegate_info_count_total": 1,
        "xeystore_pagination": False})
    v41._deleg_query("/k/f")
    rec = v41.DELEG_RESULT["records"][0]
    assert rec["delegation_client_ip"] == "-"
    assert rec["vip_addr"] == "-"
    assert rec["revoke_in_progress"] is None


# ---------------------------------------------------------------------------
# SAFETY: GET-only by construction (D-008)
# ---------------------------------------------------------------------------
def test_lookup_helper_has_no_method_parameter():
    """_deleg_lookup_get cannot be asked to DELETE: the HTTP method is not a
    parameter. The nfs4_delegs DELETE sibling revokes live delegations."""
    import nfs_v41

    params = list(inspect.signature(nfs_v41._deleg_lookup_get).parameters)
    assert params == ["tenant_id", "file_path"]
    src = inspect.getsource(nfs_v41._deleg_lookup_get)
    assert '"GET"' in src and "DELETE" not in src.replace(
        "DELETE sibling", "")


def test_full_lookup_flow_never_issues_a_non_get(engine41, vms):
    vms.state.deleg_records["/view/300/f"] = [proven_record()]
    vms.reset_calls()
    submit_path(engine41, "/view/300/f")
    engine41._dispatch_key(" ")     # manual re-query
    non_gets = [(m, p) for _t, m, p, _s in vms.calls()
                if "nfs4_deleg" in p and m != "GET"]
    assert non_gets == []
    deletes = [(m, p) for _t, m, p, _s in vms.calls() if m == "DELETE"]
    assert deletes == []


def test_file_path_is_url_encoded(v41, monkeypatch):
    seen = []
    monkeypatch.setattr(v41, "api_request", lambda m, p, payload=None: (
        seen.append((m, p)) or {"delegate_info": [],
                                "delegate_info_count_total": 0}))
    monkeypatch.setattr(v41, "_DELEG_VIEWS",
                        [fake_view(755, "/k", 1, "default")])
    v41._deleg_query("/k/dir with space/f&g.dat")
    method, path = seen[0]
    assert method == "GET"
    assert "file_path=%2Fk%2Fdir%20with%20space%2Ff%26g.dat" in path


# ---------------------------------------------------------------------------
# API COST: zero on the refresh path, bounded per lookup
# ---------------------------------------------------------------------------
def test_normal_refresh_path_makes_zero_delegation_calls(engine41, vms):
    engine41.poll_tick()
    engine41.manual_refresh()
    engine41.poll_tick()
    delegs = [p for _t, _m, p, _s in vms.calls() if "nfs4_deleg" in p]
    views = [p for _t, _m, p, _s in vms.calls() if p.endswith("/views/")]
    assert delegs == [], "refresh path must never touch the delegation API"
    assert views == [], "refresh path must not fetch the view inventory"


def test_one_lookup_costs_at_most_views_plus_two_gets(engine41, vms):
    vms.reset_calls()
    submit_path(engine41, "/view/300")
    calls = [(m, p) for _t, m, p, _s in vms.calls()]
    delegs = [p for m, p in calls if "nfs4_deleg" in p]
    views = [p for m, p in calls if p.endswith("/views/")]
    assert len(delegs) <= 2
    assert len(views) <= 1
    assert len(calls) <= 3


def test_view_inventory_is_cached_across_lookups(engine41, vms):
    submit_path(engine41, "/view/300")
    vms.reset_calls()
    submit_path(engine41, "/view/301")
    views = [p for _t, _m, p, _s in vms.calls() if p.endswith("/views/")]
    assert views == [], "second lookup must reuse the cached view inventory"


def test_space_on_a_result_requeries_the_same_path_only(engine41, vms):
    submit_path(engine41, "/view/300")
    vms.reset_calls()
    engine41._dispatch_key(" ")
    calls = [(m, p) for _t, m, p, _s in vms.calls()]
    assert calls, "space on a result must re-query"
    assert all("nfs4_deleg" in p for _m, p in calls), (
        f"re-query must touch only the delegation endpoint, got {calls}")
    assert "file_path=%2Fview%2F300" in calls[0][1]


def test_space_without_a_result_is_the_normal_manual_refresh(engine41, vms):
    assert engine41.DELEG_RESULT is None
    vms.reset_calls()
    engine41._dispatch_key(" ")
    delegs = [p for _t, _m, p, _s in vms.calls() if "nfs4_deleg" in p]
    assert delegs == []
    assert vms.calls(), "manual refresh must still poll the monitors"


def test_no_timed_delegation_refresh_exists(engine41, vms):
    """Owner decision: [space] is the only re-query. A result sitting on
    screen across poll ticks must not poll the delegation endpoint."""
    submit_path(engine41, "/view/300")
    vms.reset_calls()
    for _ in range(3):
        engine41.poll_tick()
    delegs = [p for _t, _m, p, _s in vms.calls() if "nfs4_deleg" in p]
    assert delegs == []


def test_lookup_leaves_no_temporary_monitor(engine41, vms):
    before = set(vms.live_monitors())
    submit_path(engine41, "/view/300")
    assert set(vms.live_monitors()) == before


# ---------------------------------------------------------------------------
# RENDERING: footer survives every delegation state at every width
# ---------------------------------------------------------------------------
DELEG_SCREENS = [
    ("prompt", {"DELEG_PROMPT": "/kmacs/nfste"}),
    ("status", {"DELEG_STATUS": "Looking up NFSv4.1 delegations..."}),
    ("live", {"DELEG_RESULT": deleg_result(
        "live", records=[{
            "delegation_type": "WRITE",
            "delegation_client_ip": "172.200.204.50",
            "vip_addr": "172.200.204.6", "revoke_in_progress": False,
            "client_id": 4000, "delegation_stateid": 7000}],
        count=1)}),
    ("empty", {"DELEG_RESULT": deleg_result("empty")}),
    ("invalid", {"DELEG_RESULT": deleg_result(
        "invalid", message="Path was not found in the selected tenant "
        "namespace.")}),
    ("invalid_input", {"DELEG_RESULT": deleg_result(
        "invalid_input", message="The path must be the full path as the "
        "cluster exports it and start with /.")}),
    ("unavailable", {"DELEG_RESULT": deleg_result(
        "unavailable", message="Delegation lookup is not available from "
        "this cluster.")}),
    ("ambiguous", {"DELEG_RESULT": deleg_result(
        "ambiguous", message="namespace ownership is ambiguous across "
        "tenants")}),
    ("error", {"DELEG_RESULT": deleg_result("error", message="boom")}),
    ("malformed", {"DELEG_RESULT": deleg_result(
        "malformed", message="Unrecognized response; raw details are in "
        "the API log.")}),
]


@pytest.mark.parametrize("width", [200, 140, 120, 80, 60, 40, 24, 10])
@pytest.mark.parametrize("name,state", DELEG_SCREENS, ids=[s[0] for s in DELEG_SCREENS])
def test_footer_survives_every_delegation_state(v41, monkeypatch, name, state, width):
    """Every delegation state, at every width the navigation suite treats as
    required - including 24 (the _MIN_FRAME_WIDTH clamp) and 10, where the
    25-character panel title itself must truncate rather than overflow."""
    for attr, value in state.items():
        monkeypatch.setattr(v41, attr, value)
    frame = render_frame(v41, columns=width)
    marker = "[q] Quit" if width >= 40 else "[q]"
    assert marker in frame, f"footer lost in deleg state {name} at {width}"
    budget = max(width, v41._MIN_FRAME_WIDTH)
    for line in frame.splitlines():
        assert tui_layout.display_width(line) <= budget, (
            f"frame exceeds terminal width in state {name} at {width}: "
            f"{line!r}")


def test_delegation_title_tag_is_shown(v41, monkeypatch):
    monkeypatch.setattr(v41, "DELEG_RESULT", deleg_result("empty"))
    assert "| DELEGATION" in render_frame(v41)


def test_live_screen_shows_primary_fields_and_dim_id_line(v41, monkeypatch):
    monkeypatch.setattr(v41, "DELEG_RESULT", DELEG_SCREENS[2][1]["DELEG_RESULT"])
    frame = render_frame(v41)
    assert "WRITE" in frame
    assert "172.200.204.50" in frame
    assert "Serving VIP" in frame and "172.200.204.6" in frame
    assert "Revoke in progress" in frame and "No" in frame
    # IDs are present, demoted to one secondary line (owner decision 5).
    assert "client_id 4000" in frame and "stateid 7000" in frame


def test_empty_screen_is_honest_information_not_a_dash(v41, monkeypatch):
    monkeypatch.setattr(v41, "DELEG_RESULT", deleg_result("empty"))
    frame = render_frame(v41)
    assert "No active NFSv4.1 delegation exists for this path." in frame
    assert "path is valid" in frame


def test_truncated_screen_names_the_shown_and_total_counts(v41, monkeypatch):
    result = deleg_result("live", records=[
        DELEG_SCREENS[2][1]["DELEG_RESULT"]["records"][0]] * 8,
        count=12, truncated=True)
    monkeypatch.setattr(v41, "DELEG_RESULT", result)
    frame = render_frame(v41)
    assert "8 of 12 delegations shown" in frame


def test_prompt_screen_explains_the_path_convention(v41, monkeypatch):
    monkeypatch.setattr(v41, "DELEG_PROMPT", "/km")
    frame = render_frame(v41)
    assert "full path" in frame and "starts with /" in frame
    assert "> /km" in frame


def test_footer_legend_advertises_the_d_key(v41):
    frame = render_frame(v41)
    assert "[d] Delegation" in frame


# ---------------------------------------------------------------------------
# REGRESSION: mode exits and interactions with other modes
# ---------------------------------------------------------------------------
def test_x_exits_the_result_screen(v41, monkeypatch):
    monkeypatch.setattr(v41, "DELEG_RESULT", deleg_result("empty"))
    monkeypatch.setattr(v41, "exit_drill_mode", lambda: None)
    monkeypatch.setattr(v41, "exit_exporter_mode", lambda: None)
    v41._dispatch_key("x")
    assert v41.DELEG_RESULT is None


def test_navigating_to_a_drill_dismisses_the_result(v41, monkeypatch):
    monkeypatch.setattr(v41, "DELEG_RESULT", deleg_result("empty"))
    monkeypatch.setattr(v41, "switch_drill_mode", lambda mode: None)
    v41._dispatch_key("c")
    assert v41.DELEG_RESULT is None


def test_delegation_panel_takes_render_precedence_over_exporter(v41, monkeypatch):
    monkeypatch.setattr(v41, "DELEG_PROMPT", "/k")
    monkeypatch.setattr(v41, "EXPORTER_MODE", "native")
    frame = render_frame(v41)
    assert "DELEGATION LOOKUP" in frame


# ---------------------------------------------------------------------------
# REVIEW FIXES (2026-08-18 specialist review pass): the wire key path, the
# buffered-read quit hazard, and cost bounds the first matrix only proved
# via monkeypatch.
# ---------------------------------------------------------------------------
def test_wire_key_path_preserves_path_case(v41, monkeypatch):
    """dispatch_queued_keys used to iterate chars.lower(), silently
    rewriting a case-sensitive VAST namespace path before lookup - the
    prompt tests bypassed that layer by calling _dispatch_key directly.
    This drives the REAL delivery path with mixed case."""
    seen = []
    monkeypatch.setattr(v41, "api_request", lambda m, p, payload=None: (
        seen.append(p) or {"delegate_info": [],
                           "delegate_info_count_total": 0}))
    monkeypatch.setattr(v41, "_DELEG_VIEWS",
                        [fake_view(755, "/Kmacs", 1, "default")])
    monkeypatch.setattr(v41, "render_screen", lambda: None)
    vast_drill.dispatch_queued_keys("d", v41._dispatch_key, lambda: None)
    vast_drill.dispatch_queued_keys(
        "/Kmacs/File.DAT\r", v41._dispatch_key, lambda: None)
    assert seen, "the lookup never fired through the wire key path"
    assert "file_path=%2FKmacs%2FFile.DAT" in seen[0], (
        "path case was not preserved end-to-end: %r" % seen[0])


def test_wire_key_path_mixed_case_in_one_buffered_read(v41, monkeypatch):
    """d and the path can arrive in ONE buffered read on a slow cluster;
    characters after the d that opens the prompt must stay raw."""
    seen = []
    monkeypatch.setattr(v41, "api_request", lambda m, p, payload=None: (
        seen.append(p) or {"delegate_info": [],
                           "delegate_info_count_total": 0}))
    monkeypatch.setattr(v41, "_DELEG_VIEWS",
                        [fake_view(755, "/Kmacs", 1, "default")])
    monkeypatch.setattr(v41, "render_screen", lambda: None)
    vast_drill.dispatch_queued_keys(
        "d/Kmacs/Q.dat\r", v41._dispatch_key, lambda: None)
    assert seen and "file_path=%2FKmacs%2FQ.dat" in seen[0]


def test_uppercase_command_keys_still_work(v41):
    """Commands stay case-insensitive: engines fold case in the command
    branch now that dispatch delivers raw keys."""
    assert v41.DELEG_PROMPT is None
    v41._dispatch_key("D")
    assert v41.DELEG_PROMPT == "", "an uppercase D must still open the prompt"


def test_buffered_d_then_q_in_one_read_does_not_quit(v41):
    """_should_quit simulates prompt state across the buffer: a q typed
    after the d that opens the prompt is path text even though the prompt
    was closed when the read returned - the owner-called-out hazard in its
    buffered form."""
    assert v41.DELEG_PROMPT is None
    assert v41._should_quit("d/path/q") is False
    assert v41._should_quit("d/path/q\rq") is True, (
        "after Enter closes the prompt a later q quits again")
    assert v41._should_quit("d\x7fq") is True, (
        "backspace on the empty prompt cancels it, so the q quits")
    assert v41._should_quit("d/a\x7f\x7f\x7fq") is True, (
        "backspacing through the text and then the empty line cancels")
    assert v41._should_quit("\x03") is True
    assert v41._should_quit("d\x03") is True, "Ctrl-C quits even mid-prompt"
    assert v41._should_quit("xq") is True


def test_rule_b_wire_cost_is_exactly_two_gets(engine41, vms):
    """Rule B over the real wire, not a monkeypatch: two root views owned by
    distinct tenants, a path no view owns exactly -> exactly one bounded
    fallback (2 delegation GETs), then the honest invalid state naming the
    tenants tried."""
    vms.state.extra_views = [
        {"id": 9001, "path": "/", "name": "root-a", "title": "root-a",
         "tenant_id": 1, "tenant_name": "default",
         "protocols": ["NFS", "NFS4"]},
        {"id": 9002, "path": "/", "name": "root-b", "title": "root-b",
         "tenant_id": 2, "tenant_name": "second",
         "protocols": ["NFS", "NFS4"]},
    ]
    vms.reset_calls()
    submit_path(engine41, "/outside/every/view.dat")
    r = engine41.DELEG_RESULT
    assert r["state"] == "invalid"
    assert "default" in r["message"] and "second" in r["message"], (
        "the invalid message must name the tenants actually queried")
    delegs = [p for _t, _m, p, _s in vms.calls() if "nfs4_deleg" in p]
    assert len(delegs) == 2, (
        "root-only match must try exactly one bounded fallback, got %r"
        % delegs)


def test_space_requery_is_bounded_to_a_single_get(engine41, vms):
    submit_path(engine41, "/view/300")
    vms.reset_calls()
    engine41._dispatch_key(" ")
    assert len(vms.calls()) <= 2, (
        "space re-query must stay bounded, got %d calls" % len(vms.calls()))


def test_submit_paints_a_loading_frame_before_the_lookup(v41, monkeypatch):
    """with_loading_status ordering for the prompt-submit path: status set,
    frame rendered, THEN the blocking work, status cleared - the ordering
    tests/test_drill_loading.py pins for every other drill."""
    events = []
    monkeypatch.setattr(v41, "render_screen",
                        lambda: events.append("render"))
    monkeypatch.setattr(v41, "_set_deleg_status",
                        lambda t: events.append(("status", t)))
    monkeypatch.setattr(v41, "_deleg_query",
                        lambda path: events.append(("work", path)))
    v41.DELEG_PROMPT = "/k/f"
    v41._dispatch_key("\r")
    assert events == [
        ("status", vast_drill.LOADING_MESSAGES["delegation"]),
        "render", ("work", "/k/f"), ("status", None)]


def test_space_requery_paints_a_loading_frame_first(v41, monkeypatch):
    events = []
    monkeypatch.setattr(v41, "render_screen",
                        lambda: events.append("render"))
    monkeypatch.setattr(v41, "_set_deleg_status",
                        lambda t: events.append(("status", t)))
    monkeypatch.setattr(v41, "_deleg_query",
                        lambda path: events.append(("work", path)))
    monkeypatch.setattr(v41, "DELEG_RESULT", deleg_result("empty"))
    v41._dispatch_key(" ")
    assert events[:4] == [
        ("status", vast_drill.LOADING_MESSAGES["delegation"]),
        "render", ("work", "/kmacs/nfstest/f.dat"), ("status", None)]
    assert events[4:] == ["render"], "the result repaints after the work"


def test_truncation_notice_no_longer_claims_an_unfetched_remainder(v41, monkeypatch):
    """Display-bounding 12 fetched records to 8 is a display fact, not a
    cluster fact: the old notice said the cluster held more than the
    diagnostic fetched, which was false - all 12 were in the response."""
    rec = DELEG_SCREENS[2][1]["DELEG_RESULT"]["records"][0]
    monkeypatch.setattr(v41, "DELEG_RESULT", deleg_result(
        "live", records=[rec] * 8, count=12, fetched=12, truncated=True,
        count_native=True))
    frame = render_frame(v41)
    assert "8 of 12 delegations shown" in frame
    assert "diagnostic fetches" not in frame
    assert "more than this response carried" not in frame, (
        "all 12 records were fetched; no server-side remainder exists")


def test_pagination_flag_is_named_not_interpreted(v41, monkeypatch):
    """xeystore_pagination=true has never been observed on a real cluster;
    the notice names the flag rather than asserting what it means."""
    rec = DELEG_SCREENS[2][1]["DELEG_RESULT"]["records"][0]
    monkeypatch.setattr(v41, "DELEG_RESULT", deleg_result(
        "live", records=[rec], count=1, fetched=1, truncated=True,
        count_native=True, pagination=True))
    frame = render_frame(v41)
    assert "response marked xeystore_pagination" in frame
    assert "1 of 1" not in frame, (
        "a self-contradictory '1 of 1 shown, more exist' must not render")


def test_derived_count_is_not_presented_as_reported(v41, monkeypatch):
    rec = DELEG_SCREENS[2][1]["DELEG_RESULT"]["records"][0]
    monkeypatch.setattr(v41, "DELEG_RESULT", deleg_result(
        "live", records=[rec], count=1, fetched=1, truncated=False,
        count_native=False))
    frame = render_frame(v41)
    assert "1 delegation record(s) returned" in frame
    assert "delegation(s) reported" not in frame


def test_empty_state_always_carries_the_directory_caveat(v41, monkeypatch):
    """The var204 capture proved a directory answers empty while files
    inside it hold live delegations, and the capture's directory path had
    NO trailing slash - so the caveat cannot key on path syntax."""
    monkeypatch.setattr(v41, "DELEG_RESULT", deleg_result(
        "empty", path="/kmacs/nfstest/nfs41_loadgen"))
    frame = render_frame(v41)
    assert "If this path is a directory" in frame
    assert "query the file itself" in frame


def test_long_paths_keep_their_tail_visible(v41, monkeypatch):
    long_path = "/kmacs/nfstest/" + "deep/" * 30 + "distinctive-tail.dat"
    monkeypatch.setattr(v41, "DELEG_PROMPT", long_path)
    frame = render_frame(v41, columns=80)
    assert "distinctive-tail.dat" in frame, (
        "the prompt must show the tail - the user types at the end")
    monkeypatch.setattr(v41, "DELEG_PROMPT", None)
    monkeypatch.setattr(v41, "DELEG_RESULT",
                        deleg_result("empty", path=long_path))
    frame = render_frame(v41, columns=80)
    assert "distinctive-tail.dat" in frame, (
        "the result must show the tail - siblings differ at the end")
