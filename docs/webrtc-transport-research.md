# WebRTC Transport Research

Last updated: 2026-05-18

## Decision

WebRTC transport is an R&D track, not a production RootVPN tariff yet.

Do not advertise it on the public website and do not sell it as a stable plan until a closed beta proves:

- stable connection setup on common client networks;
- predictable speed and latency;
- acceptable server cost per GB;
- clean operational monitoring;
- clear legal/product wording.

## Goal

Explore a reserve transport for cases where the standard VLESS Reality subscription is unreliable for a customer network.

Important distinction:

- the current self-hosted WebRTC gateway PoC is only a lab baseline for DataChannel mechanics;
- the real whitelist-resilience hypothesis requires a carrier layer that routes WebRTC traffic through already-allowed video/conference services, similar to the olcRTC carrier concept.

The intended product shape, if successful:

- `Standard`: current VLESS Reality subscription through Happ/V2Box/etc.
- `Pro` or `Rescue Beta`: standard subscription plus experimental WebRTC fallback.
- WebRTC fallback is explicitly labelled beta until operational data proves otherwise.

## Non-Goals

- Do not replace the current Marzban/VLESS stack.
- Do not promise universal bypass.
- Do not copy GPL code into the main RootVPN codebase unless we are ready to comply with GPL obligations for the derived component.
- Do not depend on third-party whitelisted services as a hidden production dependency without explicit risk review.
- Do not expose raw unauthenticated WebRTC gateways.

## High-Level Architecture

```text
Client app / local proxy
  -> stream mux + app encryption
  -> transport (DataChannel first)
  -> carrier (direct lab / Telemost / Jazz / WB Stream style service)
  -> RootVPN server participant
  -> internet
```

## External Reference: olcRTC Notes

Reference reviewed: `https://github.com/openlibrecommunity/olcrtc/blob/master/docs/about.md`.

Useful ideas to learn from, without copying code into the main RootVPN product:

- Separate the system into replaceable layers:
  - client/server business logic;
  - stream multiplexer;
  - encryption wrapper;
  - link adapter;
  - transport;
  - carrier/provider.
- Treat WebRTC DataChannel as only one transport. The olcRTC design also explores video-based transports (`vp8channel`, `seichannel`, visual/video frames) because DataChannel may be removed, limited, or fingerprinted by a carrier.
- Add an application encryption layer over WebRTC DTLS. olcRTC uses ChaCha20-Poly1305 before data enters the carrier transport, so the carrier only sees encrypted blobs.
- Use stream multiplexing over the WebRTC message channel. olcRTC uses `smux`; for RootVPN this means Phase 3 should not directly map "one TCP connection = one DataChannel" unless it is just a throwaway test.
- Respect payload limits. olcRTC reports SFU/DataChannel payload limits around 8-12 KB depending on provider; RootVPN should frame and chunk well below that, likely 4-8 KB initially.
- Start with SOCKS5 as the client boundary. This is easier to test than TUN and lets browsers/apps use the tunnel without kernel-level routing.
- Keep carrier and transport concepts separate. A "carrier" is the WebRTC service/path; a "transport" is the encoding over that carrier. This keeps experiments swappable.
- Plan for reconnects, keepalive, backpressure, and queue visibility from the first non-echo PoC.
- For Android, `VpnService.protect()`-style socket protection matters if the WebRTC client runs inside a VPN-style app, otherwise the tunnel can recursively route itself.

Additional `vp8channel` review from `openlibrecommunity/olcrtc` branch `refactor/universal-carrier`:

- `vp8channel` is not visual QR/tile encoding. It publishes valid-looking VP8 samples with a small VP8 keyframe prefix, a binding/epoch header, and raw KCP packet bytes.
- The Go implementation relies on Pion `TrackLocalStaticSample` with `MimeTypeVP8`, so it can send prebuilt encoded samples directly.
- KCP supplies reliable ordered delivery, retransmits, stream mode, and backpressure; this is a stronger architecture than adding more custom ACK logic above visual frames.
- A direct Python port is uncertain because the current RootVPN WB lab path publishes raw video frames through LiveKit and lets the SDK encode them. That path may destroy arbitrary VP8 bitstream injection.
- Pragmatic next step: keep Python `tile2` as a protocol lab, but evaluate a Go/Pion vp8 sidecar or olcRTC backend spike before spending time on a pure-Python vp8 implementation.

Important cautions:

- olcRTC intentionally depends on third-party whitelisted video-call services. That is operationally fragile and may carry ToS/legal/reputation risk.
- The RootVPN experiment should first use our own signaling/gateway path, then evaluate third-party carrier research only in an isolated lab.
- Do not promote "white-list bypass" language in customer-facing RootVPN materials.
- Do not mix this with production Marzban, payments, or the public site until closed beta criteria are met.

Components:

1. Client
- Browser proof of concept first.
- Later: desktop bridge or mobile app if the PoC is useful.
- Current subscription clients like Happ/V2Box do not natively consume WebRTC DataChannel, so this is a separate client path.

2. Signaling service
- HTTPS/WebSocket service exchanging SDP offer/answer and ICE candidates.
- Authenticates short-lived RootVPN tokens.
- Does not carry user traffic in the direct lab mode.
- In carrier mode, signaling may need to create/join a third-party room or consume an externally-created room URL.

