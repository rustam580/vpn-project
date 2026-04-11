# Subscription Migration Runbook

Last updated: 2026-04-11

## Goal
Migrate from direct config links to subscription links without breaking existing users.

## 1) Prepare domain
1. Create DNS `A` record for a subdomain, for example: `sub.yourdomain.tld` -> server IPv4.
2. Wait for propagation:
```bash
dig +short sub.yourdomain.tld A
```

## 2) Configure HTTPS in front of Marzban
Ensure Caddy (or your reverse proxy) serves `https://sub.yourdomain.tld` and forwards to Marzban.

Minimal Caddyfile example:
```caddy
sub.yourdomain.tld {
    reverse_proxy 127.0.0.1:8000
}
```

Reload Caddy:
```bash
systemctl reload caddy
systemctl status caddy --no-pager
```

## 3) Configure Marzban subscription base URL
Set in Marzban `.env`:
```env
XRAY_SUBSCRIPTION_URL_PREFIX=https://sub.yourdomain.tld
XRAY_SUBSCRIPTION_PATH=sub
SUB_SUPPORT_URL=https://t.me/lKRRworkl
```

Restart Marzban stack and verify:
```bash
docker restart marzban_marzban_1
curl -I https://sub.yourdomain.tld
```

## 4) Enable bot delivery mode (safe phase)
In bot `.env`:
```env
CONFIG_DELIVERY_MODE=subscription_first
# Required if Marzban returns relative links like /sub/...
SUBSCRIPTION_PUBLIC_BASE_URL=https://sub.yourdomain.tld
```

Then restart bot:
```bash
systemctl restart vpn-bot
systemctl status vpn-bot --no-pager
```

Modes:
- `direct` - old behavior, direct links only.
- `subscription_first` - subscription link preferred; fallback to direct link.
- `subscription_only` - subscription links only.

## 5) Validate end-to-end
1. Request config in bot as test user.
2. Confirm output contains `https://sub.yourdomain.tld/sub/...`.
3. Import into client and check traffic works.
4. Verify old users still receive at least one working link (fallback during migration).

## 6) User migration communication
Send broadcast:
1. "We switched to subscription links."
2. "Re-import link once in your client."
3. "After that, updates happen automatically."

Keep `subscription_first` for 3-7 days.

## 7) Finalize
After migration window:
1. Switch bot mode to:
```env
CONFIG_DELIVERY_MODE=subscription_only
```
2. Restart bot.
3. Keep one rollback option:
```env
CONFIG_DELIVERY_MODE=direct
```

## 8) Rollback plan
If issue appears:
1. Set `CONFIG_DELIVERY_MODE=direct`.
2. Restart bot.
3. Investigate domain/TLS/Marzban subscription prefix.
