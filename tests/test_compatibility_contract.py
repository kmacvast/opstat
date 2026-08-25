"""Cross-release compatibility contracts (D-018).

opstat has one published release, `v0.1.2`, pointing at a commit far behind
main, so a real released-version upgrade cannot be validated yet. What can be
pinned today is the *source-level* contract: the surfaces D-018 declares
stable, so that breaking one becomes a deliberate act with a failing test
rather than an accident.

Scope discipline matters as much as coverage here. These tests deliberately
do NOT freeze the TUI, diagnostic wording, `--discover-metrics` output, row
ordering, or the `--log-api-calls` prose - all of which D-018 declares free to
change. Freezing them would make ordinary development painful for no
compatibility benefit. Where D-018 says a surface may GROW (CSV columns,
JSON Lines keys and attributes), these tests assert a superset relation, never
equality, so the permitted change passes untouched.

Reused rather than duplicated: the CLI surface is pinned by
`tests/test_cli_contract.py`, the version output by
`tests/test_version_contract.py`, artifact naming by
`tests/test_packaging_contract.py`, and the API log's private mode and
`OPSTAT_API_LOG_DIR` handling by `tests/test_vast_api_log_destination.py`.
"""

from __future__ import annotations

import ast
import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import openmetrics

ROOT = Path(__file__).resolve().parent.parent


def _dispatched_engines():
    """The engines `opstat` actually dispatches to, derived from its source.

    Hardcoding the list would make the classification test below compare one
    literal to another and pass even when a new engine ships unclassified.
    """
    tree = ast.parse((ROOT / "opstat").read_text())
    found = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "run"
                and isinstance(node.func.value, ast.Name)):
            name = node.func.value.id
            path = ROOT / (name + ".py")
            # A protocol engine is one that exports telemetry. This is what
            # separates the five engines from `wizard`, which opstat also
            # dispatches to via .run().
            if path.exists() and "openmetrics.configure" in path.read_text():
                found.add(name)
    assert found, "no engine dispatch found in ./opstat - has it been renamed?"
    return found


ENGINES = _dispatched_engines()

# The published CSV schemas. FROZEN BASELINE - do not edit these lists to make
# a test pass. D-018 allows a release to APPEND a column, and the prefix test
# below accepts that with no edit here. Rewriting a schema would silently
# rebase the baseline and destroy the additive guarantee it exists to provide.
# nfs_v3, smb and nvme_tcp are byte-identical to the v0.1.2 tag; s3 arrived
# with the S3 engine, which did not exist then.
CSV_SCHEMAS = {
    "nfs_v3": [
        "local_time", "runtime", "vms", "port", "cluster", "cluster_id",
        "rpc_monitor_id", "bw_monitor_id", "sample_mode", "api_time_frame",
        "selected_sample", "rpc", "operations_per_sec", "percent_workload",
        "avg_latency_us", "run_min_latency_us", "run_max_latency_us",
        "run_mean_latency_us", "avg_throughput_gb_sec", "min_throughput_gb_sec",
        "max_throughput_gb_sec", "avg_io_size_bytes",
    ],
    "smb": [
        "local_time", "runtime", "vms", "port", "cluster", "cluster_id",
        "headline_monitor_id", "sample_mode", "api_time_frame",
        "selected_sample", "metrics_source", "panel", "label", "ops_per_sec",
        "pct_workload", "avg_latency_us", "throughput_mb_sec", "avg_io_bytes",
    ],
    "s3": [
        "local_time", "runtime", "vms", "port", "cluster", "cluster_id",
        "headline_monitor_id", "sample_mode", "api_time_frame",
        "selected_sample", "metrics_source", "panel", "label", "ops_per_sec",
        "pct_workload", "avg_latency_us", "throughput_mb_sec", "avg_io_bytes",
    ],
    "nvme_tcp": [
        "local_time", "runtime", "vms", "port", "cluster", "cluster_id",
        "ops_monitor_id", "proto_monitor_id", "sample_mode", "api_time_frame",
        "selected_sample", "operation", "category", "ops_per_sec",
        "percent_workload", "avg_latency_us", "throughput_mb_sec",
        "avg_io_bytes",
    ],
}

