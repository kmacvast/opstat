#!/bin/bash
################################################################################
# smb-loadgen.sh
#
# SMB2 protocol feature and traffic exerciser for VAST (Linux CIFS client).
# Default share: //172.200.203.6/opstattest  mounted at /mnt/smbtest
#
# Lights up the signals opstat --smb tracks: READ/WRITE, metadata churn,
# QUERY_DIRECTORY, QUERY_INFO/SET_INFO, byte-range LOCK, CHANGE_NOTIFY.
################################################################################

if [ "$EUID" -ne 0 ]; then
  echo "Please run as root (sudo)."
  exit 1
fi

SMB_SHARE="${SMB_SHARE:-//172.200.203.6/opstattest}"
MOUNT_POINT="${1:-/mnt/smbtest}"
CREDFILE="${SMB_CREDFILE:-/etc/vast-loadgen/smb.cred}"
WORKDIR="$MOUNT_POINT/smb_loadgen"
MOUNTED_BY_US=0

is_cifs_mount() {
  local mp="$1"
  local fstype=""
  if command -v findmnt >/dev/null 2>&1; then
    fstype=$(findmnt -n -o FSTYPE --target "$mp" 2>/dev/null || true)
    [ "$fstype" = "cifs" ]
    return
  fi
  mount | grep -q " on ${mp} type cifs "
}

ensure_mount() {
  mkdir -p "$MOUNT_POINT"
  if is_cifs_mount "$MOUNT_POINT"; then
    return 0
  fi
  if [ ! -r "$CREDFILE" ]; then
    echo "[!] Error: $MOUNT_POINT is not a CIFS mount and $CREDFILE is missing."
    echo "    Create it as root (mode 600) with:"
    echo "      username=<smb-user>"
    echo "      password=<smb-password>"
    exit 1
  fi
  echo "[+] Mounting $SMB_SHARE on $MOUNT_POINT ..."
  mount -t cifs "$SMB_SHARE" "$MOUNT_POINT" \
    -o "credentials=$CREDFILE,vers=3.0,sec=ntlmssp,nounix,actimeo=1" || {
    echo "[!] Error: CIFS mount failed for $SMB_SHARE"
    exit 1
  }
  MOUNTED_BY_US=1
}

ensure_mount

if ! command -v fio >/dev/null 2>&1; then
  echo "[!] Error: fio is not installed. Install it and re-run."
  exit 1
fi

mkdir -p "$WORKDIR/data" "$WORKDIR/metadata" "$WORKDIR/traverse" "$WORKDIR/notify_watch" || {
  echo "[!] Error: cannot create $WORKDIR on the SMB mount."
  exit 1
}

echo "======================================================================"
echo " LAUNCHING VAST SMB2 PROTOCOL FEATURE & TRAFFIC EXERCISER             "
echo "======================================================================"
echo " -> Share:     $SMB_SHARE"
echo " -> Mount:     $MOUNT_POINT"
echo " -> Work dir:  $WORKDIR"
echo " -> Exercising: READ/WRITE, CREATE/CLOSE, QUERY_DIRECTORY, QUERY_INFO,"
echo "                SET_INFO, LOCK, CHANGE_NOTIFY"
echo "----------------------------------------------------------------------"
echo " [+] RUNNING FOREVER. Stop with: systemctl stop smb-loadgen"
echo "======================================================================"

PIDS=""

cleanup() {
  echo -e "\n\n[!] Caught stop signal. Cleaning up background traffic loops..."
  trap - INT TERM
  # shellcheck disable=SC2086
  kill $PIDS 2>/dev/null
  # shellcheck disable=SC2086
  wait $PIDS 2>/dev/null
  rm -rf "$WORKDIR" 2>/dev/null
  if [ "$MOUNTED_BY_US" -eq 1 ]; then
    umount "$MOUNT_POINT" 2>/dev/null || true
  fi
  echo "[+] All stress testing loops terminated cleanly. Exiting."
  exit 0
}
trap cleanup INT TERM

