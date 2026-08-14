# VAST Lab Handoff

Temporary operational handoff between the personal development laptop and the
work laptop used for SELab validation.

This file is NOT a durable engineering source of truth.

Use it for:
- copy/paste-ready work-laptop commands
- var203 validation instructions
- returned validation output
- temporary observations
- exact monitor IDs from the current validation pass
- API-call counts and timing observations
- PASS / FAIL / UNVERIFIED notes

Do not use it for:
- settled architecture decisions
- permanent project requirements
- credentials
- passwords/tokens
- raw secrets
- long-term refactor state

Durable conclusions discovered here should later be reconciled into:
- docs/REFACTOR_HANDOFF.md
- docs/CLAUDE_HANDOFF.md
- docs/decisions/ where warranted

Current lab target:
  var203.selab.vastdata.com

var204.selab.vastdata.com is unavailable until explicitly restored.

Yes. That’s the cleanest move now.

I’d treat refactor/tui-performance at b69fb6b as the completed first refactor milestone, promote that exact history to main, and only then check out the newer continuation branch at d2214d1. That gives us a clear baseline:

main
  └── completed/tested refactor through b69fb6b
refactor/tui-performance-local-continuation-wip
  └── interrupted WIP + continuation work through d2214d1

The continuation branch already descends from b69fb6b, so advancing main to that point does not break its ancestry. In fact, it makes the eventual cleanup much easier.

On the work laptop, I’d use this exact sequence.

cd ~/git/opstat
echo "===== PRE-FLIGHT ====="
git status --short
git branch --show-current
git fetch origin
echo
echo "===== CURRENT REFS ====="
git rev-parse main
git rev-parse origin/main
git rev-parse refactor/tui-performance
git rev-parse origin/refactor/tui-performance
echo
echo "===== ANCESTRY CHECK ====="
if git merge-base --is-ancestor origin/main origin/refactor/tui-performance; then
    echo "GOOD: refactor/tui-performance is a direct descendant of main"
else
    echo "STOP: refactor/tui-performance is not a direct descendant of main"
    exit 1
fi

We expect:

origin/main
77549f064ad851fb6902394f55d1c76d80a34188
origin/refactor/tui-performance
b69fb6b...

If the working tree is clean and that says GOOD, validate the branch one last time before promotion:

git switch refactor/tui-performance
git pull --ff-only origin refactor/tui-performance
git rev-parse HEAD
./scripts/validate.sh

If that is green, promote it to main with a fast-forward only. No merge commit should be necessary:

git switch main
git pull --ff-only origin main
git merge --ff-only origin/refactor/tui-performance

Now inspect before publishing:

echo "===== MAIN BEFORE PUSH ====="
git status --short
git log --oneline --decorate -10
git rev-parse HEAD

HEAD should now equal b69fb6b....

Then push main only:

git push origin main

And prove all refs agree:

echo
echo "===== MAIN VERIFICATION ====="
echo "LOCAL MAIN:"
git rev-parse main
echo "TRACKING MAIN:"
git rev-parse origin/main
echo "ACTUAL REMOTE MAIN:"
git ls-remote origin refs/heads/main

All three should resolve to the same SHA, ideally b69fb6b....

At that point, I’d leave refactor/tui-performance alone for now. It serves as a historical milestone pointer. We can delete/archive old branches later once the entire project settles.

Then fetch the continuation work:

git fetch origin refactor/tui-performance-local-continuation-wip

Since it likely does not exist locally on the work Mac yet:

git switch -c refactor/tui-performance-local-continuation-wip \
  --track origin/refactor/tui-performance-local-continuation-wip

Verify:

echo "===== CONTINUATION ====="
git status --short
git rev-parse HEAD
git log --oneline --decorate -10

Expected HEAD:

d2214d1c5451cc61fdd98a5488cacf7d9433b825

Then run the local gate before touching var203:

./scripts/validate.sh

Expected:

504 collected
504 passed
0 skipped

on both current Python and Python 3.8.

For the VAST password, don’t put it in the command line:

read -s -p "VAST password: " VAST_PASSWORD
echo
export VAST_PASSWORD

Then the validation package starts here:

cat scripts/var203_validation/README.md

and the automated probe will be:

python3 scripts/var203_validation/probe_var203.py \
  --vms var203.selab.vastdata.com \
  --user admin \
  > /tmp/opstat-var203-probe.txt 2>&1

One thing I would not do yet is delete any branches. Once the continuation work is proven on var203, we can decide how we want its history to land on main. The 779cd6e WIP checkpoint is in that ancestry, so before the final merge I want to look at whether we preserve it as historical provenance or rebuild/squash that segment into cleaner logical commits. That’s the last potential bowl of spaghetti, and we can deal with it deliberately rather than letting Git serve it al dente.

And yes, we can put this exact promotion + checkout procedure into docs/CLAUDE_HANDOFF.md before the next machine switch so you only have to open one file and copy/paste downward.
