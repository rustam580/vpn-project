#!/usr/bin/env bash
set -Eeuo pipefail

echo "===== Time ====="
date -u

echo "===== Uptime ====="
uptime

echo "===== Memory ====="
free -h

echo "===== Disk ====="
df -h /

echo "===== Services ====="
for svc in vpn-bot caddy fail2ban; do
  state="$(systemctl is-active "$svc" 2>/dev/null || true)"
  echo "$svc: ${state:-unknown}"
done

echo "===== vpn-bot (last logs) ====="
journalctl -u vpn-bot -n 40 --no-pager || true

echo "===== Caddy status ====="
systemctl --no-pager --full status caddy | sed -n '1,20p' || true

echo "===== Fail2ban sshd ====="
fail2ban-client status sshd 2>/dev/null || true

echo "===== Backup timers ====="
systemctl list-timers --all | grep -E 'vpn-bot-backup|vpn-bot-restore-check' || true

echo "===== Listening ports ====="
ss -lntp | grep -E ':22 |:80 |:443 |:8000 ' || true

echo "===== Docker (if installed) ====="
if command -v docker >/dev/null 2>&1; then
  docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}'
fi

echo "OK: health check done"
