"""Committed lab scripts are maintained repository tooling (owner policy,
2026-08-17): syntax-checked, /tmp-free for artifacts, ZIP-only deliverables.
An ad-hoc pasted block once stranded the API log in /tmp and cost a forensic
pass its /views/ payload; these checks keep the committed scripts honest."""

import glob
import shutil
import subprocess

import pytest

SCRIPTS = sorted(glob.glob("scripts/opstat-lab-*.sh"))


def test_lab_scripts_exist():
    assert SCRIPTS, "lab scripts are expected under scripts/opstat-lab-*.sh"


@pytest.mark.parametrize("script", SCRIPTS)
def test_lab_script_syntax(script):
    if shutil.which("bash") is None:
        pytest.skip("bash unavailable")
    proc = subprocess.run(["bash", "-n", script], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


@pytest.mark.parametrize("script", SCRIPTS)
def test_lab_script_policy(script):
    body = open(script).read()
    assert "tar.gz" not in body, "deliverable must be a ZIP, never tar.gz"
    assert 'TMPDIR="$RUN/tmp"' in body or "TMPDIR=$RUN/tmp" in body, (
        "TMPDIR must be routed into the run directory")
    assert "OPSTAT_API_LOG_DIR" in body, (
        "the API log must be explicitly routed beneath the run directory")
    assert "sha256sum" in body and "unzip -t" in body.replace("-tq", "-t"), (
        "final archive must be integrity-checked and checksummed")
    assert "VAST_PASSWORD" in body and "echo $VAST_PASSWORD" not in body, (
        "credentials come from the environment and are never printed")
