# Project State

Last updated: 2026-05-15

## Current Production

RootVPN currently uses a two-host production layout.

### Bot/VPN Host

- IP: `77.110.125.105`
- Path: `/opt/vpn-bot`
- Runs: Telegram bot, website API, subscription gateway, Marzban, backups.

### Website Host

- IP: `205.196.81.194`
- Static site path observed: `/var/www/rootvpn`
- Runs: Caddy public website for `rootvpn.tech` / `www.rootvpn.tech`.
- `/api/*` is proxied to the website API. Verify the actual Caddy target before changing infra.

## DNS

- `rootvpn.tech`, `www.rootvpn.tech` -> `205.196.81.194`
- `sub.rootvpn.tech`, `bot.rootvpn.tech` -> `77.110.125.105`

## Core Services On Bot/VPN Host

- `vpn-bot.service` (aiogram polling)
- `vpn-site-api.service` (website checkout/status API)
- `vpn-sub-gateway.service` (subscription dedupe + hit logging)
- Marzban (`127.0.0.1:8000`)
- `caddy.service` (reverse proxy)
- Backup timers: `vpn-bot-backup.timer`, `vpn-bot-restore-check.timer`

## Product Rules (Current)

- One config = one device slot.
- Slot renewal is separate (`plan_device`) and supported.
- Renew all slots is supported (`plan_all`).
- Additional slot purchase is supported (`device_add`).
- Referral program is active.
- Renewal reminders and expired alerts are active.
- Website standalone sales are active (`/api/checkout`, `/api/order/{id}`).
- Paid web orders can be bound to Telegram via `/start webbind_*`.
- Public website API should expose subscription URL(s), not raw direct config links.
- Admin Marzban/DB audit supports guided critical-drift actions via inline buttons; use first on known-safe stale/test findings after deploy validation.
- User reply-keyboard handlers are extracted to `bot_handlers_user_runtime.py` and must be registered before fallback.
- The catch-all fallback handler must stay last among message handlers, otherwise user/admin commands can be swallowed with "Открыл меню."

## Payments

- Providers in production: `card` (YooKassa), `crypto` (CryptoBot)
- Anti-duplicate apply: payment claim lock (`processing` -> `paid_applied`)
- Auto-check workers are active for both providers.
- Admin payment/access report is available through `/payment_issues` and the `💳 Проблемные оплаты` admin button. It is read-only and surfaces stale `processing` payments, old unfinished payments, paid website orders without access, and paid website orders whose Marzban user is missing.

## Important Env Flags

- `DEPLOY_BROADCAST_USERS`: user broadcast after deploy
- `RENEWAL_ALERTS_ENABLED`: renewal reminders
- `RENEWAL_EXPIRED_ALERT_ENABLED`: expired notifications
- `ADMIN_ALERTS_ENABLED`: admin alerts for worker failures
- `ADMIN_ALERT_COOLDOWN_SEC`: anti-spam cooldown for repeated worker alerts
- `AUTO_RENEW_INVOICE_ENABLED`: auto-created invoice before expiry
- `MARZBAN_SYNC_AUDIT_ENABLED`: background Marzban/DB drift audit alerts
- `MARZBAN_SYNC_AUDIT_INTERVAL_SEC`: drift audit interval
- `XRAY_ERROR_LOG_PATH`: local Xray error log path used by `/xray_errors`
- `XRAY_QUALITY_MONITOR_ENABLED`: optional background Xray error spike alerts
- `PLANS_JSON`: multi-tariff config

## Operations

### Bot/API deploy

- Deploy script: `/usr/local/sbin/vpn-ops-deploy`
- Smoke script: `/usr/local/sbin/vpn-ops-smoke`
- Deploy report file: `/opt/vpn-bot/deploy/last-deploy.log`
- DB: `/opt/vpn-bot/data/bot.sqlite3`
- DB migrations: `schema_version` + `db/migrations/*.sql` (auto-applied on start)
- Backups: `/opt/backups/vpn-bot`
- Website API local endpoint: `127.0.0.1:8011`
- Subscription gateway local endpoint: `127.0.0.1:8010`

### Website deploy

- Website static files are served from the site-host, observed path: `/var/www/rootvpn`.
- Do not assume `/opt/vpn-bot` exists on site-host.
- Verify Caddy root before deploying:
  - `grep -n "root \*" /etc/caddy/Caddyfile`

## Current Code Snapshot

- Entrypoint: `bot.py` imports and runs `src.vpnbot.bot_runtime.main()`.
- Router assembly: `src/vpnbot/bot_runtime.py` (~761 lines).
- User callback facade: `src/vpnbot/handlers/bot_handlers_callbacks_user.py` (~16 lines).
- User callback domains:
  - `bot_handlers_callbacks_user_quick.py` (~242 lines): quick actions, legal, referral, FAQ, channel, support issue.
  - `bot_handlers_callbacks_user_devices.py` (~144 lines): rename/replace device callbacks.
  - `bot_handlers_callbacks_user_configs.py` (~60 lines): config show/show-all callbacks.
  - `bot_handlers_callbacks_user_payments.py` (~389 lines): buy/select/payment/check/device-add callbacks.
- Website checkout API: `website_api.py` (~650 lines), aiohttp on `127.0.0.1:8011`.
- Subscription gateway: `subscription_gateway.py`, sync HTTP proxy/dedupe/logging service on `127.0.0.1:8010`.
- SQLite schema version latest: `4`.
- Local checks as of 2026-05-15:
  - `python scripts/compile_all.py` OK
  - `python -m ruff check .` OK
  - `python -m mypy . --ignore-missing-imports` OK
  - `python -m pytest -q` OK

## Documentation Freshness

- Most reliable docs right now: `docs/assistant-context.md`, `docs/STATE.md`, `docs/open-issues.md`, `docs/infra-state.md`, `docs/website.md`.
- Older docs (`README.md`, `docs/product-decisions.md`, `docs/release-readiness.md`, `docs/integrations.md`, `docs/runbook.md`) are useful for background, but may omit current website API, drift-resolution, Xray diagnostics, two-host deploy details, and recent router fixes.

## Context Handoff Protocol (Do Not Skip)

Use this sequence at the start of every new session:

1. Read `docs/assistant-context.md`.
2. Read this file (`docs/STATE.md`).
3. Read `docs/open-issues.md`.
4. Run baseline checks:
   - `python scripts/compile_all.py`
   - `python -m ruff check .`
   - `python -m pytest -q`
5. Only then proceed with feature work.

At the end of each meaningful change:

1. Update date in this file.
2. Update `docs/open-issues.md` (move done items to closed).
3. Add any new risks with priority (`P0/P1/P2`).
