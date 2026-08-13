#!/usr/bin/env python3
"""Verify that relative Markdown links in tracked documentation resolve.

The documentation set is deliberately cross-linked rather than duplicated —
AGENTS.md points at .claude/rules/, the rules point at docs/decisions/, the
handoff points at both. That only works while the links resolve, and a broken
link is invisible until someone follows it.

Checks, for every tracked Markdown file (plus .cursor/*.mdc):

  * relative link targets exist on disk
  * in-page anchors (#heading) match a heading in the target file

Skips external URLs, mailto:, and image-only links to generated artifacts.
Standard library only, Python 3.8 compatible.

    python3 scripts/check_docs_links.py
"""

import io
import os
import re
import subprocess
import sys

# Pre-existing broken links, accepted rather than silently ignored.
#
# SMB_README.md and SMB_OPCODES.md link to `../../docs/dev/smb/...`. That path
# was never tracked in this repository (`git log --all -- 'docs/dev/*'` is
# empty) and `../../` from the repository root escapes it entirely, so these
# have been broken since they were written. They point at design records that
# lived on a developer machine.
#
# They are listed here so the gate stays green on known debt while the debt
# stays visible. Do NOT add an entry to make a NEW broken link pass — fix the
# link. Remove an entry when the target is restored or the reference dropped.
KNOWN_BROKEN = {
    "../../docs/dev/smb/SMB_PHASE0_RESULTS.md",
    "../../docs/dev/smb/SMB_IMPLEMENTATION_PLAN.md",
}

LINK_RE = re.compile(r"(?<!\!)\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
HEADING_RE = re.compile(r"^#{1,6}\s+(.*?)\s*$")
SKIP_PREFIXES = ("http://", "https://", "mailto:", "#!", "tel:")


def tracked_docs(root):
    """Markdown-ish files git knows about, plus anything not yet added."""
    out = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=root,
    ).decode("utf-8", "replace")
    for name in out.splitlines():
        if name.endswith(".md") or name.endswith(".mdc"):
            yield name


def slugify(heading):
    """GitHub-style anchor slug."""
    text = heading.strip().lower()
    text = re.sub(r"`|\*|_", "", text)
    text = re.sub(r"[^\w\s-]", "", text)
    return re.sub(r"\s+", "-", text).strip("-")


def anchors_in(path):
    try:
        body = io.open(path, encoding="utf-8").read()
    except (IOError, OSError, UnicodeDecodeError):
        return set()
    found = set()
    for line in body.splitlines():
        m = HEADING_RE.match(line)
        if m:
            found.add(slugify(m.group(1)))
    return found


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    problems = []
    checked = 0
    accepted = 0

    for name in sorted(tracked_docs(root)):
        src = os.path.join(root, name)
        try:
            body = io.open(src, encoding="utf-8").read()
        except (IOError, OSError, UnicodeDecodeError):
            continue
        # Ignore fenced code blocks: examples there are illustrative.
        body = re.sub(r"```.*?```", "", body, flags=re.S)

        for target in LINK_RE.findall(body):
            if target.startswith(SKIP_PREFIXES):
                continue
            if target in KNOWN_BROKEN:
                accepted += 1
                continue
            checked += 1
            path_part, _, anchor = target.partition("#")

            if not path_part:                      # same-file anchor
                dest = src
            else:
                dest = os.path.normpath(
                    os.path.join(os.path.dirname(src), path_part)
                )

            if not os.path.exists(dest):
                problems.append("%s -> %s (missing)" % (name, target))
                continue

            if anchor and os.path.isfile(dest) and dest.endswith((".md", ".mdc")):
                if anchor.lower() not in anchors_in(dest):
                    problems.append("%s -> %s (no such heading)" % (name, target))

    if problems:
        sys.stderr.write("Broken documentation links:\n")
        for p in problems:
            sys.stderr.write("  %s\n" % p)
        sys.stderr.write("\n%d broken of %d checked\n" % (len(problems), checked))
        return 1

    sys.stdout.write("%d relative documentation links OK" % checked)
    if accepted:
        sys.stdout.write(
            " (%d known-broken references skipped -- see KNOWN_BROKEN)" % accepted
        )
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
