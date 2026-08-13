# AGENTS.md — opstat

Tool-neutral operating instructions for AI coding agents (Claude Code, Cursor,
Codex, or any compatible agent) and for humans who want the same contract
written down. **This file is the source of truth** for how work is done in this
repository.

Tool-specific guidance lives elsewhere and adds only routing:

- [CLAUDE.md](CLAUDE.md) — Claude Code entrypoint; imports this file.
- [.cursor/rules/lab-git-sync.mdc](.cursor/rules/lab-git-sync.mdc) — Cursor
  adapter; points here.

Detailed rules live in [.claude/rules/](.claude/rules/) and are referenced from
the relevant sections below. They are not auto-loaded — read the one that covers
the area you are changing.

---

## Repository purpose

`opstat` is a terminal dashboard for VAST Data clusters. It polls the VMS REST
API and renders live per-protocol performance panels, with drill-downs by cNode,
view, tenant and host.

It is an **observability tool**. It reads from production storage clusters that
other people depend on. Two consequences run through everything below: it must
not change cluster state, and it must not display a number the cluster did not
actually report.

## Architecture overview

Single-threaded, one module per protocol, module-global state, no runtime
dependencies outside the standard library.

```
opstat                 # CLI entrypoint (extensionless; loaded by tests via runpy)
nfs_v3.py              # NFS v3 engine        — fully refactored, real-VMS validated
nfs_v41.py             # NFS v4.1 engine      — fully refactored + native exporter telemetry
smb.py                 # SMB/SMB2 engine      — partially refactored
s3.py                  # S3 engine            — partially refactored
nvme_tcp.py            # NVMe-oTCP engine     — least refactored
vast_common.py         # Auth, keep-alive HTTPS transport, monitor lifecycle,
                       #   signals, terminal/keyboard, sample selection, catalog reader
vast_drill.py          # Shared drill machinery: candidate ranking, probe-validated
                       #   batch monitors, re-query throttle, loading-status helper
vast_discovery.py      # Read-only survey of the VMS observability surface
                       #   (--discover-metrics). Discovery-time only.
nfs4_native.py         # Native NFSv4 telemetry from the Prometheus exporter
tui_layout.py          # Column layout, display width, value formatting
openmetrics.py         # JSON Lines exporter
vast_api_log.py        # --log-api-calls REST logging
wizard.py              # Interactive setup when run with no args on a TTY
tests/                 # pytest suite + mock_vms.py (in-process HTTPS VMS mock)
scripts/               # Lab load generators, systemd units, build helpers, validate.sh
docs/                  # Durable decisions, refactor handoff, workstation bootstrap
.github/workflows/     # test.yml (pytest matrix), release.yml (tag-triggered builds)
```

Two **separate** telemetry paths exist and must not be conflated: the VMS
monitor API (`/monitors/`) and the Prometheus exporter
(`/prometheusmetrics/*`). See [docs/decisions/D-001-monitor-api-and-exporter-are-separate-paths.md](docs/decisions/D-001-monitor-api-and-exporter-are-separate-paths.md).

## Supported Python versions

**Python 3.8 is the floor and it is mandatory.** CI
(`.github/workflows/test.yml`) runs 3.8, 3.10, 3.12 and 3.14.

- Do not use syntax or stdlib APIs newer than 3.8. No walrus-only idioms that
  break parsing on 3.8, no `dict |` merge, no `list[str]` annotations at
  runtime, no `functools.cache`, no `str.removeprefix`.
- **Current Python must also stay green.** Do not fix 3.8 by breaking 3.12/3.14.
- A change is not done until it has been run on both. 3.12 accepts things 3.8
  rejects silently; several changes on this branch needed 3.8 fixes that no
  amount of 3.12 testing would have surfaced.

## No new runtime dependencies

