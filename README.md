# VPN Bot + Marzban

Р‘РѕС‚ РІС‹РґР°РµС‚ VPN-РїРѕРґРїРёСЃРєРё РёР· Marzban Рё СѓРјРµРµС‚ РїСЂРѕРґР»РµРІР°С‚СЊ РґРѕСЃС‚СѓРї РїРѕСЃР»Рµ РѕРїР»Р°С‚С‹.

## Р§С‚Рѕ РµСЃС‚СЊ РІ Р±РѕС‚Рµ
- `рџ”‘ РџРѕР»СѓС‡РёС‚СЊ РїРѕРґРїРёСЃРєСѓ`
- `рџ’і РљСѓРїРёС‚СЊ РґРѕСЃС‚СѓРї` (CryptoBot auto / РљР°СЂС‚Р° YooKassa)
- `рџ“Љ РњРѕР№ СЃС‚Р°С‚СѓСЃ` (СЃСЂРѕРє, СЃРєРѕР»СЊРєРѕ РѕСЃС‚Р°Р»РѕСЃСЊ, С‚СЂР°С„РёРє)
- `вќ“ FAQ`
- `рџ† РџРѕРґРґРµСЂР¶РєР°`

РђРґРјРёРЅ-РєРѕРјР°РЅРґС‹:
- `/admin_stats`
- `/grant <telegram_id> <days> <gb>`
- `/disable <telegram_id>`
- `/link <telegram_id> <marzban_username>`

## Р‘С‹СЃС‚СЂС‹Р№ Р·Р°РїСѓСЃРє
```bash
cd /opt/vpn-bot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
nano .env
python bot.py
```

## Р—Р°РїСѓСЃРє РєР°Рє СЃРµСЂРІРёСЃ
```bash
cp deploy/vpn-bot.service.example /etc/systemd/system/vpn-bot.service
systemctl daemon-reload
systemctl enable --now vpn-bot
systemctl status vpn-bot
journalctl -u vpn-bot -f
```

## Р’Р°Р¶РЅС‹Рµ РїРµСЂРµРјРµРЅРЅС‹Рµ `.env`
- `TRIAL_DAYS=1`
- `TRIAL_GB=0` (Р±РµР·Р»РёРјРёС‚)
- `PAY_DAYS=30`
- `PAY_GB=0` (Р±РµР·Р»РёРјРёС‚)
- `PAY_RUB=99`

РџРѕРґРґРµСЂР¶РєР°:
- `SUPPORT_USERNAME=RootVPN_support_1`
- `SUPPORT_TEXT=РќР°РїРёС€РёС‚Рµ РЅР°Рј, РїРѕРјРѕР¶РµРј СЃ РїРѕРґРєР»СЋС‡РµРЅРёРµРј Рё РѕРїР»Р°С‚РѕР№.`

РћРїР»Р°С‚Р°:
- CryptoBot: `CRYPTOBOT_TOKEN`
- РђРІС‚РѕРїСЂРѕРІРµСЂРєР° CryptoBot: `CRYPTOBOT_POLL_SECONDS` (РїРѕ СѓРјРѕР»С‡Р°РЅРёСЋ 45 СЃРµРє)
- РљР°СЂС‚Р° YooKassa:
  - `YOOKASSA_SHOP_ID`
  - `YOOKASSA_SECRET_KEY`
  - `YOOKASSA_RETURN_URL`
- Anti-stuck РґР»СЏ `processing`: `PAYMENT_PROCESSING_REQUEUE_SECONDS` (РїРѕ СѓРјРѕР»С‡Р°РЅРёСЋ 600 СЃРµРє)

РњРёРіСЂР°С†РёСЏ РЅР° РїРѕРґРїРёСЃРєРё (РґРѕР¶РёРј):
- `SUB_MIGRATION_REMINDER_ENABLED` вЂ” РІРєР»СЋС‡РёС‚СЊ РјСЏРіРєРёРµ РЅР°РїРѕРјРёРЅР°РЅРёСЏ РЅРµ РїРµСЂРµС€РµРґС€РёРј
- `SUB_MIGRATION_REMINDER_INTERVAL_SEC` вЂ” РїРµСЂРёРѕРґ РїСЂРѕРІРµСЂРєРё
- `SUB_MIGRATION_REMINDER_COOLDOWN_HOURS` вЂ” Р·Р°С‰РёС‚Р° РѕС‚ РїРѕРІС‚РѕСЂРЅС‹С… РЅР°РїРѕРјРёРЅР°РЅРёР№
- `SUBSCRIPTION_HITS_RETENTION_DAYS` вЂ” С…СЂР°РЅРµРЅРёРµ СЃС‚Р°С‚РёСЃС‚РёРєРё РїРµСЂРµС…РѕРґР° РЅР° РїРѕРґРїРёСЃРєРё (РґРЅРµР№)

## РџР°РЅРµР»СЊ Marzban
РћС‚РєСЂС‹РІР°Р№С‚Рµ:
- `http://<server_ip>/dashboard/`

Р•СЃР»Рё Р·Р°Р±С‹Р»Рё Р°РґРјРёРЅ-РґРѕСЃС‚СѓРї:
```bash
cd /opt/marzban
docker compose exec marzban marzban cli admin create --sudo
```

## РћР±РЅРѕРІР»РµРЅРёРµ Р±РѕС‚Р° РЅР° СЃРµСЂРІРµСЂРµ
1. Р—Р°РјРµРЅРёС‚Рµ `bot.py` Рё `.env` РїСЂРё РЅРµРѕР±С…РѕРґРёРјРѕСЃС‚Рё.
2. РџРµСЂРµР·Р°РїСѓСЃС‚РёС‚Рµ:
```bash
systemctl restart vpn-bot
journalctl -u vpn-bot -n 50 --no-pager
```

## РћРїС†РёРѕРЅР°Р»СЊРЅРѕ: Gateway РґР»СЏ РїРѕРґРїРёСЃРѕРє
Р•СЃР»Рё Marzban РѕС‚РґР°РµС‚ РґСѓР±Р»Рё РІ РїРѕРґРїРёСЃРєРµ, РІРєР»СЋС‡РёС‚Рµ Р»РѕРєР°Р»СЊРЅС‹Р№ gateway:
- СЃРµСЂРІРёСЃ: `deploy/vpn-sub-gateway.service.example`
- РѕРїРёСЃР°РЅРёРµ Рё РєРѕРјР°РЅРґС‹: `docs/subscription-gateway.md`

## РћРїС†РёРѕРЅР°Р»СЊРЅРѕ: Website
- РЎС‚Р°С‚РёС‡РµСЃРєРёР№ СЃР°Р№С‚ Р»РµР¶РёС‚ РІ `site/`
- Р”РµРїР»РѕР№ Рё Caddy-РєРѕРЅС„РёРі: `docs/website.md`

