# RootVPN Rescue Beta Closed-Beta Plan

Last updated: 2026-05-18

## Goal

Make the successful manual `wbstream + vp8channel` field test repeatable for non-technical users:

```text
User opens bot
-> gets one Rescue Beta link/instruction
-> installs RootVPN/Olcbox APK
-> taps Start
-> mobile internet works during whitelist restrictions
```

The user must not create rooms, copy YAML, run terminals, or understand WB Stream.

## Current Truth

- The tunnel works in lab and one real mobile field check.
- `olcrtc` server + Olcbox client is the fast path.
- Guest WB room creation currently fails with `Guests are not allowed to create room`.
- Therefore fully automatic room creation requires either:
  - an authenticated WB account room broker;
  - a pre-created room pool for closed beta;
  - a second carrier where unauthenticated room creation remains possible.

## Product Shape

Rescue Beta should be a fallback, not the default VPN.

```text
Normal day:
  User uses standard VLESS Reality config.

Whitelist/mobile restriction:
  User opens RootVPN Rescue app/profile.
  Traffic goes through WB Stream/WebRTC to RootVPN VPS egress.
```

Do not stack ordinary Xray over Rescue by default. Rescue already exits through a non-RU VPS, so most blocked sites should open directly. Add a second VPN layer only if a specific site requires it; otherwise it wastes latency and throughput.

## Room Strategy

Start conservative:

- 1 active user/device per WB room for the first 7-day beta.
- 1 `olcrtc` server process per room/session.
- Session TTL: 2 hours by config, then rotate.
- Room TTL target: measure how long a room remains joinable and stable.

Why not one shared room for everyone:

- WebRTC carrier bandwidth is the bottleneck.
- Multiple clients in the same room can trigger extra offers/reconnects.
- Per-user rooms make support, revocation, and traffic attribution simpler.

After measurements:

- If one room stays stable under load, test 2 active users per room.
- Do not exceed 3 active users per room until reconnect and throughput metrics prove it is safe.

## Target Architecture

```text
Telegram bot/admin command
  -> Rescue session manager
     -> room broker creates or leases WB room
     -> generates per-user key + URI
     -> writes server.yaml
     -> starts systemd olcrtc-rescue@<session>
  -> bot sends APK link + one-tap/import URI

Android app
  -> imports olcrtc:// URI
  -> starts TUN
  -> WB Stream carrier
  -> olcrtc VPS sidecar
  -> internet egress
```

## VPS Placement

The Finnish VPS can be both:

- a Rescue/WebRTC node running `olcrtc-rescue@<session>` services;
- a regular RootVPN/Xray egress node for a second VPN location.

Do not move the core bot, payment flow, Marzban database, or website API there during beta. Keep those on the main production host.

Recommended split:

```text
Main RootVPN host
  - Telegram bot
  - website API
  - Marzban control plane
  - payments/subscriptions

Finland node
  - Xray inbound for "Finland" configs
  - olcrtc Rescue session services
  - logs/metrics for local sessions
```

Rules:

- no shared listener ports between Xray and `olcrtc`;
- one `olcrtc` process per active Rescue room/session;
- add systemd resource limits before broader beta;
- keep Rescue configs/secrets separate from normal Xray configs;
- if Rescue traffic spikes CPU/bandwidth, move Rescue to its own disposable VPS.

## Phase 1: Repeatable Admin Package

Use this for the next field checks.

1. Create/lease a WB room.
2. Run `scripts/generate_olcrtc_rescue_configs.py`.
3. Copy `server.yaml` to `/etc/rootvpn/rescue/<session>/server.yaml`.
4. Start `systemctl start olcrtc-rescue@<session>`.
5. Send the APK and `uri.txt` to the tester.

Success criteria:

- non-technical tester can install/import/start from a short instruction;
- `2ip.ru` shows the Rescue VPS IP;
- simple sites/apps load for at least 10 minutes;
- server logs show `control alive` and `traffic`, not rapid reconnect loops.

## Phase 2: Room Broker

Preferred path: authenticated WB account broker.

Responsibilities:

- keep a small pool of fresh rooms;
- replace rooms before they expire or fail;
- expose `lease_room()` to the bot/session manager;
- track room age, account used, active session id, and last health.

Implementation options:

- API-cookie broker: reuse authenticated WB web session cookies and call the same room API.
- Browser broker: Playwright/Chrome signs in once, creates rooms, extracts room IDs.
- Manual pool for beta: operator creates 5-10 rooms and stores them in a local queue.

Start with manual pool if authenticated automation is slower than expected. It is not elegant, but it keeps beta moving while isolating account-risk research.

## Phase 3: Bot UX

Admin-only first:

```text
/rescue <telegram_id> <wb_room_url>
```

The command should:

- verify user is paid/eligible;
- use a manually supplied room in the first beta;
- generate per-user key and client id;
- optionally start server session via non-interactive SSH/systemd auto-deploy;
- send an admin card with status and logs;
- send the user a simple install/import instruction.

Later user-facing button:

```text
🛟 Rescue Beta
```

Only show it to allowlisted beta users.

## Phase 4: Monitoring

Minimum metrics before public beta:

- room id/session id;
- client id / Telegram id;
- process uptime;
- reconnect count;
- last `control alive`;
- traffic in/out;
- speed-test sample if available;
- error tail.

Alert when:

- process exits repeatedly;
- no `control alive` for 60 seconds;
- reconnect count spikes;
- room age exceeds expected TTL;
- traffic is zero after user started.

## User Instruction Target

The final instruction should fit in one Telegram message:

```text
1. Установи RootVPN Rescue APK.
2. Открой эту ссылку: <olcrtc://...>
3. Нажми Start.
4. Проверь 2ip.ru: должен быть IP RootVPN.
```

If Android blocks direct `olcrtc://` opening, send a QR code or copy button as fallback.

## Open Decisions

- Whether to fork/brand Olcbox now or keep "Olcbox Rescue" for beta.
- Whether to use authenticated WB broker or manual room pool for the first 7 days.
- How many room-creation accounts are needed before account-risk is acceptable.
- Whether to expose Rescue Beta as a paid add-on or keep it as emergency fallback for existing paid users.
