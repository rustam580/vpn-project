# WebRTC Gateway Experiment

Local Phase 1 proof of concept for RootVPN WebRTC/DataChannel transport research.

This is intentionally isolated from production bot, payments, Marzban, and website API.

## What It Does

- Serves a browser test page on `http://127.0.0.1:8090`.
- Browser creates a WebRTC `RTCDataChannel`.
- Python `aiortc` gateway accepts the offer and returns an answer.
- DataChannel supports:
  - `ping` -> `pong`
  - any other text -> `echo:<message>`
- `/metrics` exposes simple JSON counters.

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
