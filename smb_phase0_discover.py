#!/usr/bin/env python3
################################################################################
# Script Name: smb_phase0_discover.py
# Description: Phase 0 READ-ONLY SMB metric discovery wrapper. Delegates to
#              smb.discover_metrics() and writes SMB_PHASE0_RESULTS.md.
# Author: KMac kmac@vastdata.com
# Version: 0.1.1
################################################################################
"""Run from vast/opstat on a host with ~/.vastconf or explicit --vms creds."""

import argparse
import os
import sys
from types import SimpleNamespace

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

_REPO_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from vast.common.utils import load_vast_config

import smb

DEFAULT_PORT = 443


def main():
    parser = argparse.ArgumentParser(description="SMB Phase 0 VMS metric discovery")
    parser.add_argument("--vms", help="VMS host (overrides ~/.vastconf)")
    parser.add_argument("--vms-port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--user", default=None)
    parser.add_argument("--password", default=None)
    parser.add_argument("--config", default="~/.vastconf")
    parser.add_argument(
        "--output",
        default="SMB_PHASE0_RESULTS.md",
        help="Write markdown report to this path (under opstat/)",
    )
    args = parser.parse_args()

    try:
        conf = load_vast_config(args.config)
    except FileNotFoundError as e:
        print(f"ERROR: {e}")
        print("Provide --vms/--user/--password or create ~/.vastconf on the lab host.")
        return 1

    if args.vms:
        conf["vms"] = args.vms
    if args.user:
        conf["user"] = args.user
    if args.password:
        conf["password"] = args.password

    host = conf.get("vms") or conf.get("address") or conf.get("host")
    if not host:
        print("ERROR: No VMS host in config")
        return 1

    ns = SimpleNamespace(
        vms=host,
        port=args.vms_port,
        user=conf.get("user") or conf.get("username") or "admin",
        password=conf.get("password"),
        refresh=5,
        sample_average=None,
        discover_metrics=True,
        log_api_calls=False,
        clients=None,
    )
    smb.init_config(ns)
    return smb.discover_metrics(write_report_path=args.output) or 0


if __name__ == "__main__":
    sys.exit(main() or 0)
