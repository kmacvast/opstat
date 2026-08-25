"""CLI contract: parser, `--help` and README must describe one interface.

The parser is the source of truth. This suite derives the option set from
`opstat.build_parser()` and holds the user-facing documentation to it, so the
three cannot drift apart again silently.

The drift this was written for: `--no-menu` was implemented, behaviour-tested
in `tests/test_wizard.py`, described in `wizard.should_launch`'s docstring and
documented in README's user-facing option table - yet hidden from `--help` by
`argparse.SUPPRESS`, so a user reading `--help` could not discover a flag the
README told them to use.

Deliberately NOT a Markdown parser: README's option tables are pipe tables,
and every option row names its flags inside backticks. Reading those rows is
enough, and it stays legible when someone edits the tables.
"""

from __future__ import annotations

import argparse
import re
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"

# argparse builds these; every CLI has them and no README documents them.
BUILTINS = {"-h", "--help"}

# Options intentionally kept out of `--help`, each with the reason. An entry
# here is a decision on the record, not a way to silence this suite: a hidden
# option must ALSO stay out of the user-facing README tables (asserted below).
INTENTIONALLY_HIDDEN = {
    "--port": (
        "Compatibility alias for --vms-port, accepted since the repository "
        "migration. Still works for older scripts, but documenting two "
        "spellings of one option is the drift this suite prevents."
    ),
}


@pytest.fixture(scope="module")
def cli_module():
    """The opstat entrypoint as a namespace (same loader as conftest's
    opstat_cli fixture, module-scoped so the CLI is read once)."""
    return runpy.run_path(str(ROOT / "opstat"), run_name="opstat_cli")


@pytest.fixture(scope="module")
def parser(cli_module):
    return cli_module["build_parser"]()


def parser_options(parser):
    """(visible, hidden) option strings, straight from the parser."""
    visible, hidden = set(), set()
    for action in parser._actions:
        target = hidden if action.help == argparse.SUPPRESS else visible
        target.update(action.option_strings)
    return visible, hidden


def readme_table_flags():
    """Flags named in README's pipe-table option rows.

    Only table rows: prose and fenced examples also mention flags belonging
    to other tools entirely (PyInstaller's --onefile/--clean/--noconfirm,
    systemd's --now), and those must never be mistaken for opstat options.

    Every pipe table is scanned, not just the option tables, because the
    protocol-selection table is where --nfs/--smb/--s3/--block/
    --nvme-over-tcp/--version are actually documented. The consequence to
    remember: a future table listing a THIRD-PARTY tool's flags would be
    read as opstat drift - document those in prose or a fenced block.
    """
    flags = set()
    for line in README.read_text().splitlines():
        if not line.startswith("|"):
            continue
        for span in re.findall(r"`([^`]*)`", line):
            flags.update(re.findall(r"(?<![\w-])(--?[A-Za-z][\w-]*)", span))
    return flags


# ---------------------------------------------------------------------------
# Parser <-> README
# ---------------------------------------------------------------------------
def test_every_documented_flag_exists_in_the_parser(parser):
    visible, hidden = parser_options(parser)
    documented = readme_table_flags()
    unknown = documented - visible - hidden - BUILTINS
    assert not unknown, (
        f"README documents flags the parser does not accept: {sorted(unknown)}")


def test_every_visible_option_is_documented_in_readme(parser):
    visible, _hidden = parser_options(parser)
    missing = visible - readme_table_flags() - BUILTINS
    assert not missing, (
        f"supported options missing from README's option tables: {sorted(missing)}")


def test_hidden_options_are_allowlisted_with_a_reason(parser):
    _visible, hidden = parser_options(parser)
    undeclared = hidden - set(INTENTIONALLY_HIDDEN)
    assert not undeclared, (
        f"options hidden from --help without a recorded reason: "
        f"{sorted(undeclared)}. Either give them help text or add them to "
        f"INTENTIONALLY_HIDDEN with the rationale.")
    for flag, reason in INTENTIONALLY_HIDDEN.items():
        assert reason.strip(), f"{flag} needs a real rationale"


def test_hidden_options_are_not_advertised_to_users(parser):
    """A hidden option must not appear in the user-facing option tables -
    that combination is exactly the --no-menu drift."""
    _visible, hidden = parser_options(parser)
    advertised = hidden & readme_table_flags()
    assert not advertised, (
        f"hidden from --help yet documented for users: {sorted(advertised)}")


def test_stale_allowlist_entries_are_caught(parser):
    _visible, hidden = parser_options(parser)
    stale = set(INTENTIONALLY_HIDDEN) - hidden
    assert not stale, (
        f"allowlisted as hidden but no longer hidden (or removed): {sorted(stale)}")


