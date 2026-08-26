"""Executable regressions for the FR14 lab validation script.

Three defects shipped in `scripts/opstat-lab-fr14-production-validation.sh`
and were caught by the repository owner reading it, not by any check:

  1. `OWNER_RC` was never assigned after the ownership pipeline, so under
     `set -u` the script aborted on an unbound variable before validating
     anything - and a genuine target mismatch could never be reported.
  2. The workload proof exited 0 when client-side counters were unavailable,
     even with traffic required. Absence of counters is not evidence of work.
  3. The final banner printed a verdict the process never returned: the
     script always exited 0, so any caller read a failed validation as
     success.

These tests RUN the published script and its embedded Python - they do not
grep for text, because every one of these defects was invisible to reading
and would survive any string assertion.

External tools (git, systemctl, findmnt, zip, sha256sum) are stubbed onto
PATH so the run is hermetic; the VMS is the in-process mock. Client-side
counters are deliberately absent in the end-to-end cases, so those runs pass
`OPSTAT_FR14_REQUIRE_TRAFFIC=0`; the counter logic itself is exercised
directly against synthetic mountstats in the defect-2 tests below.
"""

from __future__ import annotations

import io
import os
import re
import shutil
import stat
import subprocess
import sys

import pytest

from tests.mock_vms import MockVMS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "opstat-lab-fr14-production-validation.sh")

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("openssl") is None,
    reason="bash and openssl are required to drive the lab script",
)

_STUBS = {
    # Never touches the real repository: every git verb the script uses.
    "git": """#!/usr/bin/env bash
case "$1" in
  rev-parse) echo "0000000000000000000000000000000000000000" ;;
  status)    ;;                      # clean tree
  log)       echo "0000000 stub commit" ;;
  *)         ;;                      # fetch / checkout / merge succeed
esac
exit 0
""",
    "systemctl": """#!/usr/bin/env bash
[ "$1" = "is-active" ] && { echo active; exit 0; }
echo "stub loadgen status"; exit 0
""",
    "zip": "#!/usr/bin/env bash\ntouch \"$2\"\nexit 0\n",
    "unzip": "#!/usr/bin/env bash\necho 'stub archive listing'\nexit 0\n",
    "sha256sum": "#!/usr/bin/env bash\necho 'stubsha  '\"$1\"\nexit 0\n",
}


def _write_stub(bindir, name, body):
    path = os.path.join(bindir, name)
    with io.open(path, "w") as fh:
        fh.write(body)
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC | stat.S_IXGRP)


@pytest.fixture
def vms(tmp_path):
    server = MockVMS(certdir=str(tmp_path)).start()
    yield server
    server.stop()


@pytest.fixture
def harness(tmp_path, vms):
    """A hermetic environment in which the REAL script can run to completion."""
    bindir = tmp_path / "bin"
    home = tmp_path / "home"
    bindir.mkdir()
    home.mkdir()
    for name, body in _STUBS.items():
        _write_stub(str(bindir), name, body)

    def run(workload_ip="10.1.0.1", engines="s3", extra=None, timeout=300):
        # findmnt decides the workload target; the mock's VIP pool contains
        # 10.1.0.x, so a different address exercises the mismatch branch.
        _write_stub(str(bindir), "findmnt",
                    "#!/usr/bin/env bash\necho '%s:/export'\nexit 0\n" % workload_ip)
        env = dict(os.environ)
        env.update(
            PATH="%s:%s" % (bindir, env.get("PATH", "")),
            HOME=str(home),
            VAST_TOKEN="test-token",
            OPSTAT_REPO=ROOT,
            OPSTAT_VMS="127.0.0.1",
            OPSTAT_PORT=str(vms.port),
            OPSTAT_LOADGEN="nfs3-loadgen",
            OPSTAT_NFS3_MOUNT="/mnt/fr14-test",
            OPSTAT_FR14_ENGINES=engines,
            OPSTAT_FR14_REQUIRE_TRAFFIC="0",
        )
        env.pop("OPSTAT_EXPECTED_HEAD", None)
        env.update(extra or {})
        proc = subprocess.run(["bash", SCRIPT], env=env, cwd=ROOT,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              timeout=timeout)
        return proc.returncode, proc.stdout.decode(errors="replace")

    return run


# ---------------------------------------------------------------------------
# Defect 1 - the ownership status must be captured and acted on
# ---------------------------------------------------------------------------
def test_target_mismatch_is_reported_and_fails(harness):
    """A workload pointed at another cluster must stop the run.

    The published script never assigned OWNER_RC, so this path could not
    work at all: under `set -u` it died on an unbound variable instead.
    """
    rc, out = harness(workload_ip="192.0.2.1")      # not in the mock VIP pool
    assert "TARGET MISMATCH" in out, out[-1500:]
    assert rc != 0, "a target mismatch must fail the run"
    assert "unbound variable" not in out, (
        "OWNER_RC is unset - PIPESTATUS was not captured after the pipeline")