# nfs_v41 accepts --csv and writes nothing (D-018 "known gaps").
NO_CSV_ENGINES = {"nfs_v41"}

JSONL_REQUIRED_KEYS = {"timestamp", "metric_name", "metric_type", "value",
                       "unit", "attributes"}
JSONL_ATTRIBUTE_KEYS = {"cluster", "vms", "protocol", "operation", "category",
                        "drill_mode", "target_name"}
ENGINE_PROTOCOL_IDS = {
    "nfs_v3": "nfs3",
    "nfs_v41": "nfs41",
    "smb": "smb",
    "s3": "s3",
    "nvme_tcp": "nvme_tcp",
}


# ---------------------------------------------------------------------------
# CSV: column identity and order
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("engine", sorted(CSV_SCHEMAS))
def test_csv_columns_may_only_grow(engine):
    """The whole CSV rule in one assertion: the frozen baseline stays a prefix.

    Appending a column passes with no edit to this file - D-018 allows it.
    Renaming, reordering or removing one fails, because the prefix no longer
    matches. A consumer reading by index breaks on a reorder; one reading by
    header breaks on a rename.
    """
    module = importlib.import_module(engine)
    recorded = CSV_SCHEMAS[engine]
    actual = list(module.CSV_HEADER)
    assert actual[:len(recorded)] == recorded, (
        "%s renamed, reordered or removed a published CSV column, which is a "
        "breaking change under D-018.\n  published: %r\n  now:       %r\n"
        "Appending a NEW column at the end is allowed and needs no change "
        "here - do NOT rewrite CSV_SCHEMAS to make this pass."
        % (engine, recorded, actual[:len(recorded)]))


def test_nfs_v41_still_has_no_csv_writer():
    """Bookkeeping marker for a documented gap, not a prohibition.

    nfs_v41 accepts --csv and silently writes nothing. If a writer is added -
    which D-018 says is additive and welcome - this fails, to force the new
    schema into CSV_SCHEMAS in the same commit rather than shipping an
    unrecorded one.
    """
    module = importlib.import_module("nfs_v41")
    source = (ROOT / "nfs_v41.py").read_text().lower()
    assert not hasattr(module, "CSV_HEADER") and "csv" not in source, (
        "nfs_v41 gained CSV support. That is an allowed, additive change: add "
        "its header to CSV_SCHEMAS, drop it from NO_CSV_ENGINES, and correct "
        "the 'known gaps' section of D-018 and the docs that describe --csv.")


def test_docs_do_not_promise_nfs41_csv():
    """Four documentation sites promised NFSv4.1 CSV output that no code
    produces, and the gap survived a correction pass because nothing checked.

    Anchored to a behavioural fact rather than to prose: this only applies
    while nfs_v41 has no CSV writer, which the test above pins.
    """
    allowed = ("ignore", "writes nothing", "no csv", "does nothing",
               "accepted and")
    # docs/decisions/ is deliberately excluded: a decision record's job is to
    # describe the gap, so scanning it re-flags its own correction notice.
    offenders = []
    for doc in sorted(ROOT.glob("*.md")) + sorted(ROOT.glob("docs/*.md")):
        for number, line in enumerate(doc.read_text().splitlines(), 1):
            lowered = line.lower()
            if "--csv" not in lowered:
                continue
            if not any(tag in lowered for tag in ("4.1", "nfs41", "v41")):
                continue
            # Saying the flag is IGNORED is the correction, not the defect.
            if any(word in lowered for word in allowed):
                continue
            offenders.append("%s:%d: %s" % (doc.name, number, line.strip()))
    assert not offenders, (
        "documentation promises NFSv4.1 CSV output that nfs_v41.py does not "
        "produce:\n  " + "\n  ".join(offenders))


