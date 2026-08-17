# Backlog ownership

Permanent rule for `opstat`. Applies to every agent and every engineering
pass that starts, finishes, defers, or discovers work.

Short form lives in [AGENTS.md](../../AGENTS.md).

## The one live backlog

**[docs/FR_BACKLOG.json](../../docs/FR_BACKLOG.json) is the authoritative
feature-request / engineering backlog and priority state.** There is exactly
one live backlog. `docs/CLAUDE_HANDOFF.md`, `docs/REFACTOR_HANDOFF.md` and
other documents may *summarize* work and hold historical evidence, but they
must never become competing active backlog sources. On any conflict of
status or priority, **FR_BACKLOG.json wins**.

## Why this rule exists

At the close of the TUI performance refactor milestone (2026-08-16), the
active work list existed in three slightly different forms across two
handoff documents and a milestone report. Each was accurate when written;
none was authoritative, so every reconciliation required re-deriving the
truth from all of them. One machine-checkable file ends that.

## Mandatory behavior

1. **Read `FR_BACKLOG.json` before beginning backlog work.** Priority 1 is
   the current recommended next work; confirm it with the owner if scope is
   ambiguous.
2. **When implementation of an FR actually begins:** set
   `status = "in_progress"`, update the item's `updated` date, and confirm
   its `blocked_by` / `dependencies` are current.
3. **When an FR completes:** set `status = "done"`, set `completed`, record
   concise evidence in `evidence`, remove it from the active priority order,
   and renumber the remaining active priorities contiguously from 1. Never
   delete the FR.
4. **When blocked:** set `status = "blocked"` and populate `blocked_by`.
5. **When deliberately postponed:** set `status = "deferred"`. The FR keeps
   its number.
6. **When replaced:** set `status = "superseded"`, reference the replacement
   FR in `notes`, and never reuse the number.
7. **When a new idea emerges**, decide what it is before writing anything:
   scope of a current FR (extend that FR), a technical note or constraint
   (add to `project_constraints` or the FR's `notes`), or genuinely new
   work. Only genuinely new work gets an FR: allocate `next_fr_number`,
   increment `next_fr_number`, assign a priority, and leave every existing
   FR number untouched.
8. **Never reuse an FR number.** Done, deferred and superseded items retain
   theirs forever.
9. **Never silently remove an FR.** History stays in the file.
10. **At the end of every milestone or substantial engineering pass,**
    reconcile the JSON against actual repository state — statuses, dates,
    evidence, priorities.
11. Keep the file valid: `scripts/check_fr_backlog.py` runs inside
    `./scripts/validate.sh` and enforces unique well-formed ids, allowed
    statuses, unique contiguous active priorities, dependency references,
    `next_fr_number` above every allocated number, and done/completed-date
    consistency.
12. The legacy letter identifiers `FR-A`..`FR-D` are historic refactor
    labels recorded in the file's `legacy_frs` map; they are not part of the
    numeric sequence and must not be reused for new work.

## What automated enforcement detects

`scripts/check_fr_backlog.py` (in the gate) catches structural drift:
malformed or duplicate ids, broken priority order, illegal statuses, dangling
dependencies, and missing/spurious completion dates.

## What automated enforcement CANNOT detect

- Whether a `done` status is true. Only the evidence field and the
  completion report behind it say so.
- Whether the priority order still reflects the owner's intent.
- A second backlog quietly accreting in a document. The rule — one live
  backlog — is the actual control.

## Stopping conditions

Stop and report rather than improvising when:

- The backlog and the repository visibly disagree and the correct
  reconciliation is not obvious.
- Completing or reprioritizing an item would contradict an explicit owner
  instruction.
- A change would require deleting or renumbering an existing FR.
