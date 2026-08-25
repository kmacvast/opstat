"""Terminal resize behaviour, proven against a real pseudo-terminal.

The repository had no PTY coverage at all - `.claude/rules/tui-behavior.md`
said so explicitly - so resize was the one rendering behaviour nobody had
measured. Measuring it found a real defect.

`shutil.get_terminal_size()` honours the `COLUMNS` environment variable
BEFORE asking the terminal. Measured here, pre-fix, against a running opstat
in a pty: with `COLUMNS=200` exported and the terminal resized 120 -> 80 ->
40, opstat kept emitting 120-column frames and **never converged** - wrapping
and corrupting the display indefinitely. With COLUMNS unset the same sequence
converged within one refresh tick every time. `vast_common.terminal_width`
now asks the terminal's own file descriptor first, and these tests are the
regression.

No SIGWINCH handler exists, and the measurements say none is needed: every
engine re-reads the width inside `_render_frame`, so the next scheduled
repaint adapts, with no keypress, and without ever emitting a frame wider
than the new terminal.

PLATFORM BOUNDARY: `pty.fork` is POSIX-only. The gate runs on macOS
(developer) and ubuntu-latest (CI), so the guard below never fires there.
Windows terminal behaviour is FR5's scope and is NOT covered here.
"""

from __future__ import annotations

import codecs
import os
import re
import select
import shutil
import signal
import struct
import subprocess
import sys
import time
from pathlib import Path

import pytest

import tui_layout
import vast_common
from tests.mock_vms import MockVMS

# importorskip, NOT a module-level import plus a skipif: these are POSIX-only,
# and importing them at module scope made Windows a collection ERROR that
# aborted the whole session, because markers are consulted only after the
# module body has already run.
fcntl = pytest.importorskip("fcntl", reason="POSIX-only; Windows is FR5's scope")
pty = pytest.importorskip("pty", reason="POSIX-only; Windows is FR5's scope")
termios = pytest.importorskip("termios",
                              reason="POSIX-only; Windows is FR5's scope")

pytestmark = pytest.mark.skipif(
    shutil.which("openssl") is None,
    reason="openssl binary required for the mock VMS")

ROOT = Path(__file__).resolve().parent.parent
CSI = re.compile(r"\033\[[0-9;?]*[A-Za-z]")


class PtySession:
    """A real pseudo-terminal running a real child, resizable while it runs.

    Deliberately small: enough to set the window size, read frames and reap
    the child. Every wait is bounded and the child is terminated
    unconditionally on exit, so a hung process cannot stall the gate.
    """

    def __init__(self, argv, env=None, cols=120, rows=40):
        self.argv, self.cols, self.rows = argv, cols, rows
        self.env = dict(os.environ if env is None else env)
        self.buf = ""
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self.pid = self.fd = None

    def __enter__(self):
        pid, fd = pty.fork()
        if pid == 0:                                # pragma: no cover - child
            try:
                os.execve(self.argv[0], self.argv, self.env)
            finally:
                os._exit(127)
        self.pid, self.fd = pid, fd
        self.resize(self.cols, self.rows)
        return self

    def resize(self, cols, rows=None):
        """Resize the terminal under the RUNNING child - the whole point."""
        rows = self.rows if rows is None else rows
        self.cols, self.rows = cols, rows
        fcntl.ioctl(self.fd, termios.TIOCSWINSZ,
                    struct.pack("HHHH", rows, cols, 0, 0))

    def read(self, timeout=0.2):
        end = time.time() + timeout
        while time.time() < end:
            ready, _w, _e = select.select([self.fd], [], [],
                                          max(0.0, end - time.time()))
            if not ready:
                break
            try:
                chunk = os.read(self.fd, 65536)
            except OSError:
                break
            if not chunk:
                break
            # Incremental: a box-drawing character split across two reads
            # would decode to replacement characters and inflate every width.
            self.buf += self._decoder.decode(chunk)
        return self.buf

    def frames(self):
        """COMPLETE frames only.

        Split on the cursor-home that starts each flush_frame write, and keep
        only parts carrying the trailing erase-to-end (``\033[J``) that ends
        one. Accepting partial reads made assertions meaningless: a frame
        still arriving "fits" any width and has no footer yet, so a
        half-written 40-column frame passed a width check and then failed a
        footer check for the wrong reason.

        Split BEFORE stripping CSI - stripping first destroys the boundary.
        """
        return [CSI.sub("", part).replace("\r", "")
                for part in self.buf.split("\033[H")
                if part.strip() and "\033[J" in part]

    def wait_for(self, needle, timeout=60):
        end = time.time() + timeout
        while time.time() < end:
            if needle in CSI.sub("", self.buf):
                return True
            self.read(0.2)
        return needle in CSI.sub("", self.buf)

    def clear(self):
        self.buf = ""

    def close(self):
        for action in (lambda: os.kill(self.pid, signal.SIGTERM),
                       lambda: os.waitpid(self.pid, os.WNOHANG),
                       lambda: os.kill(self.pid, signal.SIGKILL),
                       lambda: os.waitpid(self.pid, 0),
                       lambda: os.close(self.fd)):
            try:
                action()
            except OSError:
                pass

    def __exit__(self, *exc):
        self.close()