`requirements.txt` documents that the runtime is stdlib-only. `pytest` is the
only development dependency. Reach for `http.client`, `select`, `ssl`, `re`,
`argparse` rather than adding a package. Adding a runtime dependency is an
L1 decision (see below).

---

## Command and test contract

These are the commands this repository supports. **Do not invent others.** If
something you need does not exist, say so rather than guessing at a command
line.

| Command | Purpose |
|---|---|
| `./scripts/validate.sh` | **The gate.** Full validation: current Python + 3.8, tooling checks, collection count. Use this by default. |
| `./scripts/validate.sh --fast` | Current Python only; skips the 3.8 leg. For inner-loop iteration, never for sign-off. |
| `python3 -m pytest -q` | Full suite on the current interpreter |
| `python3 -m pytest tests/test_nfs4_native.py -q` | One suite |
| `python3 -m pytest -q -k "loading or footer"` | By keyword |
| `uv run --python 3.8 --no-project --with pytest -- python -m pytest -q` | The 3.8 floor |
| `python3 tests/mock_vms.py --port 8443` | Standalone mock VMS, for driving the TUI with no cluster |
| `./opstat --help` / `./opstat -V` | CLI sanity, no cluster needed |
| `./opstat … --log-api-calls` | Real-cluster REST logging |
| `./opstat … --discover-metrics` | Read-only VMS observability survey |

`openssl` is **required**: `tests/mock_vms.py` generates a throwaway TLS
certificate with it, and 171 of the 400 tests are gated on its presence by
module-level `pytestmark`. Without it those suites skip *cleanly*, so a broken
environment looks like a passing run. `scripts/validate.sh` fails loudly rather
than letting that happen — see
[.claude/rules/testing-and-evidence.md](.claude/rules/testing-and-evidence.md).

---

## Decision hierarchy

Adapted to this repository. The point is to stop both over-asking and
over-reaching.

### L1 — STOP AND ASK

Do not proceed without explicit approval for the specific action:

- **Publishing anything**: `git push`, merge, tag, PR creation, release.
- **Any operation that changes cluster state**, or any REST call that is not a
  `GET` outside the application's existing audited monitor-lifecycle code.
- **Changing displayed telemetry semantics** — what a number means, its units,
  its source family, or whether a panel appears.
- **Adding a runtime dependency**, or raising the Python floor above 3.8.
- **Introducing concurrency** (threads, async, subprocesses) into the engines.
  Background threading for the exporter scrape was deliberately deferred; see
  [docs/decisions/D-005-native-telemetry-is-an-on-demand-throttled-drill.md](docs/decisions/D-005-native-telemetry-is-an-on-demand-throttled-drill.md).
- **Reopening a settled decision** in [docs/decisions/](docs/decisions/).
- **Destructive git**: force-push, history rewriting, `reset --hard`, branch
  deletion, discarding uncommitted work.
- **Deleting or weakening a test.**

### L2 — CONTINUE, AND DOCUMENT IT

Reasonable alternatives exist; choose the strongest, state the rationale in the
completion report, and keep going. The owner supersedes later if they disagree.

- Module boundaries and where a shared helper lives.
- Monitor merge strategy and probe/fallback shape.
- Throttle intervals, cache lifetimes, batch sizes — provided the invariants
  below hold.
- Panel layout within existing conventions; column ordering; wording of labels.
- Which test layer covers a behavior.

### L3 — DO NOT STOP

Delegated outright. Do not ask.

- Reading anything in the repository; running the gate; running any read-only
  command in the table above.
- Naming, comments, docstrings, formatting, test organization.
- Fixing review findings inside already-approved scope.
- Investigating, measuring, and reproducing a defect.

---

## Working method

The approach that has worked here, in order:

1. **Inspect repository state first.** `git status`, current branch, whether
   HEAD is ahead of origin. Identify uncommitted work that is not yours and
   never discard it.
2. **Measure first.** Establish a baseline before optimizing.
   `tests/mock_vms.py` records every API call and can inject latency and
   failures.
