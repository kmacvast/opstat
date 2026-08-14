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


def render_frame(module, columns=200, lines=40):
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
    """nfs_v41 primed with plausible rendered state, no VMS needed.

    The exporter drill panels read the module-global NFS4 / HOSTVIEW
    collectors, which init_config normally constructs. This fixture builds
    the same collector objects itself (never scraped, so they render their
    warm-up/empty states) rather than depending on another test having run
    init_config first - these tests must pass in isolation, not just in
    full-suite order.
    """
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
# Exporter-backed drills render through a different path and must keep the
# same footer.
EXPORTER_MODES = ["native", "hosts"]


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
    ("4", "Native v4"), ("h", "v4 hosts"),
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
@pytest.mark.parametrize("columns", [240, 200, 140, 120, 100, 80, 60, 40, 24, 10])
def test_narrow_terminals_never_drop_the_footer_entirely(v41, mode, columns):
    """Truncation may shorten the control list but must never erase it."""
    v41.DRILL_MODE = mode
    frame = render_frame(v41, columns=columns)
    assert "[q]" in frame, (
        f"footer vanished at {columns} columns in mode={mode}"
    )


@pytest.mark.parametrize("columns", [240, 200, 140, 120, 80, 40, 24, 10])
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
    ("nfs_v3", ["[q]", "[x] Exit drill"]),
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



# ---------------------------------------------------------------------------
# Startup / waiting frame: the pre-data frame must own the footer too.
# nfs_v3 and nvme_tcp used a bare `print("Waiting for data…"); return` that
# bypassed the footer entirely - the exact early-return pattern this suite
# guards against. Fails on that pre-change code.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("engine_name,token", [
    ("nfs_v3", "[q]"),
    ("nvme_tcp", "[q]"),
])
@pytest.mark.parametrize("columns", [200, 120, 80, 40])
def test_waiting_frame_keeps_the_footer(engine_name, token, columns):
    import importlib

    module = importlib.import_module(engine_name)
    tui_layout.set_color(False)
    module._COLOR = False
    module.VMS, module.PORT = "10.0.0.1", 443
    module.CLUSTER_NAME = "c1"
    module.REFRESH_SECONDS = 5
    module.DRILL_MODE = None
    module.LAST_ROWS = []          # no data yet -> the waiting frame
    if hasattr(module, "STARTUP_STATUS"):
        module.STARTUP_STATUS = None
    frame = render_frame(module, columns=columns)
    assert token in frame, (
        f"{engine_name} waiting frame dropped the footer token {token!r} at {columns}c")


@pytest.mark.parametrize("engine_name,token", [
    ("nfs_v3", "[q]"),
    ("nfs_v41", "[q]"),
    ("smb", "[q]"),
    ("s3", "[q]"),
    ("nvme_tcp", "[q]"),
])
def test_startup_status_frame_keeps_the_footer(engine_name, token):
    """The startup interstitial frame must render the nav footer too."""
    import importlib

    module = importlib.import_module(engine_name)
    tui_layout.set_color(False)
    module._COLOR = False
    module.VMS, module.PORT = "10.0.0.1", 443
    module.CLUSTER_NAME = None      # earliest phase: cluster not resolved yet
    module.REFRESH_SECONDS = 5
    module.DRILL_MODE = None
    module.LAST_ROWS = []
    module.STARTUP_STATUS = "Connecting to 10.0.0.1:443, please stand by..."
    try:
        frame = render_frame(module)
    finally:
        module.STARTUP_STATUS = None
    assert "Connecting to 10.0.0.1:443" in frame, f"{engine_name} lost the startup message"
    assert token in frame, f"{engine_name} startup frame dropped the footer token {token!r}"


# ---------------------------------------------------------------------------
# Exporter-backed drills (native NFSv4 / client attribution)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("mode", EXPORTER_MODES)
def test_footer_present_in_exporter_drill_modes(v41, mode):
    v41.EXPORTER_MODE = mode
    try:
        frame = render_frame(v41)
    finally:
        v41.EXPORTER_MODE = None
    assert "[q] Quit" in frame
    assert "[x] Exit drill" in frame
    assert "[4] Native v4" in frame
    assert "[h] v4 hosts" in frame


