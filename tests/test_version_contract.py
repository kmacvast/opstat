"""One version, everywhere: runtime, build, docs and release tag.

Before `opstat_version.py` existed the version string lived in six places -
the `opstat` entrypoint and all five protocol engines - agreeing only by
luck, while three of those engines' own header comments still said 0.1.1.
Nothing compared them, and the release workflow never read the version at
all: pushing `v9.9.9` would have built, uploaded and published binaries
reporting `opstat 0.1.2`.

These tests derive the version from the authoritative module and hold every
other surface to it. They deliberately do NOT enforce every mention of a
version anywhere in the repository - historical statements that correctly
name an old version (`pre-v0.1.2 UI`, a captured lab frame) must survive
untouched, so only *active claim* forms are checked.
"""

from __future__ import annotations

import importlib.util
import re
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

import opstat_version

ROOT = Path(__file__).resolve().parent.parent
VERSION = opstat_version.VERSION

# Modules that render the version in their own UI. Each must derive it.
ENGINES = ("nfs_v3", "nfs_v41", "smb", "s3", "nvme_tcp")

# Docs whose "**Version:**" line is an ACTIVE claim about the current build.
ACTIVE_VERSION_DOCS = ("README.md", "SMB_README.md", "S3_README.md")

# Files whose version mentions are historical evidence, not active claims:
# they describe what an older build did, or when a screenshot/frame was
# captured, and rewriting them would falsify the record.
HISTORICAL = ("SMB_OPCODES.md", "images/README.md", "docs/")