3. **Prove against the real cluster.** Mock behavior is necessary but not
   sufficient. Several defects appeared only against VAST OS 5.5.0.1.
4. **Reduce API calls.** Treat request volume as a first-class metric.
5. **Encode the finding as a test.** Every real-cluster defect fixed on this
   branch has a regression test reproducing the literal payload shape.
6. **Report faithfully.** If something is unproven, say so. If a measurement
   contradicts an earlier claim, correct the claim.

### Distinguish observed facts from assumptions

State which is which, every time. The most expensive failure on this branch was
reading a summary line instead of the evidence file underneath it: a discovery
report contained `Nfs4Metrics` 944 times while the console printed only an
aggregate count, and the finding was missed until the repository owner read the
raw output.

### Verify claims against the repository

Do not trust a summary — including this file, [docs/REFACTOR_HANDOFF.md](docs/REFACTOR_HANDOFF.md),
or a previous session's report — over the current code and `git log`. Documents
go stale; the repository does not. When resuming, re-derive branch state, test
counts and outstanding work from the repo.

### Scope discipline

- Work on one engine, one defect, or one change at a time.
- Do not silently broaden scope. If a fix in `nfs_v41.py` looks portable to
  `smb.py`, say so and let the owner decide; do not port it in the same change.
- Do not silently change architecture. Surface it for review.
- **Distinguish a pre-existing defect from one this work introduced.** Say which
  it is, with evidence — the missing navigation footer was pre-existing, and the
  bandwidth-scoping loss was introduced by a change on this branch. Those are
  different reports and different obligations.

---

## Evidence requirements

### Never fabricate telemetry

Never display a metric the cluster did not return. Panels are
**evidence-gated**: a row appears only when a live query actually returned that
property. Catalog presence is not sufficient — a real VAST build advertised
`OPEN`/`CLOSE` counters that no monitor would ever return.

If a derived figure is shown, label it as derived. Operations per compound is
printed as `DERIVED RATIO (not a native metric)` because VMS publishes no
compound counter.

### Zero is not the same as unavailable

A measured `0.00 ops/s` means "no traffic" and is information. A `-` means "no
data". Do not collapse the two. Zero session churn is a healthy-cluster signal
and must render as a real zero.

### Never invent metric semantics

Units, cumulative-vs-instantaneous, scope and label meaning are properties of
the cluster, not of a plausible reading of a metric name. Each one that this
project relies on was proven, and the proof is recorded in
[docs/decisions/](docs/decisions/). If you need a semantic that is not recorded
there, it is unproven — investigate it or say it is unknown.

### Real-VMS observations outrank mock assumptions

The mock is built to reproduce real cluster behavior, not to define it. Where
they disagree, the cluster is right and the mock is a bug. Conversely, do not
present mock-only behavior (~1 ms loopback scrape latency, synthetic values,
planted busy views) as cluster behavior.

See [.claude/rules/testing-and-evidence.md](.claude/rules/testing-and-evidence.md).

---

## API efficiency principles

This application has previously made far more API calls than necessary, and the
failure mode is invisible in a passing test suite. Request volume is a
**regression dimension**, tested in `tests/test_api_efficiency.py` and
`tests/test_drill_semantics.py`.

These invariants were each established by measurement. Breaking one is a
regression even if every test still passes.

| Invariant | Why |
|---|---|
| The 5-second refresh path must not scrape `/prometheusmetrics/*` | `/prometheusmetrics/basic` is ~276 KB and took 1.2–2.4 s on a real cluster, with ~2x run-to-run variance |
| Never request `/prometheusmetrics/all` | 4.8 MB for the same 118 `Nfs4Metrics` series `basic` already carries |
| One keep-alive HTTPS connection per session | `vast_common.request` reuses a persistent connection; a fresh TCP+TLS handshake per call was ~10x slower |
| No per-object monitor/query explosions | Drill-downs batch into one monitor and slice by `object_id` |
| Rank candidates by activity, never by API order | A head-slice of `/views/` shows arbitrary idle views on a cluster with hundreds |
| Throttle drill and exporter refreshes | Object-scoped families publish ~1/min; the exporter far slower still |
| Select the newest *complete* sample, scoped per metric family | VMS publishes a still-filling newest bucket; scoring across mixed families loses whole columns |

