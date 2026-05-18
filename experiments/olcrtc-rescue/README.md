# RootVPN olcRTC Rescue Beta Lab

This is the chosen fast path for the whitelist-resilience experiment.

## Decision

Use `olcrtc` as an isolated Go/Pion sidecar for the first real Rescue Beta test.

Primary profile:

```text
wbstream + vp8channel
```

Why this, not the current Python `tile2` path:

- The business goal is "internet still works when only whitelisted services work".
- WB Stream is closer to that real condition than a self-hosted WebRTC gateway.
- `vp8channel` is already a byte tunnel over valid-looking VP8 samples with KCP reliability.
- The Python `tile2` path is useful R&D, but current measured speed is still hundreds of B/s to ~1 KB/s.
- `olcrtc` already has client/server modes, SOCKS5, encryption, smux, liveness, lifecycle rotation, and Docker/Podman wrappers.

## Boundary

Keep this outside the production RootVPN bot/site/Marzban flow until closed beta criteria are met.

Do not advertise it as a public tariff yet. Treat it as admin-only Rescue Beta.

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

For `wbstream`, full URLs like `https://stream.wb.ru/room/<id>` are normalized to `<id>` because
the upstream auth provider joins rooms by room ID.

Defaults:

- carrier: `wbstream`
- transport: `vp8channel`
- VP8 FPS: `60`
- VP8 batch: `64`
- DNS: `1.1.1.1:53`
- local SOCKS: `127.0.0.1:8808`
- liveness: `10s` interval, `5s` timeout, `3` failures
- lifecycle rotation: `2h`
- traffic cap: `4096` bytes with `5ms..30ms` pacing

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
