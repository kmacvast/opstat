#!/bin/bash
# Install VAST protocol loadgen systemd units on this host.
# Run as root from a tree that contains scripts/*.sh and scripts/systemd/*.service
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
DEST_SCRIPTS="${DEST_SCRIPTS:-/home/vastdata/kmactools/scripts}"
DEST_UNITS=/etc/systemd/system

if [ "$EUID" -ne 0 ]; then
  echo "Please run as root (sudo)."
  exit 1
fi

mkdir -p "$DEST_SCRIPTS"
for s in nfs3-loadgen.sh nfs41-loadgen.sh smb-loadgen.sh block-loadgen.sh s3-loadgen.sh; do
  install -m 0755 "$ROOT/scripts/$s" "$DEST_SCRIPTS/$s"
done

UNIT_SRC="$ROOT/scripts/systemd"
DEFAULT_SCRIPTS="/home/vastdata/kmactools/scripts"
for u in nfs3-loadgen.service nfs41-loadgen.service smb-loadgen.service block-loadgen.service s3-loadgen.service; do
  sed "s|$DEFAULT_SCRIPTS|$DEST_SCRIPTS|g" "$UNIT_SRC/$u" > "$DEST_UNITS/$u"
  chmod 0644 "$DEST_UNITS/$u"
done

mkdir -p /etc/vast-loadgen
if [ ! -f /etc/vast-loadgen/smb.cred ] && [ -r /home/vastdata/.vastcreds ]; then
  python3 - <<'PY'
from pathlib import Path
creds = {}
for line in Path("/home/vastdata/.vastcreds").read_text().splitlines():
    if "=" in line:
        k, v = line.split("=", 1)
        creds[k.strip()] = v.strip()
Path("/etc/vast-loadgen/smb.cred").write_text(
    f"username={creds.get('username','')}\npassword={creds.get('password','')}\n"
)
PY
  chmod 600 /etc/vast-loadgen/smb.cred
  echo "Wrote /etc/vast-loadgen/smb.cred from lab vastcreds (mode 600)."
fi

systemctl daemon-reload
echo "Installed units:"
systemctl list-unit-files 'nfs3-loadgen.service' 'nfs41-loadgen.service' 'smb-loadgen.service' 'block-loadgen.service' 's3-loadgen.service'
echo
echo "Enable and start, for example:"
echo "  systemctl enable --now nfs41-loadgen smb-loadgen block-loadgen s3-loadgen"
echo "  systemctl enable --now nfs3-loadgen   # replaces a transient systemd-run unit"
