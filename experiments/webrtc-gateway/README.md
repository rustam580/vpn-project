# WebRTC Gateway Experiment

Local Phase 1 proof of concept for RootVPN WebRTC/DataChannel transport research.

This is intentionally isolated from production bot, payments, Marzban, and website API.

## What It Does

- Serves a browser test page on `http://127.0.0.1:8090`.
- Browser creates a WebRTC `RTCDataChannel`.
- Python `aiortc` direct carrier accepts the offer and returns an answer.
- DataChannel supports:
  - `ping` -> `pong`
  - any other text -> `echo:<message>`
- `/metrics` exposes simple JSON counters.
- Carrier interface exists so direct local WebRTC can stay as the baseline adapter while WB Stream is added separately.

## What It Does Not Do Yet

- No VPN/proxy tunneling.
- No Marzban integration.
- No payment/auth integration.
- No TURN relay.
- No public deployment.

## Run Locally

From repo root:

```powershell
python -m venv experiments/webrtc-gateway/.venv
experiments/webrtc-gateway/.venv/Scripts/python -m pip install -r experiments/webrtc-gateway/requirements.txt
experiments/webrtc-gateway/.venv/Scripts/python experiments/webrtc-gateway/server.py
```

Open:

```text
http://127.0.0.1:8090
```

Click `Connect`, then `Send ping`.

## Success Criteria

- DataChannel reaches `open` state.
- `ping` returns `pong`.
- `/metrics` shows at least one opened channel and received message.

## Local Verification

Verified on 2026-05-15:

- server starts on `127.0.0.1:8090`;
- browser DataChannel reaches `open`;
- `ping` returns `pong`;
- custom message `hello-rootvpn` returns `echo:hello-rootvpn`;
- `/metrics` increments received/sent counters.

## Next Step After Success

Phase 2 should add a short-lived signed token before signaling accepts offers.

After token auth, see `WBSTREAM_NOTES.md` for WB Stream carrier-adapter notes.

## WB Stream Room Probe

After creating a WB Stream room manually, check whether guest join + LiveKit token retrieval works:

```powershell
experiments/webrtc-gateway/.venv/Scripts/python experiments/webrtc-gateway/wbstream_api.py `
  https://stream.wb.ru/room/019e30d5-9b63-700e-8453-b514a5db7746
```

The script prints a masked room token and `server_url`. It does not start proxying traffic.

## WB Stream LiveKit Ping

If the room probe works, verify that two guest participants can exchange arbitrary bytes over
LiveKit data packets inside the same WB Stream room:

```powershell
experiments/webrtc-gateway/.venv/Scripts/python experiments/webrtc-gateway/wbstream_livekit_ping.py `
  https://stream.wb.ru/room/019e30d5-9b63-700e-8453-b514a5db7746
```

If an existing room gives guest users `can_publish_data=false`, create a guest-owned lab room:

```powershell
experiments/webrtc-gateway/.venv/Scripts/python experiments/webrtc-gateway/wbstream_livekit_ping.py --create-room
```

Expected result:

```json
{
  "ok": true,
  "received_by_b": "ping",
  "received_by_a": "pong"
}
```

This is still an R&D probe: it does not tunnel VPN traffic and does not touch production services.

## WB Stream Video Carrier Probe

Some WB Stream rooms grant guest users media publishing but not LiveKit data packet publishing
(`can_publish_data=false`). In that case, test the media-carrier path:

```powershell
experiments/webrtc-gateway/.venv/Scripts/python experiments/webrtc-gateway/wbstream_livekit_video_probe.py `
  https://stream.wb.ru/room/019e30d5-9b63-700e-8453-b514a5db7746
```

Expected result:

```json
{
  "ok": true,
  "frame_width": 160,
  "frame_height": 120
}
```

If this works, the next R&D step is byte encoding over media frames, not LiveKit data packets.

## WB Stream Encrypted Frame Message

The next lab probe encodes one encrypted/authenticated payload into high-contrast video cells and
decodes it from the received video frame:

```powershell
experiments/webrtc-gateway/.venv/Scripts/python experiments/webrtc-gateway/wbstream_livekit_frame_message.py `
  https://stream.wb.ru/room/019e30d5-9b63-700e-8453-b514a5db7746 `
  --message "RootVPN WB video bytes OK"
