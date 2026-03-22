# Infrastructure State

Last updated: 2026-03-20

## Host
- Provider: Aeza
- Region: USA
- Public IPv4: `77.110.125.105`
- Hostname: `rootvpn` (ptr: `rootvpn.ptr.network`)
- OS: Ubuntu 24.04
- Main admin user: `root`

## Services
- `vpn-bot.service` (Telegram bot)
- `caddy.service` (reverse proxy for Marzban)
- Marzban (Docker compose v1)

## Key Paths
- Bot project: `/opt/vpn-bot`
- Bot env: `/opt/vpn-bot/.env`
- Bot DB: `/opt/vpn-bot/data/bot.sqlite3`
- Marzban compose: `/opt/marzban/docker-compose.yml`
- Marzban data: `/var/lib/marzban`
- Ops scripts: `/usr/local/sbin/vpn-ops-deploy`

## Network/Ports
- `22/tcp` SSH (public)
- `80/tcp` Caddy (public)
- `443/tcp` Xray inbound (public)
- `127.0.0.1:8000` Marzban API/UI (local, via Caddy)

## Security Baseline
- Root access is enabled (default Aéza image)
- Firewall/UFW status: not standardized yet

## Automation
- Ops deploy: `/usr/local/sbin/vpn-ops-deploy`

## Dashboard/Panel Access
- Marzban web: `http://77.110.125.105/dashboard/`

## Notes
- Marzban binds to `127.0.0.1:8000` (no SSL); Caddy proxies to it.
