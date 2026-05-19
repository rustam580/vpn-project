from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any

import aiosqlite

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "db" / "migrations"
SCHEMA_VERSION_LATEST = 5
MIGRATIONS: tuple[tuple[int, str], ...] = (
    (1, "001_init.sql"),
    (2, "002_legacy_columns.sql"),
    (3, "003_subscription_hits.sql"),
    (4, "004_web_orders.sql"),
    (5, "005_rescue_rooms.sql"),
)


class Repo:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn: aiosqlite.Connection | None = None

    async def open(self) -> None:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = await aiosqlite.connect(self.db_path)
        self.conn.row_factory = aiosqlite.Row
        # Improve concurrency and reduce "database is locked" issues on SQLite.
        await self.conn.execute("PRAGMA journal_mode=WAL")
        await self.conn.execute("PRAGMA synchronous=NORMAL")
        await self.conn.execute("PRAGMA busy_timeout=5000")
        await self._ensure_schema_version_table()
        await self._run_migrations()
        await self.conn.commit()

    async def _ensure_schema_version_table(self) -> None:
        assert self.conn is not None
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER NOT NULL
            )
            """
        )
        c = await self.conn.execute("SELECT COUNT(*) AS cnt FROM schema_version")
        row = await c.fetchone()
        await c.close()
        count = int(row["cnt"] if row is not None else 0)
        if count == 0:
            await self.conn.execute("INSERT INTO schema_version(version) VALUES (0)")

    async def _get_schema_version(self) -> int:
        assert self.conn is not None
        c = await self.conn.execute("SELECT version FROM schema_version LIMIT 1")
        row = await c.fetchone()
        await c.close()
        return int(row["version"] if row and row["version"] is not None else 0)

    async def _set_schema_version(self, version: int) -> None:
        assert self.conn is not None
        await self.conn.execute("UPDATE schema_version SET version = ?", (int(version),))

    @staticmethod
    def _split_sql_statements(sql: str) -> list[str]:
        statements: list[str] = []
        chunks = sql.split(";")
        for chunk in chunks:
            statement = chunk.strip()
            if statement:
                statements.append(statement + ";")
        return statements

    async def _apply_sql_file(self, path: Path, *, ignore_duplicate_column: bool = False) -> None:
        assert self.conn is not None
        sql = path.read_text(encoding="utf-8")
        if not ignore_duplicate_column:
            await self.conn.executescript(sql)
            return
        for statement in self._split_sql_statements(sql):
            try:
                await self.conn.execute(statement)
            except (aiosqlite.Error, sqlite3.Error) as exc:
                if "duplicate column name" in str(exc).lower():
                    continue
                raise

    async def _run_migrations(self) -> None:
        assert self.conn is not None
        current = await self._get_schema_version()
        for version, filename in MIGRATIONS:
            if version <= current:
                continue
            path = MIGRATIONS_DIR / filename
            if not path.exists():
                raise RuntimeError(f"Missing migration file: {path}")
            try:
                await self._apply_sql_file(
                    path,
                    ignore_duplicate_column=(version == 2),
                )
                await self._set_schema_version(version)
                await self.conn.commit()
            except Exception:
                await self.conn.rollback()
                raise
        final_version = await self._get_schema_version()
        if final_version < SCHEMA_VERSION_LATEST:
            raise RuntimeError(
                f"Schema migration incomplete: current={final_version}, expected={SCHEMA_VERSION_LATEST}"
            )

    async def close(self) -> None:
        if self.conn:
            await self.conn.close()

    async def get_user(self, telegram_id: int) -> dict[str, Any] | None:
        assert self.conn is not None
        c = await self.conn.execute(
            "SELECT telegram_id, marzban_username FROM users WHERE telegram_id = ?",
            (telegram_id,),
        )
        row = await c.fetchone()
        await c.close()
        return dict(row) if row else None

    async def upsert_user(self, telegram_id: int, marzban_username: str) -> None:
        assert self.conn is not None
        now = int(time.time())
        await self.conn.execute(
            """
            INSERT INTO users (telegram_id, marzban_username, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE
            SET marzban_username = excluded.marzban_username,
                updated_at = excluded.updated_at
            """,
            (telegram_id, marzban_username, now, now),
        )
        await self.conn.commit()
        await self.upsert_device(telegram_id, 1, marzban_username)
        await self.touch_chat(telegram_id)

    async def touch_chat(self, telegram_id: int) -> None:
        assert self.conn is not None
        now = int(time.time())
        await self.conn.execute(
            """
            INSERT INTO known_chats (telegram_id, first_seen_at, last_seen_at)
            VALUES (?, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE
            SET last_seen_at = excluded.last_seen_at
            """,
            (telegram_id, now, now),
        )
        await self.conn.commit()

    async def upsert_payment(
        self,
        *,
        provider: str,
        external_id: str,
        telegram_id: int,
        days: int,
        gb: int,
        amount_rub: float,
        pay_url: str,
        status: str,
        purpose: str = "plan",
        device_slot: int | None = None,
    ) -> None:
        assert self.conn is not None
        now = int(time.time())
        await self.conn.execute(
            """
            INSERT INTO payments (
                provider, external_id, telegram_id, days, gb, amount_rub, pay_url, status, purpose, device_slot, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(provider, external_id) DO UPDATE
            SET status = excluded.status,
                pay_url = excluded.pay_url,
                purpose = excluded.purpose,
                device_slot = excluded.device_slot,
                updated_at = excluded.updated_at
            """,
            (
                provider,
                external_id,
                telegram_id,
                days,
                gb,
                amount_rub,
                pay_url,
                status,
                purpose,
                device_slot,
                now,
                now,
            ),
        )
        await self.conn.commit()

    async def get_payment(self, provider: str, external_id: str) -> dict[str, Any] | None:
        assert self.conn is not None
        c = await self.conn.execute(
            """
            SELECT provider, external_id, telegram_id, days, gb, amount_rub, pay_url, status, purpose, device_slot, created_at, updated_at
            FROM payments WHERE provider = ? AND external_id = ?
            """,
            (provider, external_id),
        )
        row = await c.fetchone()
        await c.close()
        return dict(row) if row else None

    async def get_payment_any(self, external_id: str) -> dict[str, Any] | None:
        assert self.conn is not None
        c = await self.conn.execute(
            """
            SELECT provider, external_id, telegram_id, days, gb, amount_rub, pay_url, status, purpose, device_slot, created_at, updated_at
            FROM payments WHERE external_id = ?
            LIMIT 1
            """,
            (external_id,),
        )
        row = await c.fetchone()
        await c.close()
        return dict(row) if row else None

    async def set_payment_status(self, provider: str, external_id: str, status: str) -> None:
        assert self.conn is not None
        await self.conn.execute(
            "UPDATE payments SET status = ?, updated_at = ? WHERE provider = ? AND external_id = ?",
            (status, int(time.time()), provider, external_id),
        )
        await self.conn.commit()

    async def claim_payment_for_apply(self, provider: str, external_id: str) -> bool:
        assert self.conn is not None
        cur = await self.conn.execute(
            """
            UPDATE payments
            SET status = 'processing', updated_at = ?
            WHERE provider = ?
              AND external_id = ?
              AND status NOT IN ('paid_applied', 'processing')
            """,
            (int(time.time()), provider, external_id),
        )
        await self.conn.commit()
        return cur.rowcount > 0

    async def list_users(self) -> list[dict[str, Any]]:
        assert self.conn is not None
        c = await self.conn.execute(
            "SELECT telegram_id, marzban_username FROM users ORDER BY created_at DESC"
        )
        rows = await c.fetchall()
        await c.close()
        return [dict(row) for row in rows]

    async def list_known_telegram_ids(self) -> list[int]:
        assert self.conn is not None
        c = await self.conn.execute(
            """
            SELECT telegram_id FROM known_chats
            UNION
            SELECT telegram_id FROM users
            UNION
            SELECT telegram_id FROM devices
            UNION
            SELECT telegram_id FROM payments
            ORDER BY telegram_id ASC
            """
        )
        rows = await c.fetchall()
        await c.close()
        result: list[int] = []
        for row in rows:
            tg_id = row["telegram_id"]
            if tg_id is None:
                continue
            try:
                result.append(int(tg_id))
            except (TypeError, ValueError):
                continue
        return result

    async def get_device(self, telegram_id: int, device_id: int) -> dict[str, Any] | None:
        assert self.conn is not None
        c = await self.conn.execute(
            """
            SELECT telegram_id, device_id, marzban_username, device_name
            FROM devices
            WHERE telegram_id = ? AND device_id = ?
            """,
            (telegram_id, device_id),
        )
        row = await c.fetchone()
        await c.close()
        return dict(row) if row else None

    async def list_devices(self, telegram_id: int) -> list[dict[str, Any]]:
        assert self.conn is not None
        c = await self.conn.execute(
            """
            SELECT telegram_id, device_id, marzban_username, device_name
            FROM devices
            WHERE telegram_id = ?
            ORDER BY device_id ASC
            """,
            (telegram_id,),
        )
        rows = await c.fetchall()
        await c.close()
        return [dict(row) for row in rows]

    async def get_device_by_username(self, marzban_username: str) -> dict[str, Any] | None:
        assert self.conn is not None
        c = await self.conn.execute(
            """
            SELECT telegram_id, device_id, marzban_username, device_name
            FROM devices
            WHERE marzban_username = ?
            LIMIT 1
            """,
            (marzban_username,),
        )
        row = await c.fetchone()
        await c.close()
        return dict(row) if row else None

    async def get_user_by_username(self, marzban_username: str) -> dict[str, Any] | None:
        assert self.conn is not None
        c = await self.conn.execute(
            """
            SELECT telegram_id, marzban_username
            FROM users
            WHERE marzban_username = ?
            LIMIT 1
            """,
            (marzban_username,),
        )
        row = await c.fetchone()
        await c.close()
        return dict(row) if row else None

    async def upsert_device(
        self,
        telegram_id: int,
        device_id: int,
        marzban_username: str,
        device_name: str | None = None,
    ) -> None:
        assert self.conn is not None
        now = int(time.time())
        await self.conn.execute(
            """
            INSERT INTO devices (telegram_id, device_id, marzban_username, device_name, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(telegram_id, device_id) DO UPDATE
            SET marzban_username = excluded.marzban_username,
                device_name = COALESCE(excluded.device_name, devices.device_name),
                updated_at = excluded.updated_at
            """,
            (telegram_id, device_id, marzban_username, device_name, now, now),
        )
        await self.conn.commit()

    async def set_device_name(self, telegram_id: int, device_id: int, device_name: str) -> None:
        assert self.conn is not None
        await self.conn.execute(
            """
            UPDATE devices
            SET device_name = ?, updated_at = ?
            WHERE telegram_id = ? AND device_id = ?
            """,
            (device_name, int(time.time()), telegram_id, device_id),
        )
        await self.conn.commit()

    async def list_device_usernames(self, telegram_id: int) -> list[str]:
        assert self.conn is not None
        c = await self.conn.execute(
            """
            SELECT marzban_username
            FROM devices
            WHERE telegram_id = ?
            """,
            (telegram_id,),
        )
        rows = await c.fetchall()
        await c.close()
        seen: set[str] = set()
        result: list[str] = []
        for row in rows:
            name = str(row["marzban_username"])
            if name and name not in seen:
                seen.add(name)
                result.append(name)
        return result

    async def payment_status_counts(self) -> dict[str, int]:
        assert self.conn is not None
        c = await self.conn.execute(
            "SELECT status, COUNT(*) AS cnt FROM payments GROUP BY status"
        )
        rows = await c.fetchall()
        await c.close()
        result: dict[str, int] = {}
        for row in rows:
            result[str(row["status"])] = int(row["cnt"])
        return result

    async def log_event(
        self,
        *,
        event_type: str,
        telegram_id: int | None = None,
        event_value: str = "",
        event_meta: dict[str, Any] | None = None,
    ) -> None:
        assert self.conn is not None
        meta_raw = ""
        if event_meta:
            try:
                meta_raw = json.dumps(event_meta, ensure_ascii=False, separators=(",", ":"))
            except (TypeError, ValueError) as exc:
                logging.warning("log_event: cannot serialize meta for %s: %s", event_type, exc)
                meta_raw = ""
        await self.conn.execute(
            """
            INSERT INTO events (telegram_id, event_type, event_value, event_meta, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                telegram_id,
                event_type.strip()[:80],
                event_value.strip()[:250],
                meta_raw,
                int(time.time()),
            ),
        )
        await self.conn.commit()

    async def event_counts_since(self, since_ts: int) -> dict[str, dict[str, int]]:
        assert self.conn is not None
        c = await self.conn.execute(
            """
            SELECT
                event_type,
                COUNT(*) AS total,
                COUNT(DISTINCT telegram_id) AS users
            FROM events
            WHERE created_at >= ?
            GROUP BY event_type
            """,
            (since_ts,),
        )
        rows = await c.fetchall()
        await c.close()
        data: dict[str, dict[str, int]] = {}
        for row in rows:
            key = str(row["event_type"] or "").strip()
            if not key:
                continue
            data[key] = {
                "total": int(row["total"] or 0),
                "users": int(row["users"] or 0),
            }
        return data

    async def get_latest_payment(self, telegram_id: int) -> dict[str, Any] | None:
        assert self.conn is not None
        c = await self.conn.execute(
            """
            SELECT provider, external_id, purpose, device_slot, days, gb, amount_rub, status, updated_at
            FROM payments
            WHERE telegram_id = ?
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (telegram_id,),
        )
        row = await c.fetchone()
        await c.close()
        return dict(row) if row else None

    async def has_open_plan_payment(
        self,
        *,
        telegram_id: int,
        purpose: str,
        device_slot: int = 0,
    ) -> bool:
        assert self.conn is not None
        c = await self.conn.execute(
            """
            SELECT 1
            FROM payments
            WHERE telegram_id = ?
              AND purpose = ?
              AND COALESCE(device_slot, 0) = ?
              AND status IN ('pending', 'processing')
            LIMIT 1
            """,
            (telegram_id, purpose, device_slot),
        )
        row = await c.fetchone()
        await c.close()
        return row is not None

    async def has_paid_plan_payment(self, telegram_id: int) -> bool:
        assert self.conn is not None
        c = await self.conn.execute(
            """
            SELECT 1
            FROM payments
            WHERE telegram_id = ?
              AND status = 'paid_applied'
              AND purpose IN ('plan', 'plan_device', 'plan_all')
            LIMIT 1
            """,
            (telegram_id,),
        )
        row = await c.fetchone()
        await c.close()
        return row is not None

    async def list_unfinished_crypto_payments(self, limit: int = 100) -> list[dict[str, Any]]:
        assert self.conn is not None
        c = await self.conn.execute(
            """
            SELECT provider, external_id, telegram_id, days, gb, amount_rub, pay_url, status, purpose, device_slot
            FROM payments
            WHERE provider = 'crypto'
              AND status NOT IN ('paid_applied', 'canceled', 'expired', 'processing')
            ORDER BY updated_at ASC
            LIMIT ?
            """,
            (limit,),
        )
        rows = await c.fetchall()
        await c.close()
        return [dict(row) for row in rows]

    async def list_unfinished_payments(
        self, provider: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        assert self.conn is not None
        c = await self.conn.execute(
            """
            SELECT provider, external_id, telegram_id, days, gb, amount_rub, pay_url, status, purpose, device_slot
            FROM payments
            WHERE provider = ?
              AND status NOT IN ('paid_applied', 'canceled', 'expired', 'processing')
            ORDER BY updated_at ASC
            LIMIT ?
            """,
            (provider, limit),
        )
        rows = await c.fetchall()
        await c.close()
        return [dict(row) for row in rows]

    async def requeue_stuck_processing_payments(
        self, provider: str, *, older_than_sec: int, limit: int = 100
    ) -> list[dict[str, Any]]:
        assert self.conn is not None
        cutoff = int(time.time()) - max(60, older_than_sec)
        c = await self.conn.execute(
            """
            SELECT provider, external_id, telegram_id, purpose, device_slot, updated_at
            FROM payments
            WHERE provider = ?
              AND status = 'processing'
              AND updated_at <= ?
            ORDER BY updated_at ASC
            LIMIT ?
            """,
            (provider, cutoff, limit),
        )
        rows = await c.fetchall()
        await c.close()
        if not rows:
            return []
        ids = [(str(row["provider"]), str(row["external_id"])) for row in rows]
        for p, ext_id in ids:
            await self.conn.execute(
                """
                UPDATE payments
                SET status = 'pending', updated_at = ?
                WHERE provider = ? AND external_id = ? AND status = 'processing'
                """,
                (int(time.time()), p, ext_id),
            )
        await self.conn.commit()
        return [dict(row) for row in rows]

    async def list_stale_processing_payments(
        self, *, older_than_sec: int, limit: int = 20
    ) -> list[dict[str, Any]]:
        assert self.conn is not None
        cutoff = int(time.time()) - max(60, int(older_than_sec))
        c = await self.conn.execute(
            """
            SELECT
                provider, external_id, telegram_id, days, gb, amount_rub, pay_url,
                status, purpose, device_slot, created_at, updated_at
            FROM payments
            WHERE status = 'processing'
              AND updated_at <= ?
            ORDER BY updated_at ASC
            LIMIT ?
            """,
            (cutoff, max(1, int(limit))),
        )
        rows = await c.fetchall()
        await c.close()
        return [dict(row) for row in rows]

    async def list_old_unfinished_payments(
        self, *, older_than_sec: int, limit: int = 20
    ) -> list[dict[str, Any]]:
        assert self.conn is not None
        cutoff = int(time.time()) - max(60, int(older_than_sec))
        c = await self.conn.execute(
            """
            SELECT
                provider, external_id, telegram_id, days, gb, amount_rub, pay_url,
                status, purpose, device_slot, created_at, updated_at
            FROM payments
            WHERE status NOT IN ('paid_applied', 'canceled', 'expired', 'failed', 'processing')
              AND created_at <= ?
            ORDER BY created_at ASC
            LIMIT ?
            """,
            (cutoff, max(1, int(limit))),
        )
        rows = await c.fetchall()
        await c.close()
        return [dict(row) for row in rows]

    async def mark_notification_once(
        self,
        *,
        telegram_id: int,
        device_id: int,
        mark_type: str,
        expire_ts: int,
    ) -> bool:
        assert self.conn is not None
        cur = await self.conn.execute(
            """
            INSERT OR IGNORE INTO notification_marks (
                telegram_id, device_id, mark_type, expire_ts, created_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                telegram_id,
                device_id,
                str(mark_type).strip()[:80],
                int(expire_ts),
                int(time.time()),
            ),
        )
        await self.conn.commit()
        return cur.rowcount > 0

    async def prune_notification_marks(self, *, older_than_sec: int) -> int:
        assert self.conn is not None
        cutoff = int(time.time()) - max(86400, older_than_sec)
        cur = await self.conn.execute(
            "DELETE FROM notification_marks WHERE created_at < ?",
            (cutoff,),
        )
        await self.conn.commit()
        return int(cur.rowcount or 0)

    async def delete_notification_mark(
        self,
        *,
        telegram_id: int,
        device_id: int,
        mark_type: str,
        expire_ts: int,
    ) -> int:
        assert self.conn is not None
        cur = await self.conn.execute(
            """
            DELETE FROM notification_marks
            WHERE telegram_id = ?
              AND device_id = ?
              AND mark_type = ?
              AND expire_ts = ?
            """,
            (
                int(telegram_id),
                int(device_id),
                str(mark_type).strip()[:80],
                int(expire_ts),
            ),
        )
        await self.conn.commit()
        return int(cur.rowcount or 0)

    async def prune_subscription_hits(self, *, older_than_sec: int) -> int:
        assert self.conn is not None
        cutoff = int(time.time()) - max(86400, older_than_sec)
        cur = await self.conn.execute(
            "DELETE FROM subscription_hits WHERE created_at < ?",
            (cutoff,),
        )
        await self.conn.commit()
        return int(cur.rowcount or 0)

    async def create_web_order(
        self,
        *,
        order_id: str,
        provider: str,
        external_id: str,
        status: str,
        plan_key: str,
        days: int,
        gb: int,
        amount_rub: float,
        customer_contact: str,
        pay_url: str,
    ) -> None:
        assert self.conn is not None
        now = int(time.time())
        await self.conn.execute(
            """
            INSERT INTO web_orders (
                order_id, provider, external_id, status, plan_key, days, gb, amount_rub,
                customer_contact, marzban_username, pay_url, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)
            ON CONFLICT(order_id) DO UPDATE SET
                provider = excluded.provider,
                external_id = excluded.external_id,
                status = excluded.status,
                plan_key = excluded.plan_key,
                days = excluded.days,
                gb = excluded.gb,
                amount_rub = excluded.amount_rub,
                customer_contact = excluded.customer_contact,
                pay_url = excluded.pay_url,
                updated_at = excluded.updated_at
            """,
            (
                order_id,
                provider,
                external_id,
                status,
                plan_key,
                int(days),
                int(gb),
                float(amount_rub),
                customer_contact,
                pay_url,
                now,
                now,
            ),
        )
        await self.conn.commit()

    async def get_web_order(self, order_id: str) -> dict[str, Any] | None:
        assert self.conn is not None
        c = await self.conn.execute(
            """
            SELECT
                order_id, provider, external_id, status, plan_key, days, gb, amount_rub,
                customer_contact, marzban_username, pay_url, created_at, updated_at
            FROM web_orders
            WHERE order_id = ?
            LIMIT 1
            """,
            (order_id,),
        )
        row = await c.fetchone()
        await c.close()
        return dict(row) if row else None

    async def list_payments_for_user(self, telegram_id: int, *, limit: int = 10) -> list[dict[str, Any]]:
        assert self.conn is not None
        c = await self.conn.execute(
            """
            SELECT provider, external_id, telegram_id, days, gb, amount_rub, pay_url, status, purpose, device_slot, created_at, updated_at
            FROM payments
            WHERE telegram_id = ?
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (telegram_id, max(1, int(limit))),
        )
        rows = await c.fetchall()
        await c.close()
        return [dict(row) for row in rows]

    async def find_web_orders(self, query: str, *, limit: int = 5) -> list[dict[str, Any]]:
        assert self.conn is not None
        value = str(query or "").strip()
        if not value:
            return []
        like = f"%{value}%"
        c = await self.conn.execute(
            """
            SELECT
                order_id, provider, external_id, status, plan_key, days, gb, amount_rub,
                customer_contact, marzban_username, pay_url, created_at, updated_at
            FROM web_orders
            WHERE order_id = ?
               OR external_id = ?
               OR marzban_username = ?
               OR order_id LIKE ?
               OR external_id LIKE ?
               OR marzban_username LIKE ?
               OR customer_contact LIKE ?
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (value, value, value, like, like, like, like, max(1, int(limit))),
        )
        rows = await c.fetchall()
        await c.close()
        return [dict(row) for row in rows]

    async def list_web_orders_for_usernames(
        self, usernames: list[str], *, limit: int = 10
    ) -> list[dict[str, Any]]:
        assert self.conn is not None
        clean = [str(name).strip() for name in usernames if str(name or "").strip()]
        if not clean:
            return []
        unique = list(dict.fromkeys(clean))
        placeholders = ",".join("?" for _ in unique)
        c = await self.conn.execute(
            f"""
            SELECT
                order_id, provider, external_id, status, plan_key, days, gb, amount_rub,
                customer_contact, marzban_username, pay_url, created_at, updated_at
            FROM web_orders
            WHERE marzban_username IN ({placeholders})
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (*unique, max(1, int(limit))),
        )
        rows = await c.fetchall()
        await c.close()
        return [dict(row) for row in rows]

    async def set_web_order_status(self, order_id: str, status: str) -> None:
        assert self.conn is not None
        await self.conn.execute(
            """
            UPDATE web_orders
            SET status = ?, updated_at = ?
            WHERE order_id = ?
            """,
            (status, int(time.time()), order_id),
        )
        await self.conn.commit()

    async def attach_web_order_access(self, *, order_id: str, marzban_username: str) -> None:
        assert self.conn is not None
        await self.conn.execute(
            """
            UPDATE web_orders
            SET marzban_username = ?, updated_at = ?
            WHERE order_id = ?
            """,
            (marzban_username, int(time.time()), order_id),
        )
        await self.conn.commit()

    async def list_paid_web_orders_without_access(self, *, limit: int = 20) -> list[dict[str, Any]]:
        assert self.conn is not None
        c = await self.conn.execute(
            """
            SELECT
                order_id, provider, external_id, status, plan_key, days, gb, amount_rub,
                customer_contact, marzban_username, pay_url, created_at, updated_at
            FROM web_orders
            WHERE status IN ('paid', 'succeeded', 'paid_applied')
              AND (marzban_username IS NULL OR marzban_username = '')
            ORDER BY updated_at ASC
            LIMIT ?
            """,
            (max(1, int(limit)),),
        )
        rows = await c.fetchall()
        await c.close()
        return [dict(row) for row in rows]

    async def list_paid_web_orders_with_access(self, *, limit: int = 50) -> list[dict[str, Any]]:
        assert self.conn is not None
        c = await self.conn.execute(
            """
            SELECT
                order_id, provider, external_id, status, plan_key, days, gb, amount_rub,
                customer_contact, marzban_username, pay_url, created_at, updated_at
            FROM web_orders
            WHERE status IN ('paid', 'succeeded', 'paid_applied')
              AND marzban_username IS NOT NULL
              AND marzban_username != ''
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (max(1, int(limit)),),
        )
        rows = await c.fetchall()
        await c.close()
        return [dict(row) for row in rows]

    async def web_bind_conversion_stats(self, *, days: int = 7) -> dict[str, int | float]:
        assert self.conn is not None
        lookback_days = max(1, int(days))
        since_ts = int(time.time()) - lookback_days * 86400

        c = await self.conn.execute(
            """
            SELECT event_type, event_meta
            FROM events
            WHERE created_at >= ?
              AND event_type IN ('web_order_paid_applied', 'web_order_bound')
            """,
            (since_ts,),
        )
        rows = await c.fetchall()
        await c.close()

        paid_ids: set[str] = set()
        bound_ids: set[str] = set()
        for row in rows:
            event_type = str(row["event_type"] or "").strip()
            raw_meta = str(row["event_meta"] or "").strip()
            if not raw_meta:
                continue
            try:
                meta = json.loads(raw_meta)
            except (TypeError, ValueError) as exc:
                logging.warning("web_order events: malformed meta in event %s: %s", event_type, exc)
                continue
            order_id = str(meta.get("order_id") or "").strip()
            if not order_id:
                continue
            if event_type == "web_order_paid_applied":
                paid_ids.add(order_id)
            elif event_type == "web_order_bound":
                bound_ids.add(order_id)

        bound_from_paid = len(paid_ids & bound_ids)
        paid_total = len(paid_ids)
        conversion_pct = (bound_from_paid / paid_total * 100.0) if paid_total > 0 else 0.0
        return {
            "days": lookback_days,
            "paid_orders": paid_total,
            "bound_orders": len(bound_ids),
            "bound_from_paid": bound_from_paid,
            "pending_bind": max(paid_total - bound_from_paid, 0),
            "conversion_pct": conversion_pct,
        }

    async def bind_referrer(self, invited_telegram_id: int, referrer_telegram_id: int) -> str:
        assert self.conn is not None
        if invited_telegram_id == referrer_telegram_id:
            return "self"

        c = await self.conn.execute(
            "SELECT referrer_telegram_id FROM referrals WHERE invited_telegram_id = ?",
            (invited_telegram_id,),
        )
        row = await c.fetchone()
        await c.close()
        if row:
            if int(row["referrer_telegram_id"]) == referrer_telegram_id:
                return "exists_same"
            return "exists_other"

        now = int(time.time())
        await self.conn.execute(
            """
            INSERT INTO referrals (invited_telegram_id, referrer_telegram_id, created_at, bonus_applied)
            VALUES (?, ?, ?, 0)
            """,
            (invited_telegram_id, referrer_telegram_id, now),
        )
        await self.conn.commit()
        return "bound"

    async def get_referral_stats(self, referrer_telegram_id: int) -> dict[str, int]:
        assert self.conn is not None
        c = await self.conn.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN bonus_applied = 1 THEN 1 ELSE 0 END) AS rewarded
            FROM referrals
            WHERE referrer_telegram_id = ?
            """,
            (referrer_telegram_id,),
        )
        row = await c.fetchone()
        await c.close()
        total = int((row["total"] if row and row["total"] is not None else 0) or 0)
        rewarded = int((row["rewarded"] if row and row["rewarded"] is not None else 0) or 0)
        return {
            "total": total,
            "rewarded": rewarded,
            "pending": max(total - rewarded, 0),
        }

    async def get_referral_global_stats(self) -> dict[str, int]:
        assert self.conn is not None
        c = await self.conn.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN bonus_applied = 1 THEN 1 ELSE 0 END) AS rewarded
            FROM referrals
            """
        )
        row = await c.fetchone()
        await c.close()
        total = int((row["total"] if row and row["total"] is not None else 0) or 0)
        rewarded = int((row["rewarded"] if row and row["rewarded"] is not None else 0) or 0)
        return {
            "total": total,
            "rewarded": rewarded,
            "pending": max(total - rewarded, 0),
        }

    async def list_top_referrers(self, limit: int = 10) -> list[dict[str, Any]]:
        assert self.conn is not None
        c = await self.conn.execute(
            """
            SELECT
                r.referrer_telegram_id AS telegram_id,
                u.marzban_username AS marzban_username,
                COUNT(*) AS total,
                SUM(CASE WHEN r.bonus_applied = 1 THEN 1 ELSE 0 END) AS rewarded
            FROM referrals r
            LEFT JOIN users u ON u.telegram_id = r.referrer_telegram_id
            GROUP BY r.referrer_telegram_id
            ORDER BY rewarded DESC, total DESC, r.referrer_telegram_id ASC
            LIMIT ?
            """,
            (max(1, limit),),
        )
        rows = await c.fetchall()
        await c.close()
        result: list[dict[str, Any]] = []
        for row in rows:
            total = int((row["total"] if row["total"] is not None else 0) or 0)
            rewarded = int((row["rewarded"] if row["rewarded"] is not None else 0) or 0)
            result.append(
                {
                    "telegram_id": int(row["telegram_id"]),
                    "marzban_username": str(row["marzban_username"] or "").strip(),
                    "total": total,
                    "rewarded": rewarded,
                    "pending": max(total - rewarded, 0),
                }
            )
        return result

    async def subscription_adoption_stats(self, *, days: int = 7) -> dict[str, int | float]:
        assert self.conn is not None
        lookback_days = max(1, int(days))
        since_ts = int(time.time()) - lookback_days * 86400
        c = await self.conn.execute(
            """
            WITH known_users AS (
                SELECT DISTINCT telegram_id
                FROM users
                UNION
                SELECT DISTINCT telegram_id
                FROM devices
            ),
            adopted_users AS (
                SELECT DISTINCT telegram_id
                FROM subscription_hits
                WHERE telegram_id IS NOT NULL
                  AND created_at >= ?
                UNION
                SELECT DISTINCT u.telegram_id
                FROM subscription_hits h
                JOIN users u ON u.marzban_username = h.marzban_username
                WHERE h.created_at >= ?
                UNION
                SELECT DISTINCT d.telegram_id
                FROM subscription_hits h
                JOIN devices d ON d.marzban_username = h.marzban_username
                WHERE h.created_at >= ?
            )
            SELECT
                (SELECT COUNT(*) FROM known_users) AS total_users,
                (SELECT COUNT(*) FROM adopted_users) AS adopted_users
            """
            ,
            (since_ts, since_ts, since_ts),
        )
        row = await c.fetchone()
        await c.close()
        total_users = int((row["total_users"] if row and row["total_users"] is not None else 0) or 0)
        adopted_users = int((row["adopted_users"] if row and row["adopted_users"] is not None else 0) or 0)
        pending_users = max(total_users - adopted_users, 0)
        adoption_pct = (adopted_users / total_users * 100.0) if total_users > 0 else 0.0
        return {
            "days": lookback_days,
            "total_users": total_users,
            "adopted_users": adopted_users,
            "pending_users": pending_users,
            "adoption_pct": adoption_pct,
        }

    async def list_subscription_non_adopters(
        self,
        *,
        days: int = 7,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        assert self.conn is not None
        lookback_days = max(1, int(days))
        row_limit = max(1, int(limit))
        since_ts = int(time.time()) - lookback_days * 86400
        c = await self.conn.execute(
            """
            WITH adopted_users AS (
                SELECT DISTINCT telegram_id
                FROM subscription_hits
                WHERE telegram_id IS NOT NULL
                  AND created_at >= ?
                UNION
                SELECT DISTINCT u.telegram_id
                FROM subscription_hits h
                JOIN users u ON u.marzban_username = h.marzban_username
                WHERE h.created_at >= ?
                UNION
                SELECT DISTINCT d.telegram_id
                FROM subscription_hits h
                JOIN devices d ON d.marzban_username = h.marzban_username
                WHERE h.created_at >= ?
            )
            SELECT
                u.telegram_id,
                u.marzban_username,
                u.updated_at
            FROM users u
            LEFT JOIN adopted_users a ON a.telegram_id = u.telegram_id
            WHERE a.telegram_id IS NULL
            ORDER BY u.updated_at DESC, u.telegram_id DESC
            LIMIT ?
            """,
            (since_ts, since_ts, since_ts, row_limit),
        )
        rows = await c.fetchall()
        await c.close()
        return [dict(row) for row in rows]

    async def claim_referral_bonus(self, invited_telegram_id: int) -> int | None:
        assert self.conn is not None
        c = await self.conn.execute(
            """
            SELECT referrer_telegram_id
            FROM referrals
            WHERE invited_telegram_id = ?
              AND bonus_applied = 0
            """,
            (invited_telegram_id,),
        )
        row = await c.fetchone()
        await c.close()
        if not row:
            return None

        referrer_telegram_id = int(row["referrer_telegram_id"])
        now = int(time.time())
        cur = await self.conn.execute(
            """
            UPDATE referrals
            SET bonus_applied = 1, bonus_paid_at = ?
            WHERE invited_telegram_id = ?
              AND bonus_applied = 0
            """,
            (now, invited_telegram_id),
        )
        await self.conn.commit()
        if cur.rowcount > 0:
            return referrer_telegram_id
        return None

    async def rollback_referral_bonus_claim(self, invited_telegram_id: int, referrer_telegram_id: int) -> None:
        assert self.conn is not None
        await self.conn.execute(
            """
            UPDATE referrals
            SET bonus_applied = 0, bonus_paid_at = NULL
            WHERE invited_telegram_id = ?
              AND referrer_telegram_id = ?
              AND bonus_applied = 1
            """,
            (invited_telegram_id, referrer_telegram_id),
        )
        await self.conn.commit()

    async def add_rescue_room(
        self,
        *,
        room_id: str,
        room_url: str,
        note: str = "",
    ) -> dict[str, Any]:
        assert self.conn is not None
        now = int(time.time())
        await self.conn.execute(
            """
            INSERT INTO rescue_rooms (
                room_id, room_url, status, note, created_at, updated_at
            )
            VALUES (?, ?, 'free', ?, ?, ?)
            ON CONFLICT(room_id) DO UPDATE
            SET room_url = excluded.room_url,
                note = excluded.note,
                updated_at = excluded.updated_at
            """,
            (room_id.strip(), room_url.strip(), note.strip()[:500], now, now),
        )
        await self.conn.commit()
        row = await self.get_rescue_room_by_room_id(room_id)
        if row is None:
            raise RuntimeError("failed to load rescue room after insert")
        return row

    async def get_rescue_room_by_room_id(self, room_id: str) -> dict[str, Any] | None:
        assert self.conn is not None
        c = await self.conn.execute(
            """
            SELECT id, room_id, room_url, status, assigned_tg_id, session_id, note, fail_count,
                   created_at, updated_at, last_ok_at
            FROM rescue_rooms
            WHERE room_id = ?
            LIMIT 1
            """,
            (room_id.strip(),),
        )
        row = await c.fetchone()
        await c.close()
        return dict(row) if row else None

    async def list_rescue_rooms(self, *, include_archived: bool = False) -> list[dict[str, Any]]:
        assert self.conn is not None
        where = "" if include_archived else "WHERE status <> 'archived'"
        c = await self.conn.execute(
            f"""
            SELECT id, room_id, room_url, status, assigned_tg_id, session_id, note, fail_count,
                   created_at, updated_at, last_ok_at
            FROM rescue_rooms
            {where}
            ORDER BY
                CASE status
                    WHEN 'assigned' THEN 1
                    WHEN 'reserved' THEN 2
                    WHEN 'free' THEN 3
                    WHEN 'bad' THEN 4
                    ELSE 5
                END,
                updated_at DESC,
                id DESC
            """
        )
        rows = await c.fetchall()
        await c.close()
        return [dict(row) for row in rows]

    async def claim_next_free_rescue_room(self, *, telegram_id: int) -> dict[str, Any] | None:
        assert self.conn is not None
        now = int(time.time())
        await self.conn.execute("BEGIN IMMEDIATE")
        try:
            c = await self.conn.execute(
                """
                SELECT id, room_id, room_url, status, assigned_tg_id, session_id, note, fail_count,
                       created_at, updated_at, last_ok_at
                FROM rescue_rooms
                WHERE status = 'free'
                ORDER BY updated_at ASC, id ASC
                LIMIT 1
                """
            )
            row = await c.fetchone()
            await c.close()
            if row is None:
                await self.conn.commit()
                return None
            room_id = str(row["room_id"])
            await self.conn.execute(
                """
                UPDATE rescue_rooms
                SET status = 'reserved',
                    assigned_tg_id = ?,
                    session_id = NULL,
                    updated_at = ?
                WHERE room_id = ? AND status = 'free'
                """,
                (int(telegram_id), now, room_id),
            )
            await self.conn.commit()
            return await self.get_rescue_room_by_room_id(room_id)
        except Exception:
            await self.conn.rollback()
            raise

    async def mark_rescue_room_assigned(
        self,
        *,
        room_id: str,
        telegram_id: int,
        session_id: str,
    ) -> None:
        assert self.conn is not None
        now = int(time.time())
        await self.conn.execute(
            """
            UPDATE rescue_rooms
            SET status = 'assigned',
                assigned_tg_id = ?,
                session_id = ?,
                updated_at = ?,
                last_ok_at = ?
            WHERE room_id = ?
            """,
            (int(telegram_id), session_id.strip(), now, now, room_id.strip()),
        )
        await self.conn.commit()

    async def mark_rescue_room_status(
        self,
        *,
        room_id: str,
        status: str,
        session_id: str | None = None,
        telegram_id: int | None = None,
        increment_fail_count: bool = False,
    ) -> None:
        assert self.conn is not None
        allowed = {"free", "reserved", "assigned", "bad", "archived"}
        if status not in allowed:
            raise ValueError(f"status must be one of: {', '.join(sorted(allowed))}")
        now = int(time.time())
        await self.conn.execute(
            """
            UPDATE rescue_rooms
            SET status = ?,
                assigned_tg_id = ?,
                session_id = ?,
                fail_count = fail_count + ?,
                updated_at = ?
            WHERE room_id = ?
            """,
            (
                status,
                telegram_id,
                session_id,
                1 if increment_fail_count else 0,
                now,
                room_id.strip(),
            ),
        )
        await self.conn.commit()