```

Verified result on 2026-05-16:

```json
{
  "ok": true,
  "message": "RootVPN WB video bytes OK",
  "frame_width": 320,
  "frame_height": 240,
  "max_payload_bytes": 115,
  "decode_attempts": 1
}
```

Current limitations:

- one frame = one small payload only;
- default lab secret is not production key management;
- no chunking, retransmit, ordering, congestion control, or SOCKS/VPN tunnel yet.

## WB Stream Multi-Frame Payload

For payloads larger than one frame, use the multi-frame stream probe. It splits payload into
encrypted/authenticated chunks, repeats frames as a carousel, and reassembles by `seq/total`.

```powershell
experiments/webrtc-gateway/.venv/Scripts/python experiments/webrtc-gateway/wbstream_livekit_frame_stream.py `
  https://stream.wb.ru/room/019e30d5-9b63-700e-8453-b514a5db7746 `
  --payload-bytes 1024
```

Verified result on 2026-05-16:

```json
{
  "ok": true,
  "payload_bytes": 1024,
  "encoded_frames": 9,
  "chunks_received": 9,
  "decode_attempts": 14,
  "elapsed_ms": 8003
}
```

This is still a one-way lab stream. It has duplicate tolerance via chunk de-duplication, but no
ACK/retry channel yet.

## WB Stream Video ACK Probe

The ACK probe publishes two video tracks:

- sender -> receiver: encrypted data chunks;
- receiver -> sender: encrypted ACK bitmap frames.

The sender stops retransmitting chunks after they are acknowledged.

```powershell
experiments/webrtc-gateway/.venv/Scripts/python experiments/webrtc-gateway/wbstream_livekit_frame_ack.py `
  https://stream.wb.ru/room/019e30d5-9b63-700e-8453-b514a5db7746 `
  --payload-bytes 1024
```

Verified result on 2026-05-16:

```json
{
  "ok": true,
  "payload_bytes": 1024,
  "encoded_frames": 9,
  "chunks_received": 9,
  "acked_chunks": 9,
  "data_frames_sent": 10,
  "ack_frames_sent": 5,
  "elapsed_ms": 13066
}
```

This confirms reverse media-frame signaling works. The next step is a real sliding window/retry
policy and repeated throughput runs, not a production tunnel.

## WB Stream Sliding Window Probe

The windowed probe sends only a bounded number of unacknowledged chunks and retransmits timed-out
chunks instead of looping the whole payload.

```powershell
experiments/webrtc-gateway/.venv/Scripts/python experiments/webrtc-gateway/wbstream_livekit_frame_window.py `
  https://stream.wb.ru/room/019e30d5-9b63-700e-8453-b514a5db7746 `
  --payload-bytes 2048 `
  --window-size 4 `
  --retry-timeout-sec 2.5
```

Verified result on 2026-05-16:

```json
{
  "ok": true,
  "payload_bytes": 2048,
  "encoded_frames": 18,
  "chunks_received": 18,
  "acked_chunks": 18,
  "data_frames_sent": 34,
  "retransmits": 16,
  "throughput_bps": 74.32
}
```

With `1024` bytes, the same settings completed with `0` retransmits and about `104` B/s.

The `tile2` codec encodes two bits per visual cell using four brightness levels. It is denser than
the default binary codec, but may be more sensitive to video compression artifacts:

```powershell
experiments/webrtc-gateway/.venv/Scripts/python experiments/webrtc-gateway/wbstream_livekit_frame_window.py `
  https://stream.wb.ru/room/019e30d5-9b63-700e-8453-b514a5db7746 `
  --payload-bytes 1024 `
  --window-size 4 `
  --retry-timeout-sec 2.5 `
  --codec tile2
```

Verified `tile2` result on 2026-05-16:

```json
{
  "ok": true,
  "payload_bytes": 1024,
  "max_payload_per_frame": 265,
  "encoded_frames": 4,
  "chunks_received": 4,
  "acked_chunks": 4,
  "data_frames_sent": 4,
  "retransmits": 0,
  "throughput_bps": 119.83,
  "codec": "tile2"
}
```

