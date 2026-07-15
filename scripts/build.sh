#!/usr/bin/env bash
# Build a standalone opstat binary for the current Linux or macOS host.
# Output: releases/opstat-<os>-<arch>
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if ! command -v python3 >/dev/null 2>&1; then
  echo "ERROR: python3 not found" >&2
  exit 1
fi

python3 -m pip install --upgrade pip wheel
python3 -m pip install 'pyinstaller>=6.0'

python3 scripts/build_opstat.py "$@"
echo
echo "Binary ready under releases/. Example:"
echo "  ./releases/opstat-$(uname -s | tr '[:upper:]' '[:lower:]' | sed 's/darwin/macos/')-$(uname -m | sed 's/amd64/x86_64/;s/aarch64/arm64/') --help"
