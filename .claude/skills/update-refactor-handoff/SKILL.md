---
name: update-refactor-handoff
description: >-
  Keep opstat's documentation split correct — durable decisions in
  docs/decisions/, point-in-time branch state in docs/REFACTOR_HANDOFF.md. Use
  after settling a decision, finishing a work item, or when the handoff has gone
  stale.
---

# update-refactor-handoff

opstat's engineering documentation is split by **lifetime**, and the split is
the thing worth maintaining:

| Location | Holds | Lifetime |
|---|---|---|
| [`docs/decisions/`](../../../docs/decisions/) | Settled decisions with the evidence that settled them | Durable. Superseded, never rewritten |
| [`docs/REFACTOR_HANDOFF.md`](../../../docs/REFACTOR_HANDOFF.md) | Branch state, SHAs, measurements, open work, unproven items | Point-in-time |
| [`AGENTS.md`](../../../AGENTS.md) | Permanent behavioral contract | Durable policy |
| [`docs/WORKSTATION_BOOTSTRAP.md`](../../../docs/WORKSTATION_BOOTSTRAP.md) | How to set up a machine to continue | Durable procedure |

Content in the wrong place is how a doc set goes stale: a SHA in a policy file
rots, and a settled decision buried in a branch handoff disappears when the
branch merges.

## Preconditions

- Something actually changed: a decision was settled or superseded, a work item
  closed or opened, or the handoff was found to disagree with the repository.
- Verify against the repository first. Do not update one document from another.

## Workflow

1. **Re-derive the facts.**

   ```bash
   git rev-parse --abbrev-ref HEAD
   git rev-parse HEAD
   git rev-parse origin/refactor/tui-performance
   git log --oneline --reverse origin/main..HEAD
   git status --short
   python3 -m pytest --collect-only 2>&1 | tail -2
   ```

2. **Classify what changed.**
   - *Durable decision* — a settled question with evidence behind it, that a
     future session must not casually reopen → `docs/decisions/`.
   - *Branch state* — a SHA, a count, a measurement, an open item, a "not yet
     validated" → the handoff.
   - *Permanent behavioral rule* → `AGENTS.md` and the relevant
     `.claude/rules/` file. Rules are stated once and linked, never duplicated.

3. **For a new decision**, add a record to `docs/decisions/` following the
   existing shape: context, the decision, the evidence that settled it,
   consequences, and what would justify reopening it. Add it to
   [`docs/decisions/README.md`](../../../docs/decisions/README.md).

4. **To change an accepted decision, supersede it.** Write a new record that
   explains why, mark the old one `Superseded by …`, and leave the old reasoning
   intact. Never rewrite an accepted decision in place — the reason it was made
   is as valuable as the decision.

5. **Update the handoff's point-in-time sections**: the baseline table, test
   counts, refactoring history, per-protocol status, known defects, and open
   decisions. Do not copy durable decision text back into it — link.

6. **Check the cross-links still resolve** and that nothing was duplicated
   between files.

## Expected output

- Focused edits to the correct file for each fact's lifetime.
- A short statement of what moved where, and why.
- Any place where a document disagreed with the repository, reported explicitly
  — the repository is right and the document gets corrected.

## Stopping conditions

- Do not delete content because it looks old. Reclassify it or mark it
  superseded.
- Do not rewrite an accepted decision in place.
- Do not record a decision as settled if the evidence does not settle it. An
  open question belongs in the handoff's *Decisions still open*, not in
  `docs/decisions/`.
- Do not put a SHA, a test count, or a validation status into `AGENTS.md`,
  `CLAUDE.md`, or a rule file. Those are point-in-time facts.
- Do not put credentials or real cluster identifiers into any tracked file.
