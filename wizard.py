#!/usr/bin/env python3
################################################################################
# Script Name: wizard.py
# Description: Interactive, menu-driven launcher for opstat. When the tool
#              is run on a TTY with no arguments (or with --menu), this wizard
#              asks a short, ordered set of questions and produces an argv list
#              that is fed back through the normal argparse validator + dispatch.
#              Secrets are collected securely and exported via environment
#              variables (VAST_PASSWORD / VAST_TOKEN) so they never land on the
#              command line, in `ps`, or in the printed "equivalent command".
#
# Author: KMac kmac@vastdata.com
# Version: 1.0.0
################################################################################
"""Interactive launcher for opstat.

Design notes:
- The wizard never re-implements flag rules. It emits an argv list that the
  caller runs through ``opstat.parse_args`` + ``dispatch``; that keeps a
  single source of truth for validation (``validate_protocol_args``).
- Every I/O seam (input, output, getpass, environ, config loader, isatty) is
  injectable so the flows are fully unit-testable without a real terminal.
"""

import getpass as _getpass
import os
import sys

DEFAULT_PORT = 443
DEFAULT_USER = "admin"
DEFAULT_REFRESH = 5

# Protocol registry - data-driven so new protocols/versions are a table edit.
# Each entry: menu label, the flags it emits, optional NFS-style versions, and
# an optional scoping question (attr name -> CLI flag).
PROTOCOLS = [
    {
        "key": "nfs",
        "label": "NFS",
        "flags": ["--nfs"],
        "versions": ["3.0", "4.1"],
        "scope": None,
    },
    {
        "key": "block",
        "label": "Block (NVMe-oTCP)",
        "flags": ["--block", "--nvme-over-tcp"],
        "versions": None,
        "scope": ("volumes", "--volumes", "volume name(s)"),
    },
    {
        "key": "smb",
        "label": "SMB",
        "flags": ["--smb"],
        "versions": None,
        "scope": ("clients", "--clients", "client IP(s)/host(s)"),
    },
    {
        "key": "s3",
        "label": "S3",
        "flags": ["--s3"],
        "versions": None,
        "scope": ("buckets", "--buckets", "bucket/view name(s)"),
    },
]


class _Quit(Exception):
    """Raised internally when the user aborts the wizard."""


def should_launch(argv_source, stdin_isatty, stdout_isatty):
    """Return True when the interactive wizard should run.

    Launches only on an interactive terminal with no CLI arguments. Explicit
    ``--menu``/``-i`` forces it on; ``--no-menu`` forces it off.
    """
    if "--no-menu" in argv_source:
        return False
    if "--menu" in argv_source or "-i" in argv_source:
        return bool(stdin_isatty and stdout_isatty)
    return not argv_source and bool(stdin_isatty and stdout_isatty)


def _supports_token(protocol_key, version):
    """Every engine resolves VAST_TOKEN (Bearer) auth via vast_common."""
    return True


class _Prompt:
    """Small prompt toolkit bound to injectable I/O seams."""

    def __init__(self, input_fn, output_fn, getpass_fn):
        self._in = input_fn
        self._out = output_fn
        self._getpass = getpass_fn

    def say(self, text=""):
        self._out(text)

    def _read(self, prompt):
        try:
            return self._in(prompt)
        except (EOFError, KeyboardInterrupt):
            raise _Quit()

    def text(self, label, default=None, required=False, validate=None):
        suffix = f" [{default}]" if default not in (None, "") else ""
        while True:
            raw = self._read(f"{label}{suffix}: ").strip()
            if raw.lower() in ("q", "quit"):
                raise _Quit()
            if not raw:
                if default not in (None, ""):
                    return default
                if not required:
                    return ""
                self.say("  A value is required.")
                continue
            if validate:
                err = validate(raw)
                if err:
                    self.say(f"  {err}")
                    continue
            return raw

    def yes_no(self, label, default=True):
        hint = "Y/n" if default else "y/N"
        while True:
            raw = self._read(f"{label} [{hint}]: ").strip().lower()
            if raw in ("q", "quit"):
                raise _Quit()
            if not raw:
                return default
            if raw in ("y", "yes"):
                return True
            if raw in ("n", "no"):
                return False
            self.say("  Please answer y or n.")

    def choice(self, label, options, default_index=0):
        """Ask a numbered menu. *options* is a list of display strings."""
        self.say(label)
        for i, opt in enumerate(options, 1):
            marker = " (default)" if (i - 1) == default_index else ""
            self.say(f"  {i}) {opt}{marker}")
        while True:
            raw = self._read("Select: ").strip().lower()
            if raw in ("q", "quit"):
                raise _Quit()
            if not raw:
                return default_index
            if raw.isdigit():
                idx = int(raw) - 1
                if 0 <= idx < len(options):
                    return idx
            self.say(f"  Enter a number 1-{len(options)}.")

    def secret(self, label):
        try:
            return self._getpass(label)
        except (EOFError, KeyboardInterrupt):
            raise _Quit()


