"""API-log destination selection (owner lab artifact policy).

A lab run set TMPDIR beneath the run's evidence tree, yet the API log still
landed in a hard-coded /tmp - the one artifact outside the archive, and the
one that held the raw /views/ payload the forensics needed. Destination is
now selectable: explicit directory > OPSTAT_API_LOG_DIR > tempfile.gettempdir()
(which honors TMPDIR and keeps /tmp as the historical default). Log content
and lifecycle are unchanged.
"""

import os

import vast_api_log


def _teardown():
    vast_api_log.close()


def test_explicit_directory_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("OPSTAT_API_LOG_DIR", str(tmp_path / "env-should-lose"))
    (tmp_path / "env-should-lose").mkdir()
    explicit = tmp_path / "evidence"
    explicit.mkdir()
    try:
        path = vast_api_log.configure(True, "probe", "vms.example", 443,
                                      directory=str(explicit))
        assert os.path.dirname(path) == str(explicit)
        assert os.path.isfile(path), "log file must be created at the destination"
    finally:
        _teardown()


def test_env_var_wins_over_tempdir(tmp_path, monkeypatch):
    envdir = tmp_path / "envdir"
    envdir.mkdir()
    monkeypatch.setenv("OPSTAT_API_LOG_DIR", str(envdir))
    try:
        path = vast_api_log.configure(True, "probe", "vms.example", 443)
        assert os.path.dirname(path) == str(envdir)
    finally:
        _teardown()


def test_tmpdir_is_honored_via_gettempdir(tmp_path, monkeypatch):
    """The lab script exports TMPDIR; gettempdir() must be what we consult."""
    tdir = tmp_path / "lab-tmp"
    tdir.mkdir()
    monkeypatch.delenv("OPSTAT_API_LOG_DIR", raising=False)
    monkeypatch.setattr(vast_api_log.tempfile, "gettempdir",
                        lambda: str(tdir))
    try:
        path = vast_api_log.configure(True, "probe", "vms.example", 443)
        assert os.path.dirname(path) == str(tdir)
    finally:
        _teardown()


def test_default_destination_remains_system_tempdir(monkeypatch):
    """With nothing set, behavior matches the historical default (/tmp on a
    machine with no TMPDIR): the system temp directory."""
    monkeypatch.delenv("OPSTAT_API_LOG_DIR", raising=False)
    import tempfile as _tf
    try:
        path = vast_api_log.configure(True, "probe", "vms.example", 443)
        assert os.path.dirname(path) == _tf.gettempdir()
    finally:
        _teardown()


def test_log_lines_still_written_and_private(tmp_path):
    try:
        path = vast_api_log.configure(True, "probe", "vms.example", 443,
                                      directory=str(tmp_path))
        vast_api_log.log_call("GET", "https://vms.example/api/x/", None,
                              200, "{}", None, 12.0)
        vast_api_log.close()
        content = open(path).read()
        assert "GET https://vms.example/api/x/ 12ms" in content
        assert oct(os.stat(path).st_mode & 0o777) == "0o600"
    finally:
        _teardown()
