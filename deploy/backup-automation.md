# Backup Automation Setup

## 1) Copy scripts and units to server

```bash
install -m 700 /opt/vpn-bot/deploy/vpn-bot-backup.sh /usr/local/sbin/vpn-bot-backup.sh
install -m 700 /opt/vpn-bot/deploy/vpn-bot-restore-check.sh /usr/local/sbin/vpn-bot-restore-check.sh

install -m 644 /opt/vpn-bot/deploy/vpn-bot-backup.service /etc/systemd/system/vpn-bot-backup.service
install -m 644 /opt/vpn-bot/deploy/vpn-bot-backup.timer /etc/systemd/system/vpn-bot-backup.timer
install -m 644 /opt/vpn-bot/deploy/vpn-bot-restore-check.service /etc/systemd/system/vpn-bot-restore-check.service
install -m 644 /opt/vpn-bot/deploy/vpn-bot-restore-check.timer /etc/systemd/system/vpn-bot-restore-check.timer
```

## 2) Enable timers

```bash
systemctl daemon-reload
systemctl enable --now vpn-bot-backup.timer
systemctl enable --now vpn-bot-restore-check.timer
systemctl list-timers --all | grep -E 'vpn-bot-backup|vpn-bot-restore-check'
```

## 3) Run once manually (smoke test)

```bash
systemctl start vpn-bot-backup.service
systemctl status vpn-bot-backup.service --no-pager

systemctl start vpn-bot-restore-check.service
systemctl status vpn-bot-restore-check.service --no-pager
```

## 4) Inspect logs

```bash
journalctl -u vpn-bot-backup.service -n 100 --no-pager
journalctl -u vpn-bot-restore-check.service -n 100 --no-pager
```

## Notes

- Backups are stored in `/opt/backups/vpn-bot`.
- Retention is controlled by `RETENTION_DAYS` in `vpn-bot-backup.service`.
- The restore-check verifies:
  - archive readability
  - checksum (if `.sha256` exists)
  - required paths in backup
  - sqlite integrity (if `sqlite3` is installed)
