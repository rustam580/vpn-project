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
