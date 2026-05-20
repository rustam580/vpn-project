# RootVPN olcRTC Rescue Beta Lab

This is the chosen fast path for the whitelist-resilience experiment.

## Decision

Use `olcrtc` as an isolated Go/Pion sidecar for the first real Rescue Beta test.

Primary profile:

```text
wbstream + vp8channel
```

Why this path:

- The business goal is "internet still works when only whitelisted services work".
- WB Stream is closer to that real condition than a self-hosted WebRTC gateway.
- `vp8channel` is already a byte tunnel over valid-looking VP8 samples with KCP reliability.
- `olcrtc` already has client/server modes, SOCKS5, encryption, smux, liveness, lifecycle controls, and Docker/Podman wrappers.

## Boundary

Keep this outside the production RootVPN bot/site/Marzban flow until closed beta criteria are met.

Do not advertise it as a public tariff yet. Treat it as admin-only Rescue Beta.

## Field Status

2026-05-18 validation:

- Lab/LDPlayer: Olcbox TUN routed browser traffic through the test VPS `104.238.29.239`; `2ip.ru` showed the VPS IP, and Speedtest reported about `6.19 Mbps` download with about `404 ms` ping.
- Real mobile field check in Anapa, RU: during a whitelist-like mobile restriction, the tester confirmed non-whitelisted apps/pages did not load on mobile data without VPN, then loaded after importing the RootVPN Rescue Beta URI and starting Olcbox.

Interpretation: the core Rescue Beta hypothesis is confirmed for one real-world sample. Next work is packaging and reliability, not proving the basic tunnel concept again.

## License

`openlibrecommunity/olcrtc` is WTFPL-licensed as of the inspected `refactor/universal-carrier` branch.
That avoids GPL-style copyleft concerns, but operational risk remains: third-party carrier accounts/rooms can break or be rate-limited.

## Generate Lab Configs

From the RootVPN repo:

```powershell
python scripts/generate_olcrtc_rescue_configs.py `
  "https://stream.wb.ru/room/019e3ab0-e4c1-7d0b-8ea4-7df731ec636d" `
  --out-dir out/olcrtc-rescue `
  --debug
```

Outputs:

- `out/olcrtc-rescue/server.yaml`
- `out/olcrtc-rescue/client.yaml`
- `out/olcrtc-rescue/uri.txt`
- `out/olcrtc-rescue/room-url.txt`
- `out/olcrtc-rescue/session.json`

For `wbstream`, full URLs like `https://stream.wb.ru/room/<id>` are normalized to `<id>` because
the upstream auth provider joins rooms by room ID.

You can also try automatic WB room creation:

```powershell
python scripts/generate_olcrtc_rescue_configs.py `
  --create-wb-room `
  --client-id olcbox `
  --out-dir out/olcrtc-rescue
```

As of 2026-05-18, guest room creation can fail with `Guests are not allowed to create room`.
That means product automation needs an authenticated room broker or a pre-created room pool.

Defaults:

- carrier: `wbstream`
- transport: `vp8channel`
- VP8 FPS: `60`
- VP8 batch: `64`
- DNS: `1.1.1.1:53`
- local SOCKS: `127.0.0.1:8808`
- liveness: `10s` interval, `5s` timeout, `3` failures
- lifecycle max session duration: `168h`
- traffic cap: transport default (`max_payload_size: 0`) with `5ms..30ms` pacing

## Run Shape

Build or install `olcrtc` from the upstream project, then run:

```bash
olcrtc server.yaml
```

On the client machine:

```bash
olcrtc client.yaml
curl --socks5-hostname 127.0.0.1:8808 https://icanhazip.com
```

Success means the returned IP is the server egress IP and ordinary TCP over SOCKS works.

## Server Service Shape

For closed beta, run each session as a managed service instead of an interactive shell:

```bash
install -D -m 0644 experiments/olcrtc-rescue/systemd/olcrtc-rescue@.service \
  /etc/systemd/system/olcrtc-rescue@.service

