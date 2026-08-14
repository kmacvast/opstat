"""Queued-keystroke dispatch across all five engines (mock-VMS backed).

``check_keypress`` returns the entire pending input buffer, and keys are only
read between poll cycles. On var203 a cycle blocks 30-80 s, so several
keystrokes routinely arrive in one read - and every engine used to fire ONE
action per read and silently discard the rest. The real lab log shows the
consequence: a buffered space swallowed the queued ``x`` and ``i`` after a
cNode drill entry, the drill could not be exited until quit, and the VIP/HOST
drills were never entered at all.

Every engine now routes its buffer through
``vast_drill.dispatch_queued_keys`` with a per-engine ``_dispatch_key``: one
action per key, arrival order preserved, bindings unchanged. These tests
replay multi-key buffers - the literal defect shape - not just single
keypresses.
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
        clients=None, buckets=None, tenants=None, volumes=None, volume=None,
        version=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _engine(name, vms, monkeypatch):
    import importlib

    module = importlib.import_module(name)
    monkeypatch.setenv("VAST_TOKEN", "test-token")
    module.init_config(_args(vms))
    module.CLUSTER_ID, module.CLUSTER_NAME = module.get_current_cluster()
    if name == "nvme_tcp":
        module.create_cluster_monitors()
    else:
        module.create_headline_monitors()
    return module


ENGINES = ["nfs_v3", "nfs_v41", "smb", "s3", "nvme_tcp"]
# One drill each engine can enter against the mock, and a second one to prove
# a later queued key still lands.
FIRST_DRILL = {"nfs_v3": "c", "nfs_v41": "c", "smb": "c", "s3": "c",
               "nvme_tcp": "c"}
SECOND_DRILL = {"nfs_v3": ("t", "tenant"), "nfs_v41": ("t", "tenant"),
                "smb": ("t", "tenant"), "s3": ("t", "tenant"),
                "nvme_tcp": ("i", "vip")}


@pytest.fixture(params=ENGINES)
def engine(request, vms, monkeypatch):
    module = _engine(request.param, vms, monkeypatch)
    yield module
    module.exit_drill_mode()
    if hasattr(module, "exit_exporter_mode"):
        module.exit_exporter_mode()
    module.cleanup()
    module._CLEANED_UP = False
    vast_common.close_connection()


def _drain(module, chars):
    """Feed one multi-key buffer exactly the way the main loops now do.

    The deferred render is stubbed out: these tests assert state transitions
    and dispatch ordering, not frame contents (the render suites own those).
    """
    return vast_drill.dispatch_queued_keys(chars, module._dispatch_key, lambda: None)


def test_space_does_not_swallow_queued_exit_and_drill_keys(engine, capsys):
    """The literal var203 defect: buffer ' x<drill>' after entering a drill.

    Space must refresh, x must exit, and the trailing drill key must land -
    three actions from one read, in order.
    """
    module = engine
    name = module.__name__
    module._dispatch_key(FIRST_DRILL[name])
    assert module.DRILL_MODE is not None, "fixture drill entry failed"
    second_key, second_mode = SECOND_DRILL[name]

    _drain(module, " x" + second_key)
    capsys.readouterr()
    assert module.DRILL_MODE == second_mode, (
        "%s: queued keys dropped - expected drill %r, got %r"
        % (name, second_mode, module.DRILL_MODE))


def test_exit_then_reenter_in_one_buffer(engine, capsys):
    """'x' followed by a drill key in the same read must both apply."""
    module = engine
    name = module.__name__
    module._dispatch_key(FIRST_DRILL[name])
    first_mode = module.DRILL_MODE
    assert first_mode is not None

    _drain(module, "x" + FIRST_DRILL[name])
    capsys.readouterr()
    assert module.DRILL_MODE is not None, (
        "%s: the re-enter key after x was dropped" % name)


def test_unbound_keys_in_the_buffer_do_not_block_later_keys(engine, capsys):
    module = engine
    name = module.__name__
    _drain(module, "zz" + FIRST_DRILL[name])
    capsys.readouterr()
    assert module.DRILL_MODE is not None, (
        "%s: a bound key after unbound keys was dropped" % name)
    _drain(module, "x")
    capsys.readouterr()


@pytest.mark.parametrize("name,buffer,expected", [
    ("nfs_v3", "ol", "latency"),      # both sorts applied, order preserved
    ("nfs_v3", "lo", "ops"),
    ("nfs_v41", "on", "default"),
    ("nfs_v41", "no", "ops"),
])
def test_queued_sort_keys_apply_in_arrival_order(name, buffer, expected,
                                                 vms, monkeypatch, capsys):
    module = _engine(name, vms, monkeypatch)
    try:
        _drain(module, buffer)
        capsys.readouterr()
        assert module.SORT_MODE == expected, (
            "%s: buffer %r ended at %r, expected %r (order not preserved)"
            % (name, buffer, module.SORT_MODE, expected))
    finally:
        module.cleanup()
        module._CLEANED_UP = False
        vast_common.close_connection()


def test_rendered_outcome_reports_back_for_timer_rearm(engine, capsys):
    """Space refreshes inline; the helper must say so, so the main loop
    re-arms its refresh timer instead of double-polling."""
    module = engine
    rendered = vast_drill.dispatch_queued_keys(
        " ", module._dispatch_key, lambda: None)
    capsys.readouterr()
    assert rendered is True


def test_dispatch_helper_renders_once_per_batch():
    """A burst of refresh-owing keys costs one deferred repaint, not many."""
    renders = []
    outcomes = iter(["refresh", "refresh", None, "refresh"])

    def dispatch(_key):
        return next(outcomes)

    rendered = vast_drill.dispatch_queued_keys(
        "abcd", dispatch, lambda: renders.append(1))
    assert rendered is False
    assert renders == [1], "expected exactly one deferred render per batch"
