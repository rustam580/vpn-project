# Project State

Last updated: 2026-05-17

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
- WebRTC/DataChannel transport is documented as an R&D track only (`docs/webrtc-transport-research.md`); do not sell or advertise it until a closed beta proves stability and cost. Phase 1 local direct echo PoC exists under `experiments/webrtc-gateway/` and was verified locally on 2026-05-15. For whitelist-resilience, the intended hypothesis is carrier-based WebRTC through already-allowed video/conference services; the direct gateway is only a lab baseline.
- Admin Marzban/DB audit supports guided critical-drift actions via inline buttons; use first on known-safe stale/test findings after deploy validation.
- Admin `/user <telegram_id>` uses a unified customer profile renderer: Telegram identity, primary Marzban profile, all device slots, recent bot payments, related web orders, and drift warnings.
- Admin `/user <order_id|email|web_username>` renders web-order support cards with linked Telegram IDs/devices, Marzban status, direct action hints (`/user`, `/check`, `/sync_audit`), and read-only inline support buttons for payment status check, customer lookup, and drift audit.
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
- Customer support profile: `src/vpnbot/customer_profile.py`.
- Website order support profile: `src/vpnbot/web_order_profile.py`.
- User callback facade: `src/vpnbot/handlers/bot_handlers_callbacks_user.py` (~16 lines).
- User callback domains:
  - `bot_handlers_callbacks_user_quick.py` (~242 lines): quick actions, legal, referral, FAQ, channel, support issue.
  - `bot_handlers_callbacks_user_devices.py` (~144 lines): rename/replace device callbacks.
  - `bot_handlers_callbacks_user_configs.py` (~60 lines): config show/show-all callbacks.
  - `bot_handlers_callbacks_user_payments.py` (~389 lines): buy/select/payment/check/device-add callbacks.
- Website checkout API: `website_api.py` (~650 lines), aiohttp on `127.0.0.1:8011`.
- Subscription gateway: `subscription_gateway.py`, sync HTTP proxy/dedupe/logging service on `127.0.0.1:8010`.
- WebRTC R&D PoC: `experiments/webrtc-gateway/`, Python `aiortc` local browser DataChannel echo gateway. It now has a carrier interface with `direct` as the baseline adapter; WB Stream implementation notes live in `experiments/webrtc-gateway/WBSTREAM_NOTES.md`. On 2026-05-16, WB Stream room probing confirmed guest join + LiveKit token retrieval, but guest `publish_data` is blocked (`can_publish_data=false`) in the tested room. Synthetic video-track publishing works; a one-frame encrypted/authenticated message (`RootVPN WB video bytes OK`) was decoded over WB video; multi-frame one-way delivery works (`1024` bytes, 9 chunks, ~8s); reverse video ACK works (`1024` bytes, 9/9 chunks ACKed, ~13s); sliding-window ACK works (`1024` bytes ~104 B/s, `2048` bytes ~74 B/s); denser `tile2` visual codec works and on a fresh 2026-05-17 WB room outperformed binary for 1024-byte payloads (`tile2` median ~146 B/s vs binary ~113 B/s, both 0 retransmits). Current lab baseline candidate is `tile2,data_repeats=1,window=4,retry=2.5s,fps=8,ack_fps=4`; fresh-room sweeps reached 2048 bytes at median ~242.5 B/s with 0 retransmits and 4096 bytes at median ~253.8 B/s with median 1 retransmit. `stream_protocol.py` provides experimental logical byte-stream framing/reassembly (`stream_id`, offset, fin) over video payloads, tested through `tile2` frames; `wbstream_livekit_frame_window.py --stream-mode` wires that framing into WB field probes. Fresh stream-mode validation succeeded up to 16384-byte payloads (67 stream/video chunks, 0 retransmits, ~1098 B/s). `socks5_proto.py`, `proxy_messages.py`, `local_bridge.py`, and `local_socks_server.py` now provide the local-only skeleton for a future SOCKS-like bridge: no-auth SOCKS5 CONNECT parsing, internal OPEN/DATA/CLOSE/ERROR messages, in-memory fake carrier/egress harness, and a loopback-only SOCKS5 listener that does not dial external targets. Turnable review confirmed the needed architecture direction: explicit carrier/transport/protocol/engine layers, route/user validation before real egress, multiplexed flows, KCP-like reliability, and later multi-peer scaling. Later WB live checks can fail if the manual room expires (`HTTP 403: guests cannot create rooms`), so fresh rooms are required for field measurements. The weakest observed layer is provider/session setup reliability, not media-frame chunk decode after both participants join. The viable WB carrier path is media-frame encoding rather than LiveKit data packets.
- SQLite schema version latest: `4`.
- Local checks as of 2026-05-15:
  - `python scripts/compile_all.py` OK
  - `python -m ruff check .` OK
  - `python -m mypy . --ignore-missing-imports` OK
  - `python -m pytest -q` OK

## Documentation Freshness

- Most reliable docs right now: `docs/assistant-context.md`, `docs/STATE.md`, `docs/open-issues.md`, `docs/infra-state.md`, `docs/website.md`, `docs/webrtc-transport-research.md`.
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
