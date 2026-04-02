# Open Issues

Last updated: 2026-04-02

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

2. P1 - Critical alerting to admin chat
- Status: pending
- Problem: failures are mostly visible only in logs.
- Next action: push alerts for repeated payment worker failures, backup/restore failures, restart loops.
- Owner: dev

3. P1 - Deploy smoke checks
- Status: pending
- Problem: deploy verifies syntax/restart, but no functional smoke path.
- Next action: add post-deploy smoke checklist and lightweight automated probe.
- Owner: ops/dev

4. P1 - Marketing analytics funnel
- Status: pending
- Problem: no end-to-end attribution from source -> trial -> payment.
- Next action: add source tags + conversion events + weekly funnel summary.
- Owner: product/dev

5. P2 - Renewal worker scaling
- Status: pending
- Problem: periodic full scan of users/devices can become expensive under growth.
- Next action: batch processing or next-check schedule index.
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
