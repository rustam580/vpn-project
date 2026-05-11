# Website Deployment

Last updated: 2026-05-10

The website in `site/` is the public sales page for RootVPN:

- customer buys on the website;
- website checks payment through `/api/*`;
- customer receives a subscription URL;
- Telegram binding is optional but recommended for renewals/support.

## Current Production Layout

Website production is separate from the bot/VPN host.

| Role | IP | Path / service |
|---|---:|---|
| Website host | `205.196.81.194` | static files in `/var/www/rootvpn`, Caddy public HTTPS |
| Bot/API host | `77.110.125.105` | `/opt/vpn-bot`, `vpn-site-api.service`, Marzban, sub-gateway |

DNS:

- `rootvpn.tech`, `www.rootvpn.tech` -> `205.196.81.194`
- `sub.rootvpn.tech`, `bot.rootvpn.tech` -> `77.110.125.105`

## Check What Is Live

From any machine:

```bash
curl -sI https://rootvpn.tech/ | grep -i "Last-Modified\|Content-Length\|Server"
curl -s https://rootvpn.tech/api/health
```

Expected API response:

```json
{"ok": true, "service": "website_api"}
```

As of 2026-05-10, public website headers showed:

```text
Server: Caddy
Last-Modified: Sun, 10 May 2026 10:08:16 GMT
Content-Length: 29393
```

## Update Static Website Files

### Option A: deploy from local machine via `scp`

Use this if site-host has no GitHub credentials or no repo clone.

PowerShell from repo root:

```powershell
scp -r .\site\* root@205.196.81.194:/var/www/rootvpn/
ssh root@205.196.81.194 "systemctl reload caddy && curl -sI https://rootvpn.tech/ | grep -i 'Last-Modified\|Content-Length'"
```

### Option B: deploy from a repo clone on site-host

Use this only if GitHub auth/deploy key is already configured on `205.196.81.194`.

```bash
ssh root@205.196.81.194
cd /opt/vpn-project   # or actual clone path; verify it exists
git pull --ff-only
rsync -av --delete /opt/vpn-project/site/ /var/www/rootvpn/
systemctl reload caddy
```

If `/opt/vpn-project` or `/opt/vpn-bot` does not exist on site-host, do not run `git pull`; use Option A or configure a deploy key first.

## Update Website API / Checkout Logic

`website_api.py` is currently part of the bot/API deployment. Deploy backend/API changes on `77.110.125.105` unless a local site API has intentionally been installed on site-host.

```bash
ssh root@77.110.125.105
cd /opt/vpn-bot
git pull --ff-only
systemctl restart vpn-site-api
curl -s http://127.0.0.1:8011/api/health
curl -s https://rootvpn.tech/api/health
```

## Caddy Notes

On site-host, verify the static root:

```bash
grep -n "root \*" /etc/caddy/Caddyfile
```

Observed intended static root:

```caddyfile
rootvpn.tech, www.rootvpn.tech {
    root * /var/www/rootvpn
    encode zstd gzip

    handle /api/* {
        # Verify actual target before editing:
        # reverse_proxy 127.0.0.1:8011
        # or reverse_proxy http://77.110.125.105
    }

    file_server
}
```

On bot-host, `sub.rootvpn.tech:8443` should stay on the bot/VPN host and proxy to `127.0.0.1:8010`.

## Common Problems

1. `rootvpn.tech` shows old content
- You updated GitHub or bot-host, but not the site-host static files.
- Deploy `site/*` to `/var/www/rootvpn` on `205.196.81.194`.

2. `git clone` asks for GitHub username/password on site-host
- Password auth is not supported by GitHub.
- Use `scp` from local machine or configure a deploy key/PAT.

3. `:443 bind: address already in use`
- Usually nginx/FASTPANEL occupies the port.
- Check: `ss -lntup | grep ':443'`.
- Stop/disable only if you intentionally use Caddy as the web server.

4. API does not respond
- Check bot/API host:
  ```bash
  systemctl status vpn-site-api --no-pager
  journalctl -u vpn-site-api -n 100 --no-pager
  curl -s http://127.0.0.1:8011/api/health
  ```
- Then check site-host Caddy proxy target.
