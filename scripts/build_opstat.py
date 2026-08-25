#!/usr/bin/env python3
"""Build a standalone opstat binary with PyInstaller.

Produces a one-file executable under releases/, named:
  opstat-<system>-<machine>[.exe]

Native to the host OS/arch. For Linux, macOS, and Windows artifacts, run this
script on each platform or use the GitHub Actions release workflow.
"""

from __future__ import annotations

import argparse
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENTRY = ROOT / "opstat"
RELEASES = ROOT / "releases"
DIST = ROOT / "dist"
BUILD = ROOT / "build"

HIDDEN_IMPORTS = [
    "opstat_version",
    "nfs_v3",
    "nfs_v41",
    "nvme_tcp",
    "smb",
    "s3",
    "wizard",
    "vast_common",
    "vast_api_log",
    "openmetrics",
    "tui_layout",
]


def artifact_name() -> str:
    system = platform.system().lower()
    machine = platform.machine().lower()
    # Normalize common values
    if machine in {"amd64", "x86_64"}:
        machine = "x86_64"
    elif machine in {"aarch64", "arm64"}:
        machine = "arm64"
    if system == "darwin":
        system = "macos"
    name = f"opstat-{system}-{machine}"
    if system == "windows" or sys.platform.startswith("win"):
        name += ".exe"
    return name


def ensure_pyinstaller() -> None:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("ERROR: PyInstaller is not installed.", file=sys.stderr)
        print("  pip install pyinstaller", file=sys.stderr)
        sys.exit(1)


def build(name: str, clean: bool) -> Path:
    ensure_pyinstaller()
    if not ENTRY.exists():
        print(f"ERROR: entrypoint not found: {ENTRY}", file=sys.stderr)
        sys.exit(1)

    RELEASES.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--onefile",
        "--console",
        "--name",
        "opstat",
        "--paths",
        str(ROOT),
        "--distpath",
        str(DIST),
        "--workpath",
        str(BUILD),
        "--specpath",
        str(BUILD),
        "--noconfirm",
    ]
    if clean:
        cmd.append("--clean")
    for mod in HIDDEN_IMPORTS:
        cmd.extend(["--hidden-import", mod])
    cmd.append(str(ENTRY))

    # dist/ is NOT cleaned by --clean (that clears PyInstaller's cache and
    # work directory only), so a previous build's binary can sit there and be
    # packaged as if it were new. Observed before this guard: with PyInstaller
    # exiting 0 but writing nothing, the PREVIOUS dist/opstat was copied to
    # releases/ and reported as "Created" - a stale artifact presented as a
    # successful build, which a release would then publish.
    #
    # Removing the expected output first makes its existence afterwards the
    # proof of freshness. That is deterministic, unlike comparing mtimes
    # against the wall clock: filesystem timestamp granularity, clock skew on
    # a network mount, and PyInstaller reusing an unchanged EXE under
    # --no-clean can all make a perfectly good binary look stale.
    expected = DIST / ("opstat.exe" if sys.platform.startswith("win") else "opstat")
    for leftover in list(DIST.glob("opstat*")) if DIST.exists() else []:
        if leftover.is_file():
            leftover.unlink()

    print("Running:", " ".join(cmd))
    subprocess.check_call(cmd, cwd=str(ROOT))

    built = expected
    if not built.exists():
        # Fallback: whatever PyInstaller actually wrote this run. Files only -
        # a leftover directory (e.g. from a --onedir spec) would otherwise be
        # selected and fail later with IsADirectoryError.
        candidates = [p for p in DIST.glob("opstat*") if p.is_file()]
        if not candidates:
            print("ERROR: PyInstaller did not produce dist/opstat "
                  "(nothing new in dist/ after the build)", file=sys.stderr)
            sys.exit(1)
        built = max(candidates, key=lambda p: p.stat().st_mtime)

    if built.stat().st_size == 0:
        print(f"ERROR: {built} is empty", file=sys.stderr)
        sys.exit(1)

    dest = RELEASES / name
    shutil.copy2(built, dest)
    dest.chmod(dest.stat().st_mode | 0o111)
    print(f"Created: {dest}")
    return dest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build opstat with PyInstaller")
    parser.add_argument(
        "--name",
        default=None,
        help="Output filename under releases/ (default: opstat-<os>-<arch>)",
    )
    parser.add_argument(
        "--no-clean",
        action="store_true",
        help="Do not pass --clean to PyInstaller",
    )
    args = parser.parse_args(argv)
    build(args.name or artifact_name(), clean=not args.no_clean)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
