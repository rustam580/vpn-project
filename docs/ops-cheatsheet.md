# Ops Cheatsheet

## Bot
- Status: `sudo systemctl status vpn-bot --no-pager`
- Restart: `sudo systemctl restart vpn-bot`
- Logs: `sudo journalctl -u vpn-bot -n 200 --no-pager`

## Subscription Gateway
- Status: `sudo systemctl status vpn-sub-gateway --no-pager`
- Restart: `sudo systemctl restart vpn-sub-gateway`
- Logs: `sudo journalctl -u vpn-sub-gateway -n 200 --no-pager`
- Health: `curl -I https://sub.rootvpn.tech:8443/health`

## Website API
- Status: `sudo systemctl status vpn-site-api --no-pager`
- Restart: `sudo systemctl restart vpn-site-api`
- Logs: `sudo journalctl -u vpn-site-api -n 200 --no-pager`
- Health: `curl -I https://rootvpn.tech/api/health`

## Marzban
- Compose: `cd /opt/marzban`
- Up: `sudo docker-compose up -d`
- Down: `sudo docker-compose down`
- Logs: `sudo docker-compose logs --tail=200`

## Caddy
- Status: `sudo systemctl status caddy --no-pager`
- Reload: `sudo systemctl reload caddy`
- Config: `/etc/caddy/Caddyfile`
- Site health: `curl -I https://rootvpn.tech`

## Deploy
- Run: `sudo /usr/local/sbin/vpn-ops-deploy`
- Report file: `/opt/vpn-bot/deploy/last-deploy.log`

## Quick Checks
- Open panel: `http://77.110.125.105/dashboard/`
- Local API: `curl -s http://127.0.0.1:8000/api/system`
