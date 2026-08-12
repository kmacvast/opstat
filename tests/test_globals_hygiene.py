"""Static hygiene check for the engines' module-global state pattern.

The opstat engines keep TUI state in ALL_CAPS module globals and mutate them
from many functions. Assigning one of those names inside a function without a
``global`` declaration silently creates a function local — the class of bug
that left NFSv4.1 drill-down permanently stuck on "Waiting for data…"
(``DRILL_MONITORS`` was missing from ``enter_drill_mode``'s global statement).
This test walks every top-level function and fails on any assignment to a
known module-global ALL_CAPS name that is not declared ``global``.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

CHECKED_FILES = [
    "nfs_v3.py", "nfs_v41.py", "nvme_tcp.py", "smb.py", "s3.py",
    "vast_common.py", "openmetrics.py", "vast_api_log.py",
    "wizard.py", "tui_layout.py",
]

# Names that are legitimately shadowed as locals somewhere (none today).
ALLOWED_SHADOWS: set = set()


def _module_global_names(tree):
    names = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def _check_function(fn, module_globals, filename, problems):
    declared = set()
    assigned = {}
    for node in ast.walk(fn):
        if isinstance(node, ast.Global):
            declared.update(node.names)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    assigned.setdefault(target.id, node.lineno)
        elif isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
            assigned.setdefault(node.target.id, node.lineno)
    for name, lineno in assigned.items():
        if (name.isupper() and name in module_globals
                and name not in declared and name not in ALLOWED_SHADOWS):
            problems.append(
                f"{filename}:{lineno}: {fn.name}() assigns module global "
                f"{name} without a 'global' declaration"
            )


@pytest.mark.parametrize("filename", CHECKED_FILES)
def test_no_shadowed_module_globals(filename):
    tree = ast.parse((ROOT / filename).read_text())
    module_globals = _module_global_names(tree)
    problems = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            _check_function(node, module_globals, filename, problems)
    assert not problems, "\n".join(problems)