# ---------------------------------------------------------------------------
# Aliases
# ---------------------------------------------------------------------------
def test_aliases_are_documented_together(parser):
    """Every MULTI-SPELLING action (--menu/-i, -V/--tool-version) is named
    in README, so a user cannot meet a spelling the docs never mention. The
    singular/plural scoping pairs are separate actions and are covered by
    test_scoping_singular_aliases_share_their_plural_destination."""
    documented = readme_table_flags()
    for action in parser._actions:
        if action.help == argparse.SUPPRESS or len(action.option_strings) < 2:
            continue
        options = set(action.option_strings) - BUILTINS
        if not options:
            continue
        missing = options - documented
        assert not missing, (
            f"alias(es) {sorted(missing)} of {sorted(options)} are undocumented")


def test_scoping_singular_aliases_share_their_plural_destination(parser):
    """--volume/--volumes, --client/--clients, --bucket/--buckets and
    --tenant/--tenants are documented as alias pairs; the parser must really
    fold each singular into its plural."""
    dests = {}
    for action in parser._actions:
        for option in action.option_strings:
            dests[option] = action.dest
    for singular, plural in (("--volume", "--volumes"), ("--client", "--clients"),
                             ("--bucket", "--buckets"), ("--tenant", "--tenants")):
        assert dests[singular] == dests[plural], (
            f"{singular} no longer shares a destination with {plural}")


# ---------------------------------------------------------------------------
# Non-opstat flags in README must not be misread as opstat options
# ---------------------------------------------------------------------------
FOREIGN_FLAGS = ("--onefile", "--clean", "--noconfirm", "--hidden-import",
                 "--paths", "--name", "--now")


def test_foreign_tool_flags_are_present_but_never_classified_as_opstat(parser):
    """README documents PyInstaller and systemd invocations too. They appear
    in prose and fenced blocks, never in an opstat option table - if one ever
    does, the extraction above would start reporting it as parser drift."""
    text = README.read_text()
    seen = [f for f in FOREIGN_FLAGS if f in text]
    assert seen, "expected PyInstaller/systemd examples in README"
    table = readme_table_flags()
    misread = set(seen) & table
    assert not misread, (
        f"non-opstat flags leaked into an opstat option table: {sorted(misread)}")


# ---------------------------------------------------------------------------
# --help / -V behaviour
# ---------------------------------------------------------------------------
def run_cli(*args):
    return subprocess.run([sys.executable, str(ROOT / "opstat"), *args],
                          capture_output=True, text=True, timeout=60)


@pytest.mark.parametrize("flag", ["--help", "-h"])
def test_help_exits_successfully(flag):
    result = run_cli(flag)
    assert result.returncode == 0, result.stderr[-400:]
    assert "usage: opstat" in result.stdout


def test_help_lists_every_visible_option(parser):
    visible, _hidden = parser_options(parser)
    out = run_cli("--help").stdout
    missing = [f for f in visible if f not in out]
    assert not missing, f"options absent from --help output: {sorted(missing)}"


def test_help_does_not_leak_hidden_options(parser):
    _visible, hidden = parser_options(parser)
    out = run_cli("--help").stdout
    # Word-bounded on purpose. The real collision class is the singular /
    # plural scoping pairs: a bare "--tenant" substring matches inside
    # "--tenants", so a naive check would report a false leak.
    leaked = [f for f in hidden if re.search(rf"(?<![\w-]){re.escape(f)}(?![\w-])", out)]
    assert not leaked, f"hidden options appeared in --help: {leaked}"


@pytest.mark.parametrize("flag", ["-V", "--tool-version"])
def test_version_flag_prints_the_real_version(flag, cli_module):
    """Compares against VERSION, not just the shape: asserting the pattern
    alone let VERSION drift to anything while README kept claiming 0.1.2."""
    result = run_cli(flag)
    assert result.returncode == 0, result.stderr[-400:]
    assert result.stdout.strip() == f"opstat {cli_module['VERSION']}"


def test_readme_version_claims_match_the_shipped_version(cli_module):
    """Every "opstat X.Y.Z" the README prints must be the version the CLI
    actually reports - the release-preparation drift FR7 will meet next."""
    version = cli_module["VERSION"]
    claims = set(re.findall(r"opstat (\d+\.\d+\.\d+)", README.read_text()))
    claims |= set(re.findall(r"\*\*Version:\*\* (\d+\.\d+\.\d+)", README.read_text()))
    assert claims, "README no longer states a version anywhere"
    wrong = {c for c in claims if c != version}
    assert not wrong, (
        f"README claims version(s) {sorted(wrong)} but the CLI reports {version}")


def test_both_version_spellings_print_the_same_thing():
    assert run_cli("-V").stdout == run_cli("--tool-version").stdout


