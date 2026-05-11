from __future__ import annotations

import re
import sqlite3
import time
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class DbRef:
    source: str
    telegram_id: int | None
    device_id: int | None
    username: str
    detail: str


@dataclass(frozen=True)
class SyncAuditReport:
    db_refs: int
    db_unique_usernames: int
    marzban_users_seen: int
    marzban_list_error: str | None
    missing_in_marzban: list[str]
    unknown_in_db: list[str]
    web_orders_without_access: list[str]
    non_standard_device_names: list[str]
    shared_db_refs: list[str]
    db_known_summary: list[str]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def has_critical_findings(self) -> bool:
        return bool(self.missing_in_marzban or self.web_orders_without_access)

    def has_findings(self, *, include_noncritical: bool = True) -> bool:
        if self.has_critical_findings():
            return True
        if not include_noncritical:
            return False
        return bool(
            self.unknown_in_db
            or self.non_standard_device_names
            or self.shared_db_refs
            or self.marzban_list_error
        )

    def summary_text(self, *, show: int = 8, include_noncritical: bool = True) -> str:
        show = max(1, int(show))
        lines = [
            "🧭 Marzban/DB sync audit",
            f"DB refs: {self.db_refs}",
            f"DB usernames: {self.db_unique_usernames}",
            f"Marzban users seen: {self.marzban_users_seen}",
        ]
        if self.marzban_list_error:
            lines.append(f"WARN: Marzban list fallback: {self.marzban_list_error}")
        sections = [
            ("missing_in_marzban", self.missing_in_marzban, True),
            ("web_orders_without_access", self.web_orders_without_access, True),
            ("unknown_in_db_tg_or_web", self.unknown_in_db, include_noncritical),
            ("non_standard_device_names", self.non_standard_device_names, include_noncritical),
            ("shared_db_refs", self.shared_db_refs, include_noncritical),
        ]
        for title, items, enabled in sections:
            if not enabled or not items:
                continue
            lines.append(f"\n{title}: {len(items)}")
            lines.extend(f"- {item}" for item in items[:show])
            if len(items) > show:
                lines.append(f"... and {len(items) - show} more")
        if not self.has_findings(include_noncritical=include_noncritical):
            lines.append("\nResult: OK")
        return "\n".join(lines)


def build_device_username(telegram_id: int, device_id: int) -> str:
    if device_id <= 1:
        return f"tg_{telegram_id}"
    return f"tg_{telegram_id}_d{device_id}"


def is_expected_device_username(telegram_id: int, device_id: int, username: str) -> bool:
    expected = build_device_username(telegram_id, device_id)
    return username == expected or username.startswith(f"{expected}_r")


def fmt_ts(value: Any) -> str:
    try:
        ts = int(value or 0)
    except (TypeError, ValueError):
        return "-"
    if ts <= 0:
        return "-"
    return time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(ts))


def fmt_expire(user: dict[str, Any] | None) -> str:
    if not user:
        return "-"
    try:
        expire = int(user.get("expire") or 0)
    except (TypeError, ValueError):
        return "-"
    if expire <= 0:
        return "no_expire"
    left_days = round((expire - int(time.time())) / 86400, 2)
    return f"{fmt_ts(expire)} ({left_days}d)"


