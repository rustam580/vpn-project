#!/usr/bin/env bash
set -Eeuo pipefail

ENV_FILE="${ENV_FILE:-/opt/vpn-bot/.env}"
SMOKE_REQUIRE_WEBSITE_API="${SMOKE_REQUIRE_WEBSITE_API:-1}"
SMOKE_REQUIRE_SUB_GATEWAY="${SMOKE_REQUIRE_SUB_GATEWAY:-1}"
SMOKE_CHECK_PUBLIC_ROUTES="${SMOKE_CHECK_PUBLIC_ROUTES:-1}"
SMOKE_INSECURE_TLS="${SMOKE_INSECURE_TLS:-0}"

BOT_LOCAL_HEALTH_URL="${BOT_LOCAL_HEALTH_URL:-http://127.0.0.1:8000/api/system}"
SITE_LOCAL_HEALTH_URL="${SITE_LOCAL_HEALTH_URL:-http://127.0.0.1:8011/api/health}"
SITE_LOCAL_PLANS_URL="${SITE_LOCAL_PLANS_URL:-http://127.0.0.1:8011/api/plans}"
SUB_GATEWAY_LOCAL_HEALTH_URL="${SUB_GATEWAY_LOCAL_HEALTH_URL:-http://127.0.0.1:8010/health}"

_ok() {
  echo "OK: $*"
}

_warn() {
  echo "WARN: $*" >&2
}

_fail() {
  echo "FAIL: $*" >&2
  exit 1
}

_env_get() {
  local key="$1"
  local file="$2"
  [[ -f "$file" ]] || return 0
  local line
  line="$(grep -E "^${key}=" "$file" | tail -n1 || true)"
  [[ -n "$line" ]] || return 0
  local value="${line#*=}"
  value="${value%\"}"
  value="${value#\"}"
  value="${value%\'}"
  value="${value#\'}"
  printf '%s' "$value"
}

_is_true() {
  case "${1:-}" in
    1|true|TRUE|yes|YES|on|ON) return 0 ;;
    *) return 1 ;;
  esac
}

_check_service_active() {
  local svc="$1"
  local required="$2"
  if ! systemctl list-unit-files | grep -q "^${svc}\.service"; then
    if [[ "$required" == "1" ]]; then
      _fail "service ${svc}.service is not installed"
    fi
    _warn "service ${svc}.service is not installed (optional)"
    return 0
  fi
  local state
  state="$(systemctl is-active "$svc" 2>/dev/null || true)"
  if [[ "$state" != "active" ]]; then
    _fail "service ${svc}.service is ${state:-unknown}, expected active"
  fi
  _ok "service ${svc}.service is active"
}

_curl_json_check() {
  local url="$1"
  local must_contain="$2"
  local extra=()
  if _is_true "$SMOKE_INSECURE_TLS"; then
    extra+=("-k")
  fi
  local body
  body="$(curl -fsS --max-time 15 "${extra[@]}" "$url")" || _fail "HTTP check failed: $url"
  if [[ -n "$must_contain" ]] && [[ "$body" != *"$must_contain"* ]]; then
    _fail "unexpected response for $url (missing '$must_contain')"
  fi
  _ok "HTTP check passed: $url"
}

_curl_alive_check() {
  local url="$1"
  local extra=()
  if _is_true "$SMOKE_INSECURE_TLS"; then
    extra+=("-k")
  fi
  local code
  code="$(curl -sS -o /dev/null --max-time 15 -w "%{http_code}" "${extra[@]}" "$url" || true)"
  case "$code" in
    200|201|202|204|301|302|307|308|401|403)
      _ok "HTTP alive check passed: $url (status=$code)"
      ;;
    *)
      _fail "HTTP alive check failed: $url (status=${code:-n/a})"
      ;;
  esac
}

echo "===== Smoke: time ====="
date -u

echo "===== Smoke: services ====="
_check_service_active "vpn-bot" "1"
_check_service_active "caddy" "1"
_check_service_active "vpn-site-api" "$SMOKE_REQUIRE_WEBSITE_API"
_check_service_active "vpn-sub-gateway" "$SMOKE_REQUIRE_SUB_GATEWAY"

echo "===== Smoke: local endpoints ====="
_curl_alive_check "$BOT_LOCAL_HEALTH_URL"
if _is_true "$SMOKE_REQUIRE_WEBSITE_API"; then
  _curl_json_check "$SITE_LOCAL_HEALTH_URL" "\"ok\": true"
  _curl_json_check "$SITE_LOCAL_PLANS_URL" "\"plans\""
fi
if _is_true "$SMOKE_REQUIRE_SUB_GATEWAY"; then
  _curl_json_check "$SUB_GATEWAY_LOCAL_HEALTH_URL" "ok"
fi

if _is_true "$SMOKE_CHECK_PUBLIC_ROUTES"; then
  website_public_url="$(_env_get "WEBSITE_PUBLIC_URL" "$ENV_FILE")"
  subscription_public_base_url="$(_env_get "SUBSCRIPTION_PUBLIC_BASE_URL" "$ENV_FILE")"

  echo "===== Smoke: public endpoints ====="
  if [[ -n "$website_public_url" ]]; then
    _curl_json_check "${website_public_url%/}/api/health" "\"ok\": true"
  else
    _warn "WEBSITE_PUBLIC_URL is empty, skipping public website health check"
  fi

  if [[ -n "$subscription_public_base_url" ]]; then
    _curl_json_check "${subscription_public_base_url%/}/health" "ok"
  else
    _warn "SUBSCRIPTION_PUBLIC_BASE_URL is empty, skipping public subscription health check"
  fi
fi

echo "OK: smoke check completed"
