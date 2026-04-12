# Subscription Gateway

Purpose:
- Deduplicate duplicate links in Marzban subscription response.
- Log real subscription usage (`/sub/...` hits) into SQLite.
- Show migration/adoption in admin stats.

## 1) Enable service

```bash
cd /opt/vpn-bot
cp deploy/vpn-sub-gateway.service.example /etc/systemd/system/vpn-sub-gateway.service
systemctl daemon-reload
systemctl enable --now vpn-sub-gateway
systemctl status vpn-sub-gateway --no-pager
```

## 2) Route Caddy to gateway

In `/etc/caddy/Caddyfile`, route subscription host to `127.0.0.1:8010`:

```caddyfile
sub.rootvpn.tech:8443 {
    reverse_proxy 127.0.0.1:8010
}
```

Reload:

```bash
caddy validate --config /etc/caddy/Caddyfile
systemctl reload caddy
```

## 3) Verify endpoint

```bash
curl -I https://sub.rootvpn.tech:8443/health
curl -L "https://sub.rootvpn.tech:8443/sub/<TOKEN>" | base64 -d -i | nl -ba
```

Expected:
- `/health` returns `200`.
- Duplicate links are removed in decoded output.

## 4) Verify migration/adoption in DB

```bash
sqlite3 /opt/vpn-bot/data/bot.sqlite3 "SELECT COUNT(*) FROM subscription_hits;"
sqlite3 /opt/vpn-bot/data/bot.sqlite3 "SELECT marzban_username, datetime(created_at,'unixepoch') FROM subscription_hits ORDER BY created_at DESC LIMIT 20;"
```

Admin report now includes:
- how many users switched to subscriptions in last 7 days,
- how many are still on old flow.
