# Integrations

Last updated: 2026-03-08

## 1) Marzban
Used for user provisioning and config links.

Required env:
- `MARZBAN_BASE_URL`
- `MARZBAN_USERNAME`
- `MARZBAN_PASSWORD`
- `MARZBAN_VERIFY_SSL`
- `MARZBAN_PROXY_PROTOCOL`

## 2) Telegram Bot
Framework: `aiogram`.

Required env:
- `BOT_TOKEN`
- `BOT_ADMIN_IDS`

## 3) CryptoBot Payments
Status: active, with auto-check worker.

Required env:
- `CRYPTOBOT_TOKEN`
- `CRYPTOBOT_TESTNET`
- `CRYPTOBOT_FIAT`
- `CRYPTOBOT_ACCEPTED_ASSETS`
- `CRYPTOBOT_EXPIRES_IN`
- `CRYPTOBOT_POLL_SECONDS`

## 4) YooKassa Payments
Status: supported in bot as `card`.

Required env:
- `YOOKASSA_SHOP_ID`
- `YOOKASSA_SECRET_KEY`
- `YOOKASSA_RETURN_URL`

## 5) Altyn Payments
Status: supported in code as optional provider (`altyn`).
API auth: Merchant HMAC headers.

Required env:
- `ALTYN_BASE_URL`
- `ALTYN_API_KEY_ID`
- `ALTYN_API_SECRET`
- `ALTYN_ACCOUNT_NUMBER`
- `ALTYN_BANK_ID`

Current signing model:
- Sign string: `<timestamp>\n<nonce>\n<absolute_url>\n<body>`
- Algo: `HMAC-SHA256`
- Header: `X-Signature: v1=<base64>`

## 6) Referral Program
Status: active.

Required env:
- `REFERRAL_BONUS_DAYS`

Behavior:
- `/start ref_<telegram_id>` binds invited -> referrer
- Bonus is issued after invited user first successful paid activation
- User command: `/ref`
- Admin commands: `/ref_stats`, `/ref_grant`

## 6.1) Device Limits
Status: active.

Required env:
- `DEVICE_LIMIT`
- `DEVICE_ADD_RUB`

Behavior:
- Device 1 is default.
- Additional devices can be created by admin via `/device_add <telegram_id> [slot]`.
- Users can add a device by paying through the bot (button "Добавить устройство").
- Payments extend access for all device slots.

## 7) Robokassa (planned)
Status: not integrated in current bot code.
Notes:
- Merchant setup requires public offer page for activation.
- Add as separate provider adapter when legal and keys are ready.
