# Сайт RootVPN

Сайт в папке `site/` теперь работает как отдельная точка продаж:
- пользователь оплачивает на сайте,
- сайт проверяет оплату,
- выдает ссылку подписки без Telegram.

## Что уже должно быть

- Бот развернут в `/opt/vpn-bot`
- `sub.rootvpn.tech:8443` уже проксирует на `127.0.0.1:8010` (subscription gateway)
- Домен `rootvpn.tech` направлен на сервер

## 1. Обновить код на сервере

```bash
cd /opt/vpn-bot
git pull --ff-only
```

## 2. Проверить `.env`

Добавьте/проверьте:

```env
WEBSITE_API_HOST=127.0.0.1
WEBSITE_API_PORT=8011
WEBSITE_PUBLIC_URL=https://rootvpn.tech
WEBSITE_SUPPORT_URL=https://t.me/lKRRworkl
WEBSITE_ENABLE_CRYPTO=true
```

## 3. Поднять API сайта как сервис

```bash
cp /opt/vpn-bot/deploy/vpn-site-api.service.example /etc/systemd/system/vpn-site-api.service
systemctl daemon-reload
systemctl enable --now vpn-site-api
systemctl status vpn-site-api --no-pager
```

## 4. Настроить Caddy

Откройте:

```bash
nano /etc/caddy/Caddyfile
```

Пример рабочего конфига (важно: блок `sub.rootvpn.tech:8443` должен быть только один раз):

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

Проверка и применение:

```bash
caddy validate --config /etc/caddy/Caddyfile
systemctl reload caddy
systemctl status caddy --no-pager
```

## 5. Проверка

```bash
curl -I https://rootvpn.tech
curl -I https://www.rootvpn.tech
curl -I https://rootvpn.tech/api/health
curl -I https://sub.rootvpn.tech:8443/health
```

Ожидаемо:
- `rootvpn.tech` -> `200`
- `/api/health` -> JSON `{"ok": true}`
- `sub...:8443/health` -> `200`

## 6. Обновление контента сайта

Если меняете цены/тексты/кнопки в `site/index.html`, `site/styles.css`, `site/site.js`:

```bash
cd /opt/vpn-bot
git pull --ff-only
```

Перезапуск `vpn-bot` для этого не нужен. Достаточно `git pull` (и при изменении Caddy — `systemctl reload caddy`).

## Частые проблемы

1. `ambiguous site definition: sub.rootvpn.tech:8443`
- В `Caddyfile` два одинаковых блока `sub.rootvpn.tech:8443`.
- Оставьте только один.

2. `www.rootvpn.tech` не открывается
- Добавьте DNS-запись `A` для `www` на IP сервера.

3. API не отвечает
- Проверьте сервис:
```bash
systemctl status vpn-site-api --no-pager
journalctl -u vpn-site-api -n 100 --no-pager
```
