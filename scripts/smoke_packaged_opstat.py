#!/usr/bin/env python3
"""Smoke-test a built standalone opstat binary.

Deliberately NOT a pytest test: it needs a real PyInstaller artifact, which
takes a build to produce and does not belong in the per-commit unit gate.
The release workflow runs it on every platform it builds, immediately after
building and before the artifact is uploaded, so a binary that cannot start
never reaches a Release.

Run locally after `python3 scripts/build_opstat.py`:

    python3 scripts/smoke_packaged_opstat.py releases/opstat-<os>-<arch>

Proves, against the ARTIFACT (never the source tree):
  * it launches, and reports opstat_version.VERSION for both -V spellings
  * --help and -h succeed and list the supported options
  * argparse rejects a missing --vms with exit 2
  * protocol validation runs, so the bundled engines imported
  * no ModuleNotFoundError anywhere
  * it works with the source tree unreachable (run from another directory)

Python 3.8+, stdlib only.
"""

import argparse
import os
import subprocess
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from opstat_version import VERSION                        # noqa: E402

FAILURES = []


def check(name, ok, detail=""):
    print("%-34s %s %s" % (name, "PASS" if ok else "FAIL", detail))
    if not ok:
        FAILURES.append(name)


def run(binary, args, cwd, timeout=60):
    """Run the artifact with the source tree unreachable from cwd.

    None of the invocations below reach dispatch(), so this never opens a
    network connection - keep it that way, it is what makes the script safe
    as a CI step. A hang is reported as a failed check rather than an
    uncaught traceback, so the remaining checks still run.
    """
    env = dict(os.environ)
    for leak in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP"):
        env.pop(leak, None)
    try:
        result = subprocess.run([binary] + args, capture_output=True,
                                text=True, cwd=cwd, env=env, timeout=timeout,
                                stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return None, "TIMED OUT after %ss" % timeout
    return result.returncode, (result.stdout or "") + (result.stderr or "")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("binary", help="path to the built standalone artifact")
    args = parser.parse_args()

    binary = os.path.abspath(args.binary)
    if not os.path.isfile(binary):
        print("ERROR: no such artifact: %s" % binary, file=sys.stderr)
        return 2
    # On Windows this is effectively an existence check (CPython ignores
    # X_OK there); the real proof of executability is the launch below.
    if not os.access(binary, os.X_OK):
        print("ERROR: artifact is not executable: %s" % binary, file=sys.stderr)
        return 2

    print("artifact : %s (%d bytes)" % (binary, os.path.getsize(binary)))
    print("expecting: opstat %s\n" % VERSION)

    # Everything runs from a scratch directory, so any accidental reliance on
    # the source tree shows up as a failure rather than passing silently.
    with tempfile.TemporaryDirectory() as elsewhere:
        for flag in ("-V", "--tool-version"):
            code, out = run(binary, [flag], elsewhere)
            check("version %s" % flag,
                  code == 0 and out.strip() == "opstat %s" % VERSION,
                  out.strip())

        for flag in ("--help", "-h"):
            code, out = run(binary, [flag], elsewhere)
            check("help %s" % flag, code == 0 and "usage: opstat" in out,
                  "rc=%d" % code)

        code, out = run(binary, ["--help"], elsewhere)
        missing = [f for f in ("--vms", "--nfs", "--smb", "--s3", "--block",
                               "--nvme-over-tcp", "--refresh", "--menu")
                   if f not in out]
        check("help lists supported options", not missing,
              "missing: %s" % missing if missing else "")

        code, out = run(binary, ["--s3"], elsewhere)
        check("missing --vms exits 2", code == 2 and "--vms" in out,
              "rc=%d" % code)

        # These run PAST argparse, in validate_protocol_args - so they prove
        # application code executes in the frozen binary, not merely that the
        # parser was built. (Every first-party module is already proven
        # imported by -V succeeding: the entrypoint imports all five engines
        # at module level.)
        #
        # Each asserts rc == 1 AND a message fragment that cannot appear in
        # argparse's usage banner. Matching on a bare flag name would pass on
        # any parser failure too, since the banner lists every flag.
        code, out = run(binary, ["--block", "--vms", "h"], elsewhere)
        check("block requires --nvme-over-tcp",
              code == 1 and "--block requires --nvme-over-tcp" in out,
              "rc=%d" % code)
        code, out = run(binary, ["--nfs", "--vms", "h"], elsewhere)
        check("nfs requires --version",
              code == 1 and "--version is required when using --nfs" in out,
              "rc=%d" % code)
        code, out = run(binary, ["--nfs", "--version=9.9", "--vms", "h"],
                        elsewhere)
        check("unsupported NFS version rejected",
              code == 1 and "Unsupported NFS version" in out, "rc=%d" % code)

        # A missing bundled module surfaces here rather than at first use.
        code, out = run(binary, ["--help"], elsewhere)
        check("no ModuleNotFoundError", "ModuleNotFoundError" not in out
              and "ImportError" not in out)

    print()
    if FAILURES:
        print("RESULT: FAIL (%s)" % ", ".join(FAILURES))
        return 1
    print("RESULT: PASS - the packaged artifact behaves like the source CLI")
    return 0


if __name__ == "__main__":
    sys.exit(main())
