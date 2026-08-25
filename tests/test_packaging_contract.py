"""Packaging contract - the cheap half.

A real PyInstaller build takes minutes and needs a build dependency, so it
does not belong in the per-commit gate. It lives in
`scripts/smoke_packaged_opstat.py`, which the release workflow runs on every
platform it builds, immediately after building and before upload.

What is pinned here is everything that can be checked without building:
artifact naming, the workflow using the same build path, failure and
staleness behaviour, and the module-coverage claim.

Evidence behind these (macOS arm64, PyInstaller 6.22.2, Python 3.12.13):
a real build produced a 9.2 MB `releases/opstat-macos-arm64` whose bundle
contains all 14 first-party modules - including `vast_drill`,
`vast_discovery` and `nfs4_native`, which are NOT in HIDDEN_IMPORTS and are
picked up by static analysis. Every HIDDEN_IMPORTS entry is in fact
redundant; the list is kept as defence in depth, not because it is load-
bearing. The artifact ran with the source tree unreachable and produced
byte-identical `--help` to the source CLI.
"""

from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BUILD_SCRIPT = ROOT / "scripts" / "build_opstat.py"
SMOKE_SCRIPT = ROOT / "scripts" / "smoke_packaged_opstat.py"
WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"


@pytest.fixture
def build_module(monkeypatch):
    """The build script as a REAL module.

    runpy.run_path returns a namespace *copy*, so patching it leaves the
    functions' own globals untouched - the staleness and failure tests below
    silently exercised the real DIST/RELEASES and passed only because
    ensure_pyinstaller() exited first. importlib gives a live module whose
    __dict__ IS the function globals.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "build_opstat_undertest", BUILD_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # PyInstaller is a build-time dependency and is not installed in the test
    # environment; the reliability tests never reach a real invocation.
    monkeypatch.setattr(module, "ensure_pyinstaller", lambda: None)
    return module


def first_party_modules():
    """Every local module reachable from the entrypoint by static import."""
    local = {p.stem for p in ROOT.glob("*.py")}
    files = {m: ROOT / (m + ".py") for m in local}
    files["opstat"] = ROOT / "opstat"
    seen, stack = set(), ["opstat"]
    while stack:
        name = stack.pop()
        if name in seen:
            continue
        seen.add(name)
        tree = ast.parse(files[name].read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                stack += [a.name for a in node.names if a.name in local]
            elif isinstance(node, ast.ImportFrom) and node.module in local:
                stack.append(node.module)
    return seen - {"opstat"}


# ---------------------------------------------------------------------------
# Artifact naming
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("system,machine,expected", [
    ("Darwin", "arm64", "opstat-macos-arm64"),
    ("Darwin", "x86_64", "opstat-macos-x86_64"),
    ("Linux", "x86_64", "opstat-linux-x86_64"),
    ("Linux", "aarch64", "opstat-linux-arm64"),
    ("Linux", "amd64", "opstat-linux-x86_64"),
])
def test_artifact_name_normalises_platform_and_arch(build_module, monkeypatch,
                                                    system, machine, expected):
    monkeypatch.setattr(build_module.platform, "system", lambda: system)
    monkeypatch.setattr(build_module.platform, "machine", lambda: machine)
    monkeypatch.setattr(build_module.sys, "platform", system.lower())
    assert build_module.artifact_name() == expected


def test_windows_artifact_gets_an_exe_suffix(build_module, monkeypatch):
    monkeypatch.setattr(build_module.platform, "system", lambda: "Windows")
    monkeypatch.setattr(build_module.platform, "machine", lambda: "AMD64")
    monkeypatch.setattr(build_module.sys, "platform", "win32")
    assert build_module.artifact_name() == "opstat-windows-x86_64.exe"


def test_workflow_artifact_names_match_what_the_script_produces(build_module,
                                                                monkeypatch):
    """The release matrix passes --name explicitly; those names must still be
    the ones this script would choose, or local and CI builds diverge."""
    names = set(re.findall(r"artifact:\s*(\S+)",
                           "\n".join(active_workflow_lines())))
    assert names, "release workflow declares no artifacts"
    produced = set()
    for system, machine, plat in (("Linux", "x86_64", "linux"),
                                  ("Darwin", "arm64", "darwin"),
                                  ("Windows", "AMD64", "win32")):
        monkeypatch.setattr(build_module.platform, "system", lambda s=system: s)
        monkeypatch.setattr(build_module.platform, "machine", lambda m=machine: m)
        monkeypatch.setattr(build_module.sys, "platform", plat)
        produced.add(build_module.artifact_name())
    assert names == produced, (
        f"workflow artifacts {sorted(names)} != script names {sorted(produced)}")


# ---------------------------------------------------------------------------
# Module coverage
# ---------------------------------------------------------------------------
def test_hidden_imports_name_only_real_modules(build_module):
    """A typo'd hidden import is silently useless - and an emptied list would
    have passed a bare for-loop trivially."""
    assert build_module.HIDDEN_IMPORTS, "HIDDEN_IMPORTS was emptied"
    for name in build_module.HIDDEN_IMPORTS:
        assert (ROOT / (name + ".py")).is_file(), f"{name} is not a local module"


def test_the_pyinstaller_invocation_keeps_its_essential_flags(
        build_module, monkeypatch, tmp_path):
    """--onefile, --paths and the hidden imports are what make the bundle
    self-contained; none of them was pinned."""
    dist, releases = tmp_path / "dist", tmp_path / "releases"
    dist.mkdir()
    captured = {}

    def capture(cmd, **kwargs):
        captured["cmd"] = cmd
        (dist / "opstat").write_bytes(b"binary")
        return 0

    monkeypatch.setattr(build_module, "DIST", dist)
    monkeypatch.setattr(build_module, "RELEASES", releases)
    monkeypatch.setattr(build_module.subprocess, "check_call", capture)
    build_module.build("opstat-flags", clean=True)
    cmd = captured["cmd"]
    assert "--onefile" in cmd, "the build is no longer a single-file bundle"
    assert "--paths" in cmd and str(ROOT) in cmd, (
        "the repository root is no longer on the analysis path")
    for name in build_module.HIDDEN_IMPORTS:
        assert cmd[cmd.index(name) - 1] == "--hidden-import", (
            f"{name} is not passed as a hidden import")
    assert str(build_module.ENTRY) == cmd[-1], "the entrypoint is not last"


# Root-level modules deliberately NOT part of the shipped binary. Empty
# today; a standalone helper would be listed here rather than silently
# failing the reachability assertion below.
NOT_BUNDLED: set = set()


def test_the_not_bundled_allowlist_stays_empty():
    """Adding to it narrows the reachability assertion below, which the
    repository rules treat as weakening a test - it must be a visible act."""
    assert NOT_BUNDLED == set(), (
        f"modules were excluded from the bundle guarantee: {NOT_BUNDLED}")


def test_every_first_party_module_is_reachable_by_static_import():
    """The bundle-coverage claim, checked the way PyInstaller sees it.

    A real build confirmed all 14 are bundled; this keeps the property that
    makes that true - every module reachable from the entrypoint by a plain
    static import - so a future dynamic import (importlib, __import__) that
    PyInstaller cannot follow shows up here rather than as a
    ModuleNotFoundError in a shipped binary.
    """
    reachable = first_party_modules()
    local = {p.stem for p in ROOT.glob("*.py")}
    unreachable = local - reachable - NOT_BUNDLED
    assert not unreachable, (
        f"local modules not statically reachable from the entrypoint: "
        f"{sorted(unreachable)} - PyInstaller would not bundle them")


def test_no_dynamic_import_of_a_first_party_module():
    """importlib/__import__ on a local module would defeat static analysis."""
    offenders = []
    local = {p.stem for p in ROOT.glob("*.py")}
    for path in list(ROOT.glob("*.py")) + [ROOT / "opstat"]:
        text = path.read_text()
        for match in re.finditer(r"(?:importlib\.import_module|__import__)\(\s*"
                                 r"[\"']([\w.]+)[\"']", text):
            if match.group(1).split(".")[0] in local:
                offenders.append(f"{path.name}: {match.group(0)}")
    assert not offenders, (
        "first-party modules imported dynamically would not be bundled:\n  "
        + "\n  ".join(offenders))


# ---------------------------------------------------------------------------
# Build reliability
# ---------------------------------------------------------------------------
def test_a_pyinstaller_failure_propagates(build_module, monkeypatch, tmp_path):
    """A non-zero PyInstaller exit must not yield a "successful" build."""
    monkeypatch.setattr(build_module, "DIST", tmp_path / "dist")
    monkeypatch.setattr(build_module, "RELEASES", tmp_path / "releases")

    def boom(*a, **k):
        raise subprocess.CalledProcessError(1, "pyinstaller")

    monkeypatch.setattr(build_module.subprocess, "check_call", boom)
    with pytest.raises(subprocess.CalledProcessError):
        build_module.build("opstat-test", clean=True)


def test_a_stale_artifact_cannot_masquerade_as_a_fresh_build(
        build_module, monkeypatch, tmp_path, capsys):
    """Observed before the guard: with PyInstaller exiting 0 but writing
    nothing, the PREVIOUS dist/opstat was copied to releases/ and reported as
    "Created" - a stale binary presented as a successful build, which a
    release would then publish. dist/ is not cleared by --clean.

    The guard removes the expected output before building, so a build that
    writes nothing leaves nothing to package. That is deterministic; an
    earlier mtime comparison against the wall clock would have falsely
    refused a legitimate --no-clean rebuild, where PyInstaller reuses an
    unchanged EXE.
    """
    dist, releases = tmp_path / "dist", tmp_path / "releases"
    dist.mkdir()
    (dist / "opstat").write_bytes(b"stale binary from a previous build")

    monkeypatch.setattr(build_module, "DIST", dist)
    monkeypatch.setattr(build_module, "RELEASES", releases)
    monkeypatch.setattr(build_module.subprocess, "check_call",
                        lambda *a, **k: 0)
    with pytest.raises(SystemExit) as excinfo:
        build_module.build("opstat-stale", clean=True)
    assert excinfo.value.code != 0
    assert "did not produce" in capsys.readouterr().err
    assert not (releases / "opstat-stale").exists(), (
        "a stale artifact was packaged anyway")


def test_a_previous_artifact_is_removed_before_building(
        build_module, monkeypatch, tmp_path):
    """The mechanism behind the guard above: nothing from a prior build may
    survive into the new one."""
    dist, releases = tmp_path / "dist", tmp_path / "releases"
    dist.mkdir()
    (dist / "opstat").write_bytes(b"previous")
    seen = {}

    def fake_build(*a, **k):
        seen["existed_during_build"] = (dist / "opstat").exists()
        (dist / "opstat").write_bytes(b"freshly built binary")
        return 0

    monkeypatch.setattr(build_module, "DIST", dist)
    monkeypatch.setattr(build_module, "RELEASES", releases)
    monkeypatch.setattr(build_module.subprocess, "check_call", fake_build)
    build_module.build("opstat-fresh", clean=True)
    assert seen["existed_during_build"] is False, (
        "the previous artifact was still present when PyInstaller ran")
    assert (releases / "opstat-fresh").read_bytes() == b"freshly built binary"


def test_an_empty_artifact_is_refused(build_module, monkeypatch, tmp_path,
                                      capsys):
    """Asserts the SIZE guard specifically - both guards raise SystemExit, so
    without checking the message this would keep passing if it were deleted."""
    dist, releases = tmp_path / "dist", tmp_path / "releases"
    dist.mkdir()
    monkeypatch.setattr(build_module, "DIST", dist)
    monkeypatch.setattr(build_module, "RELEASES", releases)
    # Write the empty file DURING the "build" so it is not removed first.
    monkeypatch.setattr(build_module.subprocess, "check_call",
                        lambda *a, **k: (dist / "opstat").write_bytes(b""))
    with pytest.raises(SystemExit):
        build_module.build("opstat-empty", clean=True)
    assert "is empty" in capsys.readouterr().err


def test_the_build_script_hardcodes_no_version(build_module):
    """Artifact names are version-free by design; a version would have to
    come from opstat_version, never a literal.

    Scans string literals only - a provenance comment such as "validated with
    PyInstaller 6.22.2" is exactly what this repository writes and must not
    fail the build contract."""
    tree = ast.parse(BUILD_SCRIPT.read_text())
    literals = [n.value for n in ast.walk(tree)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    docstrings = {ast.get_docstring(n) for n in ast.walk(tree)
                  if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef))}
    offenders = [l for l in literals if l not in docstrings
                 and re.search(r"\d+\.\d+\.\d+", l)]
    assert not offenders, f"build script hardcodes a version: {offenders}"


# ---------------------------------------------------------------------------
# Workflow wiring
# ---------------------------------------------------------------------------
def active_workflow_lines():
    """Workflow lines with comments removed - whole-line AND trailing.

    Stripping only whole-line comments was not enough: a deleted step whose
    name survived in a trailing comment ("ls releases/  # smoke_packaged...")
    still read as an active mention.
    """
    out = []
    for line in WORKFLOW.read_text().splitlines():
        if line.strip().startswith("#"):
            continue
        # Trailing comment, but never inside a quoted string.
        if "#" in line:
            quote = None
            for index, char in enumerate(line):
                if char in "\"'" and quote in (None, char):
                    quote = None if quote else char
                elif char == "#" and quote is None:
                    line = line[:index]
                    break
        if line.strip():
            out.append(line.rstrip())
    return out


def workflow_steps():
    """[(job, step_dict)] parsed by indentation - stdlib only, no yaml.

    Needed because substring/ordering checks over the text could not see a
    step neutralised by `if: false`, by `|| true`, or by living in a second
    job that also uploads.
    """
    jobs, steps = {}, []
    job = step = None
    for line in active_workflow_lines():
        job_match = re.match(r"^  (\w[\w-]*):\s*$", line)
        if job_match:
            job = job_match.group(1)
            jobs[job] = []
            step = None
            continue
        if job is None:
            continue
        if re.match(r"^\s+- name:", line) or re.match(r"^\s+- uses:", line):
            step = {"raw": [line]}
            jobs[job].append(step)
            steps.append((job, step))
            key, _, value = line.split("- ", 1)[1].partition(":")
            step[key.strip()] = value.strip()
            continue
        if step is not None and re.match(r"^\s+\w[\w-]*:", line):
            step["raw"].append(line)
            key, _, value = line.strip().partition(":")
            step[key.strip()] = value.strip()
        elif step is not None:
            step["raw"].append(line)
            step["run"] = step.get("run", "") + " " + line.strip()
    return jobs, steps


def test_the_workflow_builds_through_the_committed_script():
    """CI and a local build must be the same path, or CI validates something
    developers never run - including the wrapper scripts humans actually
    invoke."""
    _jobs, steps = workflow_steps()
    assert any("scripts/build_opstat.py" in step.get("run", "")
               for _job, step in steps), (
        "the release workflow no longer builds via scripts/build_opstat.py")
    for wrapper in ("build.sh", "build.bat"):
        path = ROOT / "scripts" / wrapper
        if path.exists():
            assert "build_opstat.py" in path.read_text(), (
                f"scripts/{wrapper} no longer delegates to build_opstat.py")


def test_every_uploading_job_smoke_tests_first():
    """Ordering AND execution. A step neutralised by `if:`, by `|| true`, or
    an upload in a second job with no smoke step, all passed a check that
    only compared line positions."""
    jobs, _steps = workflow_steps()
    uploading = {name: st for name, st in jobs.items()
                 if any("upload-artifact" in s.get("uses", "") for s in st)}
    assert uploading, "no job uploads artifacts any more"
    for name, step_list in uploading.items():
        smoke_at = [i for i, s in enumerate(step_list)
                    if "smoke_packaged_opstat.py" in s.get("run", "")]
        upload_at = [i for i, s in enumerate(step_list)
                     if "upload-artifact" in s.get("uses", "")]
        assert smoke_at, (
            f"job {name!r} uploads an artifact without smoke-testing it - a "
            f"binary that cannot start would reach a Release")
        assert min(smoke_at) < min(upload_at), (
            f"job {name!r} uploads before the smoke test")
        smoke = step_list[min(smoke_at)]
        assert "if" not in smoke, (
            f"job {name!r} makes the smoke test conditional: {smoke.get('if')}")
        run = smoke.get("run", "")
        for swallow in ("||", ";", "&"):
            assert swallow not in run, (
                f"job {name!r} discards the smoke test's exit status: {run!r}")


def test_the_build_matrix_covers_every_supported_platform():
    """A commented-out matrix leg would silently stop producing a binary."""
    text = "\n".join(active_workflow_lines())
    artifacts = set(re.findall(r"artifact:\s*(\S+)", text))
    assert artifacts == {"opstat-linux-x86_64", "opstat-macos-arm64",
                         "opstat-windows-x86_64.exe"}, (
        f"release matrix changed: {sorted(artifacts)}")


def test_the_smoke_script_is_syntactically_valid():
    """It only ever runs in CI, after three builds - a syntax error would
    have left this suite green and blown up there."""
    ast.parse(SMOKE_SCRIPT.read_text())


def test_the_smoke_script_fails_the_release_on_a_failed_check():
    """Without the non-zero exit the workflow step is decorative."""
    tree = ast.parse(SMOKE_SCRIPT.read_text())
    source = SMOKE_SCRIPT.read_text()
    assert "if FAILURES:" in source and "return 1" in source, (
        "the smoke script no longer fails on a failed check")
    main = next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == "main")
    returns = {n.value.value for n in ast.walk(main)
               if isinstance(n, ast.Return) and isinstance(n.value, ast.Constant)}
    assert {0, 1} <= returns, f"main() must return both 0 and 1, got {returns}"


def test_the_smoke_script_checks_the_artifact_not_the_source():
    text = SMOKE_SCRIPT.read_text()
    assert "from opstat_version import VERSION" in text, (
        "the smoke script must compare against the authoritative version")
    assert 'out.strip() == "opstat %s" % VERSION' in text, (
        "the smoke script must compare the exact version, not a prefix")
    assert "tempfile" in text and "cwd=cwd" in text, (
        "the smoke script must run the artifact away from the source tree")
    for leak in ("PYTHONPATH", "PYTHONHOME"):
        assert leak in text, f"the smoke script must scrub {leak}"