echo "[+] Starting SMB byte-range lock loop..."
touch "$WORKDIR/lock_stress.dat"
while true; do
  (
    flock -x 200
    echo "$(date): Lock Acquired By Process $$" >> "$WORKDIR/lock_stress.dat"
  ) 200>"$WORKDIR/lock_stress.dat"
  sleep 0.1
done >/dev/null 2>&1 &
PIDS="$PIDS $!"

echo "[+] Starting metadata CREATE/CLOSE/REMOVE churn..."
while true; do
  mkdir -p "$WORKDIR/metadata"/dir_{1..5}
  for i in {1..30}; do
    touch "$WORKDIR/metadata/dir_$((1 + RANDOM % 5))/file_$i"
  done
  mv "$WORKDIR/metadata/dir_1/file_1" "$WORKDIR/metadata/dir_1/file_1.renamed" 2>/dev/null
  ls -lR "$WORKDIR/metadata" >/dev/null 2>&1
  rm -rf "$WORKDIR/metadata"
  mkdir -p "$WORKDIR/metadata"
  sleep 0.2
done >/dev/null 2>&1 &
PIDS="$PIDS $!"

echo "[+] Starting QUERY_DIRECTORY traversal loop..."
mkdir -p "$WORKDIR/traverse"/tree_{1..8}
for d in "$WORKDIR/traverse"/tree_*; do
  for i in {1..20}; do
    echo "seed $i" > "$d/file_$i.txt"
  done
done
while true; do
  find "$WORKDIR/traverse" -type f >/dev/null 2>&1
  ls -lR "$WORKDIR/traverse" >/dev/null 2>&1
  sleep 0.3
done >/dev/null 2>&1 &
PIDS="$PIDS $!"

echo "[+] Starting QUERY_INFO / SET_INFO loop..."
touch "$WORKDIR/attr_stress.txt"
while true; do
  chmod 777 "$WORKDIR/attr_stress.txt" 2>/dev/null
  chmod 600 "$WORKDIR/attr_stress.txt" 2>/dev/null
  touch -d "now" "$WORKDIR/attr_stress.txt" 2>/dev/null
  stat "$WORKDIR/attr_stress.txt" >/dev/null 2>&1
  test -r "$WORKDIR/attr_stress.txt"
  test -w "$WORKDIR/attr_stress.txt"
  sleep 0.1
done >/dev/null 2>&1 &
PIDS="$PIDS $!"

echo "[+] Starting CHANGE_NOTIFY watcher + mutator..."
if command -v inotifywait >/dev/null 2>&1; then
  while true; do
    inotifywait -q -r -e modify,create,delete,move "$WORKDIR/notify_watch" >/dev/null 2>&1 || sleep 1
  done >/dev/null 2>&1 &
  PIDS="$PIDS $!"
fi
while true; do
  echo "$(date) notify" >> "$WORKDIR/notify_watch/events.log"
  touch "$WORKDIR/notify_watch/n_$RANDOM"
  rm -f "$WORKDIR/notify_watch/n_"* 2>/dev/null
  sleep 0.2
done >/dev/null 2>&1 &
PIDS="$PIDS $!"

echo "[+] Spawning FIO SMB data-path engine..."
while true; do
  fio --time_based --runtime=60 --ioengine=psync --direct=0 --group_reporting=0 \
    --directory="$WORKDIR/data" \
    --name=smb_randrw --filename=fio_iops.bin --rw=randrw --rwmixread=70 --bs=4k --iodepth=1 --numjobs=4 --size=512m \
    --name=smb_seq --filename=fio_bw.bin --rw=read --bs=1m --iodepth=1 --numjobs=2 --size=1g \
    >/dev/null 2>&1
  sleep 1
done &
PIDS="$PIDS $!"

echo "----------------------------------------------------------------------"
echo " ALL WORKLOADS ACTIVE. Watch with: ./opstat --smb"
echo "----------------------------------------------------------------------"

while true; do
  sleep 1
done
