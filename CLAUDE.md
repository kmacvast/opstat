# CLAUDE.md — Claude Code guidance for opstat

@AGENTS.md

The imported [AGENTS.md](AGENTS.md) is the source of truth for architecture,
the Python 3.8 floor, the command contract, the decision hierarchy, API
efficiency, VMS safety, TUI behavior, testing, secrets, git policy, prohibited
actions and the definition of done. **When in doubt, `AGENTS.md` governs.**

This file adds only what is specific to Claude Code.

---

## What loads automatically, and what does not

| Path | Loaded automatically? |
|---|---|
| `CLAUDE.md` | Yes — and its `@AGENTS.md` import pulls in the constitution |
| `CLAUDE.local.md` | Yes, if present. Git-ignored, machine-local, **never secrets**. See [CLAUDE.local.md.example](CLAUDE.local.md.example) |
| `.claude/agents/*.md` | Discovered as subagent definitions |
| `.claude/skills/*/SKILL.md` | Discovered as invocable skills |
| `.claude/settings.json` | Applied as the project permission policy |
| `.claude/rules/*.md` | **No.** These are reference documents |
| `.cursor/rules/*.mdc` | **No.** Cursor-only; its policy is mirrored in `AGENTS.md` |

`.claude/rules/` is not auto-loaded. `AGENTS.md` carries the short form of every
rule and links to the long form. **Read the relevant rule file before working in
its area** — each one holds the incident that produced it, the evidence, and
what automated enforcement cannot catch:

- [.claude/rules/git-and-approval.md](.claude/rules/git-and-approval.md) —
  before any git operation beyond reading.
- [.claude/rules/vast-api-safety.md](.claude/rules/vast-api-safety.md) — before
  touching `vast_common.py`, `vast_discovery.py`, monitor lifecycle, or
  anything that issues a request.
- [.claude/rules/testing-and-evidence.md](.claude/rules/testing-and-evidence.md)
  — before changing tests, the mock, or claiming validation.
- [.claude/rules/tui-behavior.md](.claude/rules/tui-behavior.md) — before
  touching rendering, drills, footers, or loading states.

Do not restate these rules in new files. One statement, linked from everywhere.

## Before you touch anything

1. **Inspect state.** `git status`, current branch, `git rev-parse HEAD` versus
   `origin/refactor/tui-performance`. Know whether HEAD is published.
2. **Preserve uncommitted work you did not create.** Never discard, revert, or
   overwrite it. If a change collides with it, stop and report.
3. **Read the tests before the implementation.** They encode real-cluster
   defects and are the best statement of intended behavior.
4. **Do not trust a summary over the repository** — including this file,
   [docs/REFACTOR_HANDOFF.md](docs/REFACTOR_HANDOFF.md), or a prior session's
   report. Re-derive branch state, test counts and open work from the repo.

## Read-only by default

Stay read-only — no edits, no writes, no side effects — when:

- You are reviewing, auditing, planning, measuring, or answering a question.
- The task is ambiguous, or would require an L1 action from the decision
  hierarchy in `AGENTS.md`.
- The change conflicts with a settled decision in [docs/decisions/](docs/decisions/).

In those cases, stop and report rather than improvising.

## Specialist subagents

Three read-only reviewers live in [.claude/agents/](.claude/agents/). Use one
when a change touches its area rather than self-reviewing:

- **api-efficiency-reviewer** — request volume per refresh, monitor merge and
  fallback, batching and `object_id` slicing, throttling, keep-alive reuse,
  monitor cleanup on error paths.
- **telemetry-semantics-reviewer** — evidence-gating, derived labelling,
  zero-versus-unavailable, units, cumulative-versus-instantaneous,
  newest-complete-row scoping, monitor-API versus exporter provenance.
- **tui-render-reviewer** — footer survival in every mode and width, loading
  frames before blocking work, truncation, resize, responsiveness.

They are read-only by tool grant (`Read, Grep, Glob`), not merely by
instruction. **Do not grant a reviewer write or execute access.** All three
report `Severity / Evidence / Recommendation` and separate blocking findings
from advisory ones.

Python-3.8 compatibility and test adequacy deliberately have no reviewer agent —
`./scripts/validate.sh` decides compatibility mechanically, and every reviewer
above carries regression-test adequacy as a review dimension.

## Skills

Repeatable procedures in [.claude/skills/](.claude/skills/):

- `run-quality-gates` — run the deterministic gate and interpret its output,
  including the skip traps.
- `audit-api-efficiency` — measure per-refresh and per-drill request volume
  against the instrumented mock.
- `validate-against-real-vms` — the real-cluster validation cookbook, with its
  approval boundary.
- `run-vms-discovery` — read-only interrogation of the VMS observability
  surface.
- `update-refactor-handoff` — keep the durable/transient documentation split
  correct.

## Finishing a task

End with the four-part completion report defined in
[AGENTS.md](AGENTS.md#completion-report):

**CHANGES** · **CHECKS** · **RISKS** · **REMAINING WORK**

CHECKS carries exact observed results — commands run, counts collected/passed/
failed/skipped, which interpreters, and what was *not* run. "Tests pass" is not
a check. Do not claim success without running the applicable checks, and do not
describe anything as real-cluster validated unless it was.
