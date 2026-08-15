"""Monitor-cleanup lifecycle: drain must survive an interrupting signal.

Reproduces the defect observed twice on the real cluster (var203): a SIGTERM
(or a PTY hang-up SIGHUP) arriving during the slow synchronous monitor drain
re-entered the signal handler, which `sys.exit(0)`ed and unwound the drain loop
mid-way, orphaning an `adhoc_opstat_*` monitor with no "not deleted" warning.

The fix has two parts:
  1. `vast_common.drain_monitors` blocks termination signals for the duration
     of the drain, so a signal mid-drain is deferred until the loop completes.
  2. Each engine's `cleanup()` sets its `_CLEANED_UP` guard only *after* the
     drain, so an interrupted/failed cleanup is retried by the atexit backstop
     rather than skipped.
"""

from __future__ import annotations

import os
import signal

import pytest

import vast_common


@pytest.fixture(autouse=True)
def _clean_registry():
    vast_common.reset_registry()
    yield
    vast_common.reset_registry()


def test_drain_monitors_completes_despite_a_signal_induced_exit():
    """A SIGTERM whose handler raises SystemExit mid-drain (exactly what the
    engine signal_handler does) must not abort the drain: every registered
    monitor is still deleted before the exit takes effect."""
    for mid in (1, 2, 3):
        vast_common.register_monitor(mid)

    deleted = []

    def delete_fn(mid):
        deleted.append(mid)
        if mid == 1:                       # signal arrives during the first delete
            os.kill(os.getpid(), signal.SIGTERM)

    def raiser(_signum, _frame):
        raise SystemExit(0)

    old = signal.signal(signal.SIGTERM, raiser)
    try:
        try:
            vast_common.drain_monitors(delete_fn)
        except SystemExit:
            pass                            # the deferred exit lands after the drain
    finally:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)   # discard any late pending signal
        signal.signal(signal.SIGTERM, old)

    assert deleted == [1, 2, 3], (
        "drain was interrupted by the signal; monitors %s were orphaned"
        % ([m for m in (1, 2, 3) if m not in deleted]))
    assert vast_common._CREATED_MONITORS == set(), "registry not fully drained"


def test_drain_monitors_is_idempotent():
    for mid in (10, 11):
        vast_common.register_monitor(mid)
    seen = []
    vast_common.drain_monitors(lambda m: seen.append(m))
    assert sorted(seen) == [10, 11]
    # A second drain is a safe no-op (nothing left registered).
    seen.clear()
    vast_common.drain_monitors(lambda m: seen.append(m))
    assert seen == []


def test_cleanup_message_counts_pending_monitors():
    assert vast_common.cleanup_message(1) == "Cleaning up 1 temporary monitor, please stand by..."
    assert vast_common.cleanup_message(4) == "Cleaning up 4 temporary monitors, please stand by..."
    assert vast_common.pending_monitor_count() == 0
    for mid in (1, 2, 3):
        vast_common.register_monitor(mid)
    assert vast_common.pending_monitor_count() == 3


def test_drain_continues_after_a_failed_delete_and_surfaces_it():
    """One DELETE failing (non-404) must not abandon the rest of the drain, and
    the failure must be reported truthfully via failed_deletes()."""
    for mid in (5, 6, 7):
        vast_common.register_monitor(mid)
    attempted = []

    def request_fn(method, path):
        attempted.append(path)
        if "/6/" in path:
            raise RuntimeError("HTTP 500: monitor busy")

    vast_common.drain_monitors(lambda mid: vast_common.delete_monitor(request_fn, mid))

    assert len(attempted) == 3, "drain stopped early instead of continuing past the failure"
    assert vast_common._CREATED_MONITORS == set(), "registry not fully drained"
    failed = vast_common.failed_deletes()
    assert [mid for mid, _ in failed] == [6], failed


def test_drain_only_touches_registered_session_monitors():
    for mid in (100, 101):
        vast_common.register_monitor(mid)
    deleted = []
    vast_common.drain_monitors(lambda mid: deleted.append(mid))
    assert sorted(deleted) == [100, 101], "drain touched ids it did not own"


