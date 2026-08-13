# Git and approval

Permanent rule for `opstat`. Applies to every agent and every branch.

Short form and the decision hierarchy live in [AGENTS.md](../../AGENTS.md).
This file holds the reasoning, the boundaries, and the limits of enforcement.

## Why this rule exists

The repository owner validates every change against a real VAST cluster that
this session cannot reach, and then publishes. That sequencing is the whole
quality control: **an agent that publishes has removed the only check the
process has.**

It is also the rule with the most attempts to erode it, because publishing feels
like finishing. It is not. The work is finished when it is validated, and
validation happens after the owner reads the report.

Two incidents shaped the exceptions below.

**Origin moved ahead mid-effort.** A push was rejected because the owner had
pushed load-generator commits from another machine. The correct response was to
fetch, confirm zero file overlap, **rebase**, verify the resulting content was
identical, and push — reporting that the SHA had changed from `d1e8dd5` to
`86d24ce` as a result. Force-pushing would have destroyed the owner's commits.
This is why rebase is *permitted and expected* here, and why a blanket rebase
denial would be actively harmful.

**Every push in this effort was preceded by a structured request.** The owner
asks with a checklist: confirm a clean tree, list the commits, push only the
named branch, confirm the remote SHA, and report the resulting HEAD. That shape
is the norm, not ceremony.

## Mandatory behavior

### Requires explicit approval, every time

Approval is per-action and per-session. Approval for a *task* is never approval
for a publishing *step* inside it. Approval once is never approval again.

- `git push` — including a first push of a new branch.
- `git push --force`, `--force-with-lease`, `--delete`, `--mirror`, `--prune`.
- Merge into any branch; fast-forwarding `main`.
- `git tag`, and anything that triggers the release workflow.
- Creating a pull request (`gh pr create` or equivalent).
- `git reset --hard`, `git clean -f/-d/-x`, `git checkout --` over
  uncommitted work.
- Rewriting published history: `filter-branch`, `filter-repo`,
  `update-ref -d`, `commit --amend` on a pushed commit, `reflog expire`,
  `gc --prune=now`.
- Deleting a branch, local or remote.
- Switching branches when uncommitted work is present.
- Committing. Commit when asked, as part of carrying out that instruction —
  never on your own initiative.

### Explicitly allowed

- **`git rebase` onto fetched upstream commits.** This is the correct response
  to a rejected push. Rebase, verify the content is unchanged, and report the
  new SHA.
- **`git merge --ff-only`** to advance a local branch to a published one — it
  refuses rather than destroys when the branch has diverged.
- Every read-only git command: `status`, `diff`, `log`, `show`, `branch`,
  `rev-parse`, `ls-files`, `remote`, `stash list`.
- Creating a worktree to prove a regression test fails on a prior commit. This
  was used to verify `test_bandwidth_survives_a_monitor_that_mixes_metric_families`
  failed on `cb6e5f8`, and it is a good pattern.

### When asked to push

Report, in this order:

1. `git status --short` — confirm clean, or state exactly what is dirty.
2. `git log --oneline <remote-ref>..HEAD` — the commits that will publish.
3. Whether origin has moved ahead, and if so what the rebase changed.
4. The exact command run, and only the named branch.
5. `git rev-parse HEAD` and `git rev-parse <remote-ref>` afterwards, confirming
   they agree.
6. If any SHA changed from what was approved, say so prominently and say why.

### A successful outcome does not authorize the action retroactively

If an unapproved publishing or destructive action happens anyway, report it
immediately and plainly, before being asked, including what is unrecoverable.
Do not reason backwards from the outcome. "It rebased cleanly", "nothing was
lost", "it was only a branch" are assessments the owner makes, not the agent.

## What automated enforcement detects

`.claude/settings.json` denies the destructive set outright — `rm -rf`,
force-push variants, `reset --hard`, `clean -f/-d/-x`, `filter-branch`,
`filter-repo`, `update-ref -d` — and routes `git push`, `git tag`, `gh pr`,
`git merge` and `git rebase` through an approval prompt rather than allowing
them silently.

## What automated enforcement CANNOT detect

- **Publishing through a path the pattern does not match.** A push driven by
  `gh api`, a git alias, a shell script, or a `Makefile` target reaches the
  remote without matching `Bash(git push *)`.
- **Whether approval was actually given.** The prompt confirms a human clicked;
  it cannot confirm the human understood what was in the commits.
- **Scope.** `git push origin refactor/tui-performance` and a push that carries
  three unrelated commits look identical to a permission pattern.
- **Committing the wrong thing.** `git add` and `git commit` are ordinary
  operations; nothing inspects what went in.
- **The rebase judgement.** The permission layer can ask before a rebase; it
  cannot tell a correct rebase-onto-upstream from one that rewrites published
  history.

The permission layer is a blast-radius control. **The rule above is the actual
control.**

## Lab hosts receive code through git

Edit and commit on the workstation clone, push, then `git pull` on the lab host
(`kevin-mcdonald-ubu-01`, `kmactools`, or any other clone). Never `scp` or
otherwise copy a tree that git owns; if a lab clone has leftover copies, delete
those untracked files and pull so git owns the tree.

Live systemd units under `/etc/systemd/system/` are installed from the pulled
tree via `scripts/systemd/install-lab-loadgen-units.sh`. `/etc` is not the
source of truth for script content.

## Stopping conditions

Stop and report rather than improvising when:

- A push is rejected and the reason is not simply "origin moved ahead".
- Rebasing would touch files the owner changed.
- The working tree is dirty and you did not create the changes.
- You are asked to publish and the tree does not match what was reviewed.
- Any action in the approval list above would be needed to make progress.
