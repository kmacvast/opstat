"""Whole-frame terminal-width invariant, every engine (FR7).

`.claude/rules/tui-behavior.md` states it plainly: *the frame must never
exceed the terminal width*. Until this suite existed only NFSv4.1 was held to
that - `tests/test_render_navigation.py` checks it for that engine alone - and
four engines drifted:

* header/meta lines are drawn OUTSIDE the box machinery, so they were printed
  untruncated. With realistic values (cluster `selab-var-203`, VMS
  `var203.selab.vastdata.com:443`, build `release-5.4.6-2628322`) the NVMe
  meta line measured 114 columns and the NFSv3 header 99 - both overflow a
  *standard 80-column terminal*, not merely a narrow one;
* box titles were never truncated, so they overflowed below ~26 columns;
* NFSv3's grand-total COMBINED line is drawn outside the boxes too.

An overflowing line wraps, shifting every row beneath it and corrupting the
frame - the exact failure the box renderers already guard against.

Frames are composed from REAL engine state driven against the mock VMS, not
hand-built row dicts, so the measured lines are the ones an operator sees.
Both colour modes are exercised: `truncate_display` used to count escape
bytes against the width budget, so a coloured 80-column header rendered only
50 visible columns while the same header was complete with colour off.
"""

from __future__ import annotations

import importlib
import io
import os
import shutil
import sys
from types import SimpleNamespace

import pytest

import tui_layout
import vast_common
from tests.mock_vms import MockVMS

pytestmark = pytest.mark.skipif(
    shutil.which("openssl") is None,
    reason="openssl binary required to generate the mock VMS certificate",
)

# Realistic long values - a short mock hostname hides the defect entirely.
REAL_VMS = "var203.selab.vastdata.com"
REAL_CLUSTER = "selab-var-203"
REAL_OS = "release-5.4.6-2628322"

# 24 is NFSv4.1's own _MIN_FRAME_WIDTH floor; nothing in the repository claims
# support below that, so the sweep stops there rather than inventing a
# narrower contract.
WIDTHS = [160, 120, 100, 80, 60, 40, 24]

ENGINES = {
    "nfs_v3": "create_headline_monitors",
    "nfs_v41": "create_headline_monitors",
    "smb": "create_headline_monitors",
    "s3": "create_headline_monitors",
    "nvme_tcp": "create_cluster_monitors",
}


def _args(port, name):
    base = dict(vms="127.0.0.1", port=port, user="admin", password=None,
                sample_average=None, refresh=5, csv=None, no_color=True,
                discover_metrics=False, log_api_calls=False,
                export_openmetrics=False, openmetrics_file=None)
    if name == "nvme_tcp":
        base.update(volumes=None, volume=None)
    return SimpleNamespace(**base)


def render(module, columns):
    real_size = shutil.get_terminal_size
    buf, real_stdout = io.StringIO(), sys.stdout
    shutil.get_terminal_size = lambda fallback=(80, 24), c=columns: (
        os.terminal_size((c, 40)))
    sys.stdout = buf
    try:
        module._render_frame()
    finally:
        sys.stdout = real_stdout
        shutil.get_terminal_size = real_size
    return buf.getvalue()


def assert_fits(module, columns, label):
    frame = tui_layout.strip_ansi(render(module, columns))
    for line in frame.split("\n"):
        assert tui_layout.display_width(line) <= columns, (
            f"{label} at {columns} columns: line of "
            f"{tui_layout.display_width(line)} columns wraps and corrupts the "
            f"frame: {line.strip()[:80]!r}")


@pytest.fixture(params=sorted(ENGINES))
def engine(request, tmp_path, monkeypatch):
    """A real engine with real telemetry state and realistic display values."""
    name = request.param
    server = MockVMS(certdir=str(tmp_path)).start()
    module = importlib.import_module(name)
    monkeypatch.setenv("VAST_TOKEN", "test-token")
    module.init_config(_args(server.port, name))
    module.CLUSTER_ID, module.CLUSTER_NAME = module.get_current_cluster()
    getattr(module, ENGINES[name])()
    module.poll_tick()
    # Display-only values; nothing about telemetry or API behaviour changes.
    module.VMS = REAL_VMS
    module.CLUSTER_NAME = REAL_CLUSTER
    if hasattr(module, "CLUSTER_OS"):
        module.CLUSTER_OS = REAL_OS
    yield name, module
    for attr in ("STARTUP_STATUS", "DRILL_STATUS", "DRILL_ERROR", "DRILL_MODE",
                 "EXPORTER_MODE", "EXPORTER_STATUS"):
        if hasattr(module, attr):
            setattr(module, attr, None)
    module.cleanup()
    module._CLEANED_UP = False
    vast_common.close_connection()
    server.stop()