Heavy endpoints must not enter a normal refresh path by accident. Moving
`/prometheusmetrics/basic` onto the 5-second NFSv4.1 path is an **L1 decision**
requiring new evidence and explicit approval — see
[docs/decisions/D-004-heavy-exporter-endpoints-off-the-refresh-path.md](docs/decisions/D-004-heavy-exporter-endpoints-off-the-refresh-path.md).

Keep-alive connection behavior is load-bearing. Do not change it incidentally.

See [.claude/rules/vast-api-safety.md](.claude/rules/vast-api-safety.md).

---

## VMS safety rules

- **Read-only means read-only.** Discovery and diagnostics issue `GET` only,
  plus temporary monitors that are always deleted.
- **Never call a destructive endpoint because Swagger exposes it.** The
  `nfs4_delegs` `DELETE` sibling must never be invoked; there is a test
  asserting it is not.
- **Clean up temporary monitors**, including on the error path. Tests assert
  `vms.live_monitors() == {}` after teardown.
- **Never change cluster configuration.**
- A destructive effect is a property of the *effect*, not of the command's
  stated purpose. A measurement, a retry, or a quick reproduction can all mutate
  state.
- **A successful recovery does not authorize the action retroactively.** If an
  unapproved risky action happened to be harmless, it was still unapproved. Say
  so plainly; do not reason backwards from the outcome.

See [.claude/rules/vast-api-safety.md](.claude/rules/vast-api-safety.md).

---

## TUI behavior requirements

- **The navigation footer renders in every mode** — headline, drill, error,
  loading, and at every terminal width. A drill panel that returns early takes
  the controls with it; that shipped once and 122 tests now guard against it.
- **Paint a loading frame before blocking work.** A multi-second VMS call
  otherwise looks like a hang.
- **No silent truncation of controls.** The frame must never exceed the
  terminal width; the footer must degrade legibly, not disappear.
- **Preserve responsiveness.** The main loop is `select()`-driven, not a poll
  spin. Frame composition is on the hot path.
- Asynchronous or background work is an **architectural decision (L1)**, not an
  incidental optimization.

See [.claude/rules/tui-behavior.md](.claude/rules/tui-behavior.md).

---

## Testing and regression discipline

- A behavioral change requires a test at the appropriate layer.
- **A defect fix requires a regression test that reproduces the literal payload
  shape observed**, and that fails before the fix. Several fixes on this branch
  were proven by running the new test against the previous commit in a worktree.
- Never delete, skip, weaken or loosen a test to make a change pass. Never
  change test configuration to suppress a failure.
- The mock's quirks are load-bearing — partially-filled newest buckets,
  cumulative counters, 429 views with the busy ones planted deep, an object-id
  cap, mixed-family rejection, near-identical cNode hostnames. Do not
  "simplify" them away.
- Report skipped and failing tests honestly, with output. A green run with a
  much smaller test count than expected means suites are silently skipping.

See [.claude/rules/testing-and-evidence.md](.claude/rules/testing-and-evidence.md).

---

## Secrets handling

Never put a password, token, or cluster credential in the repository, in a
commit message, in a test fixture, or in documentation.

- Auth comes from `VAST_TOKEN` / `VAST_PASSWORD` environment variables or an
  interactive prompt.
- `--password` on the command line is supported but warned against — it leaks
  through `ps` and shell history. Prefer the environment variable in examples.
- Do not paste real cluster hostnames, IPs, tenant names or view paths into
  tracked files. Use placeholders (`<VMS_HOST>`). The one exception already in
  the tree is the lab cluster named in the handoff as validation provenance;
  do not add more.