def widest(frame):
    return max((tui_layout.display_width(line)
                for line in frame.splitlines()), default=0)


# ---------------------------------------------------------------------------
# The width source itself - cheap, no engine involved
# ---------------------------------------------------------------------------
def width_in_pty(cols, env_extra, resize_to=None):
    """Run vast_common.terminal_width inside a real pty; report what it saw."""
    child = (
        "import sys, time; sys.path.insert(0, %r)\n"
        "import vast_common\n"
        "for _ in range(6):\n"
        "    print('W=%%d' %% vast_common.terminal_width(999, 999)); "
        "sys.stdout.flush(); time.sleep(0.15)\n" % str(ROOT)
    )
    env = dict(os.environ)
    env.pop("COLUMNS", None)
    env.update(env_extra)
    with PtySession([sys.executable, "-c", child], env=env, cols=cols) as session:
        if resize_to is not None:
            session.read(0.3)
            session.resize(resize_to)
        session.read(1.5)
        # Anchored on the newline: a truncated trailing "W=100" could
        # otherwise be read as "W=1".
        return [int(v) for v in re.findall(r"W=(\d+)\r?\n", session.buf)]


def test_terminal_width_uses_the_pty_when_columns_is_absent():
    assert width_in_pty(100, {})[0] == 100


def test_a_stale_columns_does_not_override_the_real_terminal():
    """The defect: shutil.get_terminal_size honours COLUMNS first, so an
    exported stale value pinned opstat to the wrong width permanently."""
    observed = width_in_pty(100, {"COLUMNS": "200"})
    assert observed and set(observed) == {100}, (
        "stale COLUMNS=200 leaked into the width: %s" % observed)


def test_width_follows_a_resize_of_the_same_process():
    """Not two processes started at two sizes - one process, resized."""
    observed = width_in_pty(100, {}, resize_to=60)
    assert observed[0] == 100 and observed[-1] == 60, observed


def test_width_follows_a_resize_even_with_a_stale_columns():
    observed = width_in_pty(100, {"COLUMNS": "200"}, resize_to=60)
    assert 200 not in observed, "stale COLUMNS pinned the width: %s" % observed
    assert observed[-1] == 60, observed


def test_width_falls_back_to_shutil_when_output_is_not_a_terminal():
    """Piped output and the render suites take this path; COLUMNS is the only
    size signal there, and honouring it is correct."""
    import vast_common

    env = dict(os.environ, COLUMNS="77")
    result = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, %r)\n"
         "import vast_common; print(vast_common.terminal_width(999, 999))"
         % str(ROOT)],
        capture_output=True, text=True, env=env, timeout=60)
    assert result.stdout.strip() == "77", result.stdout + result.stderr
    assert callable(vast_common.terminal_width)


# ---------------------------------------------------------------------------
# Every engine must use the fixed width SOURCE
# ---------------------------------------------------------------------------
ENGINES = ("nfs_v3", "nfs_v41", "smb", "s3", "nvme_tcp")


@pytest.mark.parametrize("name", ENGINES)
def test_no_engine_reads_the_width_from_shutil(name):
    """Only SMB is driven end-to-end below, and only its stale-COLUMNS case
    can tell the two width sources apart - so reverting any of the other four
    engines to shutil.get_terminal_size would leave the suite green (the
    width suites patch shutil, so they pass with either source). This pins
    the source itself, cheaply, for all five."""
    source = (ROOT / (name + ".py")).read_text()
    assert "shutil.get_terminal_size" not in source, (
        f"{name} reads the width from shutil, which honours a stale COLUMNS "
        f"and would stop following terminal resizes")
    assert "vast_common.terminal_width(" in source, (
        f"{name} no longer obtains its width from vast_common.terminal_width")


