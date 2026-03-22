#!/usr/bin/env bash
set -Eeuo pipefail

umask 077

BACKUP_DIR="${BACKUP_DIR:-/opt/backups/vpn-bot}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
STAMP="$(date -u +%Y%m%d-%H%M%S)"
HOST="${HOSTNAME:-$(hostname -s)}"

ARCHIVE="${BACKUP_DIR}/vpn-bot-${HOST}-${STAMP}.tar.gz"
CHECKSUM="${ARCHIVE}.sha256"

SOURCES=(
  /opt/vpn-bot/.env
  /opt/vpn-bot/data
  /opt/marzban
)

mkdir -p "${BACKUP_DIR}"

for src in "${SOURCES[@]}"; do
  if [[ ! -e "${src}" ]]; then
    echo "ERROR: source path is missing: ${src}" >&2
    exit 1
  fi
done

tar -czf "${ARCHIVE}" "${SOURCES[@]}"
sha256sum "${ARCHIVE}" > "${CHECKSUM}"

find "${BACKUP_DIR}" -maxdepth 1 -type f -name 'vpn-bot-*.tar.gz' -mtime +"${RETENTION_DAYS}" -delete
find "${BACKUP_DIR}" -maxdepth 1 -type f -name 'vpn-bot-*.tar.gz.sha256' -mtime +"${RETENTION_DAYS}" -delete

echo "OK: backup created: ${ARCHIVE}"
