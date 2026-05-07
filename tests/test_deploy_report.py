from types import SimpleNamespace

import pytest

import bot
from src.vpnbot import deploy_reports


class FakeBot:
    def __init__(self) -> None:
        self.messages: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str, **_kwargs) -> None:
        self.messages.append((int(chat_id), text))


@pytest.mark.asyncio
async def test_send_deploy_report_waits_until_exit_marker(local_tmp_path, monkeypatch) -> None:
    log_path = local_tmp_path / "last-deploy.log"
    monkeypatch.setattr(deploy_reports, "DEPLOY_REPORT_PATH", log_path)

    fake_bot = FakeBot()
    settings = SimpleNamespace(admin_ids=[123], deploy_broadcast_users=False)

    log_path.write_text(
        "Deploy started: 2026-03-22 07:00:00 UTC\n"
        "==> Restart vpn-bot\n",
        encoding="utf-8",
    )

    await bot.send_deploy_report_if_any(fake_bot, settings, repo=None)
    assert fake_bot.messages == []
    assert log_path.exists()

    log_path.write_text(
        "Deploy started: 2026-03-22 07:00:00 UTC\n"
        "==> Restart vpn-bot\n"
        "OK: deploy done\n"
        "exit=0\n",
        encoding="utf-8",
    )

    await bot.send_deploy_report_if_any(fake_bot, settings, repo=None)
    assert len(fake_bot.messages) == 1
    assert fake_bot.messages[0][0] == 123
    assert "Deploy: OK" in fake_bot.messages[0][1]
    assert not log_path.exists()