For fragile codecs or noisy carrier conditions, `--data-repeats N` sends each data chunk multiple
times before moving on. This increases frame cost but can reduce decode misses:

```powershell
experiments/webrtc-gateway/.venv/Scripts/python experiments/webrtc-gateway/wbstream_livekit_frame_window.py `
  https://stream.wb.ru/room/019e30d5-9b63-700e-8453-b514a5db7746 `
  --payload-bytes 512 `
  --codec tile2 `
  --data-repeats 2
```

The old WB room may expire or stop allowing guest connection details. If the probe fails with
`guests cannot create rooms`, open a fresh room manually and pass the new room URL.

## WB Stream Tuning Sweep

Run a bounded parameter sweep. Keep the grid small: every case opens live WB Stream sessions.

```powershell
experiments/webrtc-gateway/.venv/Scripts/python experiments/webrtc-gateway/wbstream_livekit_tuning_sweep.py `
  https://stream.wb.ru/room/019e30d5-9b63-700e-8453-b514a5db7746 `
  --payloads 1024 `
  --windows 2,4,6,8 `
  --retries 2.5 `
  --fps 8 `
  --ack-fps 4 `
  --codecs binary,tile2 `
  --data-repeats 1,2 `
  --repeats 3 `
  --json-out experiments/webrtc-gateway/last_tuning_sweep.json
```

Use `--repeats N` for noisy real-carrier measurements. The JSON report includes:

- `records`: every individual run;
- `aggregates`: per-case min/median/p95/max throughput, median elapsed time, and median retransmits;
- `best`: best single run;
- `best_aggregate`: best case by median throughput.

Use `--run-attempts 2` or higher when the provider occasionally fails before the media test starts
(`ClientConnectorError`, temporary WB endpoint hiccup). The report records `attempt_count` and
`transient_errors` per run.

For one-off field probes, `wbstream_livekit_frame_window.py` also supports `--connect-attempts N`.
This retries WB guest/token setup before the media tracks are created.

Window sweep on 2026-05-16:

| Payload | Window | Retry | FPS | ACK FPS | Throughput | Retransmits |
|---:|---:|---:|---:|---:|---:|---:|
| 1024 | 2 | 2.5s | 8 | 4 | 43.08 B/s | 2 |
| 1024 | 4 | 2.5s | 8 | 4 | 70.48 B/s | 0 |
| 1024 | 6 | 2.5s | 8 | 4 | 124.18 B/s | 0 |
| 1024 | 8 | 2.5s | 8 | 4 | 119.30 B/s | 0 |

FPS/ACK-FPS sweep right after that was worse and more variable (`39-54 B/s`), so treat one-off
measurements as noisy. Prefer repeated sweeps before drawing product conclusions.

Fresh-room check on 2026-05-17:

| Payload | Codec | Repeats | Median throughput | Retransmits | Note |
|---:|---|---:|---:|---:|---|
| 512 | binary | 1 | 63.25 B/s | 0 | `--run-attempts 2`, one run |
| 512 | binary | 2 | 61.65 B/s | 0 | repeat overhead did not help |
| 512 | tile2 | 1 | 65.69 B/s | 0 | best 512-byte one-off |
| 512 | tile2 | 2 | 44.90 B/s | 0 | slower from duplicate frame cost |
| 1024 | binary | 1 | 113.17 B/s | 0 | repeats=2 |
| 1024 | tile2 | 1 | 146.03 B/s | 0 | repeats=2; current best R&D baseline candidate |
| 2048 | tile2 | 1 | 242.54 B/s | 0 | window=4, repeats=2; best of windows 4/6/8 |
| 4096 | tile2 | 1 | 253.83 B/s | 1.0 median | window=4, repeats=2; provider setup needed second attempts |

Current lab baseline candidate:

```text
codec=tile2
data_repeats=1
window=4
retry_timeout_sec=2.5
fps=8
ack_fps=4
```

## Experimental Byte-Stream Framing

`stream_protocol.py` adds the first message format above raw video-frame payloads:

- `stream_id`: logical byte stream;
- `offset`: byte offset inside that stream;
- `fin`: marks the final segment;
- payload bytes.

It is still not a SOCKS/VPN tunnel. It is a small framing/reassembly layer that lets future probes
send continuous byte streams over the current `tile2` video carrier. Unit tests verify:

- encode/decode roundtrip;
- out-of-order reassembly;
- duplicate tolerance;
- gap waiting;
- conflict/overlap rejection;
- byte-stream packets carried inside `tile2` video frames.

Field probe shape:

```powershell
experiments/webrtc-gateway/.venv/Scripts/python experiments/webrtc-gateway/wbstream_livekit_frame_window.py `
  https://stream.wb.ru/room/<room-id> `
  --payload-bytes 2048 `
  --codec tile2 `
  --window-size 4 `
  --stream-mode `
  --stream-id 77 `
  --connect-attempts 2
```

