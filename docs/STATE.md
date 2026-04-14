# Project State

Last updated: 2026-04-14

## Current Production
- Server: Aeza VPS (USA)
- Public IP: `77.110.125.105`
- OS: Ubuntu 24.04
- Main path: `/opt/vpn-bot`

## Core Services
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
- `PLANS_JSON`: multi-tariff config

## Operations
- Deploy script: `/usr/local/sbin/vpn-ops-deploy`
- Smoke script: `/usr/local/sbin/vpn-ops-smoke`
- Deploy report file: `/opt/vpn-bot/deploy/last-deploy.log`
- DB: `/opt/vpn-bot/data/bot.sqlite3`
- DB migrations: `schema_version` + `db/migrations/*.sql` (auto-applied on start)
- Backups: `/opt/backups/vpn-bot`
- Website static files: `/opt/vpn-bot/site`
- Website API local endpoint: `127.0.0.1:8011`
- Subscription gateway local endpoint: `127.0.0.1:8010`

## Context Handoff Protocol (Do Not Skip)
Use this sequence at the start of every new session:

1. Read this file (`docs/STATE.md`).
2. Read `docs/open-issues.md`.
3. Run baseline checks:
   - `python scripts/compile_all.py`
   - `python -m ruff check .`
   - `python -m pytest -q`
4. Only then proceed with feature work.

At the end of each meaningful change:

1. Update date in this file.
2. Update `docs/open-issues.md` (move done items to closed).
3. Add any new risks with priority (`P0/P1/P2`).
