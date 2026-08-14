git status --short
git branch --show-current
git rev-parse HEAD

git switch -c handoff/work-laptop-wip-20260814

git add -- \
  docs/CLAUDE_HANDOFF.md \
  docs/REFACTOR_HANDOFF.md \
  nfs_v3.py \
  nfs_v41.py \
  nvme_tcp.py \
  s3.py \
  smb.py \
  tests/mock_vms.py \
  tests/test_render_navigation.py \
  tests/test_s3_helpers.py \
  vast_common.py \
  vast_drill.py \
  tests/test_cleanup_lifecycle.py \
  tests/test_nvme_tcp.py \
  tests/test_smb_s3_drill.py \
  tests/test_startup_loading.py

echo
echo "===== STAGED STATE ====="
git status --short

echo
echo "===== STAGED DIFFSTAT ====="
git diff --cached --stat

echo
echo "===== STAGED FILE LIST ====="
git diff --cached --name-status

###########
git commit -m "wip: preserve interrupted opstat refactor state"
###########

echo
echo "===== CHECKPOINT ====="
git log -1 --oneline
git rev-parse HEAD
git status --short

###########
git push -u origin handoff/work-laptop-wip-20260814
###########

echo
echo "===== LOCAL ====="
git rev-parse HEAD

echo
echo "===== TRACKING REF ====="
git rev-parse origin/handoff/work-laptop-wip-20260814

echo
echo "===== ACTUAL REMOTE ====="
git ls-remote origin refs/heads/handoff/work-laptop-wip-20260814