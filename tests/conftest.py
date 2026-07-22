"""Shared fixtures for opstat unit tests.

Loads the extensionless ``opstat`` CLI via ``runpy``, keeps the repo root on
``sys.path``, and resets module-global state between tests so engines and
exporters stay isolated without a live VMS.
"""

from __future__ import annotations

import runpy
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session", autouse=True)
def _repo_on_path():
    root = str(ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)


@pytest.fixture(scope="session")
def opstat_cli():
    """Load the extensionless ``opstat`` entrypoint as a module dict."""
    return runpy.run_path(str(ROOT / "opstat"), run_name="opstat_cli")


@pytest.fixture(autouse=True)
def _reset_shared_state():
    import openmetrics
    import tui_layout
    import vast_common

    openmetrics.close()
    vast_common.reset_registry()
    tui_layout.set_color(False)
    tui_layout.set_unicode(False)
    yield
    openmetrics.close()
    vast_common.reset_registry()
    tui_layout.set_color(False)
    tui_layout.set_unicode(False)


@pytest.fixture
def reset_s3_globals():
    import s3

    s3.BUCKET_SCOPED = False
    s3.BUCKET_NAMES = []
    s3.TENANT_SCOPED = False
    s3.TENANT_NAMES = []
    s3._PROTO_ACTIVE = s3._PROTO_S3_COMMON
    s3.METRICS_SOURCE = "S3Common"
    s3.S3_METRICS_EXPORTED = False
    s3.CLUSTER_ID = 1
    s3.API_TIME_FRAME = s3.DEFAULT_API_TIME_FRAME
    s3.DRILL_MODE = None
    yield
    s3.BUCKET_SCOPED = False
    s3.BUCKET_NAMES = []
    s3.TENANT_SCOPED = False
    s3.TENANT_NAMES = []
    s3._PROTO_ACTIVE = s3._PROTO_S3_COMMON
    s3.METRICS_SOURCE = "S3Common"
    s3.S3_METRICS_EXPORTED = False
    s3.DRILL_MODE = None


@pytest.fixture
def reset_smb_globals():
    import smb

    smb.CLIENT_SCOPED = False
    smb.CLIENT_IPS = []
    smb.LAST_TOPN = None
    smb.SMB_PER_COMMAND_EXPORTED = False
    smb.DRILL_MODE = None
    smb.METRICS_SOURCE = "SMBCommon"
    yield
    smb.CLIENT_SCOPED = False
    smb.CLIENT_IPS = []
    smb.LAST_TOPN = None
    smb.SMB_PER_COMMAND_EXPORTED = False
    smb.DRILL_MODE = None
    smb.METRICS_SOURCE = "SMBCommon"


@pytest.fixture
def ns():
    """Factory for SimpleNamespace CLI-style args."""

    def _make(**kwargs):
        return SimpleNamespace(**kwargs)

    return _make
