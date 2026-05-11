# Assistant Context Handoff

Last updated: 2026-05-11

This file is the durable context for future Codex/Claude/agent sessions. Read it before making product, infra, payment, or deployment decisions.

## Product North Star

RootVPN should evolve from "a Telegram bot that creates VPN configs" into a simple managed VPN service:

- Website: fast purchase, subscription status, renewal, legal pages, and basic onboarding.
- Telegram bot: primary customer cabinet, support entrypoint, device management, renewals, reminders, and admin ops.
- Database: business source of truth for customers, orders, payments, devices, events, and support-relevant history.
- Marzban: technical execution layer for VPN users, expiry, traffic, and subscription URLs.
- Admin UX: reduce manual SQL/Marzban edits; prefer safe bot/admin commands and audit logs.

The service should feel simple for non-technical users: pay, get one subscription URL, import into client, and renew without understanding Marzban, VLESS, slots, or usernames.

## Current Mental Model

There are several identities that must not be confused:

- Telegram user: `telegram_id`.
- Website order: `web_orders.order_id`.
- Technical Marzban username: examples `tg_123`, `tg_123_d2`, `web_abcd1234`.
- Device slot: currently encoded through device rows and username suffixes.
- Customer: not yet a first-class DB entity, but should become one.

Important product direction:

- A customer can buy from the website without Telegram.
- A paid website order can later be bound to Telegram through `/start webbind_*`.
- Subscription URL is the preferred deliverable.
- Direct raw VLESS links should not be exposed publicly because server IP/config can change while subscription URL can keep working.
- One config means one device slot.
- Device renewal must be separate per slot unless user explicitly renews all slots.
- Family/parent use case is important: one user may manage VPN access for another person.

## Production Topology Notes

Production has moved toward a two-host setup. Be careful: some docs are stale or partially wrong.

Known observed setup from prior operator sessions:

- Bot/VPN host:
  - IP: `77.110.125.105`
  - Project path: `/opt/vpn-bot`
  - Runs: `vpn-bot.service`, `vpn-site-api.service`, `vpn-sub-gateway.service`, Marzban, Caddy, backups.
  - DB: `/opt/vpn-bot/data/bot.sqlite3`
  - Marzban API/UI local: `127.0.0.1:8000`
  - Website API local: `127.0.0.1:8011`
  - Subscription gateway local: `127.0.0.1:8010`
  - Subscription public domain: `sub.rootvpn.tech:8443`

- Website host:
  - IP: `205.196.81.194`
  - Hostname observed: `s28c927b7...`
  - Static site path observed: `/var/www/rootvpn`
  - Caddy root observed: `root * /var/www/rootvpn`
  - No `/opt/vpn-bot` existed on this host during the latest checked session.
  - `/api/*` was intended to proxy to the bot/VPN host website API.

Before deploying website changes, verify the actual host and Caddyfile. Do not assume `/opt/vpn-bot/site` exists on the website host.

Useful deployment distinction:

- Bot/API code changes: deploy on `77.110.125.105` in `/opt/vpn-bot`.
- Static website changes: deploy to `205.196.81.194` into `/var/www/rootvpn`, unless infra was changed after this note.

## Known Documentation Risk

`docs/STATE.md`, `docs/website.md`, and `docs/infra-state.md` may disagree about the website host and static site path.

Before relying on docs for deployment:

1. SSH to the intended host.
2. Check `grep -n "root \\*" /etc/caddy/Caddyfile`.
3. Check whether `/opt/vpn-bot` or `/var/www/rootvpn` exists.
4. Check DNS:
   - `rootvpn.tech` / `www.rootvpn.tech` should point to the website host.
   - `sub.rootvpn.tech` should point to the VPN/bot host.
5. Only then run `git pull`, `rsync`, or service restarts.

## Important Product/UX Decisions

- Telegram should be framed as a personal cabinet, not a mandatory obstacle.
- Website checkout should remain simple and should not require Telegram.
- After website payment, show:
  - subscription URL,
  - button to bind Telegram,
  - clear explanation why binding helps,
  - support link.
- Existing users need a clear "renew existing access" path.
- Users should not see technical terms first. Use "subscription link", "device", "renewal", "support". Keep VLESS/Reality details in FAQ.
- Device names should become human-readable: "My iPhone", "Laptop", "Mom", etc.
- Admin workflows should avoid manual Marzban edits where possible.
- Admin "Find user" supports Telegram ID, website order ID, external payment ID, contact/email, and Marzban username.

## Known Technical Risks

1. Marzban and DB can drift if access is edited manually in Marzban. There is an audit script, but long term this needs a sync worker with admin review.
2. `bot_runtime.py` and some handler modules are still large after refactoring.
3. `mypy` is enabled but some large modules are excluded/loosened. Treat type safety as partial.
4. Marketing/legal claims on the site must match reality: uptime, logs, retention, support availability, refunds.
5. Two-host deployment makes "where do I deploy?" a recurring operational risk.

