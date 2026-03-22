# Ops Cheatsheet

## Bot
- Status: `sudo systemctl status vpn-bot --no-pager`
- Restart: `sudo systemctl restart vpn-bot`
- Logs: `sudo journalctl -u vpn-bot -n 200 --no-pager`

## Marzban
- Compose: `cd /opt/marzban`
- Up: `sudo docker-compose up -d`
- Down: `sudo docker-compose down`
- Logs: `sudo docker-compose logs --tail=200`

## Caddy
- Status: `sudo systemctl status caddy --no-pager`
- Reload: `sudo systemctl reload caddy`
- Config: `/etc/caddy/Caddyfile`

## Deploy
- Run: `sudo /usr/local/sbin/vpn-ops-deploy`
- Report file: `/opt/vpn-bot/deploy/last-deploy.log`

## Quick Checks
- Open panel: `http://77.110.125.105/dashboard/`
- Local API: `curl -s http://127.0.0.1:8000/api/system`
