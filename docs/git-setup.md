# Git Setup (One-Time) and Daily Workflow

This setup removes manual file copying and most terminal work.

## Goal

1. Edit code locally in IDE.
2. Commit/push changes.
3. Press `🚀 Обновить и проверить` in bot admin panel.
4. Bot pulls latest code from Git and restarts.

## 1) Local machine: create and push repository

Run once in project folder:

```bash
git init
git branch -M main
git add .
git commit -m "init: vpn bot project"
git remote add origin <YOUR_GIT_REMOTE_URL>
git push -u origin main
```

If repo already exists remotely, skip `git init` and `remote add`.

## 2) Server: connect /opt/vpn-bot to same remote

Run once on server:

```bash
cd /opt/vpn-bot
git init
git remote remove origin 2>/dev/null || true
git remote add origin <YOUR_GIT_REMOTE_URL>
git fetch origin
git checkout -B main origin/main
```

Then restore production-only files if needed (`.env`, `data/`).

## 3) Server: install ops shortcut scripts

```bash
sudo install -m 700 /opt/vpn-bot/deploy/vpn-ops-deploy.sh /usr/local/sbin/vpn-ops-deploy
sudo install -m 700 /opt/vpn-bot/deploy/vpn-ops-health.sh /usr/local/sbin/vpn-ops-health
```

## 4) Daily workflow (no manual PowerShell commands)

1. In IDE Source Control, do commit + push.
2. In Telegram admin menu, press `🚀 Обновить и проверить`.
3. Wait for deploy report from bot.

## Notes

- `vpn-ops-deploy` runs `git pull --ff-only`, installs dependencies, checks syntax, restarts `vpn-bot`, and writes report to:
  - `/opt/vpn-bot/deploy/last-deploy.log`
- If deploy log says `WARN: .git not found`, server repo is not connected to Git yet.
