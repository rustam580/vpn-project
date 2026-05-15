# WebRTC Transport Research

Last updated: 2026-05-15

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
Client app / browser page
  -> WebRTC DataChannel
  -> RootVPN signaling service
  -> RootVPN WebRTC gateway
  -> local proxy/upstream
  -> internet
```

Components:

1. Client
- Browser proof of concept first.
- Later: desktop bridge or mobile app if the PoC is useful.
- Current subscription clients like Happ/V2Box do not natively consume WebRTC DataChannel, so this is a separate client path.

2. Signaling service
- HTTPS/WebSocket service exchanging SDP offer/answer and ICE candidates.
- Authenticates short-lived RootVPN tokens.
- Does not carry user traffic.

3. WebRTC gateway
- Server process accepting WebRTC DataChannel sessions.
- Candidate stacks:
  - Go with `pion/webrtc`;
  - Rust with `webrtc-rs`.
- Translates DataChannel messages to a local upstream. First PoC can target a local HTTP echo; later a SOCKS/TUN/HTTP proxy.

4. STUN/TURN
- STUN for NAT discovery.
- TURN for relay fallback.
- `coturn` is the likely first TURN server.
- Relay traffic is expensive and must be measured before any paid plan.

5. Auth and entitlement
- Bot/API issues a short-lived token for a paid user/device.
- Signaling validates token against RootVPN backend.
- Gateway enforces session TTL, max sessions, and eventually traffic accounting.

6. Monitoring
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

Phase 1: Local lab PoC
- Browser page opens a WebRTC DataChannel to a gateway.
- Gateway receives binary messages and echoes them.
- No Marzban, no payment, no public tariff.

Phase 2: Authenticated PoC
- Add short-lived token endpoint.
- Signaling rejects missing/expired tokens.
- Gateway logs session start/stop with a user/device identifier.

Phase 3: Proxy PoC
- Browser/client sends simple HTTP proxy requests through the DataChannel.
- Gateway forwards to upstream and returns responses.
- Measure latency and throughput.

Phase 4: Closed beta
- One or two admin-owned devices only.
- No public sales copy.
- Track daily metrics and failure modes.

## Open Technical Questions

- Which server stack is better for the gateway: Go/Pion or Rust/webrtc-rs?
- Can we provide a usable client without forcing users into a custom app?
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
- the approach depends on fragile third-party whitelist behaviour;
- it materially increases risk to the main RootVPN infrastructure.

## Repository Boundary

Recommended implementation path:

- keep initial code under `experiments/webrtc-gateway/` or a separate private repository;
- do not wire it into `website_api.py`, payments, or Marzban until Phase 2 succeeds;
- do not deploy it on the main bot/VPN host without resource limits.

This keeps the main paid product stable while leaving room for a serious experiment.