def _load_config(config_loader):
    """Return a ~/.vastconf dict or None. Best-effort; never raises.

    When *config_loader* is explicitly injected (tests), it is used directly and
    the on-disk existence check is skipped so behavior is deterministic. In
    production (loader None) we require the file to exist before importing the
    shared loader.
    """
    path = os.path.expanduser("~/.vastconf")
    if config_loader is not None:
        try:
            return config_loader(path)
        except Exception:
            return None
    if not os.path.exists(path):
        return None
    loader = _default_config_loader()
    if loader is None:
        return None
    try:
        return loader(path)
    except Exception:
        return None


def _default_config_loader():
    """Import the shared loader, adding the repo root to sys.path if needed."""
    repo_root = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..")
    )
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    try:
        from vast.common.utils import load_vast_config
        return load_vast_config
    except Exception:
        return None


def _validate_port(raw):
    try:
        n = int(raw)
    except ValueError:
        return "Port must be a whole number."
    if not (1 <= n <= 65535):
        return "Port must be between 1 and 65535."
    return None


def _validate_refresh(raw):
    try:
        n = int(raw)
    except ValueError:
        return "Refresh must be a whole number of seconds."
    if n < 1:
        return "Refresh must be at least 1 second."
    return None


def _protocol_summary(ans):
    p = ans["protocol"]
    if p["key"] == "nfs":
        return f"NFS v{ans['version']}"
    return p["label"]


def _build_argv(ans):
    """Translate collected answers into a opstat argv list (no secrets)."""
    argv = list(ans["protocol"]["flags"])
    if ans["protocol"]["key"] == "nfs":
        argv.append(f"--version={ans['version']}")
    argv += ["--vms", ans["vms"]]
    if ans["port"] != DEFAULT_PORT:
        argv += ["--vms-port", str(ans["port"])]
    if ans["user"] and ans["user"] != DEFAULT_USER:
        argv += ["--user", ans["user"]]
    scope = ans["protocol"]["scope"]
    if scope and ans.get("scope_value"):
        argv += [scope[1], ans["scope_value"]]
    if ans.get("refresh", DEFAULT_REFRESH) != DEFAULT_REFRESH:
        argv += ["--refresh", str(ans["refresh"])]
    if ans.get("sample_average"):
        argv += ["--sample-average", ans["sample_average"]]
    if ans.get("csv"):
        argv += ["--csv", ans["csv"]]
    if ans.get("no_color"):
        argv.append("--no-color")
    if ans.get("log_api_calls"):
        argv.append("--log-api-calls")
    if ans.get("export_openmetrics"):
        argv.append("--export-openmetrics")
        if ans.get("openmetrics_file"):
            argv += ["--openmetrics-file", ans["openmetrics_file"]]
    return argv


def _equivalent_cli(argv):
    """Human-friendly single-line command, quoting args with spaces."""
    parts = ["opstat"]
    for a in argv:
        parts.append(f'"{a}"' if " " in a else a)
    return " ".join(parts)


# --- individual question steps (each mutates the answers dict) --------------

def _ask_protocol(p, ans):
    labels = [proto["label"] for proto in PROTOCOLS]
    idx = p.choice("Which protocol do you want to monitor?", labels, 0)
    ans["protocol"] = PROTOCOLS[idx]
    if ans["protocol"]["versions"]:
        _ask_version(p, ans)
    else:
        ans["version"] = None


