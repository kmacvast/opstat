#!/bin/bash
# Snapshot whether protocol loadgens are running and doing work.
# Usage: ./scripts/systemd/loadgen-status.sh
set -u

UNITS="nfs3-loadgen nfs41-loadgen smb-loadgen block-loadgen s3-loadgen"

cgroup_of() {
  local unit="$1"
  systemctl show -p ControlGroup --value "$unit" 2>/dev/null || true
}

cgroup_procs() {
  local cg="$1"
  local path="/sys/fs/cgroup${cg}/cgroup.procs"
  if [ -r "$path" ]; then
    wc -l < "$path" | tr -d ' '
  else
    echo "0"
  fi
}

cgroup_match() {
  local cg="$1"
  local pat="$2"
  local path="/sys/fs/cgroup${cg}/cgroup.procs"
  local n=0 pid cmd
  [ -r "$path" ] || { echo 0; return; }
  while read -r pid; do
    [ -n "$pid" ] || continue
    cmd=$(ps -o args= -p "$pid" 2>/dev/null || true)
    case "$cmd" in
      *$pat*) n=$((n + 1)) ;;
    esac
  done < "$path"
  echo "$n"
}

echo "=== systemd units ==="
printf "%-16s %-10s %-10s %8s %8s  %s\n" "UNIT" "ACTIVE" "SUB" "PID" "RESTARTS" "SINCE"
for u in $UNITS; do
  active=$(systemctl is-active "$u" 2>/dev/null || echo unknown)
  sub=$(systemctl show -p SubState --value "$u" 2>/dev/null || echo "-")
  pid=$(systemctl show -p MainPID --value "$u" 2>/dev/null || echo 0)
  restarts=$(systemctl show -p NRestarts --value "$u" 2>/dev/null || echo 0)
  since=$(systemctl show -p ActiveEnterTimestamp --value "$u" 2>/dev/null || echo "-")
  printf "%-16s %-10s %-10s %8s %8s  %s\n" "$u" "$active" "$sub" "$pid" "$restarts" "$since"
done

echo
echo "=== workers in each cgroup (healthy: fio/nvme/aws/elbencho present) ==="
printf "%-16s %6s %6s %6s %8s %6s\n" "UNIT" "TASKS" "fio" "nvme" "elbencho" "aws"
for u in $UNITS; do
  cg=$(cgroup_of "$u")
  if [ -z "$cg" ] || [ "$(systemctl is-active "$u" 2>/dev/null)" != "active" ]; then
    printf "%-16s %6s %6s %6s %8s %6s\n" "$u" "-" "-" "-" "-" "-"
    continue
  fi
  printf "%-16s %6s %6s %6s %8s %6s\n" "$u" \
    "$(cgroup_procs "$cg")" \
    "$(cgroup_match "$cg" "fio ")" \
    "$(cgroup_match "$cg" "nvme ")" \
    "$(cgroup_match "$cg" "elbencho")" \
    "$(cgroup_match "$cg" "aws ")"
done

echo
echo "=== mounts / devices / workdirs ==="
for spec in \
  "/mnt/kmacs-root:nfs3" \
  "/mnt/nfs41test:nfs41" \
  "/mnt/smbtest:cifs"; do
  mp="${spec%%:*}"
  label="${spec##*:}"
  if command -v findmnt >/dev/null 2>&1 && findmnt -n -M "$mp" >/dev/null 2>&1; then
    echo "OK  $label  $(findmnt -n -o SOURCE,FSTYPE,TARGET --target "$mp" 2>/dev/null | head -1)"
  elif findmnt -n --target "$mp" >/dev/null 2>&1; then
    echo "OK  $label  $(findmnt -n -o SOURCE,FSTYPE,TARGET --target "$mp" 2>/dev/null | head -1)"
  else
    echo "DOWN $label  $mp not mounted"
  fi
done

if command -v nvme >/dev/null 2>&1; then
  echo
  nvme list 2>/dev/null | head -8 || echo "nvme list failed"
else
  echo "nvme-cli not installed"
fi

echo
echo "=== workdir activity (files should exist and mtimes should be recent) ==="
for d in \
  /mnt/kmacs-root/nfstest/nfs3_loadgen \
  /mnt/nfs41test/nfs41_loadgen \
  /mnt/smbtest/smb_loadgen; do
  if [ -d "$d" ]; then
    echo "-- $d"
    ls -lt "$d" 2>/dev/null | head -6
  else
    echo "-- $d  (missing; unit stopped or still starting)"
  fi
done

echo
echo "=== s3 / elbencho ==="
if [ -f /tmp/s3-loadgen-elbencho.log ]; then
  echo "elbencho log (last 8 lines):"
  tail -8 /tmp/s3-loadgen-elbencho.log
else
  echo "no /tmp/s3-loadgen-elbencho.log yet"
fi

echo
echo "=== recent journal (errors only, last 3 min) ==="
journalctl -u nfs3-loadgen -u nfs41-loadgen -u smb-loadgen -u block-loadgen -u s3-loadgen \
  --since "3 min ago" -p err..alert --no-pager 2>/dev/null | tail -20 \
  || echo "(no error-priority journal lines)"

echo
echo "Healthy snapshot: all five units active, nfs/smb/block show fio (or nvme),"
echo "s3 shows aws and/or elbencho, mounts are up, workdirs have fresh files."
