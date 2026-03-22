# Project State

Last updated: 2026-03-20

## Current Production
- Server: Aeza VPS (USA)
- Public IP: `77.110.125.105`
- OS: Ubuntu 24.04
- Admin user: `root`

## Core Services
- Marzban (Docker Compose v1) in `/opt/marzban`
- Bot (systemd): `vpn-bot.service` in `/opt/vpn-bot`
- Reverse proxy: `caddy.service`

## Access
- Marzban panel: `http://77.110.125.105/dashboard/`
- Marzban API: `http://127.0.0.1:8000` (local only; proxied by Caddy)

## Data Paths
- Bot env: `/opt/vpn-bot/.env`
- Bot DB: `/opt/vpn-bot/data/bot.sqlite3`
- Marzban data: `/var/lib/marzban`

## Key Behavior
- One device = one separate config (separate marzban username per device).
- Users get direct config links (not subscriptions).
- Button-based UX (no command typing required).

## Important Flags
- `DEPLOY_BROADCAST_USERS`:
  - `1` = auto-send " нопки обновлены" after deploy
  - `0` = no auto user broadcast

## Broadcast
- `/broadcast` supports format and buttons:
  - Format: Text / Markdown / HTML (toggle in preview)
  - Buttons: attach main keyboard on/off

## Ops
- Deploy script: `/usr/local/sbin/vpn-ops-deploy`
- Health checks via admin Ops report.
