#!/usr/bin/env bash
set -Eeuo pipefail

LOG_DIR="/opt/vpn-bot/deploy"
LOG_FILE="${LOG_DIR}/last-deploy.log"
mkdir -p "${LOG_DIR}"
: > "${LOG_FILE}"
exec > >(tee -a "${LOG_FILE}") 2>&1
echo "Deploy started: $(date -u +"%Y-%m-%d %H:%M:%S UTC")"
trap 'code=$?; echo "exit=${code}"; exit ${code}' EXIT

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo $0" >&2
  exit 1
fi

cd /opt/vpn-bot

if [[ -d .git ]]; then
  echo "==> Git pull"
  git pull --ff-only || echo "WARN: git pull failed"
else
  echo "WARN: .git not found in /opt/vpn-bot, skipping git pull"
fi

echo "==> Install deps"
if [[ -x .venv/bin/pip ]]; then
  .venv/bin/pip install -U -r requirements.txt
else
  python3 -m pip install -U -r requirements.txt
fi

echo "==> Syntax check"
python3 -B -m py_compile bot.py

echo "==> Restart vpn-bot"
systemctl restart vpn-bot

echo "==> Service status"
systemctl --no-pager --full status vpn-bot | sed -n '1,25p'

echo "==> Recent logs"
journalctl -u vpn-bot -n 60 --no-pager

echo "OK: deploy done"
