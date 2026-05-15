# WB Stream Carrier Notes

This file records implementation notes from `openlibrecommunity/olcrtc` for the RootVPN WebRTC R&D track.

Source files reviewed:

- `internal/provider/wbstream/api.go`
- `internal/provider/wbstream/peer.go`
- `internal/provider/wbstream/provider.go`
- `internal/carrier/carrier.go`
- `internal/transport/datachannel/transport.go`
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
