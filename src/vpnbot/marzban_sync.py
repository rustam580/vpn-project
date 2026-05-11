from __future__ import annotations

import re
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from typing import Any


WEB_ORDER_ACCESS_STATUSES = frozenset({"paid_applied"})
DRIFT_IGNORED_EVENT_TYPE = "drift_ignored"

# DriftFinding.kind constants.
KIND_MISSING_IN_MARZBAN = "missing_in_marzban"
KIND_WEB_ORDER_NO_ACCESS = "web_order_no_access"
KIND_UNKNOWN_IN_DB = "unknown_in_db"
KIND_NON_STANDARD_DEVICE = "non_standard_device"

# Short prefixes for finding_id (kept tiny to fit Telegram's 64-byte callback_data).
_KIND_TO_PREFIX = {
    KIND_MISSING_IN_MARZBAN: "m",
    KIND_WEB_ORDER_NO_ACCESS: "w",
    KIND_UNKNOWN_IN_DB: "u",
    KIND_NON_STANDARD_DEVICE: "n",
}
_PREFIX_TO_KIND = {prefix: kind for kind, prefix in _KIND_TO_PREFIX.items()}


def kind_prefix(kind: str) -> str:
    """Short, stable single-letter prefix used in finding_id and callback_data."""
    return _KIND_TO_PREFIX.get(kind, "x")


def prefix_to_kind(prefix: str) -> str | None:
    return _PREFIX_TO_KIND.get(prefix)


@dataclass(frozen=True)
class DbRef:
    source: str
    telegram_id: int | None
    device_id: int | None
    username: str
    detail: str


@dataclass(frozen=True)
class DriftFinding:
    """Structured drift finding suitable for admin-driven resolution.

    `finding_id` is short and stable (no timestamps) so the same drift produces
    the same id across audit runs, which lets us link UI actions and ignore-records.
    `payload` carries everything a resolver needs (username, list of DB refs,
    web order id, etc.).
    """

    kind: str
    finding_id: str
    summary: str
    payload: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "finding_id": self.finding_id,
            "summary": self.summary,
            "payload": dict(self.payload),
        }


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
    findings: list[DriftFinding] = field(default_factory=list)
    ignored_finding_ids: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["findings"] = [finding.as_dict() for finding in self.findings]
        return data

    def critical_findings(self) -> list[DriftFinding]:
        return [
            finding
            for finding in self.findings
            if finding.kind in {KIND_MISSING_IN_MARZBAN, KIND_WEB_ORDER_NO_ACCESS}
        ]

    def find_by_id(self, finding_id: str) -> DriftFinding | None:
        for finding in self.findings:
            if finding.finding_id == finding_id:
                return finding
        return None

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


