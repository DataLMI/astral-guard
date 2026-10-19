#!/usr/bin/env bash
# Astral Guard — uninstaller
set -euo pipefail

RED='\033[0;31m'; GRN='\033[0;32m'; YLW='\033[1;33m'; NC='\033[0m'
ok()   { echo -e "${GRN}✔${NC} $1"; }
fail() { echo -e "${RED}✘${NC} $1"; exit 1; }

if [[ $EUID -ne 0 ]]; then
  exec pkexec bash "${BASH_SOURCE[0]}" "$@"
fi

echo "=== Astral Guard Uninstaller ==="

# Stop + disable
if systemctl is-active astral-guard >/dev/null 2>&1; then
  systemctl stop astral-guard
  ok "Service stopped"
fi
systemctl disable astral-guard 2>/dev/null || true
ok "Service disabled"

# Remove service file
if [[ -f /etc/systemd/system/astral-guard.service ]]; then
  rm -f /etc/systemd/system/astral-guard.service
  ok "Removed service file"
fi

# Remove script directory
if [[ -d /usr/local/lib/astral ]]; then
  rm -rf /usr/local/lib/astral
  ok "Removed /usr/local/lib/astral/"
fi

systemctl daemon-reload
ok "Done — astral-guard fully removed"
