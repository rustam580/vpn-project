from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import aiosqlite

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
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY,
                marzban_username TEXT NOT NULL UNIQUE,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )
            """
        )
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS payments (
                provider TEXT NOT NULL,
                external_id TEXT NOT NULL,
                telegram_id INTEGER NOT NULL,
                days INTEGER NOT NULL,
                gb INTEGER NOT NULL,
                amount_rub REAL NOT NULL,
                pay_url TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY(provider, external_id)
            )
            """
        )
        await self._ensure_payments_columns()
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS devices (
                telegram_id INTEGER NOT NULL,
                device_id INTEGER NOT NULL,
                marzban_username TEXT NOT NULL UNIQUE,
                device_name TEXT,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY(telegram_id, device_id)
            )
            """
        )
        await self._ensure_devices_columns()
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS referrals (
                invited_telegram_id INTEGER PRIMARY KEY,
                referrer_telegram_id INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                bonus_applied INTEGER NOT NULL DEFAULT 0,
                bonus_paid_at INTEGER
            )
            """
        )
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS known_chats (
                telegram_id INTEGER PRIMARY KEY,
                first_seen_at INTEGER NOT NULL,
                last_seen_at INTEGER NOT NULL
            )
            """
        )
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER,
                event_type TEXT NOT NULL,
                event_value TEXT,
                event_meta TEXT,
                created_at INTEGER NOT NULL
            )
            """
        )
        await self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_created_at ON events(created_at)"
        )
        await self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_type_created ON events(event_type, created_at)"
        )
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS notification_marks (
                telegram_id INTEGER NOT NULL,
                device_id INTEGER NOT NULL,
                mark_type TEXT NOT NULL,
                expire_ts INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                PRIMARY KEY(telegram_id, device_id, mark_type, expire_ts)
            )
            """
        )
        await self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_notification_marks_created ON notification_marks(created_at)"
        )
        await self.conn.commit()

    async def _ensure_payments_columns(self) -> None:
        assert self.conn is not None
        c = await self.conn.execute("PRAGMA table_info(payments)")
        rows = await c.fetchall()
        await c.close()
        columns = {str(row["name"]) for row in rows}
        if "purpose" not in columns:
            await self.conn.execute(
                "ALTER TABLE payments ADD COLUMN purpose TEXT NOT NULL DEFAULT 'plan'"
            )
        if "device_slot" not in columns:
            await self.conn.execute(
                "ALTER TABLE payments ADD COLUMN device_slot INTEGER"
            )
        await self.conn.commit()

    async def _ensure_devices_columns(self) -> None:
        assert self.conn is not None
        c = await self.conn.execute("PRAGMA table_info(devices)")
        rows = await c.fetchall()
        await c.close()
        columns = {str(row["name"]) for row in rows}
        if "device_name" not in columns:
            await self.conn.execute("ALTER TABLE devices ADD COLUMN device_name TEXT")
        await self.conn.commit()

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
            except Exception:
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

