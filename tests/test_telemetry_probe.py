"""Telemetry-correctness evidence probe (FR1+FR3 lab tooling).

The round-3/round-4 latency probes sampled a single instant and read idle
zeros as "no evidence" three lab trips in a row; the monitor-storm era proved
what an un-verified cleanup path costs. These tests pin the probe behaviors
those incidents bought: bounded loop-until-nonzero sampling with an honest
timeout, protocol-filtered host_view parsing, verbatim metadata preservation,
and exact-id cleanup that survives a raising section and never touches
monitors this run did not create.
"""

import importlib.util
import os

import pytest


def _load_probe():
    spec = importlib.util.spec_from_file_location(
        "probe_telemetry_correctness",
        "scripts/var203_validation/probe_telemetry_correctness.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Bounded paired sampling
# ---------------------------------------------------------------------------
def test_bounded_sampling_finds_a_later_nonzero_window():
    """Zeros first, signal later - the loop must keep going and keep all
    samples (the single-shot probe failed this exact scenario thrice)."""
    probe = _load_probe()
    feed = iter([0, 0, 0, 41.5])
    naps = []
    samples, decisive = probe.bounded_paired_sampling(
        lambda: next(feed), lambda s: bool(s), attempts=10, interval=15,
        sleep_fn=naps.append)
    assert decisive is True
    assert samples == [0, 0, 0, 41.5]
    assert naps == [15, 15, 15], "must wait between samples, not after success"


def test_bounded_sampling_times_out_honestly():
    probe = _load_probe()
    naps = []
    samples, decisive = probe.bounded_paired_sampling(
        lambda: 0, lambda s: bool(s), attempts=4, interval=1,
        sleep_fn=naps.append)
    assert decisive is False
    assert len(samples) == 4, "every attempt recorded, none invented"
    assert len(naps) == 3, "no sleep after the final attempt"


# ---------------------------------------------------------------------------
# Unit hypothesis
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("ref_us,candidate,expected", [
    (1500.0, 1.4, "ms"),       # the suspected host_view case: ~1000x smaller
    (588.5, 541.4, "us"),      # the D-003 style same-magnitude agreement
    (1.5, 1500.0, "ns"),        # 1.5 us re-expressed in nanoseconds
    (1000.0, 30.0, "inconclusive"),   # 33x fits no band - stays unproven
])
def test_unit_hypothesis_bands(ref_us, candidate, expected):
    probe = _load_probe()
    verdict, ratio = probe.unit_hypothesis(ref_us, candidate)
    assert verdict == expected
    assert ratio is not None


def test_unit_hypothesis_refuses_idle_values():
    """A zero or missing side is not evidence of anything."""
    probe = _load_probe()
    assert probe.unit_hypothesis(0, 1.2) == ("inconclusive", None)
    assert probe.unit_hypothesis(500.0, None) == ("inconclusive", None)
    assert probe.unit_hypothesis(None, None) == ("inconclusive", None)


# ---------------------------------------------------------------------------
# host_view NFS3 filtering + per-path aggregation
# ---------------------------------------------------------------------------
_EXPO = "\n".join([
    "# HELP vast_host_view_latency latency per host and view",
    "# TYPE vast_host_view_latency gauge",
    'vast_host_view_iops{ip="10.0.0.1",path="/a",protocol="NFS3",tenant="t"} 100',
    'vast_host_view_latency{ip="10.0.0.1",path="/a",protocol="NFS3",tenant="t"} 1.5',
    'vast_host_view_iops{ip="10.0.0.2",path="/a",protocol="NFS3",tenant="t"} 300',
    'vast_host_view_latency{ip="10.0.0.2",path="/a",protocol="NFS3",tenant="t"} 2.5',
    'vast_host_view_iops{ip="10.0.0.9",path="/b",protocol="NFS4",tenant="t"} 999',
    'vast_host_view_latency{ip="10.0.0.9",path="/b",protocol="NFS4",tenant="t"} 9.9',
])


def test_host_view_rows_filter_to_the_requested_protocol():
    probe = _load_probe()
    rows = probe.host_view_rows(_EXPO, "NFS3")
    assert {r["path"] for r in rows} == {"/a"}
    assert all(r["ip"].startswith("10.0.0.") for r in rows)
    nfs4 = probe.host_view_rows(_EXPO, "NFS4")
    assert {r["path"] for r in nfs4} == {"/b"}


def test_aggregate_by_path_sums_clients_and_weights_latency():
    probe = _load_probe()
    paths = probe.aggregate_by_path(probe.host_view_rows(_EXPO, "NFS3"))
    assert len(paths) == 1
    agg = paths[0]
    assert agg["path"] == "/a"
    assert agg["clients"] == 2
    assert agg["iops"] == 400
    # iops-weighted: (100*1.5 + 300*2.5) / 400 = 2.25
    assert abs(agg["latency"] - 2.25) < 1e-9


# ---------------------------------------------------------------------------
# Side-by-side accounting
# ---------------------------------------------------------------------------
def test_summarize_side_by_side_accounts_for_both_sources():
    probe = _load_probe()
    view_rows = [("/a", 120.0), ("/b", 0), ("/c", 55.0)]
    hv_paths = [{"path": "/a", "iops": 400.0}, {"path": "/d", "iops": 9.0}]
    side = probe.summarize_side_by_side(view_rows, hv_paths)
    assert side["viewmetrics_paths_active"] == ["/a", "/c"]
    assert side["host_view_nfs3_paths_active"] == ["/a", "/d"]
    assert side["overlap"] == ["/a"]
    assert side["viewmetrics_only"] == ["/c"]
    assert side["host_view_only"] == ["/d"]


# ---------------------------------------------------------------------------
# Raw metadata preservation
# ---------------------------------------------------------------------------
def test_save_evidence_preserves_comments_verbatim(tmp_path):
    """The whole point of the metadata capture is the # HELP/# TYPE lines the
    production parser deliberately skips - saving must not strip them."""
    probe = _load_probe()
    probe.EVIDENCE_DIR = str(tmp_path)
    probe.save_evidence("exposition.prom", _EXPO)
    written = (tmp_path / "exposition.prom").read_text()
    assert written == _EXPO
    assert "# HELP vast_host_view_latency" in written


def test_extract_unit_hints_finds_unit_language():
    probe = _load_probe()
    body = ('{"metric": "BlockMetrics,read_latency__avg", '
            '"description": "Average read latency in microseconds"}\n'
            '{"metric": "BlockMetrics,read_req", "description": "count"}')
    hits = probe.extract_unit_hints(body)
    assert any("microseconds" in h for h in hits)
    assert all("read_req" not in h for h in hits), (
        "a unit-free counter line must not be reported as a unit hint")


# ---------------------------------------------------------------------------
# Cleanup: exact ids, error paths, never anyone else's monitors
# ---------------------------------------------------------------------------
class _FakeVMS:
    """Minimal request_fn double: creates ids, 404s deleted ones, and records
    every call so the tests can assert what was and was not touched."""

    def __init__(self, foreign_ids=(9901, 9902)):
        self.calls = []
        self.next_id = 500
        self.live = set(foreign_ids)      # other sessions' monitors
        self.foreign = set(foreign_ids)

    def __call__(self, method, path, payload=None):
        self.calls.append((method, path))
        if method == "POST" and path == "/monitors/":
            self.next_id += 1
            self.live.add(self.next_id)
            return {"id": self.next_id}
        if method == "DELETE":
            mid = int(path.strip("/").split("/")[-1])
            self.live.discard(mid)
            return None
        if method == "GET" and path.startswith("/monitors/"):
            mid_part = path.strip("/").split("/")[1]
            if mid_part.isdigit() and int(mid_part) not in self.live:
                raise RuntimeError("GET %s failed: HTTP 404" % path)
            return {"id": mid_part, "prop_list": [], "data": []}
        return {}


def test_cleanup_deletes_exactly_the_session_ids(monkeypatch):
    probe = _load_probe()
    fake = _FakeVMS()
    monkeypatch.setattr(probe, "api", fake)
    a = probe.create_probe_monitor("t1", ["X,y"], "cluster", [1])
    b = probe.create_probe_monitor("t2", ["X,y"], "cluster", [1])
    probe.cleanup_all()
    deleted = [p for m, p in fake.calls if m == "DELETE"]
    assert deleted == ["/monitors/%s/" % a, "/monitors/%s/" % b]
    assert any("cleanup.exact_ids PASS" in line for line in probe.SUMMARY)
    assert fake.foreign <= fake.live, "another session's monitor was deleted"


def test_cleanup_runs_for_monitors_created_before_a_section_raised(monkeypatch):
    """A section that dies after creating a monitor must still be torn down -
    the round-4 leak was exactly an error path with no cleanup guarantee."""
    probe = _load_probe()
    fake = _FakeVMS()
    monkeypatch.setattr(probe, "api", fake)

    def exploding_section():
        probe.create_probe_monitor("boom", ["X,y"], "cluster", [1])
        raise RuntimeError("section failure mid-flight")

    try:
        exploding_section()
    except RuntimeError:
        pass
    finally:
        probe.cleanup_all()
    assert probe.CREATED, "monitor id was not recorded"
    assert not (set(probe.CREATED) & fake.live), "leaked session monitor"
    assert fake.foreign <= fake.live


def test_run_probes_contains_a_raising_section(monkeypatch):
    """run_probes must contain per-section failures so the other sections and
    the caller's finally-cleanup still run."""
    probe = _load_probe()
    ran = []
    monkeypatch.setattr(probe, "probe_metadata",
                        lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(probe, "probe_nfs3_view_attribution",
                        lambda *a: ran.append("fr1"))
    monkeypatch.setattr(probe, "probe_latency_units",
                        lambda *a: ran.append("fr3"))
    args = type("A", (), {"attempts": 1, "interval": 0})()
    probe.run_probes(args, cluster_id=1)
    assert ran == ["fr1", "fr3"], "later sections did not run after a failure"
    assert any("metadata.section FAIL" in line for line in probe.SUMMARY)
