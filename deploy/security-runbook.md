# Security Runbook (Ubuntu 24.04)

Use this on the server as `root` or with `sudo`.

## 0) Immediate actions

1. Rotate secrets that were ever shared in chats/screenshots:
- Telegram `BOT_TOKEN`
- Marzban admin password
- `YOOKASSA_SECRET_KEY`
- `CRYPTOBOT_TOKEN` (if used)

2. Update `/opt/vpn-bot/.env`, then restart:

```bash
cd /opt/vpn-bot
nano .env
systemctl restart vpn-bot
journalctl -u vpn-bot -n 50 --no-pager
```

## 1) OS updates

```bash
apt update
apt -y upgrade
apt -y install ufw fail2ban unattended-upgrades
```

## 2) Firewall (UFW)

If SSH uses default port 22:

```bash
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable
ufw status verbose
```

If SSH port is custom, allow it before `ufw enable`.

## 3) SSH hardening

Create admin user (recommended) and use SSH keys:

```bash
adduser vpnadmin
usermod -aG sudo vpnadmin
mkdir -p /home/vpnadmin/.ssh
chmod 700 /home/vpnadmin/.ssh
nano /home/vpnadmin/.ssh/authorized_keys
chmod 600 /home/vpnadmin/.ssh/authorized_keys
chown -R vpnadmin:vpnadmin /home/vpnadmin/.ssh
```

Harden sshd:

```bash
cp /etc/ssh/sshd_config /etc/ssh/sshd_config.bak.$(date +%F)
nano /etc/ssh/sshd_config
```

Set:

```text
PermitRootLogin no
PasswordAuthentication no
PubkeyAuthentication yes
```

Apply:

```bash
sshd -t && systemctl restart ssh
```

Important: verify new SSH session before closing current one.

## 4) Fail2ban baseline

```bash
cat >/etc/fail2ban/jail.d/sshd.local <<'EOF'
[sshd]
enabled = true
bantime = 1h
findtime = 10m
maxretry = 5
EOF

systemctl enable --now fail2ban
fail2ban-client status
```

## 5) Restrict service exposure

- Keep Marzban API on `127.0.0.1:8000` only.
- Publish only Caddy on `80/443`.
- Prefer dashboard over HTTPS + domain, not plain IP.

Quick checks:

```bash
ss -lntp
caddy validate --config /etc/caddy/Caddyfile && systemctl reload caddy
```

## 6) Backups (daily)

Create backup directory:

```bash
mkdir -p /opt/backups/vpn-bot
```

Backup command:

```bash
tar -czf /opt/backups/vpn-bot/vpn-bot-$(date +%F-%H%M).tar.gz \
  /opt/vpn-bot/.env \
  /opt/vpn-bot/data \
  /opt/marzban
```

Add to cron (example at 04:15 daily):

```bash
crontab -e
```

```text
15 4 * * * tar -czf /opt/backups/vpn-bot/vpn-bot-$(date +\%F-\%H\%M).tar.gz /opt/vpn-bot/.env /opt/vpn-bot/data /opt/marzban
```

## 7) Health checks

```bash
systemctl status vpn-bot --no-pager
systemctl status caddy --no-pager
docker ps
journalctl -u vpn-bot -n 100 --no-pager
```