async def collect_db_refs(
    repo: Any,
) -> tuple[list[DbRef], list[str], list[str], list[dict[str, Any]]]:
    if repo.conn is None:
        raise RuntimeError("Repo is not open")

    refs: list[DbRef] = []
    web_without_access: list[str] = []
    web_without_access_payload: list[dict[str, Any]] = []
    non_standard_devices: list[str] = []
    web_rows: list[Any] = []
    active_web_usernames: set[str] = set()

    cursor = await repo.conn.execute("SELECT telegram_id, marzban_username FROM users ORDER BY telegram_id")
    user_rows = await cursor.fetchall()
    await cursor.close()
    for row in user_rows:
        username = str(_row_get(row, "marzban_username") or "").strip()
        if username:
            tg_id = int(_row_get(row, "telegram_id"))
            refs.append(DbRef("users", tg_id, 1, username, f"tg={tg_id}"))

    cursor = await repo.conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'web_orders'"
    )
    web_orders_exists = await cursor.fetchone()
    await cursor.close()
    if web_orders_exists:
        cursor = await repo.conn.execute(
            """
            SELECT order_id, status, plan_key, days, gb, marzban_username, updated_at
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
                web_without_access_payload.append({
                    "order_id": str(_row_get(row, "order_id") or ""),
                    "plan_key": str(_row_get(row, "plan_key") or ""),
                    "days": int(_row_get(row, "days") or 0),
                    "gb": int(_row_get(row, "gb") or 0),
                    "updated_at_text": fmt_ts(_row_get(row, "updated_at")),
                })
            if username and status in WEB_ORDER_ACCESS_STATUSES:
                active_web_usernames.add(username)

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
        web_order_bound_to_tg = username.startswith("web_") and username in active_web_usernames
        if not web_order_bound_to_tg and not is_expected_device_username(tg_id, device_id, username):
            non_standard_devices.append(
                f"tg={tg_id} slot={device_id} db_username={username} expected={build_device_username(tg_id, device_id)}"
            )

    for row in web_rows:
        username = str(_row_get(row, "marzban_username") or "").strip()
        status = str(_row_get(row, "status") or "")
        if not username or status not in WEB_ORDER_ACCESS_STATUSES:
            continue
        detail = (
            f"order={_row_get(row, 'order_id')} status={status} "
            f"plan={_row_get(row, 'plan_key')}"
        )
        refs.append(DbRef("web_orders", None, None, username, detail))

    return refs, web_without_access, non_standard_devices, web_without_access_payload


async def collect_ignored_drift_ids(repo: Any) -> set[str]:
    """Return finding IDs explicitly ignored by an admin.

    Ignore is intentionally stored in the existing events table to avoid a schema
    migration for this safety feature. If events are unavailable, audit simply
    behaves as before.
    """
    if getattr(repo, "conn", None) is None:
        return set()
    try:
        cursor = await repo.conn.execute(
            """
            SELECT DISTINCT event_value
            FROM events
            WHERE event_type = ?
              AND COALESCE(event_value, '') != ''
            """,
            (DRIFT_IGNORED_EVENT_TYPE,),
        )
        rows = await cursor.fetchall()
        await cursor.close()
    except Exception:
        return set()
    return {str(row["event_value"]).strip() for row in rows if str(row["event_value"] or "").strip()}


def build_audit_report(
    *,
    refs: list[DbRef],
    web_without_access: list[str],
    web_without_access_payload: list[dict[str, Any]] | None = None,
    non_standard_devices: list[str],
    mz_users: dict[str, dict[str, Any]],
    list_error: str | None,
    ignored_finding_ids: set[str] | None = None,
) -> SyncAuditReport:
    ignored_finding_ids = set(ignored_finding_ids or set())
    db_by_username: dict[str, list[DbRef]] = {}
    for ref in refs:
        db_by_username.setdefault(ref.username, []).append(ref)

    unknown_in_db = [
        f"{username} status={user.get('status')} expire={fmt_expire(user)}"
        for username, user in sorted(mz_users.items())
        if username not in db_by_username
        and str(user.get("status") or "").lower() != "disabled"
        and re.match(r"^(tg_|web_)", username)
    ]

    shared_db_refs = [
        f"{username} <- " + "; ".join(f"{ref.source}:{ref.detail}" for ref in refs_for_user)
        for username, refs_for_user in sorted(db_by_username.items())
        if _has_suspicious_shared_refs(refs_for_user)
    ]

    db_known_summary = [
        f"{username} status={mz_users.get(username, {}).get('status', 'missing')} "
        f"expire={fmt_expire(mz_users.get(username))} refs={len(refs_for_user)}"
        for username, refs_for_user in sorted(db_by_username.items())
    ]

    findings = _build_findings(
        db_by_username=db_by_username,
        mz_users=mz_users,
        web_without_access_payload=web_without_access_payload or [],
    )
    if ignored_finding_ids:
        findings = [
            finding for finding in findings if finding.finding_id not in ignored_finding_ids
        ]

    missing_in_marzban = [
        finding.summary for finding in findings if finding.kind == KIND_MISSING_IN_MARZBAN
    ]
    web_orders_without_access = [
        finding.summary for finding in findings if finding.kind == KIND_WEB_ORDER_NO_ACCESS
    ]

    return SyncAuditReport(
        db_refs=len(refs),
        db_unique_usernames=len(db_by_username),
        marzban_users_seen=len(mz_users),
        marzban_list_error=list_error,
        missing_in_marzban=missing_in_marzban,
        unknown_in_db=unknown_in_db,
        web_orders_without_access=web_orders_without_access,
        non_standard_device_names=non_standard_devices,
        shared_db_refs=shared_db_refs,
        db_known_summary=db_known_summary,
        findings=findings,
        ignored_finding_ids=sorted(ignored_finding_ids),
    )


def _build_findings(
    *,
    db_by_username: dict[str, list[DbRef]],
    mz_users: dict[str, dict[str, Any]],
    web_without_access_payload: list[dict[str, Any]],
) -> list[DriftFinding]:
    findings: list[DriftFinding] = []

    for username in sorted(db_by_username.keys()):
        if username in mz_users:
            continue
        refs_for_user = db_by_username[username]
        ref_payload = [
            {
                "source": ref.source,
                "telegram_id": ref.telegram_id,
                "device_id": ref.device_id,
                "username": ref.username,
                "detail": ref.detail,
            }
            for ref in refs_for_user
        ]
        summary = f"{username} <- " + "; ".join(ref.detail for ref in refs_for_user)
        findings.append(
            DriftFinding(
                kind=KIND_MISSING_IN_MARZBAN,
                finding_id=f"{kind_prefix(KIND_MISSING_IN_MARZBAN)}:{username}",
                summary=summary,
                payload={"username": username, "refs": ref_payload},
            )
        )

    for entry in web_without_access_payload:
        order_id = str(entry.get("order_id") or "").strip()
        if not order_id:
            continue
        plan_key = str(entry.get("plan_key") or "")
        updated = str(entry.get("updated_at_text") or "")
        summary = f"order={order_id} plan={plan_key} updated={updated}"
        findings.append(
            DriftFinding(
                kind=KIND_WEB_ORDER_NO_ACCESS,
                finding_id=f"{kind_prefix(KIND_WEB_ORDER_NO_ACCESS)}:{order_id}",
                summary=summary,
                payload={
                    "order_id": order_id,
                    "plan_key": plan_key,
                    "days": int(entry.get("days") or 0),
                    "gb": int(entry.get("gb") or 0),
                },
            )
        )

    return findings


def _has_suspicious_shared_refs(refs_for_user: list[DbRef]) -> bool:
    owner_tgs = {
        int(ref.telegram_id)
        for ref in refs_for_user
        if ref.telegram_id is not None and ref.source in {"users", "devices"}
    }
    device_slots = {
        (int(ref.telegram_id), int(ref.device_id))
        for ref in refs_for_user
        if ref.source == "devices" and ref.telegram_id is not None and ref.device_id is not None
    }
    return len(owner_tgs) > 1 or len(device_slots) > 1


async def audit_marzban_sync(repo: Any, marzban: Any, *, limit: int = 100) -> SyncAuditReport:
    refs, web_without_access, non_standard_devices, web_without_access_payload = (
        await collect_db_refs(repo)
    )
    ignored_finding_ids = await collect_ignored_drift_ids(repo)
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
        web_without_access_payload=web_without_access_payload,
        non_standard_devices=non_standard_devices,
        mz_users=mz_users,
        list_error=list_error,
        ignored_finding_ids=ignored_finding_ids,
    )


def connect_sqlite(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn
