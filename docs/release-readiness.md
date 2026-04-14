# Release Readiness

Last updated: 2026-04-15

## Goal
This checklist is for "safe ad scaling": when incoming traffic grows and the bot must keep sales, delivery, and support stable.

## P0 Before Ad Scaling
1. Publish legal pages:
- Public offer
- Privacy policy
- Refund policy
- Auto-renew policy (if used)

2. Link legal pages in user flow:
- Bot `/start` text
- Payment step text
- Channel/website footer

3. Verify contact and support SLA:
- `SUPPORT_USERNAME` is set
- Response target is defined (for example, first reply within 30 minutes)

## P1 Technical Readiness
1. Local pre-push checks:
- `python scripts/compile_all.py`
- `python -m ruff check .`
- `python -m pytest -q`

2. Server deploy checks:
- `git pull --ff-only`
- `systemctl restart vpn-bot`
- `systemctl restart vpn-site-api`
- `systemctl is-active vpn-bot`
- `systemctl is-active vpn-site-api`
- `journalctl -u vpn-bot -n 80 --no-pager`
- `journalctl -u vpn-site-api -n 80 --no-pager`
- `curl -s http://127.0.0.1:8011/api/health`

3. Backups:
- Confirm both timers are active:
  - `vpn-bot-backup.timer`
  - `vpn-bot-restore-check.timer`
- Confirm latest archive exists in `/opt/backups/vpn-bot`

4. Alerts:
- Worker admin alerts enabled:
  - `ADMIN_ALERTS_ENABLED=1`
  - `ADMIN_ALERT_COOLDOWN_SEC=900`
- Renewal reminders enabled:
  - `RENEWAL_ALERTS_ENABLED=1`
  - `RENEWAL_EXPIRED_ALERT_ENABLED=1`

## P1 Sales Funnel Tracking
Track weekly:
1. New users (`/start`)
2. Trial starts
3. Payment created
4. Payment paid
5. Payment applied
6. Renewal reminder sent
7. Expired reminder sent

Use weekly summary in admin report to compare:
- Trial -> Paid conversion
- Paid -> Renew conversion
- Failed payments trend

## P1 Launch Day Runbook
1. Run smoke checks on production.
2. Send one internal test payment (small amount).
3. Validate:
- payment status changes to `paid_applied`
- user receives updated access message (bot flow)
- website order returns subscription link (site flow)
- admin receives payment notification

4. Publish channel post with:
- offer
- key benefit
- clear CTA to bot

5. Start first ad source with limited budget.
6. Watch logs and admin alerts for 2-3 hours.

## P1 Website/Subscription Checks
1. Caddy routes are valid and non-duplicated:
- one `sub.<domain>:8443` block only
- `/api/*` proxied to `127.0.0.1:8011`

2. Site API flow works:
- `GET /api/plans`
- `POST /api/checkout`
- `GET /api/order/{order_id}`

3. Subscription gateway health is OK:
- `https://sub.<domain>:8443/health` returns `200`

4. Telegram bind flow works for web order:
- paid web order has `tg_bind_url`
- `/start webbind_*` binds order to Telegram user

## P2 First 7 Days After Launch
1. Daily check:
- payments by status
- expired users
- support queue size

2. Fix top 3 repeated support questions in bot texts/FAQ.
3. Freeze non-critical features; prioritize reliability and conversion fixes.
