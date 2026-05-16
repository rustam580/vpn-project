# Open Issues

Last updated: 2026-05-16

## Priority Legend
- P0: blocks sales or legal-safe launch
- P1: important reliability/ops
- P2: optimization/quality

## Active Items

1. P1 - Deploy smoke checks rollout on production
- Status: in_progress
- Problem: smoke script is implemented in repo, but must be installed/enabled consistently on each production host.
- Next action: install `/usr/local/sbin/vpn-ops-smoke`, verify `vpn-ops-deploy` runs it and captures failures in deploy report.
- Owner: ops/dev

2. P2 - Renewal worker scaling
- Status: pending
- Problem: periodic full scan of users/devices can become expensive under growth.
- Next action: batch processing or next-check schedule index.
- Owner: dev

3. P1 - Xray/Marzban quality monitoring
- Status: in_progress
- Problem: a lightweight parser and admin command now exist, but production log path/permissions and alert thresholds still need validation on the bot/VPN host.
- Next action: deploy, verify `/xray_errors 15`, then set `XRAY_ERROR_LOG_PATH` if the default path differs. Enable `XRAY_QUALITY_MONITOR_ENABLED=1` only after threshold tuning.
- Owner: ops/dev

4. P1 - Safe Marzban/DB drift resolution
- Status: in_progress
- Problem: guided admin actions are now implemented for critical drift, but need production validation on a real finding before calling this fully closed.
- Next action: deploy, run `🧭 Marzban/DB аудит`, validate that critical findings show action cards, and use only on a known-safe stale/test finding first.
- Owner: ops/dev

5. P2 - Finish `bot_runtime.py` decomposition
- Status: in_progress
- Problem: `src/vpnbot/bot_runtime.py` is now ~761 lines (from original 1411). All inline `@router.message` handlers and the three biggest closures (`bind_web_order_to_user`, `replace_device_slot`, `list_replaceable_devices`) are extracted. User callback handlers are now split by domain. What remains: smaller inline closures (`track_event`, `start_deploy`, `handle_grant_perm`, `guard_*`, `get_bot_username`) still live in `build_router`, and the payment callback slice is still the largest user callback module.
- Next action: extract remaining closures to `runtime_helpers.py` (use factories where mutable state like rate limiters or `bot_username_cache` is involved); optionally split `bot_handlers_callbacks_user_payments.py` into plan purchase / device purchase / payment check; then enable stricter mypy on `bot_runtime.py` and handlers.
- Owner: dev

6. P2 - Refresh stale docs
- Status: pending
- Problem: `README.md`, `docs/product-decisions.md`, `docs/release-readiness.md`, `docs/integrations.md`, and `docs/runbook.md` are older than the current product architecture. They omit or under-describe the website checkout API, two-host deploy, drift-resolution actions, Xray diagnostics, and recent router-registration regressions.
- Next action: either consolidate into a single current operator guide or mark old docs as historical/background with links to `STATE.md`, `assistant-context.md`, `infra-state.md`, and `website.md`.
- Owner: dev/ops

7. P2 - WebRTC fallback transport R&D
- Status: in_progress
- Problem: WebRTC/DataChannel may be useful as a reserve transport, but whitelist-resilience requires a carrier layer through already-allowed video/conference services, not only a self-hosted gateway. WB Stream probing works for guest join/token retrieval, but guest LiveKit data packets are blocked in the tested room (`can_publish_data=false`). Synthetic video-track delivery, one-frame encrypted byte delivery, multi-frame one-way delivery, and reverse video ACK work, so the likely WB route is media-frame encoding. This adds separate client/signaling/gateway/carrier work plus unclear carrier fragility, TURN cost, stability, support, and legal/product risks.
- Next action: add sliding-window/retry policy and throughput/latency measurement over repeated longer runs; keep isolated from payments, Marzban, and public tariffs until closed beta criteria in `docs/webrtc-transport-research.md` are met.
- Owner: dev/research