mkdir -p /etc/rootvpn/rescue/<session-id>
cp out/olcrtc-rescue/server.yaml /etc/rootvpn/rescue/<session-id>/server.yaml

systemctl daemon-reload
systemctl enable --now olcrtc-rescue@<session-id>
journalctl -u olcrtc-rescue@<session-id> -f
```

For the first beta, use one active device per session/room. Increase sharing only after reconnect and throughput metrics prove it is safe.

The service template intentionally uses slow restart backoff (`RestartSec=60`) and systemd
start-limits. WB Stream can return `403 guests cannot create rooms` or `429` when rooms are
rejoined too aggressively. Do not lower restart backoff unless you are debugging interactively.

## Bot Auto-Deploy

Admin command:

```text
/rescue <telegram_id> <wb_room_url>
```

By default the bot prepares artifacts, sends the user URI/instructions, and returns a replayable
deploy command to the operator. Automatic deploy is opt-in via `.env`:

```dotenv
OLCRTC_RESCUE_AUTO_DEPLOY=1
OLCRTC_RESCUE_DEPLOY_HOST=root@104.238.29.239
OLCRTC_RESCUE_REMOTE_ROOT=/etc/rootvpn/rescue
OLCRTC_RESCUE_INSTALL_SERVICE=1
OLCRTC_RESCUE_DEPLOY_TIMEOUT_SEC=60
```

Auto-deploy uses non-interactive SSH/SCP options (`BatchMode=yes`, short connect timeout), so the
bot does not hang on password prompts. Configure SSH keys from the bot host to the Rescue VPS before
turning it on.

Status checks:

```text
/rescue_status <session_id>
```

The `/rescue` admin response also includes a `Статус Rescue` inline button. Both paths run
`systemctl status` and a bounded `journalctl` tail on the Rescue VPS through the configured SSH host.
CLI fallback:

```bash
python scripts/manage_olcrtc_rescue_session.py status rs-20260518202449-386029735 \
  --deploy-host rootvpn-rescue-fi
