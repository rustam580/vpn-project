# Project State

Last updated: 2026-05-11 (afternoon)

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

## Payments

- Providers in production: `card` (YooKassa), `crypto` (CryptoBot)
- Anti-duplicate apply: payment claim lock (`processing` -> `paid_applied`)
- Auto-check workers are active for both providers.

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