## Recently Closed
- Added WB Stream R&D probes under `experiments/webrtc-gateway/`: guest room token retrieval, LiveKit data-packet ping probe, and synthetic video-track carrier probe. Tested room `019e30d5-9b63-700e-8453-b514a5db7746`: data packets are blocked for guests, video carrier works.
- Added `video_frame_codec.py` and `wbstream_livekit_frame_message.py`: encrypted/authenticated one-frame payload delivery over WB Stream video works in the tested room (`RootVPN WB video bytes OK`, 320x240 frame, max payload 115 bytes).
- Added multi-frame WB video payload stream probe: `1024` bytes delivered in 9 chunks over the tested room, one-way carousel mode with duplicate-tolerant reassembly.
- Added reverse video ACK probe: `1024` bytes delivered with 9/9 chunks acknowledged over a second WB Stream video track.
- Added carrier interface to the WebRTC PoC and kept `direct` as the baseline carrier adapter. Captured WB Stream/LiveKit carrier notes in `experiments/webrtc-gateway/WBSTREAM_NOTES.md`.
- Reviewed olcRTC architecture notes and captured applicable lessons in `docs/webrtc-transport-research.md`: layered carrier/transport design, app-level encryption, smux-style multiplexing, payload chunking, SOCKS5 boundary, reconnect/backpressure, and Android socket-protection caveats.
- Added isolated local WebRTC/DataChannel echo PoC under `experiments/webrtc-gateway/`: browser test page, Python `aiortc` gateway, `/offer`, `/metrics`, and local verification (`ping` -> `pong`, custom echo).
- Documented WebRTC/DataChannel fallback transport as an R&D-only track with MVP phases, promotion criteria, kill criteria, and repository boundaries in `docs/webrtc-transport-research.md`.
- Added read-only inline support buttons to web-order admin cards: check provider payment status by order ID (`wo:c:*`), open linked customer lookup, and run Marzban/DB drift audit. Payment check does not mutate DB or Marzban.
- Added web-order support cards for `/user <order_id|email|web_username>` plus direct `/payment_issues` action hints (`/user <order_id>`, `/check <provider> <external_id>`, `/sync_audit`). This reduces manual SQL/Marzban work for website buyers who did not bind Telegram.
- Added unified customer profile renderer for admin `/user <telegram_id>`: Telegram identity, primary Marzban profile, all device slots, recent bot payments, related web orders, and drift warnings now come from test-covered `src/vpnbot/customer_profile.py`.
- Added admin payment/access issue report: `/payment_issues` command and `💳 Проблемные оплаты` admin button. The report highlights stale `processing` payments, old unfinished payments, paid web orders without access, and paid web orders whose Marzban user is missing.
- Split `bot_handlers_callbacks_user.py` by domain into quick/device/config/payment modules plus shared `UserCallbackDeps`; facade is now ~16 lines, with regression tests for callback prefix placement.
- Fixed post-refactor router regressions: registered extracted user runtime handlers in `build_router`, moved catch-all fallback after specific user/admin message handlers, and added regression tests for handler wiring, fallback order, and reply-keyboard coverage.
- Added guided Marzban/DB drift resolution: structured `DriftFinding` objects, action keyboards on admin sync audit, safe resolver functions (`recreate`, `drop_db_ref`, `retry_web_order`, `ignore`), ignored finding suppression, and resolver/keyboards unit tests.
- Refactor of `build_router` (commits 8726e85, 77a57d8, 11d716c): extracted 25 inline handlers to `src/vpnbot/handlers/bot_handlers_user_runtime.py` and `bot_handlers_admin_runtime.py`; extracted 3 large pure helpers (`bind_web_order_to_user`, `replace_device_slot`, `list_replaceable_devices`) to `src/vpnbot/runtime_helpers.py` with 10 new unit tests in `tests/test_runtime_helpers.py`. `bot_runtime.py`: 1411 -> 742 lines (-47%). pytest: 72 -> 82 passing.
- Added lightweight Xray quality tooling: `/xray_errors [minutes]`, `📡 Xray ошибки` admin button, reusable `xray_quality` parser, and disabled-by-default worker alert.
- Added manual Marzban/DB audit in the Telegram admin cabinet: `/sync_audit` command and `🧭 Marzban/DB аудит` button.
- Added reusable Marzban/DB sync audit module and background worker. It alerts admins about critical drift (`missing_in_marzban`, paid web orders without access) and can optionally include noncritical drift.
- Corrected infrastructure and website deployment docs for the two-host layout (`205.196.81.194` site-host with `/var/www/rootvpn`, `77.110.125.105` bot/API/VPN host).
- Expanded admin user lookup from Telegram ID only to Telegram ID, web order ID, external payment ID, contact/email, and Marzban username.
- Added explicit startup/disabled logs for payment, renewal, migration, and daily ops background workers to make deploy smoke checks easier.
- Removed raw `direct_links` from public website order delivery payloads; website API now exposes subscription URL(s) only.
- Removed 18 legacy shim files in repo root (`bot_access.py`, `bot_formatters.py`, `bot_handlers_*.py`, `bot_keyboards.py`, `bot_marzban.py`, `bot_network.py`, `bot_ops.py`, `bot_rate_limit.py`, `bot_repo.py`, `bot_router_helpers.py`, `bot_runtime.py`, `bot_workers.py`, `payment_flow.py`, `payments_service.py`); all production and test imports now use canonical `src.vpnbot.*` paths.
- Audited silent `except Exception:` in critical paths: `bot_repo.py` JSON ops narrowed to `(TypeError, ValueError)` with `logging.warning`; `bot_access.py` already correct.
- P0+P1 audit closed: fixed 2× `RUF006` dangling tasks via `src/vpnbot/background_tasks.spawn`; auto-removed 42 unused imports; unified `BYTES_IN_GB` via direct import from `bot_formatters`; tightened `pyproject.toml` ruff (`F401`, `F811`, `F841`, `RUF006`).
- Fixed UTF-8 mojibake in `README.md`, `docs/website.md`, and `.env.example` (PLANS_JSON example).
- Database migration framework added:
  - `schema_version` table
  - ordered SQL migrations in `db/migrations/`
  - automatic migration runner in `Repo.open()`
- CI now compiles/lints/tests the full Python project scope.
- Local checks (`Makefile`, `scripts/check.sh`, `scripts/check.ps1`) aligned with CI scope.
- Deploy syntax check switched from `bot.py` only to full project compile pass.
- `bot.py` decomposition started and key domains extracted to modules.
- Critical worker alerts delivered to admin chat with cooldown:
  - `ADMIN_ALERTS_ENABLED`
  - `ADMIN_ALERT_COOLDOWN_SEC`
- Fixed: `device_add` now gives new slot its own term (does not incorrectly sync to primary slot expiry).
- Fixed: website API `HTTP 500` on paid order status (bad `extract_subscription_links` call signature).
- Fixed: web order bind flow in Telegram start payload (`webbind_*`) is active.
- Added: `deploy/vpn-ops-smoke.sh` for post-deploy checks (services + local/public health endpoints).
- Closed: published public legal pages (`terms`, `privacy`, `refund`, `autorenew`) and linked them in site navigation.
- Closed: added frontend integrity tests for required checkout DOM ids and UTF-8 text safety in website files.
- Closed: added weekly website->Telegram bind conversion metric in `/admin_stats` based on `web_order_paid_applied` and `web_order_bound`.
