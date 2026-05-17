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

Fresh room tested on 2026-05-17:

```text
https://stream.wb.ru/room/019e3617-5902-7a10-896f-949571b5cd19
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
- Denser `tile2` visual codec works over WB Stream. It encodes two bits per cell with four brightness levels. First live checks: `512` bytes used 2 chunks with 0 retransmits (~73 B/s); `1024` bytes used 4 chunks with 0 retransmits (~120 B/s). A later one-off binary-vs-tile2 sweep showed `tile2` can be more fragile (`512` bytes, 2 chunks, 3 retransmits, ~43 B/s), so do not promote it as the baseline until repeated sweeps and redundancy are added.
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
- `wbstream_livekit_tuning_sweep.py` runs bounded parameter grids and writes JSON summaries. Use `--codecs binary,tile2` and `--repeats N` to get per-case aggregates (`min/median/p95/max` throughput, median elapsed time, median retransmits) before choosing a baseline.
- `--data-repeats N` is implemented in the sliding-window probe and sweep. It repeats each due data chunk multiple times without counting those duplicates as retransmits, so it can measure explicit redundancy cost separately from timeout-driven retries.
- Later live checks may fail with `HTTP 403: guests cannot create rooms` if the manual WB room is gone or no longer joinable; create a fresh room before treating this as a codec/transport failure.
- Fresh-room matrix on 2026-05-17:
  - `512` bytes, `binary`, `data_repeats=1`: 1/2 successful in the first sweep due a transient `stream.wb.ru` connection error; successful run was `68.35 B/s`.
  - `512` bytes, `binary`, `data_repeats=2`: 2/2 successful, median `56.81 B/s`, 0 retransmits.
  - `512` bytes, `tile2`, `data_repeats=1`: 2/2 successful, median `63.03 B/s`, 0 retransmits.
  - `512` bytes, `tile2`, `data_repeats=2`: 1/2 successful in first sweep due transient provider error; successful run was `65.53 B/s`.
  - With `--run-attempts 2`, all 512-byte cases completed; best one-off was `tile2,data_repeats=1` at `65.69 B/s`.
  - `1024` bytes, `binary`, `data_repeats=1`, repeats=2: median `113.17 B/s`, 0 retransmits.
  - `1024` bytes, `tile2`, `data_repeats=1`, repeats=2: median `146.03 B/s`, 0 retransmits. Current best R&D baseline candidate.
  - `2048` bytes, `tile2`, `data_repeats=1`, windows 4/6/8, repeats=2: 6/6 successful, 0 retransmits. Best median was window=4 at `242.54 B/s`.
  - `4096` bytes, `tile2`, `data_repeats=1`, window=4, repeats=2: 2/2 successful, median `253.83 B/s`, retransmits median `1.0`; both runs needed a second attempt because the first provider connection attempt failed with `ClientConnectorError`.

Current baseline candidate:

```text
codec=tile2
data_repeats=1
window=4
retry_timeout_sec=2.5
fps=8
ack_fps=4
```

The media-frame transport itself looks stable up to 4096-byte lab payloads in the fresh room. The
weakest observed layer is provider/session setup reliability (`stream.wb.ru` connection errors and
occasional LiveKit validation/signaling timeouts), not chunk decode once both participants are in
the room.

## Byte-Stream Layer

`stream_protocol.py` is the first layer above raw video-frame payloads. It frames logical byte
streams as:

```text
stream_id + offset + flags(fin) + payload
```

The reassembler tolerates out-of-order delivery and exact duplicates, waits for gaps, and rejects
conflicting overlaps. This is deliberately smaller than a real SOCKS/VPN tunnel: it gives future
probes a stable byte-stream primitive to carry over `tile2` frames before adding connection
multiplexing, flow control, reconnect/rejoin, or application protocols.

`wbstream_livekit_frame_window.py --stream-mode` now wraps payloads in this byte-stream framing
before encoding them into video frames. `--connect-attempts N` retries guest/token setup for one-off
field probes, mirroring the sweep runner's per-run retry behavior.

Attempted field probe on the 2026-05-17 room with:

```text
payload=2048 codec=tile2 data_repeats=1 window=4 stream_id=77 connect_attempts=2
```

Result: WB returned `HTTP 403: guests cannot create rooms` during connection-details retrieval.
This indicates the manual room was no longer usable for guest field measurements. The stream-mode
code path compiled and local stream-over-tile2 tests pass, but live stream-mode validation needs a
fresh room.

Fresh-room stream-mode validation succeeded later with:

```text
https://stream.wb.ru/room/019e3672-db30-748e-987e-659d44b72a99
```

Baseline:

```text
codec=tile2
data_repeats=1
window=4
retry_timeout_sec=2.5
fps=8
ack_fps=4
stream_id=77
connect_attempts=2
```

Results:

- `2048` bytes: 9 stream/video chunks, 0 retransmits, `220.67 B/s`.
- `4096` bytes: 17 chunks, 0 retransmits, `406.03 B/s`.
- `8192` bytes: 34 chunks, 0 retransmits, `608.41 B/s`.
- `16384` bytes: 67 chunks, 0 retransmits, `1098.37 B/s`.

This validates the full current lab stack: logical byte stream -> stream packets -> `tile2` video
frames -> WB media track -> ACKed sliding-window delivery -> stream reassembly. The next useful
prototype is a local SOCKS-like adapter that maps TCP-ish byte streams onto this primitive, still
strictly isolated from production.

## SOCKS-Like Protocol Skeleton

Added protocol-only local proxy pieces:

- `socks5_proto.py`: no-auth SOCKS5 greeting and CONNECT parser, plus basic reply builders.
- `proxy_messages.py`: internal `OPEN`, `DATA`, `CLOSE`, and `ERROR` messages.
- Tests carry proxy messages through `stream_protocol.py` and `tile2` video frames.

This deliberately stops short of opening a real local listener or egressing traffic. The goal is to
keep the mapping testable first:

```text
SOCKS CONNECT -> ProxyOpen -> StreamFrame -> tile2 frame
TCP bytes -> ProxyData -> StreamFrame -> tile2 frame
EOF/error -> ProxyClose/ProxyError -> StreamFrame -> tile2 frame
```

`local_bridge.py` now provides an in-process fake bridge harness. It parses SOCKS greeting/CONNECT
bytes, emits proxy messages, sends them through an `InMemoryProxyCarrier`, and receives fake egress
DATA/CLOSE replies. This is the last safe local-only step before adding an actual localhost
listener and a real carrier adapter.

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
