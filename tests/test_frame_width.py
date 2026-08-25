"""Whole-frame terminal-width invariant, every engine (FR7).

`.claude/rules/tui-behavior.md` states it plainly: *the frame must never
exceed the terminal width*. Until this suite existed only NFSv4.1 was held to
that - `tests/test_render_navigation.py` checks it for that engine alone - and
four engines drifted:

* header/meta lines are drawn OUTSIDE the box machinery, so they were printed
  untruncated. With realistic values (cluster `selab-var-203`, VMS
  `var203.selab.vastdata.com:443`, sw_version `5.4.6.0.2628322`) the widest
  line measured 101 columns on NFSv3, 100 on NVMe, 99 on SMB and 98 on S3 -
  all overflow a *standard 80-column terminal*, not merely a narrow one;
* box titles were never truncated, so they overflowed below ~26 columns;
* NFSv3's grand-total COMBINED line is drawn outside the boxes too.

An overflowing line wraps, shifting every row beneath it and corrupting the
frame - the exact failure the box renderers already guard against.

Frames are composed from REAL engine state driven against the mock VMS, not
hand-built row dicts, so the measured lines are the ones an operator sees.
Colour is exercised too: `truncate_display` used to count escape bytes
against the width budget, so a coloured 80-column header rendered only 50
visible columns while the same header was complete with colour off. Note
that a width assertion alone cannot catch that bug - eating the budget makes
lines NARROWER - so the colour cases compare visible content line by line.

Coverage, stated exactly rather than as "everything": every engine, at
24/40/60/80/100/120/160 columns, across the dashboard, startup,
drill-loading, drill-error and drill-header states each engine actually has,
plus the NFSv4.1 exporter and delegation panels and the rows-empty waiting
frame where those states exist; colour at 80 and 40. NOT covered: populated
drill row tables (they need engine-specific row shapes), the scoped
`| clients …` / `| buckets …` / `| tenants …` title variants, and any real
terminal - no committed harness drives a PTY.
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
# The observed sw_version shape is dotted (tests/mock_vms.py, D-013
# records var203 as vast-os-release-5.4.6.0). An earlier revision of
# this fixture used the BUILD id "release-5.4.6-2628322", which is not
# the field the header renders: format_os_release trims on dots, so it
# passed through whole and inflated the rendered label from 23 to 37
# columns - and inflated the overflow figures recorded with it.
REAL_OS = "5.4.6.0.2628322"

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
    real_width = vast_common.terminal_width
    buf, real_stdout = io.StringIO(), sys.stdout
    # Patch the real width seam, not shutil: vast_common.terminal_width
    # asks the tty first, so under `pytest -s` (stdout IS a terminal)
    # patching shutil alone left these suites rendering at the real
    # terminal width and quietly testing nothing.
    vast_common.terminal_width = lambda fallback, cap, c=columns: min(c, cap)
    shutil.get_terminal_size = lambda fallback=(80, 24), c=columns: (
        os.terminal_size((c, 40)))
    sys.stdout = buf
    try:
        module._render_frame()
    finally:
        sys.stdout = real_stdout
        shutil.get_terminal_size = real_size
        vast_common.terminal_width = real_width
    return buf.getvalue()


def assert_fits(module, columns, label):
    frame = tui_layout.strip_ansi(render(module, columns))
    for line in frame.split("\n"):
        assert tui_layout.display_width(line) <= columns, (
            f"{label} at {columns} columns: line of "
            f"{tui_layout.display_width(line)} columns wraps and corrupts the "
            f"frame: {line.strip()[:80]!r}")


_VOLATILE = ("VMS", "PORT", "CLUSTER_NAME", "CLUSTER_OS", "LAST_ROWS",
             "STARTUP_STATUS", "DRILL_STATUS", "DRILL_ERROR", "DRILL_MODE",
             "LAST_DRILL_ROWS", "EXPORTER_MODE", "EXPORTER_STATUS",
             "DELEG_RESULT", "DELEG_PROMPT", "DELEG_STATUS")


@pytest.fixture(params=sorted(ENGINES))
def engine(request, tmp_path, monkeypatch):
    """A real engine with real telemetry state and realistic display values.

    Every module global this fixture touches is snapshotted and restored:
    these are real engine modules shared by the whole session, and leaving
    them pointing at a dead mock made later suites depend on incidental
    ordering.
    """
    name = request.param
    server = MockVMS(certdir=str(tmp_path)).start()
    module = importlib.import_module(name)
    saved = {a: getattr(module, a) for a in _VOLATILE if hasattr(module, a)}
    try:
        monkeypatch.setenv("VAST_TOKEN", "test-token")
        module.init_config(_args(server.port, name))
        module.CLUSTER_ID, module.CLUSTER_NAME = module.get_current_cluster()
        getattr(module, ENGINES[name])()
        module.poll_tick()
        # Display-only values; nothing about telemetry or API behaviour changes.
        module.VMS = REAL_VMS
        module.CLUSTER_NAME = REAL_CLUSTER
        assert hasattr(module, "CLUSTER_OS"), f"{name} lost its CLUSTER_OS header field"
        module.CLUSTER_OS = REAL_OS
        yield name, module
    finally:
        try:
            module.cleanup()
            module._CLEANED_UP = False
            # Standing repository guarantee: no session leaves a monitor behind.
            assert server.live_monitors() == {}, (
                f"{name} leaked monitors: {sorted(server.live_monitors())}")
        finally:
            for attr, value in saved.items():
                setattr(module, attr, value)
            vast_common.close_connection()
            server.stop()


def telemetry_rows(module):
    """Flatten whichever shape this engine holds into a list of real rows.

    `assert module.LAST_ROWS` is vacuous for the dict-backed engines: their
    snapshot is a keyed dict that is truthy even when every list inside it is
    empty, so mock drift could empty the dashboards while the suite stayed
    green and silently degraded to checking headers only.
    """
    rows = module.LAST_ROWS
    if isinstance(rows, list):
        return rows
    return [r for value in rows.values() if isinstance(value, list) for r in value]

def engine_states(name, module):
    """Every frame state this engine can actually reach, as (label, apply,
    restore) triples. Structured this way rather than one test per state so
    that an engine lacking a state contributes no skipped test - the gate
    treats skips as a failure signal.

    Only states the engine's renderer really branches on are included, and
    `expected_states` asserts the list did not silently shrink.
    """
    import vast_drill

    states = [("dashboard", lambda: None, lambda: None)]
    if hasattr(module, "STARTUP_STATUS"):
        states.append((
            "startup",
            lambda: setattr(module, "STARTUP_STATUS",
                            f"Connecting to {REAL_VMS}:443, please stand by..."),
            lambda: setattr(module, "STARTUP_STATUS", None)))
    if hasattr(module, "DRILL_STATUS"):
        # The accepted cold-entry wording is long by design; it may be fitted
        # to the terminal, but its semantics live in vast_drill. "tenant" is
        # the longest mode an operator actually enters.
        states.append((
            "drill-loading",
            lambda: setattr(module, "DRILL_STATUS",
                            vast_drill.loading_message("tenant", first_time=True)),
            lambda: setattr(module, "DRILL_STATUS", None)))
    # A drill-mode header carries an extra "| VIEW DRILL" / "- HOST INITIATOR
    # VIEW" tag - roughly 16 more columns on the widest line the fix targets,
    # so it must be width-checked. Rows are left empty on purpose: the row
    # table needs engine-specific shapes, while the header tag does not.
    if hasattr(module, "DRILL_MODE"):
        drill_mode = "host" if name == "nvme_tcp" else "view"

        def _drill():
            module.DRILL_MODE = drill_mode
            if hasattr(module, "LAST_DRILL_ROWS"):
                module.LAST_DRILL_ROWS = []

        states.append(("drill-header", _drill,
                       lambda: setattr(module, "DRILL_MODE", None)))
    if _renders_drill_error(module):
        states.append((
            "drill-error",
            lambda: setattr(
                module, "DRILL_ERROR",
                f"Cannot fetch view objects: GET https://{REAL_VMS}:443"
                f"/api/views/ failed: HTTP 500: internal server error"),
            lambda: setattr(module, "DRILL_ERROR", None)))
    if name == "nfs_v41":
        # Three of this engine's frame branches are its own: the exporter
        # panels and the delegation panel. Without these the sweep re-rendered
        # the dashboard under another label and proved nothing new.
        states.append((
            "exporter",
            lambda: setattr(module, "EXPORTER_MODE", "native"),
            lambda: setattr(module, "EXPORTER_MODE", None)))
        states.append((
            "delegation",
            lambda: setattr(module, "DELEG_RESULT", {
                "path": "/kmacs/nfstest/nfs41_loadgen/attr_stress.txt",
                "state": "empty", "records": [], "count": 0, "truncated": False,
                "tenant": "default", "queried_at": "12:00:00", "message": "m"}),
            lambda: setattr(module, "DELEG_RESULT", None)))
    if isinstance(module.LAST_ROWS, list):
        # Only the list-backed engines have a real rows-empty frame; the
        # dict-backed ones show the startup box before data arrives, and a
        # bare empty dict would exercise no state an operator can reach.
        saved = {}

        def _empty():
            saved["rows"] = module.LAST_ROWS
            module.LAST_ROWS = []

        states.append(("waiting", _empty,
                       lambda: setattr(module, "LAST_ROWS", saved["rows"])))
    return states


def _renders_drill_error(module):
    """Does this engine's frame renderer actually branch on DRILL_ERROR?

    NFSv4.1 does not - it branches on DRILL_MODE/DRILL_STATUS only - so
    setting DRILL_ERROR there re-rendered the dashboard under a different
    label, adding duplicate assertions and no coverage.
    """
    import inspect

    if not hasattr(module, "DRILL_ERROR"):
        return False
    return "DRILL_ERROR" in inspect.getsource(module._render_frame)


def expected_states(name):
    """The states each engine must contribute. A renamed global would
    otherwise silently drop a state and leave the suite green with fewer
    assertions - the `hasattr` gates degrade quietly by design."""
    common = {"dashboard", "startup", "drill-loading", "drill-header"}
    if name == "nfs_v41":
        return common | {"exporter", "delegation"}
    if name in ("nfs_v3", "nvme_tcp"):
        return common | {"drill-error", "waiting"}
    return common | {"drill-error"}


def test_engine_states_did_not_silently_shrink(engine):
    name, module = engine
    labels = {label for label, _a, _r in engine_states(name, module)}
    assert expected_states(name) <= labels, (
        f"{name} lost frame states: missing {expected_states(name) - labels}")


def test_fixture_produced_real_telemetry(engine):
    name, module = engine
    assert telemetry_rows(module), (
        f"{name}: fixture produced no telemetry rows - the width sweep would "
        f"degrade to checking headers against an empty dashboard")


@pytest.mark.parametrize("columns", WIDTHS)
def test_no_frame_state_exceeds_the_terminal(engine, columns):
    name, module = engine
    assert telemetry_rows(module), f"{name}: fixture produced no telemetry rows"
    for label, apply, restore in engine_states(name, module):
        apply()
        try:
            assert_fits(module, columns, f"{name} {label}")
        finally:
            restore()


@pytest.mark.parametrize("columns", [80, 40])
def test_frame_fits_with_colour_enabled_too(engine, columns):
    """Width only - the content comparison below is what actually pins the
    escape-budget bug, since that bug made lines NARROWER and so satisfied
    any width bound."""
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


@pytest.mark.parametrize("columns", [80, 40])
def test_colour_does_not_change_any_visible_line(engine, columns):
    """The 80-column NFSv4.1 header rendered 50 visible columns with colour
    on and 80 with it off - the same frame showed different information.

    Compares EVERY line of EVERY state, not just line 0: for most engines
    line 0 is a short title that fits, so a line-0 comparison passed both
    before and after the fix.
    """
    name, module = engine
    for label, apply, restore in engine_states(name, module):
        apply()
        try:
            plain = tui_layout.strip_ansi(render(module, columns)).split("\n")
            tui_layout.set_color(True)
            module._COLOR = True
            try:
                coloured = tui_layout.strip_ansi(
                    render(module, columns)).split("\n")
            finally:
                tui_layout.set_color(False)
                module._COLOR = False
        finally:
            restore()
        assert len(plain) == len(coloured), (
            f"{name} {label}: colour changed the line count")
        for index, (p, q) in enumerate(zip(plain, coloured)):
            # Trailing whitespace only: nav_legend_lines rstrips the assembled
            # legend, and with colour the line ends in a reset escape so the
            # space before it survives. Pre-existing (unchanged at d527ddc),
            # invisible on screen, and inside the width budget - so compare
            # visible content, not trailing padding.
            assert p.rstrip() == q.rstrip(), (
                f"{name} {label} line {index} at {columns} columns: colour "
                f"changed visible content:\n  plain    {p!r}\n  coloured {q!r}")


def test_the_colour_comparison_is_not_vacuous(engine):
    """If nothing truncates at 80 columns the comparison above proves
    nothing, so assert truncation really is happening there."""
    name, module = engine
    frame = tui_layout.strip_ansi(render(module, 80))
    assert "…" in frame, (
        f"{name}: nothing truncated at 80 columns, so the colour-parity "
        f"comparison would be vacuous")


def test_wide_terminals_are_left_alone(engine):
    """The fix must not truncate anything that already fits: at 200 columns
    every engine's frame is inside its own cap, so no ellipsis may appear and
    the full cluster and VMS names must survive."""
    name, module = engine
    frame = tui_layout.strip_ansi(render(module, 200))
    assert "…" not in frame, f"{name}: truncated at 200 columns"
    assert REAL_CLUSTER in frame, f"{name}: cluster name lost at 200 columns"
    assert REAL_VMS in frame, f"{name}: VMS name lost at 200 columns"


def test_every_engine_is_covered():
    """A new engine must not silently escape the invariant.

    Discovers engines by a property they actually have - a module-level
    `_render_frame` and `poll_tick` at the repository root - rather than by
    a hardcoded filename list, which made this a comparison of one constant
    against another.
    """
    import glob

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    found = set()
    for path in glob.glob(os.path.join(root, "*.py")):
        with open(path) as handle:
            source = handle.read()
        if "def _render_frame(" in source and "def poll_tick(" in source:
            found.add(os.path.basename(path)[:-3])
    assert found == set(ENGINES), (
        f"engines missing from the width sweep: {found - set(ENGINES)}; "
        f"stale entries: {set(ENGINES) - found}")