- API logs and discovery reports contain cluster identifiers. They are written
  to `/tmp` on purpose. Do not commit them.
- `CLAUDE.local.md` is git-ignored and is **still not** a place for secrets.

---

## Git and change management

- Work happens on a feature branch, never directly on `main`.
- **Do not push, merge, tag, or open a PR unless explicitly asked.** The
  repository owner controls publication.
- **Do not commit on your own initiative.** Commit when asked, as part of
  carrying out that instruction.
- **Rebase is allowed and expected.** Before pushing, check for incoming
  commits and rebase onto them rather than force-pushing. This is the correct
  workflow here and has been used successfully when origin moved ahead.
- **Force-push and history rewriting of published commits are prohibited**
  without explicit approval for the specific action.
- Commit messages explain *why*, and state what was measured where relevant.
- Co-author trailer: `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`
- **Lab hosts receive code through git.** Edit and commit on the workstation,
  push, then `git pull` on the lab host. Never `scp` or otherwise copy a tree
  that git owns. Live systemd units under `/etc/systemd/system/` are installed
  from the pulled tree via `scripts/systemd/install-lab-loadgen-units.sh`;
  `/etc` is not the source of truth for script content.

See [.claude/rules/git-and-approval.md](.claude/rules/git-and-approval.md).

---

## Prohibited actions

Unless explicitly approved for the specific action:

- Push, merge, tag, create a PR, or publish a release.
- Force-push, rewrite published history, `reset --hard`, `clean -fdx`, or
  delete a branch.
- Discard or overwrite uncommitted work you did not create.
- Issue any non-`GET` VMS request outside the application's existing monitor
  lifecycle; invoke `nfs4_delegs` `DELETE`; change cluster configuration.
- Leave a temporary monitor behind on any path, including error paths.
- Display a value the cluster did not return, or present a derived figure as
  native.
- Collapse `0` and "unavailable" into the same rendering.
- Add a runtime dependency, or use syntax newer than Python 3.8.
- Delete, skip, weaken, or loosen a test; change test config to hide a failure.
- Introduce threads, async, or subprocesses into the engines.
- Put a credential or real cluster identifier into a tracked file.
- Claim a check passed without running it, or claim validation that was not
  actually performed.
- Broaden scope beyond what was asked.

---

## Definition of done

A change is done only when all of the following hold:

- The change matches the requested scope — no more, no less.
- `./scripts/validate.sh` passes: **current Python and Python 3.8**, with the
  `openssl`-gated suites actually running rather than skipping.
- The test count is what you expect. A smaller green run is a failure signal,
  not a success.
- New or changed behavior has tests; a defect fix has a regression test proven
  to fail before the fix.
- Documentation relevant to the change is updated — including
  [docs/decisions/](docs/decisions/) if a durable decision was made or
  superseded.
- No secrets, credentials, or real cluster identifiers were introduced.
- No API-efficiency invariant regressed.
- Remaining work, risks and unverified claims are reported honestly.

---

## Completion report

Every substantive task ends with this summary. It is not optional and it is not
a formality.

```
CHANGES
  What changed and why, by file.

CHECKS
  Every command run, with its exact observed result.
  Test counts: collected / passed / failed / skipped.
  Which interpreters were exercised.
  What was NOT run, and why.

RISKS
  Regression risk, API-efficiency impact, telemetry-semantics impact,
  3.8 compatibility, anything unverified.

REMAINING WORK
  Incomplete, blocked, or deliberately deferred items.
```

**CHECKS must contain exact observed results.** "Tests pass" is not a check —
`400 passed in 32.98s on Python 3.12.13; 400 passed on 3.8; 0 skipped` is.
If a check was not run, say that instead of implying it was.

Do not claim success without running the applicable checks. Do not describe work
as validated on a real cluster unless it actually was.
