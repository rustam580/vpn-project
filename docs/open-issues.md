# Open Issues

Last updated: 2026-05-11 (afternoon)

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
- Problem: background audit now detects drift, but admins still need safe guided actions to resolve ambiguous cases without manual SQL/Marzban edits.
- Next action: add safe resolution actions for "accept Marzban", "accept DB", "ignore known test/stale user", and event-log every resolution.
- Owner: ops/dev

5. P2 - Finish `bot_runtime.py` decomposition
- Status: in_progress
- Problem: `src/vpnbot/bot_runtime.py` is now down to ~740 lines (from original 1411). All inline `@router.message` handlers and the three biggest closures (`bind_web_order_to_user`, `replace_device_slot`, `list_replaceable_devices`) are extracted. What remains: smaller inline closures (`track_event`, `start_deploy`, `handle_grant_perm`, `guard_*`, `get_bot_username`) still live in `build_router`, and `bot_handlers_callbacks_user.py` (38 KB) is still one large module.
- Next action: extract remaining closures to `runtime_helpers.py` (use factories where mutable state like rate limiters or `bot_username_cache` is involved); split `bot_handlers_callbacks_user.py` by domain (subscription / devices / payments / referrals); then enable strict mypy on `bot_runtime.py` and `handlers/*`.
- Owner: dev

## Recently Closed
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
