# Website (RootVPN)

This project now includes a static website in `site/`.

## Local preview

```bash
cd /opt/vpn-bot/site
python3 -m http.server 8088
```

Open:
- `http://127.0.0.1:8088`

## Production deploy with Caddy

1) Ensure files are on server:

```bash
cd /opt/vpn-bot
git pull --ff-only
```

2) Backup Caddy config:

```bash
cp /etc/caddy/Caddyfile /etc/caddy/Caddyfile.bak-$(date +%F-%H%M%S)
```

3) Add site block (example):

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

4) Validate and reload:

```bash
caddy validate --config /etc/caddy/Caddyfile
systemctl reload caddy
```

5) Verify:

```bash
curl -I https://rootvpn.tech
curl -I https://www.rootvpn.tech
curl -I https://sub.rootvpn.tech:8443/health
```

## Notes

- Static site changes are instant after `git pull` (no Python restart required).
- Update Telegram links inside `site/index.html` if your bot/channel/support links change.
- Keep `sub.rootvpn.tech:8443` separate for subscription delivery.
