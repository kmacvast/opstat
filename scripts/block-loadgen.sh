#!/bin/bash
################################################################################
# block-loadgen.sh
#
# NVMe-oTCP protocol and block traffic exerciser for VAST.
# Connects discovery targets, mounts blockhead volumes when present, and
# injects write-zeroes, compare, identify, trim, fabric discovery, and FIO.
################################################################################

if [ "$EUID" -ne 0 ]; then
  echo "Please run as root (sudo)."
  exit 1
fi

DISCOVERY_CONF="${NVME_DISCOVERY_CONF:-/etc/nvme/discovery.conf}"
DISCOVERY_PORT="${NVME_DISCOVERY_PORT:-4420}"
MNT1="${BLOCK_MNT1:-/mnt/blockhead1}"
MNT2="${BLOCK_MNT2:-/mnt/blockhead2}"

load_transport() {
  if lsmod | grep -q '^nvme_tcp'; then
    return 0
  fi
  if ! modprobe nvme_tcp 2>/dev/null; then
    echo "[!] nvme_tcp kernel module is not loaded."
    echo "    Install linux-modules-extra-$(uname -r) and re-run."
    return 1
  fi
}

connect_fabrics() {
  local line ip
  if [ ! -f "$DISCOVERY_CONF" ]; then
    echo "[!] No $DISCOVERY_CONF ; skipping fabric connect."
    return 0
  fi
  while read -r line; do
    [ -z "$line" ] && continue
    echo "$line" | grep -q '^-t tcp' || continue
    ip=$(echo "$line" | awk '{for (i=1;i<=NF;i++) if ($i=="-a") print $(i+1)}')
    [ -z "$ip" ] && continue
    echo "[+] NVMe discover/connect $ip:$DISCOVERY_PORT"
    nvme discover -t tcp -a "$ip" -s "$DISCOVERY_PORT" >/dev/null 2>&1 || true
    nvme connect-all -t tcp -a "$ip" -s "$DISCOVERY_PORT" >/dev/null 2>&1 || true
  done < "$DISCOVERY_CONF"
}

wait_for_namespaces() {
  local i
  for i in {1..20}; do
    if ls /dev/nvme*n* >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  return 1
}

first_ns() {
  ls -1 /dev/nvme*n* 2>/dev/null | head -1
}

# Raw NVMe admin/data commands must not target a mounted namespace.
raw_ns() {
  local d
  for d in /dev/nvme*n*; do
    [ -e "$d" ] || continue
    if ! findmnt -n -S "$d" >/dev/null 2>&1; then
      echo "$d"
      return 0
    fi
  done
  return 1
}

if ! command -v nvme >/dev/null 2>&1; then
  echo "[!] Error: nvme-cli is not installed."
  exit 1
fi
if ! command -v fio >/dev/null 2>&1; then
  echo "[!] Error: fio is not installed."
  exit 1
fi

load_transport || exit 1
connect_fabrics

if ! wait_for_namespaces; then
  echo "[!] Error: no NVMe namespaces appeared after fabric connect."
  echo "    Check discovery.conf and that the cluster is exporting volumes to this host."
  exit 1
fi

# Mount fstab blockhead entries if the UUIDs are now visible.
# Use findmnt -M so a bare directory on rootfs is not treated as a mount.
mount "$MNT1" 2>/dev/null || true
mount "$MNT2" 2>/dev/null || true
mount -a 2>/dev/null || true

is_usable_mp() {
  local mp="$1"
  findmnt -n -M "$mp" >/dev/null 2>&1 || return 1
  [ -d "$mp" ] || return 1
  touch "$mp/.loadgen_probe" 2>/dev/null || return 1
  rm -f "$mp/.loadgen_probe" 2>/dev/null
  return 0
}

TARGET_DEV="$(raw_ns || first_ns)"
FIO_DIRS=""
for mp in "$MNT1" "$MNT2"; do
  if is_usable_mp "$mp"; then
    if [ -z "$FIO_DIRS" ]; then
      FIO_DIRS="$mp"
    else
      FIO_DIRS="$FIO_DIRS:$mp"
    fi
  elif findmnt -n -M "$mp" >/dev/null 2>&1; then
    echo "[!] $mp is mounted but not usable (I/O error). Skipping filesystem FIO there."
  fi
done

echo "======================================================================"
echo " LAUNCHING VAST NVMe/TCP BLOCK PROTOCOL & TRAFFIC EXERCISER           "
echo "======================================================================"
echo " -> Target device: $TARGET_DEV"
echo " -> FIO dirs:      ${FIO_DIRS:-<none; using raw device>}"
echo " -> Fabric:        $DISCOVERY_CONF"
echo "----------------------------------------------------------------------"
echo " [+] RUNNING FOREVER. Stop with: systemctl stop block-loadgen"
echo "======================================================================"

PIDS=""
ZERO_TMP="/tmp/block-loadgen-4k.bin"

cleanup() {
  echo -e "\n\n[!] Caught stop signal. Cleaning up background traffic loops..."
  trap - INT TERM
  # shellcheck disable=SC2086
  kill $PIDS 2>/dev/null
  # shellcheck disable=SC2086
  wait $PIDS 2>/dev/null
  rm -f "$ZERO_TMP"
  echo "[+] All stress testing loops terminated cleanly. Exiting."
  exit 0
}
trap cleanup INT TERM

dd if=/dev/zero of="$ZERO_TMP" bs=4k count=1 status=none 2>/dev/null

echo "[+] Starting NVMe write-zeroes loop..."
while true; do
  nvme write-zeroes "$TARGET_DEV" --start-block=0 --block-count=500 >/dev/null 2>&1 || sleep 2
done &
PIDS="$PIDS $!"

echo "[+] Starting NVMe compare loop..."
while true; do
  nvme compare "$TARGET_DEV" --start-block=0 --block-count=7 --data="$ZERO_TMP" >/dev/null 2>&1 || sleep 2
done &
PIDS="$PIDS $!"

echo "[+] Starting NVMe-oF discovery loop..."
while true; do
  while read -r line; do
    echo "$line" | grep -q '^-t tcp' || continue
    ip=$(echo "$line" | awk '{for (i=1;i<=NF;i++) if ($i=="-a") print $(i+1)}')
    [ -n "$ip" ] || continue
    nvme discover -t tcp -a "$ip" -s "$DISCOVERY_PORT" >/dev/null 2>&1 || true
  done < "$DISCOVERY_CONF"
  sleep 2
done &
PIDS="$PIDS $!"

echo "[+] Starting NVMe identify-namespace loop..."
while true; do
  nvme id-ns "$TARGET_DEV" >/dev/null 2>&1 || sleep 2
done &
PIDS="$PIDS $!"

echo "[+] Starting TRIM/UNMAP loop..."
while true; do
  trimmed=0
  for mp in "$MNT1" "$MNT2"; do
    if is_usable_mp "$mp"; then
      fstrim -v "$mp" >/dev/null 2>&1 && trimmed=1
    fi
  done
  if [ "$trimmed" -eq 0 ]; then
    nvme dsm "$TARGET_DEV" --ad -s 0 -b 1024 >/dev/null 2>&1 || true
  fi
  sleep 2
done &
PIDS="$PIDS $!"

echo "[+] Spawning FIO block engine..."
IOENGINE="libaio"
if fio --enghelp 2>/dev/null | grep -q '^[[:space:]]*io_uring'; then
  IOENGINE="io_uring"
fi

while true; do
  if [ -n "$FIO_DIRS" ]; then
    fio --time_based --runtime=60 --ioengine="$IOENGINE" --direct=1 --group_reporting=0 \
      --directory="$FIO_DIRS" \
      --name=small_random_iops --filename=loadgen_iops.bin --rw=randrw --rwmixread=70 --bs=4k --iodepth=64 --numjobs=4 --size=4g \
      --name=large_sequential_bandwidth --filename=loadgen_bw.bin --rw=read --bs=1m --iodepth=16 --numjobs=2 --size=10g \
      --name=database_flushes --filename=loadgen_db.bin --rw=randwrite --bs=8k --iodepth=16 --numjobs=2 --size=4g --fdatasync=10 \
      --name=space_reclaim_trim --filename=loadgen_trim.bin --rw=randtrim --bs=64k --iodepth=8 --numjobs=1 --size=4g \
      >/dev/null 2>&1
  elif [ -n "$TARGET_DEV" ]; then
    fio --time_based --runtime=60 --ioengine="$IOENGINE" --direct=1 --group_reporting=0 \
      --filename="$TARGET_DEV" \
      --name=raw_randrw --rw=randrw --rwmixread=70 --bs=4k --iodepth=32 --numjobs=4 \
      --name=raw_seq --rw=read --bs=1m --iodepth=16 --numjobs=2 \
      >/dev/null 2>&1
  else
    sleep 5
  fi
  sleep 1
done &
PIDS="$PIDS $!"

echo "----------------------------------------------------------------------"
echo " ALL WORKLOADS ACTIVE. Watch with: ./opstat --block --nvme-over-tcp"
echo "----------------------------------------------------------------------"

while true; do
  sleep 1
done
