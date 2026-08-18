#!/usr/bin/env python3
"""Find real existing files beneath the NFSv4.1 mount, read-only.

The FR2 lab workflow must query the delegation endpoint with REAL files. Two
lab trips failed to find any: the first globbed a guessed extension, the
second waited for fio processes that the nfs41 load generator does not use
(its cgroup runs bash/sleep/mkdir cycles) and filtered a `find` by size on a
mount whose contents are churned by a meta-stress delete loop.

Strategy, bounded and observational (never creates, touches, or deletes):
  1. open file descriptors of ANY process, resolved via /proc/<pid>/fd,
     that point beneath the mountpoint - a file held open right now is the
     best live-delegation candidate;
  2. paths under the mountpoint appearing in process command lines
     (ps args) that stat as existing regular files - the loadgen names its
     working files on its command lines;
  3. any existing regular file found by a shallow walk of the mountpoint,
     INCLUDING zero-byte files (a fresh file is a valid delegation target).

Samples repeatedly until the bounded deadline; prints newline-separated
client paths on stdout. Exit 0 when at least one candidate exists, exit 1
otherwise so the lab script can refuse to run the probe against nothing.

Python 3.8+, stdlib only.
"""

import argparse
import os
import subprocess
import sys
import time


def under_mount(path, mountpoint):
    mp = mountpoint.rstrip("/")
    return path == mp or path.startswith(mp + "/")


def candidates_from_fds(mountpoint, proc_root="/proc"):
    """Files under *mountpoint* held open by ANY process, via /proc fds."""
    found = set()
    try:
        pids = [p for p in os.listdir(proc_root) if p.isdigit()]
    except OSError:
        return found
    for pid in pids:
        fd_dir = os.path.join(proc_root, pid, "fd")
        try:
            fds = os.listdir(fd_dir)
        except OSError:
            continue
        for fd in fds:
            try:
                target = os.readlink(os.path.join(fd_dir, fd))
            except OSError:
                continue
            if under_mount(target, mountpoint) and os.path.isfile(target):
                found.add(target)
    return found


def candidates_from_ps_args(ps_text, mountpoint):
    """Existing regular files named on process command lines.

    The loadgen's own command lines name its working paths (the nfs41 unit
    showed `mkdir .../meta_stress/dir_4`, the nfs3 unit `sync .../commit.dat`)
    - those tokens are real, current paths straight from the workload."""
    found = set()
    for line in (ps_text or "").splitlines():
        for token in line.split():
            token = token.strip("'\";,")
            if under_mount(token, mountpoint) and os.path.isfile(token):
                found.add(token)
    return found


def candidates_from_walk(mountpoint, max_depth=4, cap=8):
    """Any existing regular file, zero-byte included - a fresh loadgen file
    is a valid delegation query target."""
    found = []
    base_depth = mountpoint.rstrip("/").count("/")
    for root, dirs, files in os.walk(mountpoint):
        if root.count("/") - base_depth >= max_depth:
            dirs[:] = []
            continue
        for name in files:
            path = os.path.join(root, name)
            if os.path.isfile(path):
                found.append(path)
                if len(found) >= cap:
                    return found
    return found


def gather_once(mountpoint, ps_text=None, proc_root="/proc"):
    """One sampling pass; open-fd candidates are listed first on purpose."""
    if ps_text is None:
        try:
            ps_text = subprocess.check_output(
                ["ps", "-eo", "args"], stderr=subprocess.DEVNULL).decode()
        except (OSError, subprocess.CalledProcessError):
            ps_text = ""
    open_files = sorted(candidates_from_fds(mountpoint, proc_root))
    named = sorted(candidates_from_ps_args(ps_text, mountpoint))
    walked = candidates_from_walk(mountpoint)
    ordered = []
    for path in open_files + named + walked:
        if path not in ordered:
            ordered.append(path)
    return ordered[:8], bool(open_files)


def build_parser():
    """CLI contract, factored so the lab script can be held to it in tests."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--mountpoint", required=True)
    parser.add_argument("--wait", type=int, default=120,
                        help="bounded seconds to keep sampling for candidates")
    parser.add_argument("--interval", type=float, default=5.0)
    return parser


def main():
    args = build_parser().parse_args()

    deadline = time.time() + args.wait
    best, first_found = [], None
    while True:
        candidates, live = gather_once(args.mountpoint)
        if candidates:
            best = candidates
            first_found = first_found or time.time()
            # A live open file is the ideal candidate; keep sampling for one
            # briefly, but never hold existing candidates hostage to it.
            if live or time.time() >= min(deadline, first_found + 30):
                break
        if time.time() >= deadline:
            break
        time.sleep(args.interval)

    for path in best:
        print(path)
    if not best:
        print("NO-CANDIDATES: no existing regular file found beneath %s "
              "within %ss" % (args.mountpoint, args.wait), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
