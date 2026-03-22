# Open Issues

Last updated: 2026-03-08

## Priority Legend
- P0: blocks sales or access
- P1: important reliability/ops
- P2: optimization/quality

## Active Items

1. P0 - Decide card payment target (YooKassa vs Robokassa)
- Status: in progress
- Current: YooKassa works in bot, Robokassa requested
- Next action: either keep YooKassa, or implement Robokassa adapter and migration checklist
- Owner: product/admin

2. P1 - Add payment provider abstraction layer
- Status: pending
- Problem: payment logic is growing inside one file
- Next action: split provider clients into modules and add shared interface
- Owner: dev

3. P1 - Add alerting for critical failures
- Status: pending
- Problem: failures visible only in logs
- Next action: send admin Telegram alert on:
- bot restart loops
- payment provider errors above threshold
- backup/restore-check failures
- Owner: dev

4. P1 - Add smoke test checklist for every deploy
- Status: pending
- Next action: define minimum test flow:
- `/start`
- `/ref`
- create payment
- `/check ...`
- Owner: ops

5. P2 - Split `bot.py` into modules
- Status: pending
- Problem: single file is hard to maintain
- Next action: extract `repo`, `payments`, `handlers`, `ops`
- Owner: dev

## Recently Closed
- Backup restore-check parsing false failure fixed (sqlite integrity output handling).
- SSH hardening completed (key-only auth, root login disabled).
- Admin panel stats/ops fixed and stabilized.
- Referral system added (link, auto bonus, admin controls).
- Device slots and strict device limit support added in bot.
