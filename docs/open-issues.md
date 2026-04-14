# Open Issues

Last updated: 2026-04-14

## Priority Legend
- P0: blocks sales or legal-safe launch
- P1: important reliability/ops
- P2: optimization/quality

## Active Items

1. P0 - Public legal pages before ad scaling
- Status: pending
- Problem: there is no explicit public package for offer/terms/privacy/refund/auto-renew policy.
- Next action: publish pages and link them in bot and payment flows.
- Owner: product/admin

2. P1 - Deploy smoke checks rollout on production
- Status: in_progress
- Problem: smoke script is implemented in repo, but must be installed/enabled consistently on each production host.
- Next action: install `/usr/local/sbin/vpn-ops-smoke`, verify `vpn-ops-deploy` runs it and captures failures in deploy report.
- Owner: ops/dev

3. P1 - Website -> Telegram bind analytics
- Status: pending
- Problem: bind flow works, but no weekly conversion metric for `web paid_applied -> tg bind`.
- Next action: add explicit conversion counters in admin/ops report.
- Owner: product/dev

4. P1 - Frontend text integrity guard
- Status: pending
- Problem: broken UI text can slip into `site/site.js` and hurt checkout trust.
- Next action: add a test/lint guard for malformed placeholders in user-facing strings.
- Owner: dev

5. P2 - Renewal worker scaling
- Status: pending
- Problem: periodic full scan of users/devices can become expensive under growth.
- Next action: batch processing or next-check schedule index.
- Owner: dev

6. P2 - Continue `bot.py` decomposition
- Status: pending
- Problem: `bot.py` is still large (~2.9k lines), onboarding and safe edits stay expensive.
- Next action: move remaining router/worker wiring and command handlers into dedicated modules.
- Owner: dev

## Recently Closed
- Database migration framework added:
  - `schema_version` table
  - ordered SQL migrations in `db/migrations/`
  - automatic migration runner in `Repo.open()`
- CI now compiles/lints/tests the full Python project scope.
- Local checks (`Makefile`, `scripts/check.sh`, `scripts/check.ps1`) aligned with CI scope.
- Deploy syntax check switched from `bot.py` only to full project compile pass.
- `bot.py` decomposition started and key domains extracted to modules.
- Critical worker alerts delivered to admin chat with cooldown:
  - `ADMIN_ALERTS_ENABLED`
  - `ADMIN_ALERT_COOLDOWN_SEC`
- Fixed: `device_add` now gives new slot its own term (does not incorrectly sync to primary slot expiry).
- Fixed: website API `HTTP 500` on paid order status (bad `extract_subscription_links` call signature).
- Fixed: web order bind flow in Telegram start payload (`webbind_*`) is active.
- Added: `deploy/vpn-ops-smoke.sh` for post-deploy checks (services + local/public health endpoints).
