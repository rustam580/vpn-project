# Product Decisions

Last updated: 2026-03-08

## Core Goal
- Provide simple VPN access via Telegram bot + Marzban.
- Minimize manual admin work.
- Keep onboarding short for non-technical users.

## Access Model
- User gets config links from bot.
- Trial is auto-issued on first config request.
- Main paid plan extends existing access.
- Traffic policy for current plans: unlimited (`GB=0` in bot config means no cap).
- Device policy: one config per device, enforced by device slots in bot.

## Current Plan Defaults
- Trial: `TRIAL_DAYS=1`, `TRIAL_GB=0`
- Paid: `PAY_DAYS=30`, `PAY_GB=0`, `PAY_RUB=99`
- Devices: `DEVICE_LIMIT=1`
- Extra device: `DEVICE_ADD_RUB=99`

## Payment Rules
- Supported providers in code:
- `crypto` (CryptoBot)
- `card` (YooKassa)
- `altyn` (Altyn API, optional)
- Access is extended only after successful payment confirmation.
- Duplicate apply is blocked by payment status lock (`processing` / `paid_applied`).

## Referral Rules
- Referral link format: `https://t.me/<bot_username>?start=ref_<telegram_id>`
- Referral bind is stored once per invited user.
- Bonus is issued only after invited user first successful paid access activation.
- Default bonus: `REFERRAL_BONUS_DAYS=3`
- Admin can issue manual referral bonus via `/ref_grant`.

## Admin UX Rules
- Main admin entrypoint: `/admin` and button `Admin cabinet`.
- Mandatory fast actions:
- stats
- ops report
- grant/disable/link
- payment check
- referral top + manual referral bonus

## Change Policy
- No direct production secrets in repo.
- Every behavior change must update:
- `.env.example`
- relevant file in `docs/`
- If payment logic changes, update `docs/integrations.md` and `docs/open-issues.md`.
