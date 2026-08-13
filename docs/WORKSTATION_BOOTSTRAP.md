# Workstation Bootstrap

Minimum procedure to continue the `refactor/tui-performance` effort on a
different machine.

General environment setup (installing Python, creating a virtualenv, running
`opstat` for the first time) is already documented in
[../SETUP.md](../SETUP.md) — this file covers only what is specific to
resuming the refactor, and links rather than duplicates.

---

## 1. Get the repository

```bash
git clone git@github.com:kmacvast/opstat.git
cd opstat
git fetch origin refactor/tui-performance
git checkout refactor/tui-performance
```

If the clone already exists:

```bash
git fetch origin refactor/tui-performance
git checkout refactor/tui-performance
git merge --ff-only origin/refactor/tui-performance
```

`--ff-only` refuses rather than destroys if the branch has diverged.

### Verify you are where you expect

```bash
git rev-parse --abbrev-ref HEAD        # refactor/tui-performance
git rev-parse HEAD
git rev-parse origin/refactor/tui-performance
git log --oneline -1 origin/main       # base should be 77549f06
git status --short                     # expect clean
```

If local HEAD and `origin/refactor/tui-performance` differ, there is
unpublished work on one side. Resolve that before starting.

---

## 2. Install what the gate needs

Runtime is **Python 3.8+, standard library only** — no runtime dependencies.
See [../SETUP.md](../SETUP.md) for platform-specific installation and
virtualenv creation.

Three things must be present before validation means anything.

### `pytest` — the only development dependency

```bash
python3 -m pip install pytest
```

### `openssl` — required, not optional

```bash
openssl version        # must succeed
```

`tests/mock_vms.py` generates a throwaway TLS certificate with the `openssl`
binary. **171 of the 400 tests are gated on it** by module-level `pytestmark`,
and without it they skip *cleanly* while pytest still reports success. A broken
environment looks exactly like a passing run.

`./scripts/validate.sh` refuses to run rather than let that happen — but check
it here so you find out now rather than mid-task.

### `uv` — for the mandatory Python 3.8 leg