def test_every_engine_is_classified():
    """Every engine `opstat` dispatches to must be recorded as CSV-writing or
    not, so a new protocol cannot ship with an undeclared export schema."""
    classified = set(CSV_SCHEMAS) | NO_CSV_ENGINES
    assert ENGINES <= classified, (
        "engine(s) dispatched by ./opstat with no recorded CSV status: %s - "
        "add the CSV_HEADER to CSV_SCHEMAS, or the engine to NO_CSV_ENGINES"
        % sorted(ENGINES - classified))


# ---------------------------------------------------------------------------
# CSV: the header actually describes the rows
# ---------------------------------------------------------------------------
def _drive_csv_writer(engine, monkeypatch, tmp_path, ops_sec, avg_us):
    """Run the engine's real CSV writer over one row; return the parsed file."""
    import csv as csv_module

    from datetime import datetime

    module = importlib.import_module(engine)
    path = tmp_path / (engine + ".csv")
    monkeypatch.setattr(module, "CSV_FILE", str(path))
    monkeypatch.setattr(module, "SAMPLE_AVERAGE_MODE", False, raising=False)
    monkeypatch.setattr(module, "RUN_STARTED_AT", datetime.now(), raising=False)
    row = {"label": "READ", "category": "data", "ops_sec": ops_sec,
           "pct": 50.0, "avg_us": avg_us, "bw_gbs": 1.0, "bw_min_gbs": 1.0,
           "bw_max_gbs": 1.0, "bw_mbs": 1.0, "avg_io_bytes": 4096.0,
           "run_min_us": 1.0, "run_max_us": 2.0, "run_mean_us": 1.5}
    module.ensure_csv_file()
    if hasattr(module, "write_csv_rows"):
        module.write_csv_rows([row], "2026-08-25T12:00:00Z")
    else:
        module.write_csv_snapshot(
            {"data": [row], "metadata": [], "opcodes": []},
            "2026-08-25T12:00:00Z")
    with open(str(path), newline="") as handle:
        return list(csv_module.reader(handle))


@pytest.mark.parametrize("engine", sorted(CSV_SCHEMAS))
def test_csv_rows_match_the_header_width(engine, monkeypatch, tmp_path):
    """The header is only a contract if the rows line up with it.

    Nothing else pins this: a writer that reorders or drops a field keeps the
    published header and silently mislabels every column after the mismatch.
    """
    rows = _drive_csv_writer(engine, monkeypatch, tmp_path, 12.5, 900.0)
    header = list(importlib.import_module(engine).CSV_HEADER)
    assert rows[0] == header, (
        "%s wrote a header row that is not CSV_HEADER" % engine)
    assert len(rows) > 1, "%s wrote no data row" % engine
    for data in rows[1:]:
        assert len(data) == len(header), (
            "%s emits %d fields under a %d-column header - every column after "
            "the mismatch is mislabelled" % (engine, len(data), len(header)))


@pytest.mark.parametrize("engine", sorted(CSV_SCHEMAS))
def test_csv_keeps_measured_zero_distinct_from_unavailable(
        engine, monkeypatch, tmp_path):
    """A measured 0 renders as 0; unavailable renders as an empty field.

    AGENTS.md: "Zero is not the same as unavailable." In CSV that distinction
    is carried by empty-versus-0, and no other test asserts it. nvme_tcp gets
    it right only incidentally, via csv module None handling rather than a
    csv_value helper, so it is worth holding in place.
    """
    header = list(importlib.import_module(engine).CSV_HEADER)
    ops = header.index("ops_per_sec" if "ops_per_sec" in header
                       else "operations_per_sec")
    latency = header.index("avg_latency_us")

    data = _drive_csv_writer(engine, monkeypatch, tmp_path, 0.0, None)[1]
    assert data[ops] != "", (
        "%s wrote a measured 0.00 ops/s as an empty field - a real zero is "
        "information (no traffic) and must not read as missing data" % engine)
    assert float(data[ops]) == 0.0
    assert data[latency] == "", (
        "%s wrote an unavailable latency as %r rather than an empty field - "
        "that asserts a measurement the cluster never made"
        % (engine, data[latency]))


