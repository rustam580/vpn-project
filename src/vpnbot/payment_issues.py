from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol


class RepoLike(Protocol):
    async def payment_status_counts(self) -> dict[str, int]: ...

    async def list_stale_processing_payments(
        self, *, older_than_sec: int, limit: int = 20
    ) -> list[dict[str, Any]]: ...

    async def list_old_unfinished_payments(
        self, *, older_than_sec: int, limit: int = 20
    ) -> list[dict[str, Any]]: ...

    async def list_paid_web_orders_without_access(self, *, limit: int = 20) -> list[dict[str, Any]]: ...

    async def list_paid_web_orders_with_access(self, *, limit: int = 50) -> list[dict[str, Any]]: ...


class MarzbanLike(Protocol):
    async def get_user(self, username: str) -> dict[str, Any] | None: ...


class SettingsLike(Protocol):
    payment_processing_requeue_seconds: int


@dataclass(frozen=True)
class PaymentIssuesReport:
    generated_at: int
    payment_counts: dict[str, int]
    stale_processing: list[dict[str, Any]]
    old_unfinished: list[dict[str, Any]]
    paid_web_without_access: list[dict[str, Any]]
    paid_web_missing_marzban: list[dict[str, Any]]

    @property
    def has_findings(self) -> bool:
        return any(
            (
                self.stale_processing,
                self.old_unfinished,
                self.paid_web_without_access,
                self.paid_web_missing_marzban,
            )
        )

    def summary_text(self, *, show: int = 8) -> str:
        result = "CHECK_FINDINGS" if self.has_findings else "OK"
        lines = [
            "💳 Payment/access issues report",
            f"Время: {_format_ts(self.generated_at)} UTC",
            f"Result: {result}",
            "",
            "Статусы платежей:",
        ]
        if self.payment_counts:
            lines.extend(f"- {status}: {count}" for status, count in sorted(self.payment_counts.items()))
        else:
            lines.append("- данных нет")

        lines.extend(_section_payments("stale_processing_payments", self.stale_processing, show=show))
        lines.extend(_section_payments("old_unfinished_payments", self.old_unfinished, show=show))
        lines.extend(_section_orders("paid_web_orders_without_access", self.paid_web_without_access, show=show))
        lines.extend(_section_orders("paid_web_orders_missing_marzban", self.paid_web_missing_marzban, show=show))

        if self.has_findings:
            lines.extend(
                [
                    "",
                    "Что делать:",
                    "- processing: подождать worker requeue или вручную /check <provider> <payment_id>",
                    "- web без доступа: открыть 🧭 Marzban/DB аудит и применить safe action",
                    "- missing Marzban: проверить пользователя в панели или восстановить через drift-аудит",
                ]
            )
        return "\n".join(lines)


async def collect_payment_issues(
    *,
    repo: RepoLike,
    marzban: MarzbanLike,
    settings: SettingsLike,
    stale_processing_limit: int = 10,
    old_unfinished_limit: int = 10,
    web_without_access_limit: int = 10,
    web_verify_limit: int = 50,
) -> PaymentIssuesReport:
    requeue_sec = max(60, int(settings.payment_processing_requeue_seconds))
    old_unfinished_sec = max(3600, requeue_sec * 2)
    payment_counts = await repo.payment_status_counts()
    stale_processing = await repo.list_stale_processing_payments(
        older_than_sec=requeue_sec,
        limit=stale_processing_limit,
    )
    old_unfinished = await repo.list_old_unfinished_payments(
        older_than_sec=old_unfinished_sec,
        limit=old_unfinished_limit,
    )
    paid_web_without_access = await repo.list_paid_web_orders_without_access(
        limit=web_without_access_limit,
    )
    paid_web_with_access = await repo.list_paid_web_orders_with_access(limit=web_verify_limit)

    missing_marzban: list[dict[str, Any]] = []
    for order in paid_web_with_access:
        username = str(order.get("marzban_username") or "").strip()
        if not username:
            continue
        user = await marzban.get_user(username)
        if user is None:
            missing_marzban.append(order)
            if len(missing_marzban) >= web_without_access_limit:
                break

    return PaymentIssuesReport(
        generated_at=int(time.time()),
        payment_counts=payment_counts,
        stale_processing=stale_processing,
        old_unfinished=old_unfinished,
        paid_web_without_access=paid_web_without_access,
        paid_web_missing_marzban=missing_marzban,
    )


async def build_payment_issues_report(
    repo: RepoLike,
    marzban: MarzbanLike,
    settings: SettingsLike,
    *,
    show: int = 8,
) -> str:
    report = await collect_payment_issues(repo=repo, marzban=marzban, settings=settings)
    return report.summary_text(show=show)


def _section_payments(title: str, rows: list[dict[str, Any]], *, show: int) -> list[str]:
    lines = ["", f"{title}: {len(rows)}"]
    if not rows:
        return lines
    now = int(time.time())
    for row in rows[: max(1, show)]:
        provider = str(row.get("provider") or "")
        external_id = str(row.get("external_id") or "")
        tg_id = row.get("telegram_id")
        status = str(row.get("status") or "")
        purpose = str(row.get("purpose") or "")
        slot = row.get("device_slot")
        updated_at = _safe_int(row.get("updated_at"))
        age = _format_age(now - updated_at) if updated_at else "n/a"
        lines.append(
            f"- {provider}:{external_id} tg={tg_id} status={status} purpose={purpose} "
            f"slot={slot or '-'} age={age} amount={row.get('amount_rub')}"
        )
    if len(rows) > show:
        lines.append(f"... еще {len(rows) - show}")
    return lines


def _section_orders(title: str, rows: list[dict[str, Any]], *, show: int) -> list[str]:
    lines = ["", f"{title}: {len(rows)}"]
    if not rows:
        return lines
    now = int(time.time())
    for row in rows[: max(1, show)]:
        order_id = str(row.get("order_id") or "")
        provider = str(row.get("provider") or "")
        external_id = str(row.get("external_id") or "")
        username = str(row.get("marzban_username") or "-")
        plan = str(row.get("plan_key") or "")
        updated_at = _safe_int(row.get("updated_at"))
        age = _format_age(now - updated_at) if updated_at else "n/a"
        contact = str(row.get("customer_contact") or "-").strip() or "-"
        lines.append(
            f"- order={order_id} provider={provider}:{external_id} plan={plan} "
            f"user={username} contact={contact} age={age}"
        )
        lines.append(f"  action: /user {order_id}")
        if provider and external_id:
            lines.append(f"  check: /check {provider} {external_id}")
    if len(rows) > show:
        lines.append(f"... еще {len(rows) - show}")
    return lines


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _format_ts(ts: int) -> str:
    return datetime.fromtimestamp(int(ts), tz=UTC).strftime("%Y-%m-%d %H:%M:%S")


def _format_age(seconds: int) -> str:
    seconds = max(0, int(seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"
