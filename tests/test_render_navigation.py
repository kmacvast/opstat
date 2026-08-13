"""Navigation-footer rendering regression tests.

The NFSv4.1 frame renderer returned early once a drill panel had been drawn,
so entering VIEW / CNODE / TENANT drill mode left the dashboard with no
visible controls: the drill box's "Press x to return to cluster view" was the
only hint, and q/o/l/n/c/v/t/space vanished. The footer is now owned by the
common rendering path, and these tests assert that for every mode, at a
range of terminal widths, in every engine that draws one.
"""

from __future__ import annotations

import io
import sys

import pytest

import tui_layout


def render_frame(module, columns=120, lines=40):
    """Capture one composed frame from an engine's _render_frame()."""
    real_size = __import__("shutil").get_terminal_size
    buf, real_stdout = io.StringIO(), sys.stdout
    import shutil

    shutil.get_terminal_size = lambda fallback=(80, 24): __import__(
        "os").terminal_size((columns, lines))
    sys.stdout = buf
    try:
        module._render_frame()
    finally:
        sys.stdout = real_stdout
        shutil.get_terminal_size = real_size
    return tui_layout.strip_ansi(buf.getvalue())


@pytest.fixture
def v41(monkeypatch):
    """nfs_v41 primed with plausible rendered state, no VMS needed."""
    import nfs_v41

    tui_layout.set_color(False)
    nfs_v41._COLOR = False
    nfs_v41.VMS, nfs_v41.PORT = "10.0.0.1", 443
    nfs_v41.CLUSTER_NAME, nfs_v41.CLUSTER_OS = "c1", "5.5.0.1"
    nfs_v41.REFRESH_SECONDS = 5
    nfs_v41.API_TIME_FRAME = "10m"
    nfs_v41.LAST_SAMPLE = "2026-08-13T14:08:00Z"
    nfs_v41.LAST_ROWS = {
        "data": [
            {"key": "read", "label": "READ", "ops_sec": 100.0, "avg_us": 900.0,
             "bw_mbs": 1200.0, "avg_io_bytes": 65536.0, "pct": 60.0},
            {"key": "write", "label": "WRITE", "ops_sec": 66.0, "avg_us": 800.0,
             "bw_mbs": 800.0, "avg_io_bytes": 65536.0, "pct": 40.0},
        ],
        "stateful": [], "state": [], "session": [],
        "meta": {"md_iops": 40.0, "rd_md_iops": 20.0, "wr_md_iops": 20.0},
    }
    nfs_v41.LAST_DRILL_ROWS = [
        {"name": "/view/317", "total_ops": 12.5, "latency_us": 900.0,
         "bw_gbs": 1.25, "top_rpc": "READ", "top_rpc_pct": 55.0},
        {"name": "/view/288", "total_ops": 4.5, "latency_us": 1200.0,
         "bw_gbs": 0.5, "top_rpc": "WR MD", "top_rpc_pct": 48.0},
    ]
    nfs_v41.DRILL_OBJECTS = [{"id": 1, "name": "/view/317"},
                             {"id": 2, "name": "/view/288"}]
    yield nfs_v41
    nfs_v41.DRILL_MODE = None
    nfs_v41.DRILL_ERROR = None
    nfs_v41.DRILL_STATUS = getattr(nfs_v41, "DRILL_STATUS", None)
    nfs_v41.LAST_DRILL_ROWS = []


ALL_MODES = [None, "view", "cnode", "tenant"]


@pytest.mark.parametrize("mode", ALL_MODES)
def test_navigation_footer_present_in_every_mode(v41, mode):
    v41.DRILL_MODE = mode
    frame = render_frame(v41)
    assert "[q] Quit" in frame, f"quit control missing in mode={mode}"
    assert "[x] Exit drill" in frame, f"exit-drill control missing in mode={mode}"