# ---------------------------------------------------------------------------
# Protocol selection: help, epilog and README examples must agree
# ---------------------------------------------------------------------------
def test_protocol_selection_agrees_across_help_and_readme(parser):
    visible, _hidden = parser_options(parser)
    out = run_cli("--help").stdout
    for flag in ("--nfs", "--smb", "--s3", "--block", "--nvme-over-tcp"):
        assert flag in visible, f"{flag} vanished from the parser"
        assert flag in out, f"{flag} missing from --help"
        assert flag in readme_table_flags(), f"{flag} missing from README tables"
    assert "--block --nvme-over-tcp" in out, (
        "the epilog must show that --block requires --nvme-over-tcp")
    assert "--block --nvme-over-tcp" in README.read_text()


def test_supported_nfs_versions_match_between_parser_and_readme(cli_module):
    """README's protocol table and the CLI must agree on which NFS versions
    are implemented versus planned."""
    module = cli_module
    text = README.read_text()
    def status_row(version):
        """The protocol-TABLE row for a version - examples elsewhere in the
        README also contain "--nfs --version=3.0" and carry no status."""
        return [l for l in text.splitlines()
                if l.startswith("|") and f"--nfs --version={version}" in l]

    for version in module["SUPPORTED_NFS_VERSIONS"]:
        row = status_row(version)
        assert row, f"supported NFS {version} has no README protocol row"
        assert "Implemented" in row[0], (
            f"NFS {version} is supported but README does not say Implemented: "
            f"{row[0].strip()}")
    for version in module["PLANNED_NFS_VERSIONS"]:
        row = status_row(version)
        assert row and "Planned" in row[0], (
            f"NFS {version} is not implemented but README does not say Planned")


def test_usage_line_reflects_the_required_protocol_choice(parser):
    out = run_cli("--help").stdout
    assert "(--block | --nfs | --smb | --s3)" in out, (
        "usage line no longer shows that exactly one protocol is required")
    # Required renders bare; optional renders "[--vms HOST]", and the bare
    # form is a substring of the bracketed one - so match on the parser and
    # on an unbracketed usage occurrence.
    assert re.search(r"(?<!\[)--vms HOST", out), (
        "usage line no longer shows --vms as required")
    vms_action = next(a for a in parser._actions if "--vms" in a.option_strings)
    assert vms_action.required, "--vms is no longer a required option"


# ---------------------------------------------------------------------------
# Documented VALUES, not just names. The name-level guards above all passed
# while defaults, required-ness and version strings drifted freely.
# ---------------------------------------------------------------------------
def readme_option_rows():
    """{flag: full table row} for README's option tables."""
    rows = {}
    for line in README.read_text().splitlines():
        if not line.startswith("|"):
            continue
        for span in re.findall(r"`([^`]*)`", line):
            for flag in re.findall(r"(?<![\w-])(--?[A-Za-z][\w-]*)", span):
                rows.setdefault(flag, line)
    return rows


def test_documented_defaults_match_the_parser(parser, cli_module):
    """README's Default column is machine-readable, so hold it to the code:
    changing DEFAULT_PORT/USER/REFRESH used to leave README stale silently."""
    rows = readme_option_rows()
    for flag, expected in (("--vms-port", cli_module["DEFAULT_PORT"]),
                           ("--user", cli_module["DEFAULT_USER"]),
                           ("--refresh", cli_module["DEFAULT_REFRESH_SECONDS"])):
        row = rows.get(flag)
        assert row, f"{flag} has no README option row"
        assert f"`{expected}`" in row, (
            f"README's default for {flag} does not match the code value "
            f"{expected!r}: {row.strip()}")
        action = next(a for a in parser._actions if flag in a.option_strings)
        assert action.default == expected, (
            f"{flag} parser default {action.default!r} != {expected!r}")


def test_required_options_are_documented_as_required(parser):
    rows = readme_option_rows()
    for action in parser._actions:
        if not action.required:
            continue
        for flag in action.option_strings:
            row = rows.get(flag)
            assert row and "(required)" in row, (
                f"{flag} is required but README does not say so: "
                f"{(row or '').strip()}")


def test_the_port_compatibility_alias_still_works(cli_module):
    """The parser comment promises --port keeps working for older scripts;
    nothing exercised it, so breaking its dest would have gone unnoticed."""
    parse_args = cli_module["parse_args"]
    assert parse_args(["--s3", "--vms", "h", "--port", "8443"]).port == 8443
    assert parse_args(["--s3", "--vms", "h", "--vms-port", "9443"]).port == 9443
    # Default survives the alias being declared without one.
    assert parse_args(["--s3", "--vms", "h"]).port == cli_module["DEFAULT_PORT"]
