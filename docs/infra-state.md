# Infrastructure State

Last updated: 2026-05-10

RootVPN is deployed across two VPS hosts. Keep this file current: most deployment mistakes came from confusing the bot/VPN host with the website host.

## DNS Map

| Domain | A record | Role |
|---|---:|---|
| `rootvpn.tech`, `www.rootvpn.tech` | `205.196.81.194` | public website + `/api/*` entrypoint |
| `sub.rootvpn.tech` | `77.110.125.105` | subscription gateway on `:8443` |
| `bot.rootvpn.tech` | `77.110.125.105` | Marzban panel/API proxy |

Verify with:

```bash
dig +short rootvpn.tech A
dig +short www.rootvpn.tech A
dig +short sub.rootvpn.tech A
dig +short bot.rootvpn.tech A
```

## Host A: `bot-host`

- Provider: Aeza
- Region: USA
- Public IPv4: `77.110.125.105`
- Hostname observed: `rootvpn` / `rootvpn.ptr.network`
- OS: Ubuntu 24.04
- SSH: `root@77.110.125.105`
- Project path: `/opt/vpn-bot`

### Services

- `vpn-bot.service` - Telegram bot.
- `vpn-site-api.service` - website checkout/status API on `127.0.0.1:8011`.
- `vpn-sub-gateway.service` - subscription gateway on `127.0.0.1:8010`.
- Marzban - local API/UI on `127.0.0.1:8000`.
- `caddy.service` - reverse proxy for Marzban/sub-gateway and legacy/local endpoints.
- Backup timers: `vpn-bot-backup.timer`, `vpn-bot-restore-check.timer`.

### Key Paths

- Project: `/opt/vpn-bot`
- Env: `/opt/vpn-bot/.env`
- DB: `/opt/vpn-bot/data/bot.sqlite3`
- Marzban compose: `/opt/marzban/docker-compose.yml`
- Marzban data: `/var/lib/marzban`
- Deploy script: `/usr/local/sbin/vpn-ops-deploy`
- Smoke script: `/usr/local/sbin/vpn-ops-smoke`
- Deploy report: `/opt/vpn-bot/deploy/last-deploy.log`

### Network/Ports

- `22/tcp` SSH
- `80/tcp` Caddy / ACME / redirects
- `443/tcp` Xray inbound via Marzban
- `8443/tcp` Caddy for `sub.rootvpn.tech`
- `127.0.0.1:8000` Marzban
- `127.0.0.1:8010` subscription gateway
- `127.0.0.1:8011` website API

### Public Endpoints

- Marzban panel: `https://bot.rootvpn.tech/dashboard/`
- Subscription URLs: `https://sub.rootvpn.tech:8443/sub/...`

## Host B: `site-host`

- Provider: FASTVPS
- Public IPv4: `205.196.81.194`
- Hostname observed: `s28c927b7...`
- OS observed: Ubuntu 24.04
- SSH: `root@205.196.81.194`

### Services

- `caddy.service` - public HTTPS website.
- Nginx was previously installed by FASTPANEL and had to be disabled because it occupied `:443`.
- The static website was observed under `/var/www/rootvpn`.
- During the latest observed setup, `/opt/vpn-bot` did **not** exist on this host.

### Key Paths

- Static site root: `/var/www/rootvpn`
- Caddy config: `/etc/caddy/Caddyfile`
- Source repo clone on this host: not guaranteed. Verify before running `git pull`.

### Expected Caddy Shape

The website host should serve static files from `/var/www/rootvpn` and proxy `/api/*` to the website API. Depending on the latest deployment, `/api/*` can point either to a local API service on `127.0.0.1:8011` or to the bot-host API. Verify the actual Caddyfile before changing it.

Current known static root pattern:

```caddyfile
rootvpn.tech, www.rootvpn.tech {
    root * /var/www/rootvpn
    encode zstd gzip

    handle /api/* {
        # Verify current target before editing:
        # reverse_proxy 127.0.0.1:8011
        # or reverse_proxy http://77.110.125.105
    }

    file_server
}
```

Check actual config with:

```bash
grep -n "root \*" /etc/caddy/Caddyfile
systemctl status caddy --no-pager
```

## Website Deploy Rules

Do **not** assume website changes deploy through `/opt/vpn-bot` on `205.196.81.194`.

1. If a git clone with credentials exists on site-host, pull there and rsync `site/` into `/var/www/rootvpn`.
2. If no git clone/credentials exist, deploy static files from a trusted machine with `scp`/`rsync` into `/var/www/rootvpn`.
3. For backend/API changes, deploy on bot-host (`77.110.125.105`) unless a local site API has been intentionally installed on site-host.

Recommended verification after site deploy:

```bash
curl -sI https://rootvpn.tech/ | grep -i "Last-Modified\|Content-Length\|Server"
curl -s https://rootvpn.tech/api/health
```

As of 2026-05-10, public headers showed a fresh site deploy:

```text
Server: Caddy
Last-Modified: Sun, 10 May 2026 10:08:16 GMT
Content-Length: 29393
```

## Security Baseline

- Root SSH is enabled on both hosts.
- Firewall/UFW policy is not standardized yet.
- Do not expose ops dashboards or log dashboards publicly without auth.
- Any Xray/Marzban log dashboard should stay private behind SSH tunnel, BasicAuth, or VPN.

## Notes

- Website and bot code live in the same GitHub repository, but they may be deployed to different paths on different hosts.
- Subscription URL should always point to `sub.rootvpn.tech:8443` and stay independent from the website host.
- Manual edits in Marzban do not automatically update the bot DB. Use `scripts/audit_marzban_sync.py` to inspect drift.
