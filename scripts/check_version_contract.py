#!/usr/bin/env python3
"""Refuse to publish a release whose tag disagrees with the runtime version.

The release workflow triggers on `v*` tags and never read the runtime
version, so tagging `v9.9.9` today would build, upload and publish binaries
that report `opstat 0.1.2` - a mismatched release with nothing to stop it.

Contract enforced here:

    tag vX.Y.Z  ==>  opstat_version.VERSION == "X.Y.Z"

Checkpoint tags are deliberately OUT of scope. `checkpoint-0.1.2-refactor-
complete` does not match the workflow's `v*` pattern, so it never reaches
this check - and if one is passed by hand it is rejected as "not a release
tag" rather than silently treated as one.

Usage:
    python3 scripts/check_version_contract.py --tag "$GITHUB_REF_NAME"
    python3 scripts/check_version_contract.py            # report the version

Python 3.8+, stdlib only.
"""

import argparse
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from opstat_version import VERSION                        # noqa: E402

# A release tag is exactly "v" + the version. Anything else - a checkpoint
# tag, a release-candidate suffix, a stray "v" prefix on prose - is not one.
RELEASE_TAG_RE = re.compile(r"^v(\d+\.\d+\.\d+)$")


def release_version_from_tag(tag):
    """The version a release tag claims, or None when it is not a release tag."""
    match = RELEASE_TAG_RE.match((tag or "").strip())
    return match.group(1) if match else None


def check(tag):
    """Return (ok, message) for *tag* against the authoritative VERSION."""
    claimed = release_version_from_tag(tag)
    if claimed is None:
        return False, (
            "'%s' is not a release tag. Release tags are exactly vX.Y.Z; "
            "checkpoint tags (checkpoint-...) are not releases and must not "
            "be published as one." % tag)
    if claimed != VERSION:
        return False, (
            "VERSION MISMATCH: tag '%s' claims version %s but the runtime "
            "reports %s.\n"
            "  Publishing would ship binaries that disagree with their own "
            "release.\n"
            "  Fix: either set VERSION in opstat_version.py to %s and tag "
            "the new commit, or tag v%s to match the current runtime.\n"
            "  Do NOT move or delete a tag that has already been pushed."
            % (tag, claimed, VERSION, claimed, VERSION))
    return True, "tag '%s' matches runtime version %s" % (tag, VERSION)


def existing_release_tag_warning():
    """Warn when v<VERSION> is already a tag in this clone.

    v0.1.2 is already published (it points at a commit far behind main), so
    the gate's one permitted tag is taken: the next release must begin with a
    VERSION bump. Moving a published tag is a prohibited destructive git
    action, so surfacing this early matters. Best-effort and never fatal -
    a CI checkout may not have tags, and this is advice, not a verdict.
    """
    import subprocess

    try:
        result = subprocess.run(
            ["git", "tag", "--list", "v%s" % VERSION],
            cwd=_ROOT, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except (OSError, ValueError):                      # pragma: no cover
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return ("NOTE: tag v%s already exists in this clone. A release needs a "
            "NEW version: bump VERSION in opstat_version.py rather than "
            "re-pointing a published tag." % VERSION)


def build_parser():
    """CLI contract, factored so the gate can be tested without a workflow."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--tag", default=None,
                        help="release tag to verify (e.g. v0.1.2)")
    return parser


def main():
    args = build_parser().parse_args()
    if args.tag is None:
        print(VERSION)
        warning = existing_release_tag_warning()
        if warning:
            print(warning, file=sys.stderr)
        return 0
    ok, message = check(args.tag)
    print(("OK: " if ok else "ERROR: ") + message,
          file=sys.stdout if ok else sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
