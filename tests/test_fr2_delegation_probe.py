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
