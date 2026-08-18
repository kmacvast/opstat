"""FR2 delegation discovery probe (read-only lab tooling).

The nfs4_delegs family has a DELETE sibling that revokes live delegations on
a running cluster (D-008: never invoked, under any circumstances). These
tests pin the probe's structural safety - its transport refuses every
non-GET method - and its evidence semantics: the required-file_path
contract, wrapper parsing, real-field capture, and honest empty results.
"""

import importlib.util
import json

import pytest


def _load_probe():
    spec = importlib.util.spec_from_file_location(
        "probe_fr2_delegations",
        "scripts/var203_validation/probe_fr2_delegations.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Structural GET-only enforcement
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("method", ["DELETE", "POST", "PUT", "PATCH"])
def test_transport_refuses_every_mutating_method(method, monkeypatch):
    """Issuing the DELETE sibling (or any write) must be impossible by
    construction, not merely absent by review."""
    probe = _load_probe()
    calls = []
    monkeypatch.setattr(probe.vast_common, "request",
                        lambda *a, **k: calls.append(a))
    with pytest.raises(RuntimeError, match="GET-only"):
        probe.get_only(method, "/tenants/1/nfs4_delegs/")
    assert calls == [], "the mutating request reached the transport"


def test_transport_passes_get_through(monkeypatch):
    probe = _load_probe()
    seen = []
    monkeypatch.setattr(probe.vast_common, "request",
                        lambda m, p, payload=None: seen.append((m, p)) or {})
    probe.get_only("GET", "/tenants/1/nfs4_delegs/?file_path=%2Fa")
    assert seen == [("GET", "/tenants/1/nfs4_delegs/?file_path=%2Fa")]


# ---------------------------------------------------------------------------
# Endpoint path building and response parsing
# ---------------------------------------------------------------------------
def test_deleg_path_encodes_file_path_exactly():
    probe = _load_probe()
    assert probe.deleg_path(7) == "/tenants/7/nfs4_delegs/"
    assert probe.deleg_path(7, "/kmacs/nfstest/a b.bin") == (
        "/tenants/7/nfs4_delegs/?file_path=%2Fkmacs%2Fnfstest%2Fa%20b.bin")


def test_summarize_response_reads_the_d008_wrapper():
    """The wrapper keys are the part D-008 proved on a real cluster:
    delegate_info / delegate_info_count_total plus pagination keys."""
    probe = _load_probe()
    payload = {
        "delegate_info": [{"client_ip": "10.0.0.1", "deleg_type": "READ"}],
        "delegate_info_count_total": 1,
        "xeystore_pagination": None,
        "xeystore_pagination_next_client_id": None,
    }
    records, count, extra = probe.summarize_response(payload)
    assert len(records) == 1 and count == 1
    assert extra == ["xeystore_pagination", "xeystore_pagination_next_client_id"]


def test_summarize_response_handles_empty_and_malformed():
    probe = _load_probe()
    assert probe.summarize_response(
        {"delegate_info": [], "delegate_info_count_total": 0}) == ([], 0, [])
    assert probe.summarize_response(None) == ([], None, [])
    assert probe.summarize_response("<html>") == ([], None, [])


def test_record_fields_captures_real_names_and_types():
    """Field capture is the whole point: the mock's record shape is modeled,
    not evidence, so the probe must report exactly what the cluster sends."""
    probe = _load_probe()
    fields = probe.record_fields([
        {"client_ip": "10.0.0.1", "expiry": 30, "odd": None},
        {"client_ip": "10.0.0.2", "state": "GRANTED"},
    ])
    assert fields["client_ip"] == "str"
    assert fields["expiry"] == "int"
    assert fields["state"] == "str"
    assert "odd" in fields


# ---------------------------------------------------------------------------
# probe_one behavior: availability contract, evidence, honest failures
# ---------------------------------------------------------------------------
def test_probe_one_reads_required_field_error_as_availability(tmp_path, monkeypatch):
    probe = _load_probe()
    probe.EVIDENCE_DIR = str(tmp_path)
    monkeypatch.setattr(probe.vast_common, "request", lambda *a, **k: (
        (_ for _ in ()).throw(RuntimeError(
            "GET .../nfs4_delegs/ failed: HTTP 400: "
            "{\"detail\":\"['__root__->file_path: field required']\"}"))))
    out = probe.probe_one("availability", 1, "default", None)
    assert out is None
    assert any("deleg.availability PASS" in l for l in probe.SUMMARY), (
        "the required-file_path 400 IS the availability proof")


def test_probe_one_saves_raw_evidence_and_reports_fields(tmp_path, monkeypatch):
    probe = _load_probe()
    probe.EVIDENCE_DIR = str(tmp_path)
    payload = {"delegate_info": [{"client_ip": "10.0.0.9", "deleg_type": "READ"}],
               "delegate_info_count_total": 1}
    monkeypatch.setattr(probe.vast_common, "request", lambda *a, **k: payload)
    probe.probe_one("file0", 1, "default", "/kmacs/nfstest/fio_iops.bin")
    saved = json.loads((tmp_path / "deleg-file0-t1.json").read_text())
    assert saved == payload, "raw response must be preserved verbatim"
    assert probe.FIELDS_SEEN.get("client_ip") == "str"
    assert any("deleg.file0 PASS" in l for l in probe.SUMMARY)


def test_probe_one_reports_an_empty_result_honestly(tmp_path, monkeypatch):
    """No delegations on the queried file is a VALID result, not a failure -
    the shape evidence still lands and nothing is invented."""
    probe = _load_probe()
    probe.EVIDENCE_DIR = str(tmp_path)
    monkeypatch.setattr(probe.vast_common, "request", lambda *a, **k: {
        "delegate_info": [], "delegate_info_count_total": 0})
    probe.probe_one("file0", 1, "default", "/kmacs/nfstest/idle.bin")
    line = [l for l in probe.SUMMARY if "deleg.file0" in l][0]
    assert "PASS" in line and "0 record(s)" in line
    assert probe.FIELDS_SEEN == {}, "no fields may be invented from emptiness"


# ---------------------------------------------------------------------------
# Pass-2 targeting: derive mount -> view -> tenant, never tenant-list order.
# Pass 1 queried tenants 37/25/51 by API order and every path returned
# GetHandleByPathCode.ILLEGAL_PATH - wrong tenants, not a broken endpoint.
# ---------------------------------------------------------------------------
def test_server_path_for_maps_client_paths_exactly():
    probe = _load_probe()
    assert probe.server_path_for(
        "/mnt/nfs41test/nfs41_loadgen/fio_iops.bin",
        "/mnt/nfs41test", "/kmacs/nfstest") == (
        "/kmacs/nfstest/nfs41_loadgen/fio_iops.bin")
    assert probe.server_path_for("/mnt/nfs41test", "/mnt/nfs41test",
                                 "/kmacs/nfstest") == "/kmacs/nfstest"
    assert probe.server_path_for("/mnt/other/file", "/mnt/nfs41test",
                                 "/kmacs/nfstest") is None, (
        "paths outside the mountpoint must never be guessed into the export")


_VIEWS = [
    {"id": 1, "path": "/", "protocols": ["NFS", "NFS4"],
     "tenant_id": 1, "tenant_name": "default"},
    {"id": 217, "path": "/", "protocols": ["NFS"],
     "tenant_id": 9, "tenant_name": "other"},
    {"id": 424, "path": "/kmacs/block", "protocols": ["BLOCK"],
     "tenant_id": 1, "tenant_name": "default"},
    {"id": 500, "path": "/kmacs/nfstest", "protocols": ["NFS4"],
     "tenant_id": 2, "tenant_name": "nfs-tenant"},
]


def test_candidate_views_exact_match_beats_root_prefix():
    probe = _load_probe()
    cands = probe.candidate_views(_VIEWS, "/kmacs/nfstest")
    assert (cands[0][0]["id"], cands[0][1]) == (500, "exact")
    # root views trail as prefix matches; the BLOCK view never qualifies
    ids = [v["id"] for v, _k in cands]
    assert 424 not in ids
    assert set(ids) >= {1, 217}


def test_candidate_views_fall_back_to_the_root_view():
    """The FR1 lesson: with no exact view, NFS mounts traverse the root
    view - the root must be a candidate, not a dead end."""
    probe = _load_probe()
    cands = probe.candidate_views(_VIEWS, "/kmacs/nfstest/nfs41_loadgen/a.bin")
    kinds = {v["id"]: k for v, k in cands}
    assert kinds[500] == "prefix", "longest prefix view must qualify"
    assert cands[0][0]["id"] == 500, "longest prefix ordered first"
    assert kinds[1] == "prefix" and kinds[217] == "prefix"


def test_candidate_tenants_dedup_order_and_ambiguity_cap():
    probe = _load_probe()
    cands = probe.candidate_views(_VIEWS, "/kmacs/nfstest/nfs41_loadgen/a.bin")
    tenants, ambiguous = probe.candidate_tenants(cands)
    assert [t[0] for t in tenants] == [2, 1, 9], (
        "derived order: exact/longest-prefix tenant first")
    assert ambiguous is False
    many = [({"id": i, "path": "/", "tenant_id": i, "tenant_name": i}, "prefix")
            for i in range(6)]
    capped, ambiguous = probe.candidate_tenants(many)
    assert len(capped) == 3 and ambiguous is True, (
        "more than the cap of distinct tenants must be reported as ambiguity")


def test_path_representations_are_derived_bounded_and_deduped():
    probe = _load_probe()
    reps = probe.path_representations(
        "/kmacs/nfstest/nfs41_loadgen/a.bin", "/kmacs/nfstest", "/kmacs/nfstest")
    assert reps == ["/kmacs/nfstest/nfs41_loadgen/a.bin",
                    "/nfs41_loadgen/a.bin"], "view==export dedups to two"
    # root view: only the namespace path remains
    assert probe.path_representations(
        "/kmacs/nfstest/a.bin", "/", "/kmacs/nfstest") == [
        "/kmacs/nfstest/a.bin", "/a.bin"]
    assert len(probe.path_representations("/a/b/c", "/a", "/a/b")) <= 3


def test_probe_one_records_illegal_path_verbatim(tmp_path, monkeypatch):
    """The pass-1 failure shape must be preserved exactly - ILLEGAL_PATH is
    evidence of wrong targeting, and hiding or truncating it away cost the
    first run its diagnosis."""
    probe = _load_probe()
    probe.EVIDENCE_DIR = str(tmp_path)
    msg = ("GET .../nfs4_delegs/?file_path=%2Fx failed: HTTP 400: "
           "{\"detail\":\"get_handle_by_path returned an error : "
           "GetHandleByPathCode.ILLEGAL_PATH\"}")
    monkeypatch.setattr(probe.vast_common, "request",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError(msg)))
    out = probe.probe_one("try0", 1, "default", "/x")
    assert out is None
    line = [l for l in probe.SUMMARY if "deleg.try0" in l][0]
    assert "FAIL" in line and "ILLEGAL_PATH" in line
    assert "ILLEGAL_PATH" in (tmp_path / "deleg-try0-t1.txt").read_text()


# ---------------------------------------------------------------------------
# Script-to-probe CLI contract. The second lab trip died at argparse: the
# committed script passed --evidence-dir to a probe parser that no longer
# accepted it, and nothing in the gate ran the two against each other. This
# test parses the ACTUAL invocation out of the committed shell script and
# feeds it to the probe's real parser.
# ---------------------------------------------------------------------------
_LAB_SCRIPT = "scripts/opstat-lab-fr2-delegation-discovery.sh"


def _extract_argv(script_path, program):
    """The flag sequence a shell script passes to *program*, with every
    shell-substituted value replaced by "1" (parseable by str/int/float
    options alike). All options in these tools take exactly one value."""
    lines = open(script_path).read().splitlines()
    invocation, grabbing = [], False
    for line in lines:
        if program in line:
            grabbing = True
        if grabbing:
            invocation.append(line.rstrip("\\").strip())
            if not line.rstrip().endswith("\\"):
                break
    assert invocation, "no invocation of %s found in %s" % (program, script_path)
    argv = []
    for tok in " ".join(invocation).split():
        if tok.startswith("--"):
            argv.extend([tok, "1"])
    return argv


def test_lab_script_probe_invocation_parses():
    """If the committed shell script and committed probe cannot start
    together, the gate must fail before publication."""
    probe = _load_probe()
    argv = _extract_argv(_LAB_SCRIPT,
                         "scripts/var203_validation/probe_fr2_delegations.py")
    args = probe.build_parser().parse_args(argv)   # SystemExit(2) = contract break
    assert args.evidence_dir is not None, (
        "the lab script must route probe evidence beneath its DTS tree")
    assert args.mountpoint and args.export_path is not None


def test_lab_script_candidate_helper_invocation_parses():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "find_nfs41_candidates",
        "scripts/var203_validation/find_nfs41_candidates.py")
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    argv = _extract_argv(_LAB_SCRIPT,
                         "scripts/var203_validation/find_nfs41_candidates.py")
    helper.build_parser().parse_args(argv)   # SystemExit(2) = contract break


# ---------------------------------------------------------------------------
# Mount-to-VMS consistency (2026-08-18 owner correction): the lab server's
# NFSv4.1 mount belongs to var204, and the earlier runs queried var203 about
# files living there - every ILLEGAL_PATH was cross-cluster noise. The probe
# now refuses to collect evidence unless the mounted server address is a VIP
# of the cluster being queried.
# ---------------------------------------------------------------------------
def test_vips_contain_ip_matches_across_payload_shapes():
    probe = _load_probe()
    assert probe.vips_contain_ip(
        [{"id": 1, "ip": "172.200.204.6"}], "172.200.204.6")
    assert probe.vips_contain_ip(
        {"results": [{"vips": [{"addr": "172.200.204.6"}]}]}, "172.200.204.6")
    assert not probe.vips_contain_ip(
        [{"id": 1, "ip": "172.200.202.6"}], "172.200.204.6"), (
        "a var204 mount server must not match var203's VIP inventory")
    assert not probe.vips_contain_ip([], "172.200.204.6")
    assert not probe.vips_contain_ip([{"ip": "172.200.204.6"}], "")


def test_mismatch_guard_precedes_all_delegation_queries():
    """Structural ordering: the consistency verdict and its refusal must sit
    before the correlation flow and every nfs4_delegs call in main()."""
    body = open("scripts/var203_validation/probe_fr2_delegations.py").read()
    guard = body.index("preflight.mount_matches_vms")
    assert body.index("Refusing to", guard) < body.index(
        "mount -> view -> tenant correlation"), (
        "the refusal must come before correlation")
    assert guard < body.index('probe_one("availability"')


def test_required_objective_decides_exit_status():
    probe = _load_probe()
    probe.SUMMARY.append("PROBE:deleg.try0 FAIL something")
    assert probe.required_objective_met() is False
    probe.SUMMARY.append("PROBE:correlation.winner PASS tenant 1 accepts ...")
    assert probe.required_objective_met() is True


def test_lab_script_nfs41_defaults_and_prerequisites():
    """NFSv4.1 discovery defaults to var204 (the cluster owning the mount);
    the loadgen prerequisite fails fast with operator guidance and never
    auto-starts anything; the mount must exist, be readable and be v4.1."""
    body = open(_LAB_SCRIPT).read()
    assert "OPSTAT_VMS:-var204.selab.vastdata.com" in body, (
        "var203 must no longer be the NFSv4.1 default target")
    assert 'REQUIRED_LOADGEN="nfs41-loadgen"' in body
    check = body.index('systemctl is-active --quiet "$REQUIRED_LOADGEN')
    probe_call = body.index("probe_fr2_delegations.py \\")
    assert check < probe_call, "the prerequisite must precede the probe"
    for line in body.splitlines():
        if "systemctl start" in line:
            assert "say " in line or "echo" in line, (
                "the start command is operator guidance, never executed: %r" % line)
    assert "vers=4.1" in body and '--mount-server "$SERVER_IP"' in body

# ---------------------------------------------------------------------------
# FR2 production validation pair: scripts/opstat-lab-fr2-delegation-
# validation.sh must start the committed validator (CLI contract), carry the
# same targeting/prerequisite discipline as the discovery script, and gate
# its verdict on the live-delegation objective. The --evidence-dir incident
# proved an untested argv drifts; every committed script/tool pair is parsed
# for real in the gate.
# ---------------------------------------------------------------------------
_VAL_LAB_SCRIPT = "scripts/opstat-lab-fr2-delegation-validation.sh"
_VALIDATOR = "scripts/var203_validation/validate_fr2_delegation.py"


def _load_validator():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "validate_fr2_delegation", _VALIDATOR)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_validation_lab_script_validator_invocation_parses():
    validator = _load_validator()
    argv = _extract_argv(_VAL_LAB_SCRIPT, _VALIDATOR)
    args = validator.build_parser().parse_args(argv)  # SystemExit(2) = break
    assert args.mountpoint and args.export_path and args.client_files
    assert args.mount_server, (
        "mount-to-VMS consistency needs the mount server address")
    assert args.frame_out is not None, (
        "captured production frames must land in the run tree")


def test_validation_lab_script_candidate_helper_invocation_parses():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "find_nfs41_candidates",
        "scripts/var203_validation/find_nfs41_candidates.py")
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    argv = _extract_argv(_VAL_LAB_SCRIPT,
                         "scripts/var203_validation/find_nfs41_candidates.py")
    helper.build_parser().parse_args(argv)


def test_validation_lab_script_defaults_prerequisites_and_verdict():
    """var204 default; nfs41-loadgen fail-fast with guidance, never
    auto-started; refusal precedes the validator when no real file exists;
    the minimum-success check requires the live-delegation objective AND an
    all-PASS production run; failures map to RUN FAILED + nonzero exit."""
    body = open(_VAL_LAB_SCRIPT).read()
    assert "OPSTAT_VMS:-var204.selab.vastdata.com" in body
    assert 'REQUIRED_LOADGEN="nfs41-loadgen"' in body
    check = body.index('systemctl is-active --quiet "$REQUIRED_LOADGEN')
    run_call = body.index("validate_fr2_delegation.py \\")
    assert check < run_call, "the prerequisite must precede the validator"
    for line in body.splitlines():
        if "systemctl start" in line:
            assert "say " in line or "echo" in line, (
                "the start command is operator guidance, never executed: %r"
                % line)
    assert body.index("refusing to run the validator") < run_call
    assert "vers=4.1" in body and '--mount-server "$SERVER_IP"' in body
    assert "CHECK:deleg.live.records *PASS" in body, (
        "minimum success must be machine-checked on the live objective")
    assert "^RESULT: PASS" in body, (
        "minimum success must also require an all-PASS validator run")
    assert "RUN FAILED" in body and "RUN VALID" in body
    assert "|| true" not in body


def test_validator_is_get_only_by_construction():
    """The in-process validator drives the production engine; its only
    direct requests are GETs (/vips/ preflight, per-id monitor checks) and
    it greps the API log for non-GET delegation calls as a hard gate."""
    body = open(_VALIDATOR).read()
    assert 'request("GET"' in body
    for verb in ('"POST"', '"DELETE"', '"PUT"', '"PATCH"'):
        assert verb not in body, (
            "the validator must never issue %s itself" % verb)
    assert "non_get_deleg_calls" in body and "safety.get_only" in body