# ---------------------------------------------------------------------------
# JSON Lines / OpenMetrics: record shape
# ---------------------------------------------------------------------------
def _records(path):
    lines = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    assert lines, "the exporter wrote nothing"
    return lines


def emit_snapshot(tmp_path, protocol="s3"):
    """One exported line, through the production writer, with the argument
    shape production actually uses: every engine passes drill_mode=None and
    target_name=None, so the defaulting branches are part of the contract."""
    path = tmp_path / "export.jsonl"
    openmetrics.configure(True, str(path), protocol, "vms.example")
    try:
        openmetrics.export_snapshot(
            "cluster-a", None, None,
            [{"operation": "READ", "category": "data", "ops_sec": 12.5,
              "avg_us": 900.0, "bw_bytes_sec": 2.0e9, "io_bytes": 65536.0}],
            sample="2026-08-25T12:00:00Z")
    finally:
        openmetrics.close()
    return path


def emit_drill(tmp_path, protocol="s3"):
    """The drill path builds its own attribute dict on a separate branch; a
    key renamed only there would pass every snapshot-only assertion."""
    path = tmp_path / "export.jsonl"
    openmetrics.configure(True, str(path), protocol, "vms.example")
    try:
        openmetrics.export_drill(
            "cluster-a", "view",
            [{"name": "/view-a", "total_ops": 4.0, "avg_us": 800.0,
              "bw_gbs": 1.0, "bw_mbs": 1024.0}],
            sample="2026-08-25T12:00:00Z")
    finally:
        openmetrics.close()
    return path


EMITTERS = [emit_snapshot, emit_drill]
EMITTER_IDS = ["snapshot", "drill"]


@pytest.mark.parametrize("emit", EMITTERS, ids=EMITTER_IDS)
def test_jsonl_record_keys_are_present(emit, tmp_path):
    """The published keys stay. Additional keys are allowed - D-018 says
    consumers must tolerate keys they do not recognise - so this is a subset
    check, not equality."""
    for record in _records(emit(tmp_path)):
        missing = JSONL_REQUIRED_KEYS - set(record)
        assert not missing, (
            "JSON Lines record lost published key(s) %s; removing or renaming "
            "one breaks every consumer (D-018)" % sorted(missing))


@pytest.mark.parametrize("emit", EMITTERS, ids=EMITTER_IDS)
def test_jsonl_attribute_keys_are_present(emit, tmp_path):
    """Same rule for attributes: removal breaks consumers, addition does not.

    D-018 explicitly permits new attribute keys, so asserting equality here
    would block a change the policy allows - adding a `tenant` attribute to
    the S3 drill, say.
    """
    for record in _records(emit(tmp_path)):
        missing = JSONL_ATTRIBUTE_KEYS - set(record["attributes"])
        assert not missing, (
            "JSON Lines attributes lost published key(s) %s. Adding a NEW "
            "attribute is allowed and needs no change here." % sorted(missing))
        for key, value in record["attributes"].items():
            assert isinstance(value, str), (
                "attributes must stay a flat string map; %r is %s"
                % (key, type(value).__name__))


@pytest.mark.parametrize("emit", EMITTERS, ids=EMITTER_IDS)
def test_jsonl_value_stays_a_json_number(emit, tmp_path):
    for record in _records(emit(tmp_path)):
        value = record["value"]
        assert isinstance(value, (int, float)) and not isinstance(value, bool), (
            "value must stay a JSON number - a consumer parsing it as such "
            "breaks on a string; got %r" % (value,))


@pytest.mark.parametrize("emit", EMITTERS, ids=EMITTER_IDS)
def test_jsonl_metric_naming_is_unchanged(emit, tmp_path):
    for record in _records(emit(tmp_path)):
        assert record["metric_name"].startswith("vast.s3."), (
            "metric names are vast.{protocol}.{suffix}; a dashboard query is "
            "written against that exact string: %r" % record["metric_name"])