def as_users(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("users", "items", "results", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


async def list_marzban_users(marzban: Any, *, limit: int) -> tuple[dict[str, dict[str, Any]], str | None]:
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
                users = as_users(payload)
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


def _row_get(row: Any, key: str) -> Any:
    if isinstance(row, dict):
        return row.get(key)
    return row[key]


async def collect_db_refs(repo: Any) -> tuple[list[DbRef], list[str], list[str]]:
    if repo.conn is None:
        raise RuntimeError("Repo is not open")

    refs: list[DbRef] = []
    web_without_access: list[str] = []
    non_standard_devices: list[str] = []

    cursor = await repo.conn.execute("SELECT telegram_id, marzban_username FROM users ORDER BY telegram_id")
    user_rows = await cursor.fetchall()
    await cursor.close()
    for row in user_rows:
        username = str(_row_get(row, "marzban_username") or "").strip()
        if username:
            tg_id = int(_row_get(row, "telegram_id"))
            refs.append(DbRef("users", tg_id, 1, username, f"tg={tg_id}"))

    cursor = await repo.conn.execute(
        """
        SELECT telegram_id, device_id, marzban_username, COALESCE(device_name, '') AS device_name
        FROM devices
        ORDER BY telegram_id, device_id
        """
    )
    device_rows = await cursor.fetchall()
    await cursor.close()
    for row in device_rows:
        username = str(_row_get(row, "marzban_username") or "").strip()
        if not username:
            continue
        tg_id = int(_row_get(row, "telegram_id"))
        device_id = int(_row_get(row, "device_id"))
        detail = f"tg={tg_id} slot={device_id} name={_row_get(row, 'device_name')}"
        refs.append(DbRef("devices", tg_id, device_id, username, detail))
        if not is_expected_device_username(tg_id, device_id, username):
            non_standard_devices.append(
                f"tg={tg_id} slot={device_id} db_username={username} expected={build_device_username(tg_id, device_id)}"
            )

    cursor = await repo.conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'web_orders'"
    )
    web_orders_exists = await cursor.fetchone()
    await cursor.close()
    if web_orders_exists:
        cursor = await repo.conn.execute(
            """
            SELECT order_id, status, plan_key, marzban_username, updated_at
            FROM web_orders
            ORDER BY updated_at DESC
            """
        )
        web_rows = await cursor.fetchall()
        await cursor.close()
        for row in web_rows:
            username = str(_row_get(row, "marzban_username") or "").strip()
            status = str(_row_get(row, "status") or "")
            if status == "paid_applied" and not username:
                web_without_access.append(
                    f"order={_row_get(row, 'order_id')} plan={_row_get(row, 'plan_key')} "
                    f"updated={fmt_ts(_row_get(row, 'updated_at'))}"
                )
            if username:
                detail = (
                    f"order={_row_get(row, 'order_id')} status={status} "
                    f"plan={_row_get(row, 'plan_key')}"
                )
                refs.append(DbRef("web_orders", None, None, username, detail))

    return refs, web_without_access, non_standard_devices


def build_audit_report(
    *,
    refs: list[DbRef],
    web_without_access: list[str],
    non_standard_devices: list[str],
    mz_users: dict[str, dict[str, Any]],
    list_error: str | None,
) -> SyncAuditReport:
    db_by_username: dict[str, list[DbRef]] = {}
    for ref in refs:
        db_by_username.setdefault(ref.username, []).append(ref)

    missing_in_marzban = [
        f"{username} <- " + "; ".join(ref.detail for ref in refs_for_user)
        for username, refs_for_user in sorted(db_by_username.items())
        if username not in mz_users
    ]

    unknown_in_db = [
        f"{username} status={user.get('status')} expire={fmt_expire(user)}"
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
        f"expire={fmt_expire(mz_users.get(username))} refs={len(refs_for_user)}"
        for username, refs_for_user in sorted(db_by_username.items())
    ]

    return SyncAuditReport(
        db_refs=len(refs),
        db_unique_usernames=len(db_by_username),
        marzban_users_seen=len(mz_users),
        marzban_list_error=list_error,
        missing_in_marzban=missing_in_marzban,
        unknown_in_db=unknown_in_db,
        web_orders_without_access=web_without_access,
        non_standard_device_names=non_standard_devices,
        shared_db_refs=shared_db_refs,
        db_known_summary=db_known_summary,
    )


async def audit_marzban_sync(repo: Any, marzban: Any, *, limit: int = 100) -> SyncAuditReport:
    refs, web_without_access, non_standard_devices = await collect_db_refs(repo)
    db_usernames = sorted({ref.username for ref in refs})
    mz_users, list_error = await list_marzban_users(marzban, limit=max(1, int(limit)))
    if not mz_users:
        for username in db_usernames:
            user = await marzban.get_user(username)
            if user:
                mz_users[username] = user
    return build_audit_report(
        refs=refs,
        web_without_access=web_without_access,
        non_standard_devices=non_standard_devices,
        mz_users=mz_users,
        list_error=list_error,
    )


def connect_sqlite(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn
