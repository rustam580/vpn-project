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