@pytest.mark.parametrize("emit", EMITTERS, ids=EMITTER_IDS)
def test_jsonl_is_one_json_object_per_line(emit, tmp_path):
    """The framing itself is the contract: line-delimited, one object per
    line, no pretty-printing and no array wrapper."""
    raw = emit(tmp_path).read_text()
    assert raw.endswith("\n"), "the last record must be newline-terminated"
    for line in raw.splitlines():
        if line.strip():
            assert isinstance(json.loads(line), dict), (
                "each line must be a single JSON object, not %r" % line[:60])


def test_jsonl_metric_suffixes_and_units_are_unchanged():
    """Suffix/unit pairs are what a dashboard actually selects on. New pairs
    may be added; an existing one must not change its unit."""
    recorded = {
        ("ops_sec", "operations", "gauge", "ops/s"),
        ("avg_us", "latency", "gauge", "microseconds"),
        ("bw_bytes_sec", "throughput", "gauge", "bytes/s"),
        ("io_bytes", "io_size", "gauge", "bytes"),
    }
    assert recorded <= set(openmetrics._METRIC_DEFS), (
        "a published metric suffix or unit changed: %s"
        % sorted(recorded - set(openmetrics._METRIC_DEFS)))


def test_measured_zero_is_emitted_and_unavailable_is_omitted(tmp_path):
    """The zero-versus-unavailable rule, at the exporter.

    A measured 0.0 must produce a real line carrying 0.0; an unavailable
    metric must produce NO line, never a JSON null. This holds in one
    direction only - D-018 records that the drill path collapses a measured
    zero to None *before* the exporter sees it, which is an open defect.
    """
    path = tmp_path / "mixed.jsonl"
    openmetrics.configure(True, str(path), "nfs3", "vms.example")
    try:
        openmetrics.export_snapshot(
            "cluster-a", None, None,
            [{"operation": "READ", "category": "data",
              "ops_sec": 0.0, "avg_us": None,
              "bw_bytes_sec": None, "io_bytes": None}],
            sample="2026-08-25T12:00:00Z")
    finally:
        openmetrics.close()
    records = _records(path)
    names = [r["metric_name"] for r in records]
    assert names == ["vast.nfs3.operations"], (
        "expected exactly the measured metric to be emitted, got %s" % names)
    assert records[0]["value"] == 0.0, (
        "a measured 0.00 ops/s must be exported as a real zero - it means "
        "'no traffic', which is information (AGENTS.md)")


def test_protocol_ids_are_bound_to_their_engines():
    """Each engine must keep passing ITS id to openmetrics.configure.

    Asserting only that the five ids exist somewhere would pass if two
    engines swapped them, which would silently invert every downstream NFS
    query. Resolution handles keyword arguments, module-level constants and
    import aliasing, so an internal refactor does not fail this.
    """
    for engine, expected in sorted(ENGINE_PROTOCOL_IDS.items()):
        path = ROOT / (engine + ".py")
        assert path.exists(), (
            "engine module %s is gone; if it was renamed, update "
            "ENGINE_PROTOCOL_IDS in the same commit" % path.name)
        tree = ast.parse(path.read_text())
        constants = {}
        for node in tree.body:
            if (isinstance(node, ast.Assign) and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)
                    and isinstance(node.value, ast.Constant)):
                constants[node.targets[0].id] = node.value.value

        def literal(argument):
            if isinstance(argument, ast.Constant):
                return argument.value
            if isinstance(argument, ast.Name):
                return constants.get(argument.id)
            return None

        found = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "attr", getattr(node.func, "id", ""))
            if name != "configure":
                continue
            for argument in node.args:
                found.add(literal(argument))
            for keyword in node.keywords:
                if keyword.arg in (None, "protocol"):
                    found.add(literal(keyword.value))
        assert expected in found, (
            "%s no longer passes protocol id %r to openmetrics.configure "
            "(found %s). The id is embedded in every exported metric name, so "
            "changing it rewrites every downstream query."
            % (engine, expected,
               sorted(v for v in found if isinstance(v, str))))


