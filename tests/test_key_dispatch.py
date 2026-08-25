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
# a later queued key still lands. The second key's END STATE differs by
# engine since FR14: [t] is an honest capability notice on nfs_v3/s3, a
# host_view exporter mode on nfs_v41/smb, and nvme uses [i] vip. What this
# suite asserts is unchanged - the queued key must LAND.
FIRST_DRILL = {"nfs_v3": "c", "nfs_v41": "c", "smb": "c", "s3": "c",
               "nvme_tcp": "c"}


def _second_landed(module):
    name = module.__name__
    if name == "nfs_v3":
        return module.TENANT_UNAVAILABLE_MARKER in (module.DRILL_ERROR or "")
    if name == "nfs_v41":
        return module.EXPORTER_MODE == "tenant"
    if name == "smb":
        return module.HV_MODE == "tenant"
    if name == "s3":
        return module.TENANT_UNAVAILABLE_MARKER in (module.DRILL_ERROR or "")
    return module.DRILL_MODE == "vip"


SECOND_KEY = {"nfs_v3": "t", "nfs_v41": "t", "smb": "t", "s3": "t",
              "nvme_tcp": "i"}


@pytest.fixture(params=ENGINES)
def engine(request, vms, monkeypatch):
    module = _engine(request.param, vms, monkeypatch)
    yield module
    module.exit_drill_mode()
    if hasattr(module, "exit_exporter_mode"):
        module.exit_exporter_mode()
    if hasattr(module, "exit_hostview_mode"):
        module.exit_hostview_mode()
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

    _drain(module, " x" + SECOND_KEY[name])
    capsys.readouterr()
    assert _second_landed(module), (
        "%s: queued keys dropped - the trailing %r never landed "
        "(DRILL_MODE=%r DRILL_ERROR=%r)"
        % (name, SECOND_KEY[name], module.DRILL_MODE, module.DRILL_ERROR))


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


# ---------------------------------------------------------------------------
# Retired aliases stay dead. [p] exited the NVMe drill historically; the
# round-3 lab validator reported it "still live", which turned out to be a
# validator state artifact - but the contract deserves direct coverage: p (and
# v-for-VIP on NVMe) must dispatch to None and mutate nothing, everywhere.
# ---------------------------------------------------------------------------
def test_retired_p_alias_is_dead_everywhere(engine, capsys):
    module = engine
    name = module.__name__
    module._dispatch_key(FIRST_DRILL[name])
    mode_before = module.DRILL_MODE
    assert mode_before is not None

    outcome = module._dispatch_key("p")
    capsys.readouterr()
    assert outcome is None, f"{name}: 'p' is bound (returned {outcome!r})"
    assert module.DRILL_MODE == mode_before, f"{name}: 'p' changed drill state"


def test_v_does_not_open_vip_on_nvme(vms, monkeypatch, capsys):
    module = _engine("nvme_tcp", vms, monkeypatch)
    try:
        outcome = module._dispatch_key("v")
        capsys.readouterr()
        assert outcome is None, "'v' is bound on NVMe (retired VIP alias)"
        assert module.DRILL_MODE is None
    finally:
        module.cleanup()
        module._CLEANED_UP = False
        vast_common.close_connection()