def _ask_version(p, ans):
    versions = ans["protocol"]["versions"]
    options = list(versions) + ["4.2 (planned - not yet available)"]
    while True:
        idx = p.choice("NFS version?", options, 0)
        if idx < len(versions):
            ans["version"] = versions[idx]
            return
        p.say("  NFS v4.2 is not implemented yet - choose 3.0 or 4.1.")


def _ask_connection(p, ans):
    ans["vms"] = p.text(
        "VMS hostname or IP (use localhost for an SSH/Teleport tunnel)",
        default=ans.get("vms") or None,
        required=True,
    )
    ans["port"] = int(p.text(
        "VMS HTTPS port", default=str(ans.get("port", DEFAULT_PORT)),
        validate=_validate_port,
    ))
    ans["user"] = p.text("Username", default=ans.get("user") or DEFAULT_USER)


def _ask_auth(p, ans, environ):
    options = ["Enter password now (secure prompt)"]
    if _supports_token(ans["protocol"]["key"], ans.get("version")):
        options.append("Enter API token now (VAST_TOKEN)")
    options.append("Use credentials already in the environment")
    idx = p.choice("How do you want to authenticate?", options, 0)
    chosen = options[idx]
    if chosen.startswith("Enter password"):
        pw = p.secret(f"Password for {ans['user']}@{ans['vms']}: ")
        environ["VAST_PASSWORD"] = pw
        ans["auth"] = "password (secure prompt)"
    elif chosen.startswith("Enter API token"):
        tok = p.secret("API token: ")
        environ["VAST_TOKEN"] = tok
        ans["auth"] = "token (VAST_TOKEN)"
    else:
        ans["auth"] = "environment (VAST_PASSWORD/VAST_TOKEN)"


def _ask_scope(p, ans):
    scope = ans["protocol"]["scope"]
    if not scope:
        ans["scope_value"] = None
        return
    _attr, _flag, human = scope
    val = p.text(
        f"Scope to {human}? Comma-separated, or Enter for cluster-wide",
        default=ans.get("scope_value") or None,
    )
    ans["scope_value"] = val or None


def _ask_advanced(p, ans):
    if not p.yes_no("Configure advanced display/capture options?", default=False):
        ans.setdefault("refresh", DEFAULT_REFRESH)
        ans.setdefault("sample_average", None)
        ans.setdefault("csv", None)
        ans.setdefault("no_color", False)
        ans.setdefault("log_api_calls", False)
        return
    ans["refresh"] = int(p.text(
        "Refresh interval (seconds)", default=str(ans.get("refresh", DEFAULT_REFRESH)),
        validate=_validate_refresh,
    ))
    ans["sample_average"] = p.text(
        "Rolling sample-average window (e.g. 10m, 1h, 4h), or Enter for none",
        default=ans.get("sample_average") or None,
    ) or None
    ans["csv"] = p.text(
        "Write samples to CSV file (path), or Enter for none",
        default=ans.get("csv") or None,
    ) or None
    ans["no_color"] = not p.yes_no("Enable ANSI color output?", default=True)
    ans["log_api_calls"] = p.yes_no(
        "Log VMS API calls to /tmp (debugging)?", default=False
    )


def _ask_openmetrics(p, ans):
    ans["export_openmetrics"] = p.yes_no(
        "Export metrics to an OpenMetrics JSON Lines (.jsonl) file?", default=False
    )
    if ans["export_openmetrics"]:
        ans["openmetrics_file"] = p.text(
            "OpenMetrics .jsonl path, or Enter for an auto-named file under /tmp",
            default=ans.get("openmetrics_file") or None,
        ) or None
    else:
        ans["openmetrics_file"] = None


def _apply_config(ans, environ, cfg):
    """Seed connection/auth answers from a ~/.vastconf dict."""
    host = cfg.get("vms") or cfg.get("address") or cfg.get("host")
    ans["vms"] = host
    ans["user"] = cfg.get("user") or cfg.get("username") or DEFAULT_USER
    ans["port"] = int(cfg.get("port") or cfg.get("vms_port") or DEFAULT_PORT)
    token = cfg.get("token")
    password = cfg.get("password")
    if token:
        environ["VAST_TOKEN"] = str(token)
        ans["auth"] = "token from ~/.vastconf"
        return True
    if password:
        environ["VAST_PASSWORD"] = str(password)
        ans["auth"] = "password from ~/.vastconf"
        return True
    return False  # config had no usable secret


