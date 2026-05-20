# RootVPN olcRTC Rescue

This is the chosen fast path for the whitelist-resilience experiment.

## Decision

Use `olcrtc` as an isolated Go/Pion sidecar for RootVPN emergency access.

Primary profile:

```text
wbstream + vp8channel
```

Why this path:

- The business goal is "internet still works when only whitelisted services work".
- WB Stream is closer to that real condition than a self-hosted WebRTC gateway.
- `vp8channel` is already a byte tunnel over valid-looking VP8 samples with KCP reliability.
- `olcrtc` already has client/server modes, SOCKS5, encryption, smux, liveness, lifecycle controls, and Docker/Podman wrappers.

## Product Boundary

Rescue is now exposed to paid users as `🆘 Аварийный доступ`. Keep it as an emergency path, not a
normal tariff replacement: the bot may issue it automatically, but operators should still monitor
carrier health and room-pool capacity.

## Field Status

2026-05-18 validation:

- Lab/LDPlayer: Olcbox TUN routed browser traffic through the test VPS `104.238.29.239`; `2ip.ru` showed the VPS IP, and Speedtest reported about `6.19 Mbps` download with about `404 ms` ping.
- Real mobile field check in Anapa, RU: during a whitelist-like mobile restriction, the tester confirmed non-whitelisted apps/pages did not load on mobile data without VPN, then loaded after importing the RootVPN Rescue URI and starting Olcbox.

Interpretation: the core Rescue hypothesis is confirmed for one real-world sample. Current work is packaging and reliability, not proving the basic tunnel concept again.

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

## Room Pool Automation

The current automation model is:

1. The bot asks the WB room broker for new rooms when the pool is short.
2. The watchdog warms rooms on the Rescue VPS before users need them.
3. A paid user presses `🆘 Аварийный доступ`; the bot first claims an active warm room.
4. If no warm room exists, the bot creates a room on demand, deploys olcRTC, waits for `active`, and sends the URI.
5. Admins can still add a manual room if WB automation is unavailable:

```text
/rescue_room_add <wb_room_url> [note]
```

The watchdog keeps a minimum number of free/warm rooms:

```dotenv
OLCRTC_RESCUE_WATCHDOG_ENABLED=1
OLCRTC_RESCUE_WATCHDOG_AUTO_RESTART=1
OLCRTC_RESCUE_POOL_AUTO_WARM=1
OLCRTC_RESCUE_POOL_MIN_WARM=1
OLCRTC_RESCUE_POOL_MAX_WARM_PER_TICK=1
```

The broker reads a stored WB access token and prints room URLs to stdout. The token must also be
available to the Rescue VPS so the olcRTC relay joins as the authenticated account instead of as a
guest.

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

For fully automatic Rescue, the olcRTC relay must also join WB Stream with the same authenticated
account token. Otherwise the broker can create a room, but the relay still enters as a guest and WB
may keep the room in `activating`/`403 guests cannot create rooms`.

The Rescue systemd template exports:

```ini
Environment=OLCRTC_WBSTREAM_ACCESS_TOKEN_FILE=/etc/rootvpn/wbstream-access-token
```

This requires an olcRTC build that supports `OLCRTC_WBSTREAM_ACCESS_TOKEN_FILE` in the `wbstream`
auth provider. After rebuilding/updating `/usr/local/bin/olcrtc`, smoke-test with an auto-created
room and verify `/rescue_dashboard` shows the new session as `active` before issuing it to users.

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
5. Logs the successful replacement quietly.

Successful self-heal, restart, broker, warm, and cleanup actions are logged quietly. Telegram alerts
are reserved for unresolved problems.

`OLCRTC_RESCUE_ASSIGNED_REPLACE_MIN_AGE_SEC` is intentionally conservative. WB/olcRTC can briefly
show `activating` while a room wakes up or systemd restarts the relay. Replacing immediately causes
unnecessary URI churn for users.

Keep `OLCRTC_RESCUE_POOL_MIN_WARM` and `OLCRTC_RESCUE_POOL_MIN_FREE` above zero before enabling this,
otherwise there may be no room available when a live user drops.

If the token expires, the broker will fail and watchdog will alert. Replace the token, then rerun
the smoke test. A future Playwright/profile broker can replace this script without changing the
Telegram bot contract, as long as it prints room URLs to stdout.

## RootVPN Integration Target

Current integration target:

1. Keep user flow simple: active subscription -> `🆘 Аварийный доступ` -> URI -> START.
2. Keep a warm room pool above zero during restrictions.
3. Keep `/rescue_dashboard`, `/rescue_rooms`, and `/rescue_reconcile apply` for operator cleanup.
4. Add traffic/accounting limits later; do not block the current emergency-access launch on limits.
5. Long-term: branded client path so users do not need to paste a custom URI manually.

## Kill Criteria

Stop this branch if:

- WB/Telemost rooms die frequently or accounts get restricted;
- setup needs too much manual support;
- throughput is not enough for basic browsing;
- server egress cannot be safely policy-limited;
- mobile client distribution becomes the main blocker.