def load_gate():
    spec = importlib.util.spec_from_file_location(
        "check_version_contract", ROOT / "scripts" / "check_version_contract.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_cli(*args):
    return subprocess.run([sys.executable, str(ROOT / "opstat"), *args],
                          capture_output=True, text=True, timeout=60)


# ---------------------------------------------------------------------------
# One source of truth
# ---------------------------------------------------------------------------
def test_the_version_is_a_plain_semver_string():
    assert re.fullmatch(r"\d+\.\d+\.\d+", VERSION), VERSION


def test_the_authoritative_module_is_import_safe():
    """Build and release tooling must be able to read the version without
    executing the application - the entrypoint is an extensionless script,
    so importing IT to get a version would run the program."""
    source = (ROOT / "opstat_version.py").read_text()
    code = [l for l in source.splitlines()
            if l.strip() and not l.strip().startswith("#")
            and not l.strip().startswith('"""')]
    assert any(l.startswith("VERSION =") for l in code)
    assert not re.search(r"^\s*(import|from)\s", source, re.M), (
        "opstat_version must stay dependency-free and side-effect-free")


@pytest.mark.parametrize("name", ENGINES)
def test_every_engine_derives_the_version(name):
    module = importlib.import_module(name)
    assert module.VERSION == VERSION
    assert module.VERSION is opstat_version.VERSION, (
        f"{name} holds its own copy of the version string")


def test_the_entrypoint_derives_the_version():
    module = runpy.run_path(str(ROOT / "opstat"), run_name="opstat_cli")
    assert module["VERSION"] is opstat_version.VERSION


def test_no_source_file_hardcodes_a_version_in_prose():
    """A comment can carry a version too, and it must be flagged by SHAPE,
    not by value: the entrypoint's own header said "(v0.1.2)" four lines
    above the constant this slice removed, and three engines carried
    "# Version: 0.1.1" while their code said 0.1.2. A guard keyed on the
    current version would have gone quiet on exactly those stale lines.

    VAST cluster versions ("VAST OS 5.5.0.1", "var204/5.5.0.1") are a
    different thing and appear legitimately in comments.
    """
    offenders = []
    scanned = sorted(ROOT.glob("*.py")) + [ROOT / "opstat"] + sorted(
        (ROOT / "scripts").glob("*.py"))
    for path in scanned:
        if path.name == "opstat_version.py":
            continue
        for number, line in enumerate(path.read_text().splitlines(), 1):
            stripped = line.strip()
            if not stripped.startswith("#"):
                continue
            if "module-local" in stripped:      # explicitly not the tool version
                continue
            # A "vX.Y.Z" tag spelling, or a "Version: X.Y.Z" header claim -
            # regardless of which version it names.
            if re.search(r"\bv\d+\.\d+\.\d+\b", stripped) or \
                    re.search(r"Version\s*:\s*\d+\.\d+\.\d+", stripped):
                offenders.append(f"{path.name}:{number}: {stripped}")
    assert not offenders, (
        "source comments state a version; delete them or annotate them as "
        "module-local - opstat_version.py is the only place a version "
        "belongs:\n  " + "\n  ".join(offenders))


def test_no_module_redefines_the_version_literal():
    """The failure this whole slice exists to prevent: a second copy that
    happens to agree today and silently drifts tomorrow."""
    offenders = []
    for path in list(ROOT.glob("*.py")) + [ROOT / "opstat"]:
        if path.name == "opstat_version.py":
            continue
        for number, line in enumerate(path.read_text().splitlines(), 1):
            if re.match(r"\s*VERSION\s*=\s*[\"']", line):
                offenders.append(f"{path.name}:{number}: {line.strip()}")
    assert not offenders, (
        "these define their own version literal instead of importing "
        "opstat_version:\n  " + "\n  ".join(offenders))


# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("flag", ["-V", "--tool-version"])
def test_runtime_reports_the_authoritative_version(flag):
    result = run_cli(flag)
    assert result.returncode == 0, result.stderr[-400:]
    assert result.stdout.strip() == f"opstat {VERSION}"


def test_both_version_spellings_agree():
    assert run_cli("-V").stdout == run_cli("--tool-version").stdout


# ---------------------------------------------------------------------------
# Release tag gate
# ---------------------------------------------------------------------------
def test_matching_release_tag_is_accepted():
    ok, message = load_gate().check(f"v{VERSION}")
    assert ok, message


@pytest.mark.parametrize("bump", [1, 2, 3])
def test_mismatched_release_tag_is_rejected(bump):
    """Derived from VERSION so the case can never collide with it."""
    parts = [int(p) for p in VERSION.split(".")]
    parts[0] += bump
    tag = "v%d.%d.%d" % tuple(parts)
    gate = load_gate()
    ok, message = gate.check(tag)
    assert not ok
    assert "MISMATCH" in message and VERSION in message


@pytest.mark.parametrize("tag", [
    "checkpoint-0.1.2-refactor-complete",   # the real checkpoint tag in use
    "checkpoint-1.0.0",
    "v0.1",                                  # not semver
    "v0.1.2-rc1",                            # pre-release suffix is not a release
    "0.1.2",                                 # missing the v
    "release-0.1.2",
    "",
])
def test_non_release_tags_are_never_treated_as_releases(tag):
    """Checkpoint tags must never be publishable. They do not match the
    workflow's v* trigger, and the gate refuses them if passed by hand."""
    gate = load_gate()
    assert gate.release_version_from_tag(tag) is None
    ok, message = gate.check(tag)
    assert not ok
    assert "not a release tag" in message


def test_the_gate_exits_nonzero_on_mismatch():
    """Exit status is what actually stops the workflow."""
    script = str(ROOT / "scripts" / "check_version_contract.py")
    good = subprocess.run([sys.executable, script, "--tag", f"v{VERSION}"],
                          capture_output=True, text=True, timeout=60)
    other = "v%d.0.0" % (int(VERSION.split(".")[0]) + 1)   # never == VERSION
    bad = subprocess.run([sys.executable, script, "--tag", other],
                         capture_output=True, text=True, timeout=60)
    assert good.returncode == 0, good.stderr
    assert bad.returncode != 0, "a mismatched tag must fail the release run"
    assert "MISMATCH" in bad.stderr


def test_the_gate_reports_the_version_when_given_no_tag():
    script = str(ROOT / "scripts" / "check_version_contract.py")
    result = subprocess.run([sys.executable, script],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0
    assert result.stdout.strip() == VERSION


# ---------------------------------------------------------------------------
# Release workflow wiring
# ---------------------------------------------------------------------------
def workflow_text():
    return (ROOT / ".github" / "workflows" / "release.yml").read_text()


def workflow_active_lines():
    """The workflow with comment-only lines removed.

    Substring checks over the raw text could not tell a live step from a
    commented-out one: commenting out the gate step left every assertion
    green while the release verified nothing.
    """
    return [line for line in workflow_text().splitlines()
            if not line.strip().startswith("#")]


def workflow_jobs():
    """{job name: [its active lines]} - tolerant of job ordering, since
    assertions keyed on byte offsets broke when jobs were merely reordered."""
    jobs, current = {}, None
    for line in workflow_active_lines():
        match = re.match(r"^(\s{2})(\w[\w-]*):\s*$", line)
        if match and not line.startswith("   "):
            current = match.group(2)
            jobs[current] = []
        elif current is not None:
            jobs[current].append(line)
    return jobs


def gate_invocations(lines):
    """Active `run:` lines that actually invoke the version gate."""
    return [l for l in lines if "check_version_contract.py" in l
            and re.search(r"\brun\s*:", l)]


def trigger_patterns():
    """EVERY tag glob under on.push.tags - reading only the first entry hid
    a second, permissive one."""
    lines = workflow_active_lines()
    patterns, in_tags = [], False
    for line in lines:
        if re.match(r"^\s*tags\s*:", line):
            in_tags = True
            inline = re.search(r"tags\s*:\s*\[(.+)\]", line)
            if inline:                       # flow style: tags: ["v*", ...]
                patterns += re.findall(r"[\"']([^\"']+)[\"']", inline.group(1))
                in_tags = False
            continue
        if in_tags:
            item = re.match(r"^\s*-\s*[\"']?([^\"'\s]+)[\"']?\s*$", line)
            if item:
                patterns.append(item.group(1))
            elif line.strip():
                in_tags = False
    return patterns


def test_the_workflow_runs_the_version_gate():
    jobs = workflow_jobs()
    assert "test" in jobs, "the release workflow lost its test job"
    invocations = gate_invocations(jobs["test"])
    assert invocations, (
        "the release workflow no longer verifies the tag against VERSION "
        "(a commented-out step does not count)")
    assert any("GITHUB_REF_NAME" in l for l in invocations), (
        "the gate is not given the pushed tag")


def test_the_gate_runs_before_anything_is_built_or_published():
    """Ordering is the whole point: the check must block the build, not run
    beside it. build waits on test (where the gate lives); publish waits on
    build and re-verifies immediately before publication."""
    jobs = workflow_jobs()
    for job in ("test", "build", "publish"):
        assert job in jobs, f"release workflow lost its {job} job"
    build_needs = " ".join(jobs["build"])
    publish_needs = " ".join(jobs["publish"])
    assert re.search(r"needs\s*:\s*\[?\s*[\"']?test\b", build_needs), (
        "build no longer waits for the gate")
    assert re.search(r"needs\s*:\s*\[?\s*[\"']?build\b", publish_needs), (
        "publish no longer waits for the build")
    assert gate_invocations(jobs["publish"]), (
        "publish no longer re-verifies the tag immediately before publishing")


def test_the_workflow_only_triggers_on_release_tags():
    """checkpoint-* must not match ANY trigger entry - that is why the
    earlier checkpoint tag was renamed out of the v* namespace."""
    import fnmatch

    patterns = trigger_patterns()
    assert patterns, "could not read the tag triggers"
    assert any(fnmatch.fnmatch(f"v{VERSION}", p) for p in patterns), (
        f"triggers {patterns} would not match this release's own tag")
    for not_a_release in ("checkpoint-0.1.2-refactor-complete",
                          "checkpoint-1.0.0", "release-0.1.2", "main",
                          "lab-snapshot"):
        matched = [p for p in patterns if fnmatch.fnmatch(not_a_release, p)]
        assert not matched, (
            f"trigger(s) {matched} would start a release run for "
            f"{not_a_release!r}")


def test_the_build_script_bundles_the_version_module():
    text = (ROOT / "scripts" / "build_opstat.py").read_text()
    assert '"opstat_version"' in text, (
        "the packaged binary must bundle the authoritative version module")


def test_artifact_names_do_not_embed_a_version():
    """Artifact names are opstat-<os>-<arch> by design. If that ever changes
    to include a version it must come from the authoritative source, not a
    literal - this test is the tripwire for that decision."""
    text = (ROOT / "scripts" / "build_opstat.py").read_text()
    assert not re.search(r"opstat-\{?\d+\.\d+\.\d+", text)
    workflow = workflow_text()
    for artifact in re.findall(r"artifact:\s*(\S+)", workflow):
        assert not re.search(r"\d+\.\d+\.\d+", artifact), artifact


# ---------------------------------------------------------------------------
# Documentation claims
# ---------------------------------------------------------------------------
def test_active_version_claims_match_the_runtime():
    """"**Version:** X.Y.Z" is an active claim about the current build.

    Swept across every tracked markdown file rather than an allowlist: a
    wrong version added to a doc nobody listed would otherwise be invisible.
    """
    for path in sorted(ROOT.glob("*.md")) + sorted(ROOT.glob("*/*.md")):
        if any(str(path.relative_to(ROOT)).startswith(h.rstrip("/"))
               for h in HISTORICAL):
            continue
        claims = re.findall(r"\*\*Version:\*\*\s*(\d+\.\d+\.\d+)",
                            path.read_text())
        wrong = [c for c in claims if c != VERSION]
        assert not wrong, (
            f"{path.name} claims version {wrong} but the runtime is {VERSION}")
    # And the docs that are SUPPOSED to carry one still do.
    for name in ACTIVE_VERSION_DOCS:
        text = (ROOT / name).read_text()
        assert re.search(r"\*\*Version:\*\*\s*%s" % re.escape(VERSION), text), (
            f"{name} no longer states the current version")


def test_documented_cli_output_matches_the_runtime():
    text = (ROOT / "README.md").read_text()
    printed = set(re.findall(r"opstat (\d+\.\d+\.\d+)", text))
    assert printed, "README no longer shows the version banner"
    assert printed == {VERSION}, (
        f"README shows {sorted(printed)} but the runtime prints {VERSION}")


def test_current_version_section_headings_match():
    """Headings like "Dashboard Panels (v0.1.2)" describe the CURRENT UI."""
    for name in ("SMB_README.md", "S3_README.md"):
        text = (ROOT / name).read_text()
        for heading in re.findall(r"^#+ .*\(v(\d+\.\d+\.\d+)\)", text, re.M):
            assert heading == VERSION, (
                f"{name} has a current-version heading claiming v{heading}")


@pytest.mark.parametrize("relative,needle", [
    ("SMB_OPCODES.md", "pre-v0.1.2"),
    ("images/README.md", "v0.1.2"),
    ("docs/VAST_LAB_HANDOFF.md", "opstat v0.1.2"),
])
def test_historical_version_references_are_left_alone(relative, needle):
    """Guards against an over-eager future sweep: these correctly name an
    older build or a captured artefact and must not be rewritten. Driven
    from HISTORICAL so the list and the protection cannot diverge."""
    assert any(relative.startswith(h.rstrip("/")) for h in HISTORICAL), (
        f"{relative} is protected here but missing from HISTORICAL")
    assert needle in (ROOT / relative).read_text(), (
        f"historical evidence in {relative} was rewritten: {needle!r} is gone")


def test_readme_documents_the_release_procedure():
    text = (ROOT / "README.md").read_text()
    assert "opstat_version.py" in text, "the version source is undocumented"
    assert "git tag vX.Y.Z" in text, (
        "the release procedure should use generic vX.Y.Z, not a pinned tag")
    assert "checkpoint-" in text, (
        "the non-release status of checkpoint tags should be documented")