## Completed In 2026-05-10 Improvement Iteration

- Added durable handoff context in `docs/assistant-context.md` and made `docs/STATE.md` point future agents to it first.
- Removed raw `direct_links` from public website order responses. Public website API should expose subscription URL(s), not raw VLESS config links.
- Added explicit startup/disabled logs for payment, renewal, migration, and daily ops workers so deploy smoke can verify background loops.
- Expanded existing admin "Find user" flow and `/user` command beyond Telegram ID:
  - Telegram ID,
  - website order ID,
  - external payment ID,
  - contact/email,
  - Marzban username.
- Added repository test coverage for web order lookup by order/contact/Marzban username.
- Corrected infra/website docs to reflect the two-host deployment and the observed website static root `/var/www/rootvpn`.
- Added reusable Marzban/DB sync audit module and a background worker. The worker is safe-by-default: it only reports drift to admins and does not mutate Marzban or DB.
- Added manual Telegram admin entrypoints for Marzban/DB drift checks: `/sync_audit` and admin cabinet button `🧭 Marzban/DB аудит`.
- Added lightweight Xray quality diagnostics:
  - admin command `/xray_errors [minutes]`;
  - admin cabinet button `📡 Xray ошибки`;
  - parser for local Xray `error.log`;
  - disabled-by-default worker controlled by `XRAY_QUALITY_MONITOR_ENABLED`.
  Validate `XRAY_ERROR_LOG_PATH` on production before enabling worker alerts.

## Recommended Strategic Improvements

### P0/P1: Reliability and Ops

- Introduce a first-class `Customer` model to connect Telegram users, web orders, contacts, devices, and payments.
- Build admin bot commands for:
  - find customer by Telegram ID, email, order ID, or Marzban username;
  - show payments and devices;
  - bind website order to Telegram;
  - rename device;
  - extend/disable device;
  - resend subscription URL;
  - show recent events.
- Add Marzban/DB sync worker:
  - detect missing users,
  - detect unknown Marzban users,
  - detect expiry/status mismatch,
  - notify admin,
  - offer safe "accept Marzban" / "accept DB" actions for ambiguous cases.
- Expand event log into a real audit trail for admin/manual/system actions.
- Add deploy smoke checks for all services and explicit worker startup logs.
- Add lightweight Xray/Marzban quality monitoring:
  - parse Xray `error.log` locally;
  - summarize top error types/IPs/SNI over 5/15/60 minute windows;
  - alert admins in Telegram on spikes after deploy or config changes;
  - keep any dashboard private behind SSH tunnel/BasicAuth/VPN.
  XrayPulse is a useful reference, but should not be exposed publicly or embedded into the core customer product yet.

### Product and Sales

- Position RootVPN around simplicity:
  - "VPN that just works",
  - payment in minutes,
  - one subscription URL,
  - Telegram support/cabinet.
- Add "family device" positioning:
  - user can pay and manage VPN for parent/relative;
  - device names and reminders stay with the manager.
- Add website subscription status/renewal flow:
  - check by order ID or subscription URL;
  - show active/expired, expiry date, plan, renewal button, Telegram bind button.
- Improve checkout funnel analytics:
  - page view,
  - checkout started,
  - order created,
  - payment paid,
  - access issued,
  - Telegram bound.

### Security and Privacy

- Keep public website API limited to subscription URLs; do not reintroduce raw config links.
- Avoid storing unnecessary customer contacts forever.
- Add retention rules and document them accurately.
- Add admin action audit logs.
- Keep secrets out of repo/docs.
- Keep subscription URL as the stable delivery mechanism.

## Current Repo State Expectations

Expected local checks before deployment or merge:

```bash
python scripts/compile_all.py
python -m ruff check .
python -m mypy . --ignore-missing-imports
python -m pytest -q
```

Expected production smoke after bot/API deploy:

```bash
systemctl is-active vpn-bot
systemctl is-active vpn-site-api
curl -s http://127.0.0.1:8011/api/health
curl -s https://rootvpn.tech/api/health
journalctl -u vpn-bot -n 200 --no-pager | grep -Ei "worker|started|error|exception|traceback"
```

## Do Not Forget

- The user often tests real payments; never assume test data unless confirmed.
- Manual Marzban edits do not automatically update DB.
- Website host and bot host are different operational surfaces.
- Direct raw config links are discouraged; subscription URL is preferred.
- Keep UX understandable for non-technical people.
- Do not rewrite business logic casually around payments/access expiry.
- When in doubt, preserve access and make admin-visible findings rather than silently deleting/disabling.