@pytest.mark.parametrize("mode", EXPORTER_MODES)
def test_footer_is_last_line_in_exporter_drill_modes(v41, mode):
    v41.EXPORTER_MODE = mode
    try:
        lines = [l for l in render_frame(v41).split("\n") if l.strip()]
    finally:
        v41.EXPORTER_MODE = None
    assert "[q] Quit" in lines[-1]


def test_scraping_status_is_visible_before_the_blocking_scrape(v41):
    """A real scrape costs seconds; the user must see progress, not a hang."""
    v41.EXPORTER_MODE = "native"
    v41.EXPORTER_STATUS = "Scraping native NFSv4 telemetry, stand by..."
    try:
        frame = render_frame(v41)
    finally:
        v41.EXPORTER_MODE = v41.EXPORTER_STATUS = None
    assert "Scraping native NFSv4 telemetry" in frame
    assert "[q] Quit" in frame, "footer lost while showing the status frame"


@pytest.mark.parametrize("mode", EXPORTER_MODES)
@pytest.mark.parametrize("columns", [240, 140, 120, 80, 40, 24])
def test_exporter_drills_never_drop_the_footer(v41, mode, columns):
    v41.EXPORTER_MODE = mode
    try:
        frame = render_frame(v41, columns=columns)
    finally:
        v41.EXPORTER_MODE = None
    assert "[q]" in frame


# ---------------------------------------------------------------------------
# FR-A: the canonical cross-protocol navigation contract.
# Same concept -> same key, same label, same relative order, in every engine.
# Protocol-specific controls come after the common set. VIP is never [v];
# exit-drill is never [p] (the old NVMe bindings that survived in help text
# after the keys themselves changed).
# ---------------------------------------------------------------------------
ENGINE_MODULES = ["nfs_v3", "nfs_v41", "smb", "s3", "nvme_tcp"]


def _controls(engine_name):
    import importlib

    return importlib.import_module(engine_name)._NAV_CONTROLS


@pytest.mark.parametrize("engine_name", ENGINE_MODULES)
def test_common_controls_use_canonical_keys_labels_and_order(engine_name):
    import vast_drill

    canonical = dict(vast_drill.CANONICAL_CONTROLS)
    order = {k: i for i, (k, _l) in enumerate(vast_drill.CANONICAL_CONTROLS)}
    controls = _controls(engine_name)
    common = [(k, l) for k, l in controls if k in canonical]
    # Canonical labels, exactly.
    for key, label in common:
        assert label == canonical[key], (
            f"{engine_name} labels [{key}] as {label!r}, contract says {canonical[key]!r}")
    # Canonical relative order.
    keys = [k for k, _l in common]
    assert keys == sorted(keys, key=order.get), (
        f"{engine_name} common controls out of canonical order: {keys}")
    # Protocol-specific controls strictly after the common set.
    tail = [(k, l) for k, l in controls if k not in canonical]
    assert list(controls) == common + tail, (
        f"{engine_name} interleaves protocol-specific controls with common ones")


@pytest.mark.parametrize("engine_name", ENGINE_MODULES)
def test_vip_is_never_v_and_exit_is_never_p(engine_name):
    for key, label in _controls(engine_name):
        if key == "v":
            assert label == "View", f"{engine_name} binds [v] to {label!r}; v means View"
        assert key != "p", f"{engine_name} still advertises the retired [p] binding"
        if label == "VIP":
            assert key == "i", f"{engine_name} binds VIP to [{key}]; VIP is [i]"
        if label == "Exit drill":
            assert key == "x", f"{engine_name} binds exit-drill to [{key}]; exit is [x]"


def test_every_engine_shares_one_quit_exit_refresh_triple():
    """The three controls every engine has must be identical everywhere."""
    for engine_name in ENGINE_MODULES:
        controls = dict(_controls(engine_name))
        assert controls.get("q") == "Quit", engine_name
        assert controls.get("x") == "Exit drill", engine_name
        assert controls.get("space") == "Refresh", engine_name