def test_the_width_helper_ignores_stderr():
    """With the frame piped to a file and stderr still on a terminal, the
    terminal's width is not the output's width."""
    import inspect

    source = inspect.getsource(vast_common.terminal_width)
    assert "__stderr__" not in source, (
        "stderr must not be consulted for the frame width")


# ---------------------------------------------------------------------------
# A real engine, resized while it runs
# ---------------------------------------------------------------------------
@pytest.fixture
def engine_pty(tmp_path):
    """A real opstat process against the mock VMS, in a real pty."""
    server = MockVMS(certdir=str(tmp_path)).start()
    sessions = []

    def start(env_extra=None, cols=120, refresh="1"):
        env = dict(os.environ)
        env["VAST_TOKEN"] = "test-token"
        env.pop("COLUMNS", None)
        env.update(env_extra or {})
        argv = [sys.executable, str(ROOT / "opstat"), "--smb",
                "--vms", "127.0.0.1", "--vms-port", str(server.port),
                "--refresh", refresh]
        session = PtySession(argv, env=env, cols=cols)
        # Register BEFORE entering: if __enter__ raises (a failed ioctl, say)
        # the child and the master fd would otherwise leak.
        sessions.append(session)
        session.__enter__()
        assert session.wait_for("SMB HEALTH", timeout=60), (
            "opstat never rendered a dashboard frame:\n"
            + CSI.sub("", session.buf)[-800:])
        return session

    try:
        yield start
    finally:
        for session in sessions:
            session.close()
        server.stop()


# SMB caps its frame at 120 columns and box-draws to exactly the available
# width, so a converged dashboard frame measures exactly min(target, cap).
SMB_FRAME_CAP = 120


def converge(session, target, budget=20.0):
    """Resize, then wait for a frame that actually USES the new width.

    Requires the exact expected width, not merely "fits". Accepting any frame
    <= target made the GROW direction vacuous: after 40 -> 120 a stale
    40-column frame is <= 120, so a mutation where the width only ever
    narrows and never widens back converged instantly and the suite stayed
    green. Verified: that mutation now fails here.

    Returns (frame, seconds, over).
    """
    expected = min(target, SMB_FRAME_CAP)
    session.clear()
    started = time.time()
    session.resize(target)
    over = 0
    while time.time() - started < budget:
        session.read(0.2)
        for frame in session.frames():
            width = widest(frame)
            if width == 0:
                continue
            if width > target:
                over = max(over, width)
            elif width == expected:
                return frame, time.time() - started, over
    return None, None, over


@pytest.mark.parametrize("env_extra,label", [
    ({}, "COLUMNS unset"),
    ({"COLUMNS": "200"}, "stale COLUMNS=200"),
])
def test_a_running_engine_adapts_to_every_resize(engine_pty, env_extra, label):
    """120 -> 80 -> 40 -> 120 on ONE running process.

    Pre-fix, the stale-COLUMNS case never converged: 120-column frames kept
    arriving inside an 80- and then a 40-column terminal.
    """
    session = engine_pty(env_extra=env_extra)
    for target in (80, 40, 120):
        frame, elapsed, over = converge(session, target)
        assert frame is not None, (
            "[%s] no frame fitting %d columns within the budget "
            "(widest seen %d)" % (label, target, over))
        assert widest(frame) == min(target, SMB_FRAME_CAP)
        assert "[q]" in frame, (
            "[%s] footer lost after resize to %d" % (label, target))
        assert over == 0, (
            "[%s] emitted a %d-column frame into a %d-column terminal before "
            "converging" % (label, over, target))
        assert elapsed <= 15.0, "[%s] took %.1fs to adapt" % (label, elapsed)


def test_resize_needs_no_keypress(engine_pty):
    """The refresh tick alone drives the repaint - no input is sent here."""
    session = engine_pty()
    frame, elapsed, _over = converge(session, 60)
    assert frame is not None and widest(frame) == 60
    assert elapsed is not None


def test_the_frame_stays_intact_across_a_resize(engine_pty):
    """Box borders and the header survive, not merely the width bound."""
    session = engine_pty()
    frame, _elapsed, _over = converge(session, 70)
    assert frame is not None
    assert "VAST SMB opstat" in frame, "header lost after resize"
    assert "┌" in frame and "└" in frame, "box borders lost"
    assert "[q]" in frame