```

## Warm Room Pool Automation

The current reliable automation model is:

1. Operator creates WB Stream rooms with an authenticated WB account.
2. Operator stores them in the bot pool:

```text
/rescue_room_add <wb_room_url> [note]
```

3. The watchdog keeps a minimum number of free rooms warmed on the Rescue VPS:

```dotenv
OLCRTC_RESCUE_WATCHDOG_ENABLED=1
OLCRTC_RESCUE_WATCHDOG_AUTO_RESTART=1
OLCRTC_RESCUE_POOL_AUTO_WARM=1
OLCRTC_RESCUE_POOL_MIN_WARM=1
OLCRTC_RESCUE_POOL_MAX_WARM_PER_TICK=1
```

This is deliberately not a full WB login bot yet. If a WB room can still be joined, the server-side
relay can be restarted and recovered automatically. If the WB room is permanently closed or WB asks
for a fresh authenticated host action, the next step is an authenticated room broker with stored
operator account state.

Optional room broker hook:

```dotenv
OLCRTC_RESCUE_ROOM_BROKER_ENABLED=1
OLCRTC_RESCUE_ROOM_BROKER_COMMAND=/opt/rootvpn/create-wb-room --count {count}
OLCRTC_RESCUE_ROOM_BROKER_TIMEOUT_SEC=45
OLCRTC_RESCUE_ROOM_BROKER_MAX_ROOMS_PER_TICK=1
OLCRTC_RESCUE_POOL_MIN_FREE=1
```

The broker command must print one or more room URLs to stdout. Both plain text and simple JSON are
accepted:

```json
{"rooms": ["https://stream.wb.ru/room/019e..."]}
```

or:

```text
https://stream.wb.ru/room/019e...
```

Keep WB cookies/profile/token storage inside that broker process, not in the Telegram bot. The bot
only consumes room URLs and manages deployment.

Pool auto-warm counts both active and non-active `warm` rows as occupied warm slots. This prevents
runaway room creation when WB temporarily returns `403/429` and several warm services sit in
`activating`. Clean or stop stale warm rooms manually instead of letting the broker create an
unbounded queue.

### Token-Based Broker Script

This repo includes a minimal broker script:

- `scripts/create_wb_room_broker.py`
- `experiments/olcrtc-rescue/bin/create-wb-room`

Install the wrapper on the bot server:

```bash
install -d -m 700 /opt/rootvpn /etc/rootvpn
install -m 700 experiments/olcrtc-rescue/bin/create-wb-room /opt/rootvpn/create-wb-room
```

Store a WB Stream access token outside git:

```bash
printf '%s\n' 'PASTE_WB_ACCESS_TOKEN_HERE' >/etc/rootvpn/wbstream-access-token
chmod 600 /etc/rootvpn/wbstream-access-token
```

On an operator Windows machine with the WB Stream desktop app logged in, token candidates can be
located without printing secret values:

```powershell
python tools/find_wbstream_token_candidates.py
```

If the `accessToken` candidate is index `1`, write it to a local ignored file:

```powershell
python tools/find_wbstream_token_candidates.py --index 1 --write-token out/secrets/wbstream-access-token.txt
```

Then copy that file to the bot server as `/etc/rootvpn/wbstream-access-token`. Do not paste the token
into Telegram, shell history, GitHub, or logs.

Smoke test:

```bash
/opt/rootvpn/create-wb-room --count 1
```

Expected stdout:

```json
{"generated_at":"...","rooms":[{"room_id":"...","room_url":"https://stream.wb.ru/room/..."}]}
```

## Assigned Session Auto-Replacement

The watchdog can also replace a broken user-assigned room instead of only restarting the same
systemd unit:

```dotenv
OLCRTC_RESCUE_ASSIGNED_AUTO_REPLACE=1
OLCRTC_RESCUE_ASSIGNED_MAX_REPLACE_PER_TICK=1
OLCRTC_RESCUE_ASSIGNED_REPLACE_MIN_AGE_SEC=600
```

When an `assigned` session appears as non-active in `/rescue_list`, the bot:

1. Claims the next `warm` room first, or a `free` room if no warm room exists.
2. Starts a new olcRTC service if the replacement was only `free`.
3. Sends the user a fresh Rescue URI.
4. Marks the old room as `bad` and stops the old service.
5. Alerts admins with the replacement result.

`OLCRTC_RESCUE_ASSIGNED_REPLACE_MIN_AGE_SEC` is intentionally conservative. WB/olcRTC can briefly
show `activating` while a room wakes up or systemd restarts the relay. Replacing immediately causes
unnecessary URI churn for users.

Keep `OLCRTC_RESCUE_POOL_MIN_WARM` and `OLCRTC_RESCUE_POOL_MIN_FREE` above zero before enabling this,
otherwise there may be no room available when a live user drops.

If the token expires, the broker will fail and watchdog will alert. Replace the token, then rerun
the smoke test. A future Playwright/profile broker can replace this script without changing the
Telegram bot contract, as long as it prints room URLs to stdout.

## RootVPN Integration Target

If the lab works for 7 days:

1. Add a bot admin-only command to generate Rescue Beta configs/URI for a paid user.
2. Add short-lived entitlement token or per-user key.
3. Run sidecar server under systemd or container with resource limits.
4. Add health checks: session connected, SOCKS roundtrip, traffic counters, room TTL, reconnect count.
5. Only then consider a branded client path.

## Kill Criteria

Stop this branch if:

- WB/Telemost rooms die frequently or accounts get restricted;
- setup needs too much manual support;
- throughput is not enough for basic browsing;
- server egress cannot be safely policy-limited;
- mobile client distribution becomes the main blocker.
