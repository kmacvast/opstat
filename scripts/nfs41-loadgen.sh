#!/bin/bash
################################################################################
# nfs41-loadgen.sh
#
# NFSv4.1 protocol feature and traffic exerciser for VAST.
# Default target: /mnt/nfs41test
#
# Compounding, OPEN/CLOSE state, byte-range locks, GETATTR/SETATTR, parallel I/O.
################################################################################

if [ "$EUID" -ne 0 ]; then
  echo "Please run as root (sudo)."
  exit 1
fi

MOUNT_POINT="${1:-/mnt/nfs41test}"
WORKDIR="$MOUNT_POINT/nfs41_loadgen"

is_nfs41_mount() {
  local mp="$1"
  local fstype="" opts=""

  if command -v findmnt >/dev/null 2>&1; then
    fstype=$(findmnt -n -o FSTYPE --target "$mp" 2>/dev/null || true)
    opts=$(findmnt -n -o OPTIONS --target "$mp" 2>/dev/null || true)
    [ -z "$fstype" ] && return 1
    case "$fstype" in
      nfs4|nfs4.*) return 0 ;;
      nfs)
        echo "$opts" | grep -qE '(^|,)(vers|nfsvers)=4(\.1)?(,|$)' && return 0
        echo "$opts" | grep -qE '(^|,)(vers|nfsvers)=4' && return 0
        return 1
        ;;
      *) return 1 ;;
    esac
  fi

  mount | grep " on ${mp} type nfs4" >/dev/null 2>&1
}

if [ ! -d "$MOUNT_POINT" ]; then
  echo "[!] Error: $MOUNT_POINT does not exist."
  exit 1
fi

if ! is_nfs41_mount "$MOUNT_POINT"; then
  echo "[!] Error: $MOUNT_POINT is not active or is not mounted as NFSv4/v4.1."
  echo "    findmnt: $(findmnt -n --target "$MOUNT_POINT" 2>/dev/null || mount | grep " $MOUNT_POINT " || echo 'not found')"
  exit 1
fi

if ! command -v fio >/dev/null 2>&1; then
  echo "[!] Error: fio is not installed. Install it and re-run."
  exit 1
fi

IOENGINE="libaio"
if fio --enghelp 2>/dev/null | grep -q '^[[:space:]]*io_uring'; then
  IOENGINE="io_uring"
fi

mkdir -p "$WORKDIR" || {
  echo "[!] Error: cannot create $WORKDIR on the NFSv4.1 mount."
  exit 1
}

echo "======================================================================"
echo " LAUNCHING VAST NFSv4.1 PROTOCOL FEATURE & TRAFFIC EXERCISER          "
echo "======================================================================"
echo " -> Target Mount: $MOUNT_POINT"
echo " -> Work dir:     $WORKDIR"
echo " -> fio engine:   $IOENGINE"
echo " -> Exercising:   Compounding, OPEN/CLOSE state, POSIX byte-locks,"
echo "                  GETATTR/SETATTR, and parallel I/O"
echo "----------------------------------------------------------------------"
echo " [+] RUNNING FOREVER. Press [Ctrl + C] or: systemctl stop nfs41-loadgen"
echo "======================================================================"

PID_LOCK=""
PID_META=""
PID_ATTR=""
PID_FIO=""

cleanup() {
  echo -e "\n\n[!] Caught stop signal. Cleaning up background traffic loops..."
  trap - INT TERM
  kill $PID_FIO $PID_META $PID_ATTR $PID_LOCK 2>/dev/null
  wait $PID_FIO $PID_META $PID_ATTR $PID_LOCK 2>/dev/null
  rm -rf "$WORKDIR" 2>/dev/null
  echo "[+] All stress testing loops terminated cleanly. Exiting."
  exit 0
}
trap cleanup INT TERM

echo "[+] Starting NFSv4 byte-range locking loop..."
touch "$WORKDIR/lock_stress.dat"
while true; do
  (
    flock -x 200
    echo "$(date): Lock Acquired By Process $$" >> "$WORKDIR/lock_stress.dat"
  ) 200>"$WORKDIR/lock_stress.dat"
  sleep 0.1
done >/dev/null 2>&1 &
PID_LOCK=$!

echo "[+] Starting metadata & compounding stress (OPEN/CLOSE/LOOKUP/REMOVE)..."
while true; do
  mkdir -p "$WORKDIR/meta_stress"/dir_{1..5}
  for i in {1..40}; do
    touch "$WORKDIR/meta_stress/dir_$((1 + RANDOM % 5))/file_$i"
  done
  ls -lR "$WORKDIR/meta_stress" >/dev/null 2>&1
  rm -rf "$WORKDIR/meta_stress"
  sleep 0.2
done >/dev/null 2>&1 &
PID_META=$!

echo "[+] Starting NFSv4 attribute loop (GETATTR/SETATTR)..."
touch "$WORKDIR/attr_stress.txt"
while true; do
  chmod 777 "$WORKDIR/attr_stress.txt"
  chown nobody:nogroup "$WORKDIR/attr_stress.txt" 2>/dev/null \
    || chown nobody:nobody "$WORKDIR/attr_stress.txt" 2>/dev/null
  chmod 600 "$WORKDIR/attr_stress.txt"
  stat "$WORKDIR/attr_stress.txt" >/dev/null 2>&1
  sleep 0.1
done >/dev/null 2>&1 &
PID_ATTR=$!

echo "[+] Spawning core FIO high-concurrency engine..."
while true; do
  fio --time_based --runtime=60 --ioengine="$IOENGINE" --direct=1 --group_reporting=0 \
    --directory="$WORKDIR" \
    --name=nfs41_posix_locks --filename=fio_locks.bin --rw=randrw --bs=4k --iodepth=16 --numjobs=4 --size=1g --lockfile=exclusive \
    --name=nfs41_heavy_iops --filename=fio_iops.bin --rw=randrw --rwmixread=70 --bs=4k --iodepth=64 --numjobs=4 --size=2g \
    --name=nfs41_seq_bw --filename=fio_bw.bin --rw=read --bs=1m --iodepth=16 --numjobs=2 --size=4g \
    >/dev/null 2>&1
  sleep 1
done &
PID_FIO=$!

echo "----------------------------------------------------------------------"
echo " ALL WORKLOADS ACTIVE. Terminal output quieted to protect dashboard lookups."
echo " Watch with: ./opstat --nfs --version=4.1"
echo "----------------------------------------------------------------------"

while true; do
  sleep 1
done