@pytest.mark.parametrize("engine_name,token", [
    ("nfs_v3", "[space] Refresh"),
    ("nfs_v41", "[space] Refresh"),
    ("smb", "[space] Refresh"),
    ("s3", "[space] Refresh"),
])
def test_shared_legend_renders_in_engine_footers(engine_name, token):
    """The rendered footer text comes from the shared legend renderer."""
    import importlib

    import vast_drill

    module = importlib.import_module(engine_name)
    tui_layout.set_color(False)
    legend = tui_layout.strip_ansi(vast_drill.nav_legend(module._NAV_CONTROLS))
    assert token in legend
    assert "|" in legend


# ---------------------------------------------------------------------------
# FR-A regression: the footer must WRAP, never silently drop controls.
#
# Observed on a real laptop terminal: the footer showed only
#   [q] Quit |[o] Ops |[l] Lat
# while c/v/t/x still worked - right-truncation made working controls
# undiscoverable. The legend now wraps onto continuation lines; a control an
# engine supports must be visible at any ordinary width.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("engine_name", ENGINE_MODULES)
@pytest.mark.parametrize("columns", [200, 100, 80, 60])
def test_every_supported_control_is_visible_at_width(engine_name, columns):
    import importlib

    module = importlib.import_module(engine_name)
    tui_layout.set_color(False)
    module._COLOR = False
    module.VMS, module.PORT = "10.0.0.1", 443
    module.CLUSTER_NAME = "c1"
    module.REFRESH_SECONDS = 5
    module.DRILL_MODE = None
    module.LAST_ROWS = []
    # The startup frame goes through the same footer-owning render path as
    # the live dashboard, without needing full per-engine row state.
    module.STARTUP_STATUS = "Connecting to 10.0.0.1:443, please stand by..."
    try:
        frame = render_frame(module, columns=columns)
    finally:
        module.STARTUP_STATUS = None
    for key, label in module._NAV_CONTROLS:
        assert f"[{key}]" in frame, (
            f"{engine_name}: control [{key}] {label} invisible at "
            f"{columns} columns - a working key must stay discoverable")


def test_the_observed_qol_truncation_cannot_recur():
    """Literal repro of the field report: q/o/l visible, c/v/t/x gone.

    At 30 columns the old single-line renderer truncated NFSv3's legend to
    exactly the reported '[q] Quit |[o] Ops |[l] Lat' prefix. The wrapped
    renderer must keep every control visible at that same width.
    """
    import vast_drill

    import nfs_v3

    tui_layout.set_color(False)
    nfs_v3._COLOR = False
    # The defective rendering, reconstructed: one line, right-truncated.
    old_style = tui_layout.truncate_display(
        tui_layout.strip_ansi(vast_drill.nav_legend(nfs_v3._NAV_CONTROLS)), 28)
    assert "[q]" in old_style and "[o]" in old_style and "[l]" in old_style
    assert "[c]" not in old_style, "repro no longer reproduces the field report"
    # The fixed rendering at the same width: everything survives, wrapped.
    lines = [tui_layout.strip_ansi(l)
             for l in vast_drill.nav_legend_lines(nfs_v3._NAV_CONTROLS, 28)]
    joined = " ".join(lines)
    for key, _label in nfs_v3._NAV_CONTROLS:
        assert f"[{key}]" in joined, f"[{key}] dropped at 28 columns"
    for line in lines:
        assert tui_layout.display_width(line) <= 28


def test_nav_legend_lines_packs_greedily_and_never_drops():
    import vast_drill

    tui_layout.set_color(False)
    controls = vast_drill.CANONICAL_CONTROLS
    for width in (200, 120, 76, 40, 20, 8):
        lines = [tui_layout.strip_ansi(l)
                 for l in vast_drill.nav_legend_lines(controls, width)]
        joined = " ".join(lines)
        for key, _label in controls:
            assert f"[{key}]" in joined, (key, width)
    # Wide enough for everything -> exactly one line, identical content to
    # the unwrapped legend.
    one = vast_drill.nav_legend_lines(controls, 500)
    assert len(one) == 1
    assert tui_layout.strip_ansi(one[0]) == tui_layout.strip_ansi(
        vast_drill.nav_legend(controls))
