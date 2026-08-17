#!/usr/bin/env python3
"""Validate docs/FR_BACKLOG.json, the authoritative opstat backlog.

The backlog is the one live list of engineering work (see
.claude/rules/backlog.md); this check keeps it structurally sound so it can
be trusted by both humans and agents. Stdlib only, Python 3.8+.

Checks: valid JSON, required fields, well-formed unique FR ids,
next_fr_number above every allocated number, allowed statuses, unique and
contiguous active priorities, dependency references, and completed-date
consistency (done <=> completed set).
"""

import json
import os
import re
import sys

BACKLOG = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "..", "docs", "FR_BACKLOG.json")

# Statuses that make an item part of the live, ordered queue. deferred,
# done and superseded items keep their FR number and history but do not
# occupy a slot in the contiguous active priority order.
ACTIVE_STATUSES = ("backlog", "ready", "in_progress", "blocked")

REQUIRED_TOP = ("schema_version", "project", "source_of_truth", "updated",
                "next_fr_number", "status_values", "project_constraints",
                "items")
REQUIRED_ITEM = ("id", "title", "priority", "status", "class", "summary",
                 "scope", "acceptance_criteria", "dependencies", "blocked_by",
                 "related_decisions", "related_legacy_frs", "evidence",
                 "notes", "created", "updated", "completed")

_ID = re.compile(r"^FR([1-9][0-9]*)$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def main():
    errors = []

    try:
        with open(BACKLOG) as fh:
            data = json.load(fh)
    except (IOError, OSError) as exc:
        print("FAIL: cannot read %s: %s" % (BACKLOG, exc))
        return 1
    except ValueError as exc:
        print("FAIL: %s is not valid JSON: %s" % (BACKLOG, exc))
        return 1

    for key in REQUIRED_TOP:
        if key not in data:
            errors.append("missing top-level field %r" % key)
    if errors:
        for e in errors:
            print("FAIL: " + e)
        return 1

    statuses = set(data["status_values"])
    items = data["items"]
    seen_ids = set()
    numbers = []
    active = []

    for item in items:
        label = item.get("id", "<no id>")
        for key in REQUIRED_ITEM:
            if key not in item:
                errors.append("%s: missing field %r" % (label, key))
        m = _ID.match(str(item.get("id", "")))
        if not m:
            errors.append("%s: id is not a well-formed numeric FR id" % label)
        else:
            if item["id"] in seen_ids:
                errors.append("%s: duplicate FR id" % label)
            seen_ids.add(item["id"])
            numbers.append(int(m.group(1)))
        if item.get("status") not in statuses:
            errors.append("%s: status %r not in status_values"
                          % (label, item.get("status")))
        for field in ("created", "updated"):
            if not _DATE.match(str(item.get(field, ""))):
                errors.append("%s: %s is not YYYY-MM-DD" % (label, field))
        if item.get("status") == "done":
            if not (item.get("completed")
                    and _DATE.match(str(item["completed"]))):
                errors.append("%s: done but completed date missing" % label)
        elif item.get("completed") is not None:
            errors.append("%s: not done but carries a completed date" % label)
        for dep_field in ("dependencies", "blocked_by"):
            for dep in item.get(dep_field, []):
                if not any(other.get("id") == dep for other in items):
                    errors.append("%s: %s references unknown FR %r"
                                  % (label, dep_field, dep))
        if item.get("status") in ACTIVE_STATUSES:
            active.append(item)

    if numbers and data["next_fr_number"] <= max(numbers):
        errors.append("next_fr_number (%s) must exceed the highest allocated "
                      "FR (%d)" % (data["next_fr_number"], max(numbers)))

    priorities = [item.get("priority") for item in active]
    if len(priorities) != len(set(priorities)):
        errors.append("active priorities are not unique: %s"
                      % sorted(priorities))
    if active:
        expected = list(range(1, len(active) + 1))
        if sorted(priorities) != expected:
            errors.append("active priorities are not contiguous from 1: got "
                          "%s, expected %s" % (sorted(priorities), expected))

    if errors:
        for e in errors:
            print("FAIL: " + e)
        return 1

    done = sum(1 for i in items if i["status"] == "done")
    deferred = sum(1 for i in items if i["status"] == "deferred")
    print("FR backlog OK: %d items (%d active, %d deferred, %d done), "
          "next_fr_number %d"
          % (len(items), len(active), deferred, done, data["next_fr_number"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