3. WebRTC gateway
- Server process accepting WebRTC DataChannel sessions.
- Candidate stacks:
  - Go with `pion/webrtc`;
  - Rust with `webrtc-rs`.
- In direct lab mode, accepts browser offers directly.
- In carrier mode, acts as the server-side participant in the same third-party WebRTC room as the client.
- Translates DataChannel/messages to a local upstream. First PoC can target a local HTTP echo; later a SOCKS/TUN/HTTP proxy.

4. Carrier layer
- Direct carrier: self-hosted offer/answer, useful only for local lab mechanics and baseline metrics.
- Third-party carrier: WebRTC service already reachable on restricted networks.
- Candidate carrier families based on olcRTC research:
  - Telemost-style room;
  - SaluteJazz-style room;
  - WB Stream/LiveKit-style room.
- Carrier adapters must be isolated behind a stable interface so they can be removed or replaced without touching payments, Marzban, or customer flows.

5. STUN/TURN
- STUN for NAT discovery.
- TURN for relay fallback.
- `coturn` is the likely first TURN server.
- For the final whitelist-resilience hypothesis, our own TURN is not enough by itself; the traffic path must still look like the chosen carrier service.
- Relay traffic is expensive and must be measured before any paid plan.

6. Auth and entitlement
- Bot/API issues a short-lived token for a paid user/device.
- Signaling validates token against RootVPN backend.
- Gateway enforces session TTL, max sessions, and eventually traffic accounting.

7. Monitoring
- ICE success/failure rate.
- Connection setup time.
- Reconnect rate.
- TURN relay usage.
- Active sessions.
- Traffic per user/session.
- CPU/RAM per active session.

## MVP Scope

Phase 0: Documentation and risk review
- Keep this file as the source of truth.
- Keep experiment isolated from production purchase flow.

Phase 1: Local direct lab PoC
- Browser page opens a WebRTC DataChannel to a gateway.
- Gateway receives binary messages and echoes them.
- No Marzban, no payment, no public tariff.
- This does not validate whitelist bypass; it only validates our local WebRTC/DataChannel mechanics.

Phase 2: Authenticated PoC
- Add short-lived token endpoint.
- Signaling rejects missing/expired tokens.
- Gateway logs session start/stop with a user/device identifier.

Phase 3: Carrier adapter PoC
- Add a carrier interface.
- Keep direct carrier as a test adapter.
- Add one experimental third-party carrier adapter in a lab-only branch or isolated module.
- Validate that both client and server can join the same room and exchange small messages.
- Do not add SOCKS/proxy traffic yet.

Phase 4: Proxy PoC
- Add a local SOCKS5 or HTTP CONNECT boundary.
- Add framing/chunking below observed DataChannel payload limits.
- Add a minimal stream multiplexer or evaluate an existing permissive-license multiplexer.
- Gateway forwards to upstream and returns responses.
- Measure latency, throughput, reconnect behaviour, and memory per active stream.

Current status inside `experiments/webrtc-gateway/`:

- `socks5_proto.py`, `proxy_messages.py`, `local_bridge.py`, and `local_socks_server.py` provide a loopback-only fake-egress SOCKS shape.
- `proxy_packet_bundle.py` defines the `RPB1` carrier boundary for proxy packets.
- `wbstream_proxy_carrier.py` can deliver request bundles over WB stream-mode video frames.
- `remote_proxy_endpoint.py` now decodes request bundles, enforces explicit route policy, runs fake egress, and returns response bundles.
- Missing before real egress: reverse WB response delivery, auth tokens, reconnect/session supervisor, flow control, traffic accounting, and account-risk baseline.

Phase 5: Closed beta
- One or two admin-owned devices only.
- No public sales copy.
- Track daily metrics and failure modes.

## Open Technical Questions

- Which server stack is better for the gateway: Go/Pion or Rust/webrtc-rs?
- Can we provide a usable client without forcing users into a custom app?
- Which carrier adapter is practical and least fragile for a lab test?
- Which carriers allow guest participation without account friction?
- Which carriers still expose usable DataChannel versus only video-track paths?
- Is TURN usage low enough to be economically viable?
- Can we account traffic accurately enough for abuse control?
- What is the support burden compared with the current subscription URL flow?
- Does the approach introduce ToS, legal, or reputation risk with third-party infrastructure?

## Promotion Criteria

Consider turning this into a paid beta only if:

- closed beta works for at least 7 days without manual intervention;
- median setup time is acceptable for users;
- relay/TURN traffic cost is understood;
- the support script is simple enough for non-technical customers;
- monitoring can alert on gateway failure before users complain;
- legal/product wording is reviewed.

## Kill Criteria

Stop the experiment if:

- client setup is more complex than current VPN support can handle;
- TURN relay cost makes the tariff unprofitable;
- gateway is unstable under modest concurrency;
- the selected carrier breaks often, blocks automation, bans accounts/IPs, or changes protocol frequently;
- it materially increases risk to the main RootVPN infrastructure.

## Repository Boundary

Recommended implementation path:

- keep initial code under `experiments/webrtc-gateway/` or a separate private repository;
- do not wire it into `website_api.py`, payments, or Marzban until Phase 2 succeeds;
- do not deploy it on the main bot/VPN host without resource limits.

This keeps the main paid product stable while leaving room for a serious experiment.