def test_owned_target_proceeds_past_the_ownership_gate(harness):
    rc, out = harness(workload_ip="10.1.0.1")
    assert "belongs to" in out, out[-1500:]
    assert "TARGET MISMATCH" not in out
    assert "unbound variable" not in out


def test_ownership_helper_failure_is_not_read_as_success(harness):
    """A helper that dies (rc other than 0/3) leaves ownership UNPROVEN.

    Treating that as a mismatch would be merely wrong; treating it as
    success would publish unattributable evidence.
    """
    rc, out = harness(workload_ip="10.1.0.1",
                      extra={"OPSTAT_PORT": "1"})   # nothing listening
    assert rc != 0
    assert "ownership helper failed" in out or "TARGET MISMATCH" in out, out[-1500:]
    assert "ownership UNPROVEN" in out or "TARGET MISMATCH" in out


# ---------------------------------------------------------------------------
# Defect 3 - the banner is not the verdict; the exit status is
# ---------------------------------------------------------------------------
def test_exit_status_is_zero_only_on_a_clean_run(harness):
    rc, out = harness(engines="s3")
    assert "RESULT: PASS" in out, out[-2000:]
    assert rc == 0, "a clean run must exit 0"


def test_failed_validation_exits_nonzero(harness):
    """The script always exited 0, so `&&` chains and CI read a failed
    validation as success."""
    rc, out = harness(engines="not_an_engine")
    assert "RESULT: PASS" not in out, out[-2000:]
    assert rc != 0, "a failed validation must not exit 0"


def test_failed_run_still_packages_its_evidence(harness):
    """Failing must not cost the operator the artifacts."""
    rc, out = harness(engines="not_an_engine")
    assert rc != 0
    assert ".zip" in out, "the ZIP must still be produced on a failed run"
    assert "return the archive anyway" in out


# ---------------------------------------------------------------------------
# Defect 2 - absent counters are not evidence of work
# ---------------------------------------------------------------------------
def _workload_gate_source():
    """The workload-proof Python, taken from the published script itself."""
    body = io.open(SCRIPT).read()
    after = body.split("<<'WORKEOF'", 1)[1]
    return after.split("\n", 1)[1].split("\nWORKEOF", 1)[0]


def _mountstats(path, read_ops, write_ops, mount="/mnt/fr14-test"):
    with io.open(path, "w") as fh:
        fh.write("device x mounted on %s with fstype nfs\n"
                 "\tREAD:\n\t\t%d 0 0\n\tWRITE:\n\t\t%d 0 0\ndevice y\n"
                 % (mount, read_ops, write_ops))


def _run_gate(tmp_path, before, after, require, mount="/mnt/fr14-test"):
    logs = tmp_path / "logs"
    logs.mkdir(exist_ok=True)
    if before is not None:
        _mountstats(str(logs / "mountstats-window-start.txt"), *before)
        _mountstats(str(logs / "mountstats-after.txt"), *after)
    else:                                    # counters entirely unavailable
        (logs / "mountstats-window-start.txt").write_text("")
        (logs / "mountstats-after.txt").write_text("")
    (logs / "cifs-window-start.txt").write_text("")
    (logs / "cifs-window-end.txt").write_text("")
    proc = subprocess.run(
        [sys.executable, "-c", _workload_gate_source(), str(tmp_path), mount, require],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120)
    return proc.returncode, proc.stdout.decode()


def test_absent_counters_fail_closed_when_traffic_is_required(tmp_path):
    """The shipped gate exited 0 here, so a run with no measurable client
    I/O at all was accepted as proof of work."""
    rc, out = _run_gate(tmp_path, None, None, "1")
    assert rc != 0, "absent counters must not be accepted as evidence"
    assert "INCONCLUSIVE" in out and "FAIL" in out, out


def test_absent_counters_accepted_only_when_explicitly_requested(tmp_path):
    rc, out = _run_gate(tmp_path, None, None, "0")
    assert rc == 0
    assert "ACCEPTED" in out and "ONLY workload evidence" in out, out


def test_idle_mount_fails_when_traffic_is_required(tmp_path):
    rc, out = _run_gate(tmp_path, (100, 50), (100, 50), "1")
    assert rc != 0, "an idle mount proves nothing about behaviour under load"
    assert "ZERO NFS client I/O" in out, out


def test_busy_mount_passes_and_reports_the_operation_count(tmp_path):
    rc, out = _run_gate(tmp_path, (100, 50), (99100, 20050), "1")
    assert rc == 0, out
    assert "PASS" in out
    total = re.search(r"during the window: (\d+) operations", out)
    assert total and int(total.group(1)) == 119000, out


def test_idle_mount_accepted_run_labels_itself_as_not_load_evidence(tmp_path):
    rc, out = _run_gate(tmp_path, (100, 50), (100, 50), "0")
    assert rc == 0
    assert "ACCEPTED" in out, out
    assert "NOT evidence about behaviour under load" in out, (
        "an accepted idle run must say what it does not prove")