# ---------------------------------------------------------------------------
# Persisted state: there is none, and that is the contract
# ---------------------------------------------------------------------------
# Call targets that would create state opstat could read back later. A path
# built from an operator-supplied variable is NOT one of these: expanduser(
# path) so that `--csv ~/out.csv` works is a fix, not a compatibility event.
# So the rule fires only on a literal path or a home-directory lookup.
_HOME_KEYS = {"HOME", "USERPROFILE", "XDG_CONFIG_HOME", "XDG_CACHE_HOME",
              "XDG_DATA_HOME", "APPDATA", "LOCALAPPDATA"}
_STATE_CALLS = {"home", "connect", "expanduser", "makedirs", "mkdir",
                "write_text", "write_bytes", "open"}
# Line-scoped, not file-scoped: the wizard reads ~/.vastconf only through an
# injected loader, and test_vastconf_is_not_read_in_production proves the
# production path never reaches the filesystem at all.
_EXEMPT = {("wizard.py", "~/.vastconf")}


def _literal_strings(node):
    return [a.value for a in node.args
            if isinstance(a, ast.Constant) and isinstance(a.value, str)]


def _state_offenders(path):
    offenders = []
    for node in ast.walk(ast.parse(path.read_text())):
        # os.environ["HOME"] and friends
        if isinstance(node, ast.Subscript):
            key = getattr(node.slice, "value", None)
            if isinstance(key, str) and key in _HOME_KEYS:
                offenders.append("%s:%d: home lookup %r"
                                 % (path.name, node.lineno, key))
            continue
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "attr", getattr(node.func, "id", ""))
        literals = _literal_strings(node)
        # os.getenv("HOME") / os.environ.get("HOME")
        if name in ("getenv", "get"):
            if literals and literals[0] in _HOME_KEYS:
                offenders.append("%s:%d: home lookup %r"
                                 % (path.name, node.lineno, literals[0]))
            continue
        if name not in _STATE_CALLS:
            continue
        if name == "home":
            offenders.append("%s:%d: Path.home()" % (path.name, node.lineno))
            continue
        if name == "open":
            # A LITERAL path opened for writing. open(variable, "w") is an
            # operator-named file and is deliberately allowed.
            modes = [v for v in literals if v and set(v) <= set("rwaxbt+")]
            targets = [v for v in literals if v not in modes]
            if targets and any("w" in m or "a" in m for m in modes):
                offenders.append("%s:%d: open(%r, write)"
                                 % (path.name, node.lineno, targets[0]))
            continue
        for value in literals:
            if (path.name, value) in _EXEMPT:
                continue
            offenders.append("%s:%d: %s(%r)"
                             % (path.name, node.lineno, name, value))
    return offenders


def test_opstat_declares_no_state_bearing_paths():
    """Static half of D-018's headline property: no source file names a fixed
    path or a home directory it could persist settings into.

    Deliberately does NOT fire on expanduser(variable) or open(variable, "w")
    - those are operator-named files, and refusing them would block
    supporting `--csv ~/out.csv`.
    """
    offenders = []
    for path in sorted(ROOT.glob("*.py")) + [ROOT / "opstat"]:
        offenders.extend(_state_offenders(path))
    assert not offenders, (
        "opstat may have gained persisted state, which would create an "
        "upgrade/migration surface it does not have today (D-018):\n  "
        + "\n  ".join(offenders))


