# opstat engineering documentation

Developer and agent-facing documentation. **User and operator documentation is
elsewhere** — see [../README.md](../README.md) and the per-protocol references
([NFSv3](../NFSv3_README.md), [NFSv4.1](../NFSv41_README.md),
[SMB](../SMB_README.md), [S3](../S3_README.md),
[NVMe-oTCP](../NVMe_TCP_README.md), [SETUP](../SETUP.md)).

Everything here is organized by **lifetime**. Putting a fact in the wrong
lifetime is how a document set goes stale: a SHA in a policy file rots, and a
settled decision buried in a branch handoff disappears when the branch merges.

## Start here

| Doc | Purpose | Lifetime |
|---|---|---|
| [../AGENTS.md](../AGENTS.md) | **Read first.** The behavioral contract for working on this repository — architecture, Python floor, command contract, decision hierarchy, API efficiency, VMS safety, TUI requirements, git policy, definition of done | Durable policy |
| [../CLAUDE.md](../CLAUDE.md) | Claude Code entrypoint; imports `AGENTS.md` and adds tool-specific routing | Durable policy |
| [decisions/](decisions/) | Settled engineering decisions with the evidence that settled them | Durable; superseded, never rewritten |
| [REFACTOR_HANDOFF.md](REFACTOR_HANDOFF.md) | State of the `refactor/tui-performance` effort: baseline, measurements, per-protocol status, open work | **Point-in-time** |
| [CLAUDE_HANDOFF.md](CLAUDE_HANDOFF.md) | Concise cross-AI handoff: current state, pending real-VMS validation, blocked items | **Point-in-time** |
| [../scripts/var203_validation/](../scripts/var203_validation/README.md) | Work-laptop validation package: automated probes + interactive checklist for the remaining live-cluster questions | Point-in-time |
| [WORKSTATION_BOOTSTRAP.md](WORKSTATION_BOOTSTRAP.md) | Setting up a machine to continue the work | Durable procedure |

## Agent configuration

| Path | Purpose |
|---|---|
| [../.claude/rules/](../.claude/rules/) | Permanent scoped rules — git and approval, VAST API safety, testing and evidence, TUI behavior. Each carries the incident behind it and what enforcement cannot catch |
| [../.claude/agents/](../.claude/agents/) | Read-only specialist reviewers |
| [../.claude/skills/](../.claude/skills/) | Repeatable procedures |
| [../.claude/settings.json](../.claude/settings.json) | Mechanical permission policy |
| [../CLAUDE.local.md.example](../CLAUDE.local.md.example) | Template for optional, git-ignored, machine-local notes |
| [../.cursor/rules/](../.cursor/rules/) | Cursor adapter; policy itself lives in `AGENTS.md` |

## Validation

One documented command:

```bash
./scripts/validate.sh
```

Current Python plus the mandatory 3.8 floor, with explicit failure when
`openssl` is missing rather than a silent skip of 171 tests. See
[../scripts/validate.sh](../scripts/validate.sh) and
[../.claude/rules/testing-and-evidence.md](../.claude/rules/testing-and-evidence.md).

## Where a new fact belongs

| The fact is… | It goes in… |
|---|---|
| A permanent rule about how work is done | `AGENTS.md` (short form) + the matching `.claude/rules/` file (long form) |
| A settled question with evidence behind it | `docs/decisions/` |
| A SHA, a test count, a measurement, a status | `docs/REFACTOR_HANDOFF.md` |
| An unresolved question | `REFACTOR_HANDOFF.md` → *Decisions still open* |
| How to set up a machine | `docs/WORKSTATION_BOOTSTRAP.md` |
| How a user runs the tool | the top-level `README.md` / protocol READMEs |

State it once and link to it. Duplication is how the two copies start
disagreeing.
