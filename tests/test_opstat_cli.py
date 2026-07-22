"""CLI argument parsing and protocol validation tests."""

import pytest


def test_parse_s3_defaults(opstat_cli):
    args = opstat_cli["parse_args"](["--s3", "--vms", "vms.example.com"])
    assert args.s3 is True
    assert args.smb is False
    assert args.nfs is False
    assert args.block is False
    assert args.vms == "vms.example.com"
    assert args.port == 443
    assert args.user == "admin"
    assert args.refresh == 5
    assert args.buckets is None
    assert args.tenants is None


def test_parse_s3_scoping_and_runtime_flags(opstat_cli):
    args = opstat_cli["parse_args"]([
        "--s3", "--vms", "host",
        "--buckets", "a,b", "--tenants", "default",
        "--refresh", "3", "--sample-average", "10m",
        "--csv", "/tmp/s3.csv", "--no-color",
        "--discover-metrics", "--log-api-calls",
        "--export-openmetrics", "--openmetrics-file", "/tmp/s3.jsonl",
    ])
    assert args.buckets == "a,b"
    assert args.tenants == "default"
    assert args.refresh == 3
    assert args.sample_average == "10m"
    assert args.csv == "/tmp/s3.csv"
    assert args.no_color is True
    assert args.discover_metrics is True
    assert args.log_api_calls is True
    assert args.export_openmetrics is True
    assert args.openmetrics_file == "/tmp/s3.jsonl"


def test_bucket_alias_sets_buckets(opstat_cli):
    args = opstat_cli["parse_args"](["--s3", "--vms", "h", "--bucket", "only"])
    assert args.buckets == "only"


def test_tenant_alias_sets_tenants(opstat_cli):
    args = opstat_cli["parse_args"](["--s3", "--vms", "h", "--tenant", "prod"])
    assert args.tenants == "prod"


@pytest.mark.parametrize(
    "argv",
    [
        ["--nfs", "--version=3.0", "--vms", "x", "--buckets", "b"],
        ["--smb", "--vms", "x", "--tenants", "t"],
        ["--block", "--nvme-over-tcp", "--vms", "x", "--bucket", "b"],
    ],
)
def test_s3_scope_rejected_on_other_protocols(opstat_cli, argv):
    with pytest.raises(SystemExit) as exc:
        opstat_cli["parse_args"](argv)
    assert "--s3" in str(exc.value)


def test_clients_only_with_smb(opstat_cli):
    with pytest.raises(SystemExit) as exc:
        opstat_cli["parse_args"](["--s3", "--vms", "x", "--clients", "10.0.0.1"])
    assert "--smb" in str(exc.value)


def test_nfs_version_aliases_and_required(opstat_cli):
    args = opstat_cli["parse_args"](["--nfs", "--version=3", "--vms", "h"])
    assert args.protocol_version == "3.0"
    args = opstat_cli["parse_args"](["--nfs", "--version=4", "--vms", "h"])
    assert args.protocol_version == "4.1"
    with pytest.raises(SystemExit):
        opstat_cli["parse_args"](["--nfs", "--vms", "h"])
    with pytest.raises(SystemExit) as exc:
        opstat_cli["parse_args"](["--nfs", "--version=4.2", "--vms", "h"])
    assert "not implemented" in str(exc.value).lower()


def test_block_requires_nvme_over_tcp(opstat_cli):
    with pytest.raises(SystemExit) as exc:
        opstat_cli["parse_args"](["--block", "--vms", "h"])
    assert "--nvme-over-tcp" in str(exc.value)


def test_mutually_exclusive_protocols(opstat_cli):
    with pytest.raises(SystemExit):
        opstat_cli["parse_args"](["--s3", "--smb", "--vms", "h"])


def test_dispatch_routes_s3(opstat_cli, monkeypatch):
    calls = []

    def fake_run(args):
        calls.append(args)
        return 0

    monkeypatch.setattr(opstat_cli["s3"], "run", fake_run)
    args = opstat_cli["parse_args"](["--s3", "--vms", "h"])
    assert opstat_cli["dispatch"](args) == 0
    assert len(calls) == 1
    assert calls[0].s3 is True


def test_epilog_mentions_s3(opstat_cli):
    assert "--s3" in opstat_cli["EPILOG"]
    assert "S3 object storage" in opstat_cli["EPILOG"]
