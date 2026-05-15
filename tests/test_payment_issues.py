from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

import pytest_asyncio

from src.vpnbot.db.bot_repo import Repo
from src.vpnbot.payment_issues import build_payment_issues_report, collect_payment_issues


@pytest_asyncio.fixture
async def repo(local_tmp_path):
    db_path = local_tmp_path / "bot.sqlite3"
    repo = Repo(str(db_path))
    await repo.open()
    try:
        yield repo
    finally:
        await repo.close()


@pytest_asyncio.fixture
async def repo_conn(repo):
    assert repo.conn is not None
    return repo.conn


class FakeMarzban:
    def __init__(self, users: dict[str, dict[str, Any]] | None = None):
        self.users = users or {}

    async def get_user(self, username: str) -> dict[str, Any] | None:
        return self.users.get(username)


async def test_collect_payment_issues_detects_stale_processing(repo, repo_conn) -> None:
    await repo.upsert_payment(
        provider="card",
        external_id="pay-stuck",
        telegram_id=1001,
        days=30,
        gb=0,
        amount_rub=99.0,
        pay_url="https://pay.local/stuck",
        status="processing",
        purpose="plan",
    )
    stale_ts = int(time.time()) - 7200
    await repo_conn.execute(
        "UPDATE payments SET updated_at = ? WHERE provider = ? AND external_id = ?",
        (stale_ts, "card", "pay-stuck"),
    )
    await repo_conn.commit()

    report = await collect_payment_issues(
        repo=repo,
        marzban=FakeMarzban(),
        settings=SimpleNamespace(payment_processing_requeue_seconds=300),
    )

    assert report.has_findings is True
    assert [row["external_id"] for row in report.stale_processing] == ["pay-stuck"]


async def test_collect_payment_issues_detects_paid_web_order_without_access(repo) -> None:
    await repo.create_web_order(
        order_id="ord-no-access",
        provider="card",
        external_id="pay-no-access",
        status="paid_applied",
        plan_key="m1",
        days=30,
        gb=0,
        amount_rub=99.0,
        customer_contact="client@example.com",
        pay_url="https://pay.local/no-access",
    )

    report = await collect_payment_issues(
        repo=repo,
        marzban=FakeMarzban(),
        settings=SimpleNamespace(payment_processing_requeue_seconds=300),
    )

    assert [row["order_id"] for row in report.paid_web_without_access] == ["ord-no-access"]


async def test_payment_issues_report_includes_direct_web_order_actions(repo) -> None:
    await repo.create_web_order(
        order_id="ord-action",
        provider="card",
        external_id="pay-action",
        status="paid_applied",
        plan_key="m1",
        days=30,
        gb=0,
        amount_rub=99.0,
        customer_contact="client@example.com",
        pay_url="https://pay.local/action",
    )

    text = await build_payment_issues_report(
        repo,
        FakeMarzban(),
        SimpleNamespace(payment_processing_requeue_seconds=300),
    )

    assert "/user ord-action" in text
    assert "/check card pay-action" in text


async def test_collect_payment_issues_detects_web_access_missing_in_marzban(repo) -> None:
    await repo.create_web_order(
        order_id="ord-missing-marzban",
        provider="card",
        external_id="pay-missing-marzban",
        status="paid_applied",
        plan_key="m3",
        days=90,
        gb=0,
        amount_rub=259.0,
        customer_contact="@client",
        pay_url="https://pay.local/missing",
    )
    await repo.attach_web_order_access(
        order_id="ord-missing-marzban",
        marzban_username="web_missing",
    )

    report = await collect_payment_issues(
        repo=repo,
        marzban=FakeMarzban(users={"web_other": {"username": "web_other"}}),
        settings=SimpleNamespace(payment_processing_requeue_seconds=300),
    )

    assert [row["order_id"] for row in report.paid_web_missing_marzban] == ["ord-missing-marzban"]


async def test_build_payment_issues_report_ok_when_clean(repo) -> None:
    text = await build_payment_issues_report(
        repo,
        FakeMarzban(),
        SimpleNamespace(payment_processing_requeue_seconds=300),
    )

    assert "Result: OK" in text
    assert "stale_processing_payments: 0" in text


async def test_build_payment_issues_report_includes_action_hints(repo, repo_conn) -> None:
    await repo.upsert_payment(
        provider="crypto",
        external_id="inv-old",
        telegram_id=1002,
        days=30,
        gb=0,
        amount_rub=99.0,
        pay_url="https://pay.local/inv-old",
        status="pending",
    )
    old_ts = int(time.time()) - 7200
    await repo_conn.execute(
        "UPDATE payments SET created_at = ?, updated_at = ? WHERE provider = ? AND external_id = ?",
        (old_ts, old_ts, "crypto", "inv-old"),
    )
    await repo_conn.commit()

    text = await build_payment_issues_report(
        repo,
        FakeMarzban(),
        SimpleNamespace(payment_processing_requeue_seconds=300),
    )

    assert "Result: CHECK_FINDINGS" in text
    assert "crypto:inv-old" in text
    assert "/check <provider> <payment_id>" in text
