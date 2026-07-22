"""Wizard protocol registry and argv construction tests."""

import pytest

import wizard


def test_protocols_include_s3():
    keys = [p["key"] for p in wizard.PROTOCOLS]
    assert keys == ["nfs", "block", "smb", "s3"]
    s3 = next(p for p in wizard.PROTOCOLS if p["key"] == "s3")
    assert s3["flags"] == ["--s3"]
    assert s3["versions"] is None
    assert s3["scope"] == ("buckets", "--buckets", "bucket/view name(s)")


def test_should_launch_rules():
    assert wizard.should_launch([], True, True) is True
    assert wizard.should_launch([], False, True) is False
    assert wizard.should_launch(["--s3", "--vms", "h"], True, True) is False
    assert wizard.should_launch(["--menu"], True, True) is True
    assert wizard.should_launch(["-i"], True, True) is True
    assert wizard.should_launch(["--menu"], False, True) is False
    assert wizard.should_launch(["--no-menu"], True, True) is False
    assert wizard.should_launch(["--no-menu", "--menu"], True, True) is False


@pytest.mark.parametrize(
    "key,version,expected",
    [
        ("smb", None, True),
        ("s3", None, True),
        ("nfs", "4.1", True),
        ("nfs", "3.0", False),
        ("block", None, False),
    ],
)
def test_supports_token(key, version, expected):
    assert wizard._supports_token(key, version) is expected


def test_build_argv_s3_with_scope_and_openmetrics():
    ans = {
        "protocol": next(p for p in wizard.PROTOCOLS if p["key"] == "s3"),
        "version": None,
        "vms": "vms.example.com",
        "port": 8443,
        "user": "ops",
        "scope_value": "bucket-a,bucket-b",
        "refresh": 7,
        "sample_average": "1h",
        "csv": "/tmp/out.csv",
        "no_color": True,
        "log_api_calls": True,
        "export_openmetrics": True,
        "openmetrics_file": "/tmp/om.jsonl",
    }
    argv = wizard._build_argv(ans)
    assert argv[0] == "--s3"
    assert argv[1:3] == ["--vms", "vms.example.com"]
    assert "--vms-port" in argv and "8443" in argv
    assert "--user" in argv and "ops" in argv
    assert "--buckets" in argv and "bucket-a,bucket-b" in argv
    assert "--refresh" in argv and "7" in argv
    assert "--sample-average" in argv and "1h" in argv
    assert "--csv" in argv and "/tmp/out.csv" in argv
    assert "--no-color" in argv
    assert "--log-api-calls" in argv
    assert "--export-openmetrics" in argv
    assert "--openmetrics-file" in argv and "/tmp/om.jsonl" in argv
    joined = " ".join(argv)
    assert "password" not in joined.lower()
    assert "token" not in joined.lower()


def test_build_argv_omits_default_port_user_refresh():
    ans = {
        "protocol": next(p for p in wizard.PROTOCOLS if p["key"] == "smb"),
        "version": None,
        "vms": "host",
        "port": wizard.DEFAULT_PORT,
        "user": wizard.DEFAULT_USER,
        "scope_value": None,
        "refresh": wizard.DEFAULT_REFRESH,
        "sample_average": None,
        "csv": None,
        "no_color": False,
        "log_api_calls": False,
        "export_openmetrics": False,
        "openmetrics_file": None,
    }
    argv = wizard._build_argv(ans)
    assert argv == ["--smb", "--vms", "host"]


def test_build_argv_nfs_includes_version():
    ans = {
        "protocol": next(p for p in wizard.PROTOCOLS if p["key"] == "nfs"),
        "version": "4.1",
        "vms": "host",
        "port": wizard.DEFAULT_PORT,
        "user": wizard.DEFAULT_USER,
        "scope_value": None,
        "refresh": wizard.DEFAULT_REFRESH,
        "sample_average": None,
        "csv": None,
        "no_color": False,
        "log_api_calls": False,
        "export_openmetrics": False,
        "openmetrics_file": None,
    }
    argv = wizard._build_argv(ans)
    assert argv[:3] == ["--nfs", "--version=4.1", "--vms"]


def test_validate_port_and_refresh():
    assert wizard._validate_port("443") is None
    assert wizard._validate_port("0") is not None
    assert wizard._validate_port("abc") is not None
    assert wizard._validate_refresh("5") is None
    assert wizard._validate_refresh("0") is not None


def test_equivalent_cli_quotes_spaces():
    line = wizard._equivalent_cli(["--s3", "--vms", "my host", "--csv", "a b.csv"])
    assert line == 'opstat --s3 --vms "my host" --csv "a b.csv"'


def test_run_s3_scripted_flow():
    answers = iter([
        "4",          # protocol: S3
        "vms.test",   # host
        "",           # port default
        "",           # user default
        "1",          # password auth
        "secret",     # getpass
        "my-bucket",  # scope
        "n",          # advanced
        "n",          # openmetrics
        "1",          # start
    ])
    environ = {}

    argv = wizard.run(
        input_fn=lambda _prompt: next(answers),
        output_fn=lambda *_a, **_k: None,
        getpass_fn=lambda _prompt: next(answers),
        config_loader=lambda _path: None,
        environ=environ,
    )
    assert argv is not None
    assert argv[0] == "--s3"
    assert "--vms" in argv and "vms.test" in argv
    assert "--buckets" in argv and "my-bucket" in argv
    assert environ.get("VAST_PASSWORD") == "secret"
    assert "secret" not in " ".join(argv)


def test_run_quit_returns_none():
    answers = iter(["q"])
    result = wizard.run(
        input_fn=lambda _prompt: next(answers),
        output_fn=lambda *_a, **_k: None,
        getpass_fn=lambda _p: "x",
        config_loader=lambda _path: None,
        environ={},
    )
    assert result is None