def test_engine_cleanup_announces_pending_monitor_count(monkeypatch, capsys):
    import smb

    monkeypatch.setattr(smb, "restore_terminal", lambda: None)
    monkeypatch.setattr(smb, "delete_monitor", lambda mid: vast_common.forget_monitor(mid))
    vast_common.register_monitor(4242)
    smb._CLEANED_UP = False
    try:
        smb.cleanup()
    finally:
        smb._CLEANED_UP = False
    err = capsys.readouterr().err
    assert "Cleaning up 1 temporary monitor, please stand by" in err, err
    assert vast_common.pending_monitor_count() == 0


def test_engine_cleanup_silent_when_no_monitors(monkeypatch, capsys):
    import smb

    monkeypatch.setattr(smb, "restore_terminal", lambda: None)
    smb._CLEANED_UP = False
    try:
        smb.cleanup()
    finally:
        smb._CLEANED_UP = False
    assert "Cleaning up" not in capsys.readouterr().err


def test_engine_cleanup_reattempts_drain_when_it_is_interrupted(monkeypatch):
    """If the drain does not complete, the engine's `_CLEANED_UP` guard must
    stay False so the atexit/finally backstop retries it, rather than being set
    too early and skipping the monitors."""
    import smb

    calls = {"n": 0}

    def flaky_drain(_delete_fn):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("drain interrupted before completion")

    monkeypatch.setattr(vast_common, "drain_monitors", flaky_drain)
    monkeypatch.setattr(smb, "restore_terminal", lambda: None)
    smb._CLEANED_UP = False
    try:
        with pytest.raises(RuntimeError):
            smb.cleanup()
        assert smb._CLEANED_UP is False, "guard set before drain completed"
        smb.cleanup()                       # retry succeeds
        assert calls["n"] == 2, "second cleanup skipped the drain"
    finally:
        smb._CLEANED_UP = False


def test_engine_cleanup_survives_an_injected_delete_failure(tmp_path, monkeypatch):
    """End-to-end through the mock: one monitor's DELETE fails with HTTP 500;
    every other session monitor must still be removed and the failure
    reported - the drain must never let one bad delete orphan the rest."""
    pytest.importorskip("ssl")
    import shutil as _shutil

    if _shutil.which("openssl") is None:
        pytest.skip("openssl binary required to generate the mock VMS certificate")
    from types import SimpleNamespace

    from tests.mock_vms import MockVMS

    import nvme_tcp

    vms = MockVMS(certdir=str(tmp_path)).start()
    try:
        monkeypatch.setenv("VAST_TOKEN", "test-token")
        nvme_tcp.init_config(SimpleNamespace(
            vms="127.0.0.1", port=vms.port, user="admin", password=None,
            sample_average=None, refresh=5, csv=None, no_color=True,
            discover_metrics=False, log_api_calls=False,
            export_openmetrics=False, openmetrics_file=None,
            volumes=None, volume=None))
        nvme_tcp.CLUSTER_ID, nvme_tcp.CLUSTER_NAME = nvme_tcp.get_current_cluster()
        nvme_tcp.create_cluster_monitors()
        created = set(nvme_tcp.OPS_MONITOR_IDS) | {nvme_tcp.PROTO_MONITOR_ID}
        victim = sorted(created)[2]
        vms.state.fail_delete_ids = {victim}

        monkeypatch.setattr(nvme_tcp, "restore_terminal", lambda: None)
        nvme_tcp._CLEANED_UP = False
        nvme_tcp.cleanup()

        live = set(vms.live_monitors())
        assert live == {victim}, (
            f"drain stopped early: {sorted(live)} still live, expected only {victim}")
        assert victim in [mid for mid, _d in vast_common.failed_deletes()], (
            "the failed delete was not reported")
    finally:
        vms.state.fail_delete_ids = set()
        nvme_tcp._CLEANED_UP = False
        nvme_tcp.cleanup()
        nvme_tcp._CLEANED_UP = False
        vast_common.close_connection()
        vms.stop()
