from __future__ import annotations

import argparse
import asyncio
import json
import re
import sqlite3
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import Settings  # noqa: E402
from src.vpnbot.services.bot_marzban import MarzbanClient  # noqa: E402


@dataclass(frozen=True)
class DbRef:
    source: str
    telegram_id: int | None
    device_id: int | None
    username: str
    detail: str


def _connect_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (name,),
    ).fetchone()
    return row is not None


def _rows(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
    return list(conn.execute(sql, params).fetchall())


def _build_device_username(telegram_id: int, device_id: int) -> str:
    if device_id <= 1:
        return f"tg_{telegram_id}"
    return f"tg_{telegram_id}_d{device_id}"


def _is_expected_device_username(telegram_id: int, device_id: int, username: str) -> bool:
    expected = _build_device_username(telegram_id, device_id)
    return username == expected or username.startswith(f"{expected}_r")


def _fmt_ts(value: Any) -> str:
    try:
        ts = int(value or 0)
    except (TypeError, ValueError):
        return "-"
    if ts <= 0:
        return "-"
    return time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(ts))


def _fmt_expire(user: dict[str, Any] | None) -> str:
    if not user:
        return "-"
    try:
        expire = int(user.get("expire") or 0)
    except (TypeError, ValueError):
        return "-"
    if expire <= 0:
        return "no_expire"
    left_days = round((expire - int(time.time())) / 86400, 2)
    return f"{_fmt_ts(expire)} ({left_days}d)"


