"""find_nfs41_candidates: real-file discovery for the FR2 lab workflow.

Two lab trips reached the cluster with no real file to query - one globbed a
guessed extension, one waited for fio processes the nfs41 loadgen does not
use and size-filtered a churned mount. These tests pin the corrected
strategy: open-fd inspection, command-line-derived paths, a walk that keeps
zero-byte files, and an honest nonzero exit when nothing exists.
"""

import importlib.util
import os

import pytest


def _load():
    spec = importlib.util.spec_from_file_location(
        "find_nfs41_candidates",
        "scripts/var203_validation/find_nfs41_candidates.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_fd_scan_finds_files_open_under_the_mount(tmp_path):
    helper = _load()
    mount = tmp_path / "mnt"
    (mount / "sub").mkdir(parents=True)
    real = mount / "sub" / "held-open.dat"
    real.write_bytes(b"x")
    outside = tmp_path / "elsewhere.dat"
    outside.write_bytes(b"y")
    proc = tmp_path / "proc"
    fd_dir = proc / "4242" / "fd"
    fd_dir.mkdir(parents=True)
    os.symlink(str(real), str(fd_dir / "3"))
    os.symlink(str(outside), str(fd_dir / "4"))
    os.symlink("socket:[12345]", str(fd_dir / "5"))
    found = helper.candidates_from_fds(str(mount), proc_root=str(proc))
    assert found == {str(real)}, "only files under the mount count"


def test_ps_args_derives_existing_files_only(tmp_path):
    helper = _load()
    mount = tmp_path / "mnt"
    (mount / "nfs41_loadgen").mkdir(parents=True)
    real = mount / "nfs41_loadgen" / "commit.dat"
    real.write_bytes(b"")
    ps = (
        "/bin/bash loadgen.sh %s\n"
        "sync %s\n"
        "mkdir -p %s/nfs41_loadgen/meta_stress/dir_1\n"
        "sync %s/nfs41_loadgen/deleted-already.dat\n"
        % (mount, real, mount, mount))
    found = helper.candidates_from_ps_args(ps, str(mount))
    assert found == {str(real)}, (
        "only paths that stat as existing regular files may be candidates")


def test_walk_keeps_zero_byte_files(tmp_path):
    """A fresh loadgen file is a valid delegation target; the size filter
    that dropped them cost the second lab trip its candidates."""
    helper = _load()
    mount = tmp_path / "mnt"
    (mount / "d").mkdir(parents=True)
    empty = mount / "d" / "fresh.bin"
    empty.write_bytes(b"")
    found = helper.candidates_from_walk(str(mount))
    assert str(empty) in found


def test_gather_once_prefers_open_files_and_dedups(tmp_path):
    helper = _load()
    mount = tmp_path / "mnt"
    mount.mkdir()
    walk_file = mount / "resting.dat"
    walk_file.write_bytes(b"z")
    live = mount / "live.dat"
    live.write_bytes(b"z")
    proc = tmp_path / "proc"
    (proc / "7" / "fd").mkdir(parents=True)
    os.symlink(str(live), str(proc / "7" / "fd" / "0"))
    ordered, has_live = helper.gather_once(
        str(mount), ps_text="", proc_root=str(proc))
    assert has_live is True
    assert ordered[0] == str(live), "open-by-a-process files rank first"
    assert str(walk_file) in ordered
    assert len(ordered) == len(set(ordered))


def test_gather_once_reports_nothing_honestly(tmp_path):
    helper = _load()
    mount = tmp_path / "mnt"
    (mount / "only-dirs").mkdir(parents=True)
    ordered, has_live = helper.gather_once(
        str(mount), ps_text="no paths here", proc_root=str(tmp_path / "noproc"))
    assert ordered == [] and has_live is False


def test_lab_script_refuses_probe_without_candidates_and_fails_loudly():
    """The workflow must stop BEFORE the probe when no real file exists, and
    a failed run must be unmistakable: RUN FAILED banner and a nonzero exit,
    while still packaging the evidence ZIP."""
    body = open("scripts/opstat-lab-fr2-delegation-discovery.sh").read()
    refusal = body.index("refusing to run the probe")
    probe_run = body.index("probe_fr2_delegations.py \\")
    assert refusal < probe_run, "the refusal must precede the probe invocation"
    assert "RUN FAILED" in body and "RUN VALID" in body
    assert 'exit 0 || exit 1' in body.replace('"$FAILURES" -eq 0 ] && ', ''), (
        "the final verdict must map failures to a nonzero exit status")
    finish_def = body.index("finish() {")
    assert body.index("zip -qr", finish_def) < body.index("RUN FAILED", finish_def), (
        "failure evidence must still be packaged before the verdict")
    assert "PROBE:correlation.winner PASS" in body, (
        "minimum success must be machine-checked")
    assert "|| true" not in body