@pytest.mark.parametrize("mode", ALL_MODES)
@pytest.mark.parametrize("key,label", [
    ("q", "Quit"), ("o", "Ops"), ("l", "Lat"), ("n", "Name"),
    ("c", "cNode"), ("v", "View"), ("t", "Tenant"),
    ("x", "Exit drill"), ("space", "Refresh"),
])
def test_every_control_is_listed_in_every_mode(v41, mode, key, label):
    v41.DRILL_MODE = mode
    frame = render_frame(v41)
    assert f"[{key}] {label}" in frame, (
        f"control [{key}] {label} missing in mode={mode}"
    )


@pytest.mark.parametrize("mode", ALL_MODES)
def test_footer_is_the_last_rendered_line(v41, mode):
    """The drill panel must not be the last thing on screen."""
    v41.DRILL_MODE = mode
    lines = [ln for ln in render_frame(v41).split("\n") if ln.strip()]
    assert "[q] Quit" in lines[-1], (
        f"footer is not the final line in mode={mode}: {lines[-1]!r}"
    )


@pytest.mark.parametrize("mode", ALL_MODES)
@pytest.mark.parametrize("columns", [200, 120, 100, 80, 60, 40, 24, 10])
def test_narrow_terminals_never_drop_the_footer_entirely(v41, mode, columns):
    """Truncation may shorten the control list but must never erase it."""
    v41.DRILL_MODE = mode
    frame = render_frame(v41, columns=columns)
    assert "[q]" in frame, (
        f"footer vanished at {columns} columns in mode={mode}"
    )


@pytest.mark.parametrize("columns", [200, 120, 80, 40, 24, 10])
def test_frame_never_exceeds_the_terminal_width(v41, columns):
    v41.DRILL_MODE = "view"
    for line in render_frame(v41, columns=columns).split("\n"):
        assert tui_layout.display_width(line) <= max(columns, v41._MIN_FRAME_WIDTH), (
            f"line wider than terminal at {columns} cols: {line!r}"
        )


def test_drill_panel_still_shows_its_own_exit_hint(v41):
    v41.DRILL_MODE = "view"
    frame = render_frame(v41)
    assert "Press x to return to cluster view" in frame
    assert "[x] Exit drill" in frame


def test_footer_survives_drill_error_and_status_panels(v41):
    v41.DRILL_MODE = "view"
    v41.DRILL_ERROR = "Cannot fetch view objects: HTTP 400"
    assert "[q] Quit" in render_frame(v41)
    v41.DRILL_ERROR = None
    v41.LAST_DRILL_ROWS = []
    assert "[q] Quit" in render_frame(v41)


# ---------------------------------------------------------------------------
# The other engines draw their own footers; guard them against the same bug.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("engine_name,expected", [
    ("nfs_v3", ["q", "x"]),
    ("smb", ["[q]", "[x]"]),
    ("s3", ["[q]", "[x]"]),
])
def test_other_engines_keep_controls_visible_in_drill_mode(engine_name, expected):
    import importlib

    module = importlib.import_module(engine_name)
    tui_layout.set_color(False)
    module._COLOR = False
    module.VMS, module.PORT = "10.0.0.1", 443
    module.CLUSTER_NAME = "c1"
    module.REFRESH_SECONDS = 5
    module.DRILL_MODE = "view"
    module.LAST_DRILL_ROWS = [{
        "name": "/v", "total_ops": 1.0, "latency_us": 900.0, "bw_gbs": 0.5,
        "top_rpc": "READ", "top_rpc_pct": 50.0,
    }]
    if engine_name == "nfs_v3":
        module.LAST_ROWS = [
            {"label": "READ", "ops_sec": 10.0, "avg_us": 900.0, "pct": 100.0,
             "bw_gbs": 0.5, "run_min_us": 1.0, "run_max_us": 2.0,
             "run_mean_us": 1.5, "bw_min_gbs": 0.1, "bw_max_gbs": 0.9,
             "avg_io_bytes": 65536.0},
        ]
        module.PREV_ROWS = []
    try:
        frame = render_frame(module)
    finally:
        module.DRILL_MODE = None
        module.LAST_DRILL_ROWS = []
    for token in expected:
        assert token in frame, f"{engine_name} drill frame lost {token!r}"