def _as_users(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("users", "items", "results", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


async def _list_marzban_users(marzban: Any, *, limit: int) -> tuple[dict[str, dict[str, Any]], str | None]:
    users_by_name: dict[str, dict[str, Any]] = {}
    endpoint_error: str | None = None
    for path in ("/api/users", "/api/users/"):
        users_by_name.clear()
        offset = 0
        try:
            while True:
                payload = await marzban.req(
                    "GET",
                    path,
                    allow_404=True,
                    params={"offset": offset, "limit": limit},
                )
                if payload is None:
                    endpoint_error = f"{path}: 404"
                    break
                users = _as_users(payload)
                if not users:
                    endpoint_error = f"{path}: empty_or_unknown_shape"
                    break
                for user in users:
                    username = str(user.get("username") or "").strip()
                    if username:
                        users_by_name[username] = user
                total = payload.get("total") if isinstance(payload, dict) else None
                if isinstance(total, int) and offset + len(users) >= total:
                    return dict(users_by_name), None
                if len(users) < limit:
                    return dict(users_by_name), None
                offset += limit
        except Exception as exc:
            endpoint_error = f"{path}: {type(exc).__name__}: {exc}"
            continue
    return {}, endpoint_error or "unable_to_list_users"


def _collect_db_refs(conn: sqlite3.Connection) -> tuple[list[DbRef], list[str], list[str]]:
    refs: list[DbRef] = []
    web_without_access: list[str] = []
    non_standard_devices: list[str] = []

    for row in _rows(conn, "SELECT telegram_id, marzban_username FROM users ORDER BY telegram_id"):
        username = str(row["marzban_username"] or "").strip()
        if username:
            refs.append(
                DbRef(
                    source="users",
                    telegram_id=int(row["telegram_id"]),
                    device_id=1,
                    username=username,
                    detail=f"tg={row['telegram_id']}",
                )
            )

    for row in _rows(
        conn,
        """
        SELECT telegram_id, device_id, marzban_username, COALESCE(device_name, '') AS device_name
        FROM devices
        ORDER BY telegram_id, device_id
        """,
    ):
        username = str(row["marzban_username"] or "").strip()
        if not username:
            continue
        telegram_id = int(row["telegram_id"])
        device_id = int(row["device_id"])
        refs.append(
            DbRef(
                source="devices",
                telegram_id=telegram_id,
                device_id=device_id,
                username=username,
                detail=f"tg={telegram_id} slot={device_id} name={row['device_name']}",
            )
        )
        if not _is_expected_device_username(telegram_id, device_id, username):
            non_standard_devices.append(
                f"tg={telegram_id} slot={device_id} db_username={username} expected={_build_device_username(telegram_id, device_id)}"
            )

    if _table_exists(conn, "web_orders"):
        for row in _rows(
            conn,
            """
            SELECT order_id, status, plan_key, marzban_username, updated_at
            FROM web_orders
            ORDER BY updated_at DESC
            """,
        ):
            username = str(row["marzban_username"] or "").strip()
            status = str(row["status"] or "")
            if status == "paid_applied" and not username:
                web_without_access.append(
                    f"order={row['order_id']} plan={row['plan_key']} updated={_fmt_ts(row['updated_at'])}"
                )
            if username:
                refs.append(
                    DbRef(
                        source="web_orders",
                        telegram_id=None,
                        device_id=None,
                        username=username,
                        detail=f"order={row['order_id']} status={status} plan={row['plan_key']}",
                    )
                )

    return refs, web_without_access, non_standard_devices


def _print_section(title: str, items: list[str], *, limit: int) -> None:
    print(f"\n== {title}: {len(items)} ==")
    for item in items[:limit]:
        print(item)
    if len(items) > limit:
        print(f"... {len(items) - limit} more")


async def main() -> int:
    parser = argparse.ArgumentParser(description="Audit bot SQLite references against Marzban users.")
    parser.add_argument("--limit", type=int, default=100, help="Marzban API page size.")
    parser.add_argument("--show", type=int, default=80, help="Max rows per report section.")
    parser.add_argument("--json", action="store_true", help="Emit JSON report.")
    args = parser.parse_args()

    settings = Settings.load()
    conn = _connect_db(settings.db_path)
    marzban = MarzbanClient(cast(Any, settings))
    try:
        refs, web_without_access, non_standard_devices = _collect_db_refs(conn)
        db_by_username: dict[str, list[DbRef]] = {}
        for ref in refs:
            db_by_username.setdefault(ref.username, []).append(ref)

        mz_users, list_error = await _list_marzban_users(marzban, limit=max(1, args.limit))
        if not mz_users:
            for username in sorted(db_by_username):
                user = await marzban.get_user(username)
                if user:
                    mz_users[username] = user

        missing_in_marzban = [
            f"{username} <- " + "; ".join(ref.detail for ref in refs_for_user)
            for username, refs_for_user in sorted(db_by_username.items())
            if username not in mz_users
        ]

        unknown_in_db = [
            f"{username} status={user.get('status')} expire={_fmt_expire(user)}"
            for username, user in sorted(mz_users.items())
            if username not in db_by_username and re.match(r"^(tg_|web_)", username)
        ]

        shared_db_refs = [
            f"{username} <- " + "; ".join(f"{ref.source}:{ref.detail}" for ref in refs_for_user)
            for username, refs_for_user in sorted(db_by_username.items())
            if len(refs_for_user) > 2
        ]

        db_known_summary = [
            f"{username} status={mz_users.get(username, {}).get('status', 'missing')} "
            f"expire={_fmt_expire(mz_users.get(username))} refs={len(refs_for_user)}"
            for username, refs_for_user in sorted(db_by_username.items())
        ]

        report = {
            "db_refs": len(refs),
            "db_unique_usernames": len(db_by_username),
            "marzban_users_seen": len(mz_users),
            "marzban_list_error": list_error,
            "missing_in_marzban": missing_in_marzban,
            "unknown_in_db": unknown_in_db,
            "web_orders_without_access": web_without_access,
            "non_standard_device_names": non_standard_devices,
            "shared_db_refs": shared_db_refs,
            "db_known_summary": db_known_summary,
        }

        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0

        print("RootVPN Marzban/DB sync audit")
        print(f"DB path: {settings.db_path}")
        print(f"DB refs: {report['db_refs']}")
        print(f"DB unique usernames: {report['db_unique_usernames']}")
        print(f"Marzban users seen: {report['marzban_users_seen']}")
        if list_error:
            print(f"WARN: Marzban full user list unavailable ({list_error}); checked DB usernames individually.")

        _print_section("missing_in_marzban", missing_in_marzban, limit=args.show)
        _print_section("unknown_in_db_tg_or_web", unknown_in_db, limit=args.show)
        _print_section("web_orders_without_access", web_without_access, limit=args.show)
        _print_section("non_standard_device_names", non_standard_devices, limit=args.show)
        _print_section("shared_db_refs", shared_db_refs, limit=args.show)
        _print_section("db_known_summary", db_known_summary, limit=args.show)

        has_findings = any(
            (
                missing_in_marzban,
                unknown_in_db,
                web_without_access,
                non_standard_devices,
            )
        )
        print("\nResult:", "CHECK_FINDINGS" if has_findings else "OK")
        return 1 if missing_in_marzban or web_without_access else 0
    finally:
        conn.close()
        await marzban.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