If WB returns `HTTP 403: guests cannot create rooms`, the manual room is no longer usable for guest
field measurements. Create a fresh room and retry; this is a provider/session setup issue, not a
stream framing failure.

Fresh-room stream-mode validation on 2026-05-17:

| Payload | Stream segments | Retransmits | Throughput |
|---:|---:|---:|---:|
| 2048 | 9 | 0 | 220.67 B/s |
| 4096 | 17 | 0 | 406.03 B/s |
| 8192 | 34 | 0 | 608.41 B/s |
| 16384 | 67 | 0 | 1098.37 B/s |

This validates the path:

```text
byte stream -> stream_protocol packets -> tile2 video frames -> WB media track -> reassembled bytes
```

The next R&D step is a local SOCKS-like prototype that uses this stream framing as its payload
primitive, still without touching production users, payments, or Marzban.

## SOCKS-Like Proxy Protocol Skeleton

The first local-proxy pieces are intentionally protocol-only:

- `socks5_proto.py`: parses no-auth SOCKS5 greeting and CONNECT requests for IPv4/domain/IPv6;
- `proxy_messages.py`: encodes internal `OPEN`, `DATA`, `CLOSE`, and `ERROR` messages;
- tests verify proxy messages carried through `stream_protocol.py` and `tile2` video frames.

Current planned stack:

```text
local SOCKS5 CONNECT
-> proxy OPEN/DATA/CLOSE messages
-> stream_protocol packets
-> tile2 video frames
-> WB media carrier
```

There is still no always-on local listener and no remote egress bridge in this step. Those should be
added only after the protocol pieces stay small and testable.

`local_bridge.py` adds the first in-process harness:

```text
SOCKS greeting/request bytes
-> socks5_proto parser
-> proxy OPEN/DATA/CLOSE messages
-> InMemoryProxyCarrier
-> FakeProxyEgress
-> proxy DATA/CLOSE response
-> SOCKS success reply + response bytes
```

This still does not open a listening port and does not connect to external targets. It validates the
control/data message contract before a real local listener or WB carrier is attached.

`local_socks_server.py` adds the next localhost-only harness:

```text
127.0.0.1 SOCKS5 client
-> LocalSocksServer
-> proxy OPEN/DATA/CLOSE messages
-> InMemoryProxyCarrier
-> FakeProxyEgress
-> response bytes back to the SOCKS client
```

Safety boundaries:

- binds only to loopback (`127.0.0.1`, `localhost`, or `::1`);
- does not dial external targets;
- handles one bounded payload per connection;
- uses fake egress only.

This is the client-facing shape of the future bridge without becoming a public proxy.

`proxy_packet_bundle.py` and `wbstream_proxy_carrier.py` add the first WB-facing adapter boundary:

```text
proxy packets
-> proxy packet bundle
-> wbstream_video_window_probe(payload=<bundle>, stream_mode=True)
-> tile2 video frames over WB Stream
```

`WBStreamProxyCarrier.deliver_packets()` is delivery-only. It proves that proxy packets can be
carried over the current WB stream-mode path, but `exchange()` deliberately raises until a remote
egress endpoint exists to decode bundles, apply route policy, and return response bundles. In other
words: the carrier is getting real; the tunnel is still lab-only.