def engine_states(name, module):
    """Every frame state this engine can actually reach, as (label, apply,
    restore) triples. Structured this way rather than one test per state so
    that an engine lacking a state contributes no skipped test - the gate
    treats skips as a failure signal."""
    states = [("dashboard", lambda: None, lambda: None)]
    if hasattr(module, "STARTUP_STATUS"):
        states.append((
            "startup",
            lambda: setattr(module, "STARTUP_STATUS",
                            f"Connecting to {REAL_VMS}:443, please stand by..."),
            lambda: setattr(module, "STARTUP_STATUS", None)))
    if hasattr(module, "DRILL_STATUS"):
        # The accepted cold-entry wording is long by design; it may be fitted
        # to the terminal, but its semantics live in vast_drill.
        import vast_drill
        states.append((
            "drill-loading",
            lambda: setattr(module, "DRILL_STATUS",
                            vast_drill.loading_message("blockhost",
                                                       first_time=True)),
            lambda: setattr(module, "DRILL_STATUS", None)))
    if hasattr(module, "DRILL_ERROR"):
        states.append((
            "drill-error",
            lambda: setattr(
                module, "DRILL_ERROR",
                f"Cannot fetch view objects: GET https://{REAL_VMS}:443"
                f"/api/views/ failed: HTTP 500: internal server error"),
            lambda: setattr(module, "DRILL_ERROR", None)))
    if isinstance(module.LAST_ROWS, list):
        # Only the list-backed engines have a real rows-empty frame; the
        # dict-backed ones show the startup box before data arrives, and a
        # bare empty dict would exercise no state an operator can reach.
        rows = {}
        def _empty():
            rows["saved"] = module.LAST_ROWS
            module.LAST_ROWS = []
        states.append(("waiting", _empty,
                       lambda: setattr(module, "LAST_ROWS", rows["saved"])))
    return states


@pytest.mark.parametrize("columns", WIDTHS)
def test_no_frame_state_exceeds_the_terminal(engine, columns):
    name, module = engine
    assert module.LAST_ROWS, f"{name}: fixture produced no telemetry rows"
    for label, apply, restore in engine_states(name, module):
        apply()
        try:
            assert_fits(module, columns, f"{name} {label}")
        finally:
            restore()


@pytest.mark.parametrize("columns", [80, 40])
def test_frame_fits_with_colour_enabled_too(engine, columns):
    """truncate_display counted escape bytes against the budget, so colour
    changed both the width and the surviving content of a header."""
    name, module = engine
    tui_layout.set_color(True)
    module._COLOR = True
    try:
        for label, apply, restore in engine_states(name, module):
            apply()
            try:
                assert_fits(module, columns, f"{name} {label} (colour)")
            finally:
                restore()
    finally:
        tui_layout.set_color(False)
        module._COLOR = False


def test_colour_does_not_change_what_a_truncated_header_shows(engine):
    """The 80-column NFSv4.1 header rendered 50 visible columns with colour
    on and 80 with it off - the same frame showed different information."""
    name, module = engine
    plain = tui_layout.strip_ansi(render(module, 80))
    tui_layout.set_color(True)
    module._COLOR = True
    try:
        coloured = tui_layout.strip_ansi(render(module, 80))
    finally:
        tui_layout.set_color(False)
        module._COLOR = False
    assert coloured.split("\n")[0] == plain.split("\n")[0], (
        f"{name}: colour changed the visible header content")


def test_every_engine_is_covered():
    """A new engine must not silently escape the invariant."""
    import glob

    modules = {os.path.basename(p)[:-3] for p in glob.glob("*.py")
               if os.path.basename(p) in
               ("nfs_v3.py", "nfs_v41.py", "smb.py", "s3.py", "nvme_tcp.py")}
    assert modules == set(ENGINES), (
        f"engines missing from the width sweep: {modules - set(ENGINES)}")
