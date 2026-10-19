#!/usr/bin/env bash
# Astral Guard — installer
set -euo pipefail
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST_DIR="/usr/local/lib/astral"
SVC_FILE="/etc/systemd/system/astral-guard.service"
RED='\033[0;31m'; GRN='\033[0;32m'; YLW='\033[1;33m'; NC='\033[0m'
ok()   { echo -e "${GRN}✔${NC} $1"; }
warn() { echo -e "${YLW}⚠${NC} $1"; }
fail() { echo -e "${RED}✘${NC} $1"; exit 1; }
if [[ $EUID -ne 0 ]]; then
  warn "Not root — retrying with pkexec..."
  exec pkexec bash "${BASH_SOURCE[0]}" "$@"
fi
echo "=== Astral Guard Installer ==="
command -v python3 >/dev/null 2>&1 || fail "python3 not found"
ok "python3: $(python3 --version 2>&1)"
[[ -e /proc/sysrq-trigger ]] || warn "/proc/sysrq-trigger missing (hard power cut may not work)"
if systemctl is-active astral-guard >/dev/null 2>&1; then
  warn "Stopping existing service..."
  systemctl stop astral-guard
fi
mkdir -p "$DEST_DIR"
cp "$SRC_DIR/astral_guard.py" "$DEST_DIR/astral_guard.py"
chmod 755 "$DEST_DIR/astral_guard.py"
ok "Script: $DEST_DIR/astral_guard.py"
cat > "$SVC_FILE" << 'UNIT'
[Unit]
Description=ASUS Astral GPU Per-Pin Imbalance Watchdog
Documentation=file:///usr/local/lib/astral/astral_guard.py
After=multi-user.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 /usr/local/lib/astral/astral_guard.py \
    --threshold 1.0 \
    --min-current 1.0 \
    --sustain 3 \
    --interval 1.0
Restart=on-failure
RestartSec=5
User=root
LimitNOFILE=256
StandardError=journal
SyslogIdentifier=astral-guard

[Install]
WantedBy=multi-user.target
UNIT
ok "Service: $SVC_FILE"
systemctl daemon-reload
systemctl enable astral-guard
systemctl start astral-guard
ok "Service enabled and started"
sleep 2
STATUS=$(systemctl is-active astral-guard 2>/dev/null || echo "unknown")
if [[ "$STATUS" == "active" ]]; then
  ok "astral-guard is RUNNING"
else
  warn "Status: $STATUS — check 'journalctl -u astral-guard -n 20'"
fi
echo
echo "  Status:    systemctl status astral-guard"
echo "  Logs:      journalctl -u astral-guard -f"
echo "  Test:      python3 ~/astral-guard/astral_guard.py --once"
echo "  Uninstall: ~/astral-guard/uninstall.sh"