def _print_summary(p, ans, argv):
    p.say("")
    p.say("── Launch summary ─────────────────────────────────────────────")
    p.say(f"  Protocol   : {_protocol_summary(ans)}")
    p.say(f"  VMS        : {ans['vms']}:{ans['port']}")
    p.say(f"  User       : {ans['user']}")
    p.say(f"  Auth       : {ans.get('auth', '-')}")
    scope = ans["protocol"]["scope"]
    if scope:
        p.say(f"  Scope      : {ans.get('scope_value') or 'cluster-wide'}")
    p.say(f"  Refresh    : {ans.get('refresh', DEFAULT_REFRESH)}s")
    if ans.get("sample_average"):
        p.say(f"  Sample avg : {ans['sample_average']}")
    if ans.get("csv"):
        p.say(f"  CSV        : {ans['csv']}")
    p.say(f"  Color      : {'off' if ans.get('no_color') else 'on'}")
    if ans.get("log_api_calls"):
        p.say("  API log    : on")
    if ans.get("export_openmetrics"):
        p.say(f"  OpenMetrics: {ans.get('openmetrics_file') or 'auto-named .jsonl'}")
    p.say("")
    p.say(f"  Equivalent : {_equivalent_cli(argv)}")
    p.say("  (credentials are passed via environment, not shown above)")
    p.say("───────────────────────────────────────────────────────────────")


# Editable fields for the confirm loop: label -> step callable(prompt, ans).
def _edit_menu(p, ans, environ):
    fields = [
        ("Protocol", lambda: _ask_protocol(p, ans)),
        ("Connection (host/port/user)", lambda: _ask_connection(p, ans)),
        ("Authentication", lambda: _ask_auth(p, ans, environ)),
    ]
    if ans["protocol"]["scope"]:
        fields.append(("Scope", lambda: _ask_scope(p, ans)))
    fields.append(("Advanced options", lambda: _ask_advanced(p, ans)))
    fields.append(("OpenMetrics export", lambda: _ask_openmetrics(p, ans)))
    labels = [f[0] for f in fields] + ["Back to summary"]
    idx = p.choice("Edit which section?", labels, len(labels) - 1)
    if idx < len(fields):
        fields[idx][1]()


def run(*, input_fn=input, output_fn=print, getpass_fn=None,
        config_loader=None, environ=None, argv_source=None):
    """Drive the interactive wizard.

    Returns an argv list to hand to ``parse_args``, or None if the user quit.
    """
    p = _Prompt(input_fn, output_fn, getpass_fn or _getpass.getpass)
    environ = os.environ if environ is None else environ
    ans = {}

    try:
        p.say("")
        p.say("VAST opstat - interactive setup  (Enter accepts [defaults], q quits)")
        p.say("")

        # Stage 0 - optional config shortcut.
        cfg = _load_config(config_loader)
        used_config = False
        if cfg and p.yes_no("Load connection details from ~/.vastconf?", default=True):
            # Protocol still must be chosen; connection/auth come from config.
            _ask_protocol(p, ans)
            used_config = _apply_config(ans, environ, cfg)
            if not ans.get("vms"):
                p.say("  ~/.vastconf had no VMS host - entering it manually.")
                _ask_connection(p, ans)
            if not used_config:
                p.say("  ~/.vastconf had no stored secret - choose auth below.")
                _ask_auth(p, ans, environ)
        else:
            # Stage 1-3 - protocol, connection, auth.
            _ask_protocol(p, ans)
            _ask_connection(p, ans)
            _ask_auth(p, ans, environ)

        # Stage 4-6 - scoping, advanced, metrics export.
        _ask_scope(p, ans)
        _ask_advanced(p, ans)
        _ask_openmetrics(p, ans)

        # Stage 6 - confirm / edit / launch.
        while True:
            argv = _build_argv(ans)
            _print_summary(p, ans, argv)
            choice = p.choice(
                "Ready?", ["Start monitoring", "Edit an answer", "Quit"], 0
            )
            if choice == 0:
                return argv
            if choice == 2:
                raise _Quit()
            _edit_menu(p, ans, environ)
    except _Quit:
        p.say("Cancelled.")
        return None
