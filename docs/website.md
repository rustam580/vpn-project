# Сайт RootVPN

В проект добавлен статический сайт в папке `site/`.

## Локальный предпросмотр

```bash
cd /opt/vpn-bot/site
python3 -m http.server 8088
```

Открыть в браузере:
- `http://127.0.0.1:8088`

## Деплой в прод через Caddy

1. Обновить код на сервере:

```bash
cd /opt/vpn-bot
git pull --ff-only
```

2. Сделать бэкап Caddy-конфига:

```bash
cp /etc/caddy/Caddyfile /etc/caddy/Caddyfile.bak-$(date +%F-%H%M%S)
```

3. Добавить блоки в `/etc/caddy/Caddyfile`:

```caddyfile
rootvpn.tech, www.rootvpn.tech {
    root * /opt/vpn-bot/site
    file_server
}

bot.rootvpn.tech {
    reverse_proxy 127.0.0.1:8000
}

sub.rootvpn.tech:8443 {
    reverse_proxy 127.0.0.1:8010
}
```

4. Проверить и применить конфиг:

```bash
caddy validate --config /etc/caddy/Caddyfile
systemctl reload caddy
```

5. Проверить доступность:

```bash
curl -I https://rootvpn.tech
curl -I https://www.rootvpn.tech
curl -I https://sub.rootvpn.tech:8443/health
```

## Важно

- Сайт статический: после `git pull` он обновляется без перезапуска Python-бота.
- Если меняются ссылки на бота/канал/поддержку, обновляйте их в `site/index.html`.
- `sub.rootvpn.tech:8443` оставляем отдельным, это endpoint подписок.
