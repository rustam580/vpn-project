#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo $0" >&2
  exit 1
fi

echo "==> Run backup now"
systemctl start vpn-bot-backup.service

echo "==> Run restore-check now"
systemctl start vpn-bot-restore-check.service

echo "==> Backup service status"
systemctl --no-pager --full status vpn-bot-backup.service | sed -n '1,20p' || true

echo "==> Restore-check status"
systemctl --no-pager --full status vpn-bot-restore-check.service | sed -n '1,20p' || true

echo "==> Last backup logs"
journalctl -u vpn-bot-backup.service -n 40 --no-pager

echo "==> Last restore-check logs"
journalctl -u vpn-bot-restore-check.service -n 40 --no-pager

echo "==> Latest backup files"
ls -lt /opt/backups/vpn-bot | head -n 10

echo "OK: backup-check done"
