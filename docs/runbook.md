# Runbook

Last updated: 2026-03-22

## 1) Connect to Server
Use SSH key auth (password auth is disabled).

```bash
ssh -i ~/.ssh/id_ed25519 root@77.110.125.105
```

## 2) Standard Deploy (bot code/env change)
```bash
cd /opt/vpn-bot
python3 -B -m py_compile bot.py
sudo systemctl restart vpn-bot
sudo systemctl status vpn-bot --no-pager
sudo journalctl -u vpn-bot -n 80 --no-pager
```

Git-based flow (recommended): see `docs/git-setup.md`.

## 3) Fast Ops Shortcuts
```bash
sudo vpn-ops-health
sudo vpn-ops-deploy
sudo vpn-ops-backup-check
```

## 4) Backup/Restore Check
Automatic timers:
- `vpn-bot-backup.timer` (daily)
- `vpn-bot-restore-check.timer` (daily)

Manual:
```bash
sudo systemctl start vpn-bot-backup.service
sudo systemctl start vpn-bot-restore-check.service
sudo journalctl -u vpn-bot-backup.service -n 40 --no-pager
sudo journalctl -u vpn-bot-restore-check.service -n 40 --no-pager
```

## 5) Quick Incident Flow
If bot is not responding:
1. Check service status/logs.
2. Check Marzban API local endpoint.
3. Check outbound internet from server.
4. Restart bot service once.

```bash
sudo systemctl status vpn-bot --no-pager
sudo journalctl -u vpn-bot -n 120 --no-pager
curl -I http://127.0.0.1:8000/dashboard/
```

## 6) Payment Support Commands
User fallback:
- `/check <crypto|altyn|card> <payment_id>`

Admin:
- Use admin panel payment check shortcut
- Or same `/check ...` command

## 7) Rollback Plan
If a release breaks production:
1. Restore previous `bot.py` from backup/source.
2. Restore previous `.env` if changed.
3. Restart `vpn-bot`.
4. Verify with `/start`, `/buy`, `/check`.

## 8) Do Not Do
- Do not store real tokens/keys in repo.
- Do not disable backup timers.
- Do not run destructive cleanup without explicit backup.
