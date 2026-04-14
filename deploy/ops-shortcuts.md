# Ops Shortcuts

## Install scripts once

```bash
sudo install -m 700 /opt/vpn-bot/deploy/vpn-ops-deploy.sh /usr/local/sbin/vpn-ops-deploy
sudo install -m 700 /opt/vpn-bot/deploy/vpn-ops-health.sh /usr/local/sbin/vpn-ops-health
sudo install -m 700 /opt/vpn-bot/deploy/vpn-ops-backup-check.sh /usr/local/sbin/vpn-ops-backup-check
sudo install -m 700 /opt/vpn-bot/deploy/vpn-ops-smoke.sh /usr/local/sbin/vpn-ops-smoke
```

## Daily usage

```bash
sudo vpn-ops-health
sudo vpn-ops-smoke
sudo vpn-ops-deploy
sudo vpn-ops-backup-check
```