def test_a_no_cluster_run_writes_nothing_outside_its_arguments(tmp_path):
    """Behavioural half: run the real entrypoint with a private HOME and cwd
    and confirm it leaves both untouched.

    Covers only the paths that need no cluster (--help, -V, an argument
    error); a full dashboard run needs a VMS and is out of reach here.
    """
    home = tmp_path / "home"
    work = tmp_path / "work"
    temp = tmp_path / "temp"
    for directory in (home, work, temp):
        directory.mkdir()
    environment = dict(os.environ)
    environment.update(HOME=str(home), USERPROFILE=str(home), TMPDIR=str(temp))
    environment.pop("VAST_TOKEN", None)
    environment.pop("VAST_PASSWORD", None)

    for argv in (["--help"], ["-V"], ["--nfs", "--vms", "host"]):
        subprocess.run([sys.executable, str(ROOT / "opstat")] + argv,
                       cwd=str(work), env=environment,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    stray_home = sorted(p.name for p in home.iterdir())
    stray_work = sorted(p.name for p in work.iterdir())
    assert not stray_home, (
        "opstat created %s in HOME - it persists no state (D-018), and a "
        "config or cache file is a compatibility event" % stray_home)
    assert not stray_work, (
        "opstat created %s in the working directory" % stray_work)


def test_vastconf_is_not_read_in_production(monkeypatch):
    """~/.vastconf is NOT a compatibility surface, because nothing reads it.

    Asserting the return value alone is too weak: _load_config swallows every
    exception, so a real loader would still return None on a machine that has
    no ~/.vastconf. This proves it never touches the filesystem at all.
    """
    import builtins

    import wizard

    def refuse(*args, **kwargs):
        raise AssertionError("the production path must not touch the filesystem")

    monkeypatch.setattr(builtins, "open", refuse)
    monkeypatch.setattr(os.path, "expanduser", refuse)
    assert wizard._load_config(None) is None, (
        "the wizard gained a production ~/.vastconf loader - that file now "
        "persists between versions and needs a documented format (D-018)")


# ---------------------------------------------------------------------------
# Exit codes and credential precedence
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("argv,expected", [
    (["--nosuchflag"], 2),                              # argparse rejection
    (["--nfs", "--vms", "host"], 1),                    # --version required
    (["--smb", "--vms", "host", "--bucket", "b"], 1),   # --bucket is S3-only
])
def test_exit_codes_are_unchanged(argv, expected):
    """A wrapper script branches on these: argparse failure is 2, a
    protocol-validation failure is 1. Nothing previously asserted the 1."""
    result = subprocess.run(
        [sys.executable, str(ROOT / "opstat")] + argv,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert result.returncode == expected, (
        "opstat %s exited %d, expected %d - callers branch on these codes "
        "(D-018)" % (" ".join(argv), result.returncode, expected))


def test_credential_precedence_is_unchanged(monkeypatch):
    """VAST_TOKEN > --password > VAST_PASSWORD > interactive prompt.

    Exercises resolve_auth rather than reading the source: an earlier version
    of this test asserted that "VAST_TOKEN" appeared before "VAST_PASSWORD"
    in the file, which a mutation that disabled the token path entirely still
    satisfied.
    """
    import vast_common

    def resolve(**env):
        for name in ("VAST_TOKEN", "VAST_PASSWORD"):
            monkeypatch.delenv(name, raising=False)
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        monkeypatch.setattr(
            vast_common.getpass, "getpass",
            lambda *a, **k: pytest.fail("the prompt must not be reached"))
        return vast_common.resolve_auth("admin", "vms", None, "ua")

    headers, auth, password = resolve(VAST_TOKEN="tok")
    assert headers["Authorization"] == "Bearer tok"
    assert auth is None and password is None, (
        "a token run must not also acquire a password")

    headers, auth, password = resolve(VAST_TOKEN="tok", VAST_PASSWORD="pw")
    assert headers["Authorization"] == "Bearer tok", (
        "VAST_PASSWORD must not override VAST_TOKEN")

    # --password loses to VAST_TOKEN too.
    for name in ("VAST_TOKEN", "VAST_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("VAST_TOKEN", "tok")
    headers, _auth, _pw = vast_common.resolve_auth("admin", "vms", "cli-pw", "ua")
    assert headers["Authorization"] == "Bearer tok", (
        "--password must not override VAST_TOKEN")

    # Without a token, --password beats VAST_PASSWORD.
    monkeypatch.delenv("VAST_TOKEN", raising=False)
    monkeypatch.setenv("VAST_PASSWORD", "env-pw")
    _headers, _auth, password = vast_common.resolve_auth(
        "admin", "vms", "cli-pw", "ua")
    assert password == "cli-pw", "--password must beat VAST_PASSWORD"
