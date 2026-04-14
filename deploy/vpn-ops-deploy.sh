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

restart_if_exists() {
  local svc="$1"
  if systemctl list-unit-files | grep -q "^${svc}\.service"; then
    echo "==> Restart ${svc}"
    systemctl restart "${svc}"
  else
    echo "WARN: ${svc}.service not found, skipping restart"
  fi
}

cd /opt/vpn-bot

if [[ -d .git ]]; then
  echo "==> Git pull"
  git pull --ff-only || echo "WARN: git pull failed"
else
  echo "WARN: .git not found in /opt/vpn-bot, skipping git pull"
fi

echo "==> Install deps"
if [[ -x .venv/bin/pip ]]; then
  .venv/bin/pip install -r requirements.txt
else
  python3 -m pip install -r requirements.txt
fi

echo "==> Syntax check"
python3 scripts/compile_all.py

echo "==> Restart vpn-bot"
systemctl restart vpn-bot
restart_if_exists vpn-site-api
restart_if_exists vpn-sub-gateway

echo "==> Service status"
systemctl --no-pager --full status vpn-bot | sed -n '1,25p'

echo "==> Recent logs"
journalctl -u vpn-bot -n 60 --no-pager

echo "==> Smoke checks"
if [[ -x /usr/local/sbin/vpn-ops-smoke ]]; then
  /usr/local/sbin/vpn-ops-smoke
elif [[ -x /opt/vpn-bot/deploy/vpn-ops-smoke.sh ]]; then
  /opt/vpn-bot/deploy/vpn-ops-smoke.sh
else
  echo "WARN: smoke script not found/executable, skipping"
fi

echo "OK: deploy done"
