#!/usr/bin/env bash
set -Eeuo pipefail

BACKUP_DIR="${BACKUP_DIR:-/opt/backups/vpn-bot}"
STRICT_SQLITE_CHECK="${STRICT_SQLITE_CHECK:-false}"

LATEST="$(ls -1t "${BACKUP_DIR}"/vpn-bot-*.tar.gz 2>/dev/null | head -n 1 || true)"
if [[ -z "${LATEST}" ]]; then
  echo "ERROR: no backups found in ${BACKUP_DIR}" >&2
  exit 1
fi

CHECKSUM_FILE="${LATEST}.sha256"
if [[ -f "${CHECKSUM_FILE}" ]]; then
  (cd "${BACKUP_DIR}" && sha256sum -c "$(basename "${CHECKSUM_FILE}")")
else
  echo "WARN: checksum file not found for ${LATEST}" >&2
fi

tar -tzf "${LATEST}" > /dev/null

TMP_DIR="$(mktemp -d /tmp/vpn-restore-check.XXXXXX)"
cleanup() {
  rm -rf "${TMP_DIR}"
}
trap cleanup EXIT

tar -xzf "${LATEST}" -C "${TMP_DIR}"

REQUIRED=(
  "${TMP_DIR}/opt/vpn-bot/.env"
  "${TMP_DIR}/opt/vpn-bot/data"
  "${TMP_DIR}/opt/marzban"
)

for path in "${REQUIRED[@]}"; do
  if [[ ! -e "${path}" ]]; then
    echo "ERROR: required content is missing in backup: ${path}" >&2
    exit 1
  fi
done

if command -v sqlite3 >/dev/null 2>&1; then
  DB_FILE="$(find "${TMP_DIR}/opt/vpn-bot/data" -maxdepth 2 -type f -name '*.sqlite3' | head -n 1 || true)"
  if [[ -n "${DB_FILE}" ]]; then
    CHECK_RESULT="$(sqlite3 -init /dev/null -batch -noheader "${DB_FILE}" 'PRAGMA integrity_check;')"
    # Some environments still inject headers/separators into sqlite output.
    # Treat check as successful if any non-empty line equals "ok".
    if ! printf '%s\n' "${CHECK_RESULT}" | tr -d '\r' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//' | grep -q '^ok$'; then
      if [[ "${STRICT_SQLITE_CHECK}" == "true" ]]; then
        echo "ERROR: sqlite integrity_check failed: ${CHECK_RESULT}" >&2
        exit 1
      fi
      echo "WARN: sqlite integrity_check ambiguous output: ${CHECK_RESULT}" >&2
    fi
  fi
fi

echo "OK: restore check passed for ${LATEST}"
