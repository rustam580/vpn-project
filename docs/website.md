# РЎР°Р№С‚ RootVPN

РЎР°Р№С‚ РІ РїР°РїРєРµ `site/` С‚РµРїРµСЂСЊ СЂР°Р±РѕС‚Р°РµС‚ РєР°Рє РѕС‚РґРµР»СЊРЅР°СЏ С‚РѕС‡РєР° РїСЂРѕРґР°Р¶:
- РїРѕР»СЊР·РѕРІР°С‚РµР»СЊ РѕРїР»Р°С‡РёРІР°РµС‚ РЅР° СЃР°Р№С‚Рµ,
- СЃР°Р№С‚ РїСЂРѕРІРµСЂСЏРµС‚ РѕРїР»Р°С‚Сѓ,
- РІС‹РґР°РµС‚ СЃСЃС‹Р»РєСѓ РїРѕРґРїРёСЃРєРё Р±РµР· Telegram.

## Р§С‚Рѕ СѓР¶Рµ РґРѕР»Р¶РЅРѕ Р±С‹С‚СЊ

- Р‘РѕС‚ СЂР°Р·РІРµСЂРЅСѓС‚ РІ `/opt/vpn-bot`
- `sub.rootvpn.tech:8443` СѓР¶Рµ РїСЂРѕРєСЃРёСЂСѓРµС‚ РЅР° `127.0.0.1:8010` (subscription gateway)
- Р”РѕРјРµРЅ `rootvpn.tech` РЅР°РїСЂР°РІР»РµРЅ РЅР° СЃРµСЂРІРµСЂ

## 1. РћР±РЅРѕРІРёС‚СЊ РєРѕРґ РЅР° СЃРµСЂРІРµСЂРµ

```bash
cd /opt/vpn-bot
git pull --ff-only
```

## 2. РџСЂРѕРІРµСЂРёС‚СЊ `.env`

Р”РѕР±Р°РІСЊС‚Рµ/РїСЂРѕРІРµСЂСЊС‚Рµ:

```env
WEBSITE_API_HOST=127.0.0.1
WEBSITE_API_PORT=8011
WEBSITE_PUBLIC_URL=https://rootvpn.tech
WEBSITE_SUPPORT_URL=https://t.me/RootVPN_support_1
WEBSITE_ENABLE_CRYPTO=true
```

## 3. РџРѕРґРЅСЏС‚СЊ API СЃР°Р№С‚Р° РєР°Рє СЃРµСЂРІРёСЃ

```bash
cp /opt/vpn-bot/deploy/vpn-site-api.service.example /etc/systemd/system/vpn-site-api.service
systemctl daemon-reload
systemctl enable --now vpn-site-api
systemctl status vpn-site-api --no-pager
```

## 4. РќР°СЃС‚СЂРѕРёС‚СЊ Caddy

РћС‚РєСЂРѕР№С‚Рµ:

```bash
nano /etc/caddy/Caddyfile
```

РџСЂРёРјРµСЂ СЂР°Р±РѕС‡РµРіРѕ РєРѕРЅС„РёРіР° (РІР°Р¶РЅРѕ: Р±Р»РѕРє `sub.rootvpn.tech:8443` РґРѕР»Р¶РµРЅ Р±С‹С‚СЊ С‚РѕР»СЊРєРѕ РѕРґРёРЅ СЂР°Р·):

```caddyfile
rootvpn.tech, www.rootvpn.tech {
    root * /opt/vpn-bot/site

    handle /api/* {
        reverse_proxy 127.0.0.1:8011
    }

    file_server
}

bot.rootvpn.tech {
    reverse_proxy 127.0.0.1:8000
}

sub.rootvpn.tech:8443 {
    reverse_proxy 127.0.0.1:8010
}
```

РџСЂРѕРІРµСЂРєР° Рё РїСЂРёРјРµРЅРµРЅРёРµ:

```bash
caddy validate --config /etc/caddy/Caddyfile
systemctl reload caddy
systemctl status caddy --no-pager
```

## 5. РџСЂРѕРІРµСЂРєР°

```bash
curl -I https://rootvpn.tech
curl -I https://www.rootvpn.tech
curl -I https://rootvpn.tech/api/health
curl -I https://sub.rootvpn.tech:8443/health
```

РћР¶РёРґР°РµРјРѕ:
- `rootvpn.tech` -> `200`
- `/api/health` -> JSON `{"ok": true}`
- `sub...:8443/health` -> `200`

## 6. РћР±РЅРѕРІР»РµРЅРёРµ РєРѕРЅС‚РµРЅС‚Р° СЃР°Р№С‚Р°

Р•СЃР»Рё РјРµРЅСЏРµС‚Рµ С†РµРЅС‹/С‚РµРєСЃС‚С‹/РєРЅРѕРїРєРё РІ `site/index.html`, `site/styles.css`, `site/site.js`:

```bash
cd /opt/vpn-bot
git pull --ff-only
```

РџРµСЂРµР·Р°РїСѓСЃРє `vpn-bot` РґР»СЏ СЌС‚РѕРіРѕ РЅРµ РЅСѓР¶РµРЅ. Р”РѕСЃС‚Р°С‚РѕС‡РЅРѕ `git pull` (Рё РїСЂРё РёР·РјРµРЅРµРЅРёРё Caddy вЂ” `systemctl reload caddy`).

## Р§Р°СЃС‚С‹Рµ РїСЂРѕР±Р»РµРјС‹

1. `ambiguous site definition: sub.rootvpn.tech:8443`
- Р’ `Caddyfile` РґРІР° РѕРґРёРЅР°РєРѕРІС‹С… Р±Р»РѕРєР° `sub.rootvpn.tech:8443`.
- РћСЃС‚Р°РІСЊС‚Рµ С‚РѕР»СЊРєРѕ РѕРґРёРЅ.

2. `www.rootvpn.tech` РЅРµ РѕС‚РєСЂС‹РІР°РµС‚СЃСЏ
- Р”РѕР±Р°РІСЊС‚Рµ DNS-Р·Р°РїРёСЃСЊ `A` РґР»СЏ `www` РЅР° IP СЃРµСЂРІРµСЂР°.

3. API РЅРµ РѕС‚РІРµС‡Р°РµС‚
- РџСЂРѕРІРµСЂСЊС‚Рµ СЃРµСЂРІРёСЃ:
```bash
systemctl status vpn-site-api --no-pager
journalctl -u vpn-site-api -n 100 --no-pager
```