CI enforces a 3.8 floor and several changes on this branch needed 3.8 fixes that
3.12 accepted silently. The fastest way to check locally without managing a
second interpreter is [`uv`](https://docs.astral.sh/uv/):

```bash
uv --version
```

If `uv` is unavailable, any 3.8 interpreter works — but then run the 3.8 suite
by hand, because `scripts/validate.sh` invokes `uv` for that leg.

---

## 3. Run the gate

One documented command:

```bash
./scripts/validate.sh
```

Expect, on both current Python and Python 3.8:

```text
Collection
  collected       : 400 (floor 395)
...
RESULT: PASS
  Current Python and Python 3.8 both green, nothing skipped.
```

Anything else — a `VALIDATION INCOMPLETE` banner, a smaller collection count,
any skipped tests — means the environment is not ready. Fix the environment, not
the gate.

`--fast` skips the 3.8 leg for inner-loop iteration. It prints the incomplete
banner and is never sign-off.

### CLI sanity, no cluster needed

```bash
./opstat --help
./opstat -V
```

### Exercise the TUI without a cluster

`tests/mock_vms.py` runs standalone as a fake VMS:

```bash
python3 tests/mock_vms.py --port 8443
# then, in another terminal:
VAST_TOKEN=dummy ./opstat --nfs --version=4.1 --vms 127.0.0.1 --vms-port 8443
```

Useful for exercising panels, drills and key bindings offline. Its latencies and
values are synthetic — do not mistake them for cluster behavior.

---

## 4. Agent configuration

### Arrives automatically through git

Nothing here needs to be copied by hand.

| File | Purpose |
|---|---|
| [`AGENTS.md`](../AGENTS.md) | **The behavioral contract.** Tool-neutral; governs every agent |
| [`CLAUDE.md`](../CLAUDE.md) | Claude Code entrypoint; imports `AGENTS.md`, adds routing. Loaded automatically |
| [`.claude/settings.json`](../.claude/settings.json) | Project permission policy — denies destructive operations, prompts for publishing |
| [`.claude/rules/`](../.claude/rules/) | Git and approval, VAST API safety, testing and evidence, TUI behavior |
| [`.claude/agents/`](../.claude/agents/) | Read-only specialist reviewers |
| [`.claude/skills/`](../.claude/skills/) | Repeatable procedures |
| [`docs/decisions/`](decisions/) | Settled decisions with their evidence |
| [`docs/REFACTOR_HANDOFF.md`](REFACTOR_HANDOFF.md) | Branch state, measurements, open work |
| [`docs/WORKSTATION_BOOTSTRAP.md`](WORKSTATION_BOOTSTRAP.md) | This file |
| [`.cursor/rules/lab-git-sync.mdc`](../.cursor/rules/lab-git-sync.mdc) | Cursor adapter; the policy itself is in `AGENTS.md` |

### Must be established locally, and never copied

- **Claude Code authentication.** Sign in on the work computer with the
  work-provided licence. **Do not copy `~/.claude/` from another machine** —
  not the credentials, not the settings, not the caches, not the session
  history. It is machine- and account-specific, it carries authentication
  material, and it has nothing to do with this repository.
- **Machine-global agent settings** (`~/.claude/settings.json` and similar) are
  personal preference. The project's shared policy is `.claude/settings.json`,
  which arrives through git.
- **VMS credentials.** See §5.
- **Shell environment**: `VAST_TOKEN` / `VAST_PASSWORD`, any `PATH` setup.

### Optional machine-local notes

Copy [`../CLAUDE.local.md.example`](../CLAUDE.local.md.example) to
`CLAUDE.local.md` for machine-specific conveniences — interpreter paths, lab
host aliases, preferred terminal size. It is git-ignored.

**It is not a place for secrets.** No `VAST_TOKEN`, no `VAST_PASSWORD`, no API
keys, no passwords. Those live in the environment or a keychain, never in a
Markdown file.

### Deliberately not transferred

- Session transcripts, caches, and history.
- Raw discovery reports and API logs — they lived in `/tmp` and downloads on the
  originating machine, and they contain cluster identifiers. Their conclusions
  are preserved in [`docs/decisions/`](decisions/); the tooling to regenerate
  them is committed.
- Scratch benchmark harnesses used for PTY-driven measurement.

---

## 5. Credentials

Never commit or paste a password into the repository, a commit message, or
documentation.

```bash
export VAST_TOKEN=...        # preferred; checked before any password
# or
export VAST_PASSWORD=...
```

If neither is set, `opstat` prompts securely. Passing `--password` works but
prints a warning — it leaks through `ps` and shell history.

### Verify connectivity without embedding a password

```bash
export VAST_TOKEN=...
./opstat --nfs --version=3.0 --vms <VMS_HOST> --user admin --discover-metrics --no-color | head -20
```

`--discover-metrics` is read-only: it queries, creates only temporary monitors,
deletes them, and exits. If it prints the cluster name and a metric catalog
summary, connectivity and auth are good.

---

## 6. Start Claude

From the repository root:

```bash
claude
```

`CLAUDE.md` loads automatically and imports `AGENTS.md`. `.claude/settings.json`
applies as the project permission policy, and `.claude/agents/` and
`.claude/skills/` are discovered.

`.claude/rules/` is **not** auto-loaded — it is referenced from `AGENTS.md` and
`CLAUDE.md` and must be read when working in the relevant area.

To resume the refactor, paste the bootstrap prompt from the end of
[REFACTOR_HANDOFF.md](REFACTOR_HANDOFF.md#bootstrap-prompt-for-a-fresh-claude-session).
It instructs a fresh session to read the contract, the decisions and the
handoff, inspect the repository itself, run the gate, check whether the handoff
has gone stale, and report its understanding **before** changing any code.

---

## 7. Before doing real work

Read, in this order:

1. [`AGENTS.md`](../AGENTS.md) — the contract, including the L1/L2/L3 decision
   hierarchy and the prohibited actions.
2. [`docs/decisions/README.md`](decisions/README.md) and the records — settled
   questions, not open ones.
3. [`docs/REFACTOR_HANDOFF.md`](REFACTOR_HANDOFF.md) — *Known defects /
   unfinished work* and *Decisions still open*.
4. The [`.claude/rules/`](../.claude/rules/) file covering the area you will
   touch.

Then confirm:

- `./scripts/validate.sh` passes on both interpreters with nothing skipped.
- `git status` is clean and you know whether HEAD is published.
- You are not about to push, merge, tag, or open a PR. Those need explicit
  approval, every time.
