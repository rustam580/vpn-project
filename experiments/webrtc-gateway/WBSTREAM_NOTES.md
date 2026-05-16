# WB Stream Carrier Notes

This file records implementation notes from `openlibrecommunity/olcrtc` for the RootVPN WebRTC R&D track.

Source files reviewed:

- `internal/provider/wbstream/api.go`
- `internal/provider/wbstream/peer.go`
- `internal/provider/wbstream/provider.go`
- `internal/carrier/carrier.go`
- `internal/transport/datachannel/transport.go`
- `internal/transport/videochannel/transport.go` from branch `refactor/universal-carrier`
- `internal/transport/videochannel/visual.go` from branch `refactor/universal-carrier`
- `internal/e2e/tunnel_test.go` from branch `refactor/universal-carrier`
- `docs/uri.md`
- `docs/fast.md`

## What WB Stream Looks Like In olcRTC

WB Stream is not a plain browser SDP endpoint. It is treated as a LiveKit-style carrier.

Observed flow:

1. Register a guest participant.
2. Create or reuse a WB Stream room ID.
3. Join the room.
4. Request room connection details / room token.
5. Connect to LiveKit at `wss://rtc-el-01.wb.ru`.
6. Send payloads as reliable LiveKit data packets with topic `olcrtc`.

## RootVPN Lab Findings On 2026-05-16

Manual room tested:

```text
https://stream.wb.ru/room/019e30d5-9b63-700e-8453-b514a5db7746
```

What worked:

- Guest registration works.
- Joining an existing room works.
- Room connection details return LiveKit endpoint `wss://rtc-el-02.wb.ru`.
- Two lab guest participants can see each other.
- Synthetic video publishing works: a `160x120` RGBA frame published by one guest was received by another guest in ~6.1s.
- Encrypted/authenticated one-frame byte payload works over video: `RootVPN WB video bytes OK` was encoded into a `320x240` frame and decoded by a second guest in ~6.3s.
- Multi-frame one-way byte payload works over video: `512` bytes took 5 encoded frames and ~7.5s; `1024` bytes took 9 encoded frames and ~8.0s in the tested room.
- Reverse ACK over video works: `512` bytes completed with 5/5 chunks ACKed in ~17.8s; `1024` bytes completed with 9/9 chunks ACKed in ~13.1s.
- Sliding-window ACK works: `1024` bytes with window 4 completed in ~9.8s with 0 retransmits (~104 B/s); `2048` bytes completed in ~27.6s with 16 retransmits (~74 B/s).
- Tuning sweep tooling works. First 1KB sweep with fps=8/ack_fps=4 found window=6 best (`124.18 B/s`, 0 retransmits), but a follow-up sweep varied significantly (`39-54 B/s`). Treat WB Stream results as noisy and require repeated runs. First repeated-run smoke test (`512` bytes, window=4, fps=8, ack_fps=4, repeats=2) completed both runs with 0 retransmits and median throughput `64.29 B/s`.

What did not work:

- LiveKit `publish_data()` from guest participants did not deliver packets.
- Token permissions showed `can_publish_data=false` for the lab guests.
- Guest room creation failed with `HTTP 400: Guests are not allowed to create room`.

Implication:

- For this WB Stream room/account path, the next viable carrier is media-track based, not LiveKit data packets.
- A future byte transport should encode small encrypted chunks into video frames or audio frames, with explicit rate limits and a kill switch.
- The first working byte path is implemented in `wbstream_livekit_frame_message.py` using high-contrast video cells plus HMAC-based envelope encryption/authentication. This is still a lab codec, not production cryptography/key management.
- Chunking/reassembly is implemented in `video_frame_codec.py` / `wbstream_livekit_frame_stream.py`. The current field probe is one-way carousel delivery with duplicate tolerance, not an ACK/retry transport.
- ACK bitmap signaling is implemented in `wbstream_livekit_frame_ack.py`. It uses a reverse video track and stops retransmitting chunks once ACKed. This validates bidirectional media-frame signaling, but it is still not a tuned sliding-window transport.
- Sliding-window sender policy is implemented in `video_window.py` and the WB field probe is `wbstream_livekit_frame_window.py`. The first tuning baseline is window=4, retry=2.5s.
- `wbstream_livekit_tuning_sweep.py` runs bounded parameter grids and writes JSON summaries. Use `--repeats N` to get per-case aggregates (`min/median/p95/max` throughput, median elapsed time, median retransmits) before choosing a baseline.

## Notes From `refactor/universal-carrier`

Useful architecture lessons from the newer olcRTC branch:

- Carrier and transport are separated and tested as a matrix; unstable carrier/transport combinations are expected and logged, not hidden.
- `videochannel` is message-oriented and reliable at the transport API boundary, but internally it uses fragmentation, CRC, ACK registry, max send attempts, and ACK timeout.
- Visual codecs include QR and tile modes. Tile mode with Reed-Solomon style redundancy is likely a better next direction than our simple black/white cell codec.
- Real E2E tests mark some carrier/transport combinations as expected unstable. RootVPN should copy this habit before any closed beta.
- Default high-throughput video settings in olcRTC are much larger than our lab frame (`1920x1080`, `30fps`, bitrate around `2M`), so our current `320x240@8fps` numbers are only a conservative baseline.
- Do not build a customer-facing tariff around this until a closed beta proves reconnect, latency, throughput, and account-risk behavior.

olcRTC's recommended URI shape for WB Stream + DataChannel:

```text
olcrtc://wbstream?datachannel@<room-id>#<32-byte-hex-key>%<client-id>$<comment>
```

Operationally useful facts:

- `wbstream + datachannel` is documented as the recommended fast path.
- Room ID and client ID must match between server and client.
- The implementation uses queueing and exposes send-queue/backpressure concepts.
- DataChannel transport advertises a max payload of `12 * 1024` bytes; RootVPN should chunk lower than that.
- The real service path is through WB/LiveKit, not through our self-hosted `/offer` route.

## RootVPN Implementation Plan

Current local `direct` carrier:

- validates our browser/gateway DataChannel mechanics;
- is not whitelist-resilient;
- remains useful as a test adapter.

Next carrier work:

1. Keep `Carrier` interface in `carrier.py`.
2. Add auth token before any third-party carrier adapter.
3. Add a `wbstream` adapter as lab-only code.
4. Treat WB Stream room ID as an explicit input first.
5. Only after manual room tests succeed, consider automatic guest/room creation.

## What We Need From The Operator

For the first manual lab test, provide:

- WB Stream room ID or room URL;
- whether two browser participants can join the room at the same time;
- whether the page requires a logged-in account or guest can join;
- whether the room stays alive after closing the browser tab.

Do not use customer traffic, production credentials, or paid RootVPN users for this test.

## Legal / Risk Notes

- Do not advertise this as a public RootVPN capability.
- Do not automate login/session reuse until the ToS and account-risk picture is clear.
- Keep all WB Stream experiments isolated from payments, Marzban, and production deploy.
