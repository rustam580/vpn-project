from __future__ import annotations

import sys
from datetime import UTC, datetime

import pytest

from scripts.manage_olcrtc_rescue_session import (
    build_rescue_admin_summary,
    build_deploy_steps,
    build_status_step,
    create_local_session,
    default_client_id,
    make_session_id,
    parse_rescue_command_args,
    run_steps_async,
    validate_session_id,
)
from src.vpnbot.olcrtc_rescue import CommandStep


KEY = "b" * 64


def test_make_session_id_is_stable_and_safe():
    now = datetime(2026, 5, 18, 16, 40, 1, tzinfo=UTC)

    session_id = make_session_id(tg_id="386029735", now=now)

    assert session_id == "rs-20260518164001-386029735"
    assert validate_session_id(session_id) == session_id


def test_validate_session_id_rejects_shell_sensitive_chars():
    with pytest.raises(ValueError, match="session_id"):
        validate_session_id("bad;id")

    with pytest.raises(ValueError, match="session_id"):
        validate_session_id("../bad")


def test_default_client_id_uses_telegram_id_when_available():
    assert default_client_id(tg_id="386029735") == "tg_386029735"
    assert default_client_id() == "olcbox"


def test_parse_rescue_command_args_accepts_bot_suffix():
    args = parse_rescue_command_args("/rescue@RootVPNBot 386029735 https://stream.wb.ru/room/room-1")

    assert args.target_tg_id == 386029735
    assert args.room == "https://stream.wb.ru/room/room-1"


def test_parse_rescue_command_args_rejects_bad_target():
    with pytest.raises(ValueError, match="bad_tg_id"):
        parse_rescue_command_args("/rescue nope https://stream.wb.ru/room/room-1")


def test_create_local_session_writes_operator_and_user_artifacts(tmp_path):
    session = create_local_session(
        room="https://stream.wb.ru/room/room-1",
        tg_id="386029735",
        session_id="rs-test",
        out_root=tmp_path,
        key_hex=KEY,
    )

    assert session.session_id == "rs-test"
    assert session.room_id == "room-1"
    assert session.client_id == "tg_386029735"
    assert session.key_hex == KEY
    assert session.out_dir == tmp_path / "rs-test"
    assert "%tg_386029735$RootVPN Rescue Beta" in session.uri
    assert (session.out_dir / "server.yaml").exists()
    assert (session.out_dir / "uri.txt").read_text(encoding="utf-8").strip() == session.uri
    assert "RootVPN Rescue Beta" in (session.out_dir / "user-message.txt").read_text(encoding="utf-8")
    assert '"session_id": "rs-test"' in (session.out_dir / "operator-summary.json").read_text(encoding="utf-8")


def test_build_rescue_admin_summary_contains_replayable_deploy_command(tmp_path):
    session = create_local_session(
        room="https://stream.wb.ru/room/room-1",
        tg_id="386029735",
        session_id="rs-test",
        out_root=tmp_path,
        key_hex=KEY,
    )

    summary = build_rescue_admin_summary(session)

    assert "--session-id rs-test" in summary
    assert f"--key {KEY}" in summary
    assert "--deploy-host root@104.238.29.239" in summary


def test_build_deploy_steps_install_and_start_plan(tmp_path):
    local_dir = tmp_path / "rs-test"
    local_dir.mkdir()

    steps = build_deploy_steps(
        session_id="rs-test",
        local_dir=local_dir,
        deploy_host="root@104.238.29.239",
        install_service=True,
    )

    commands = [step.command for step in steps]
    assert commands[0][0] == "scp"
    assert commands[0][1].replace("\\", "/").endswith("experiments/olcrtc-rescue/systemd/olcrtc-rescue@.service")
    assert ["ssh", "root@104.238.29.239", "install -d -m 700 '/etc/rootvpn/rescue/rs-test'"] in commands
    assert ["ssh", "root@104.238.29.239", "systemctl daemon-reload"] in commands
    assert ["ssh", "root@104.238.29.239", "systemctl enable --now olcrtc-rescue@rs-test"] in commands


def test_build_deploy_steps_can_use_noninteractive_ssh_options(tmp_path):
    steps = build_deploy_steps(
        session_id="rs-test",
        local_dir=tmp_path,
        deploy_host="root@example",
        safe_ssh=True,
    )

    commands = [step.command for step in steps]
    assert commands[0][:7] == [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=8",
        "-o",
        "StrictHostKeyChecking=accept-new",
    ]
    assert any(command[0] == "scp" and "BatchMode=yes" in command for command in commands)


def test_build_deploy_steps_can_skip_start(tmp_path):
    steps = build_deploy_steps(
        session_id="rs-test",
        local_dir=tmp_path,
        deploy_host="root@example",
        start_service=False,
    )

    flat = [" ".join(step.command) for step in steps]
    assert not any("systemctl enable --now" in command for command in flat)


def test_build_status_step_uses_safe_session_id_and_journal_tail():
    step = build_status_step(
        session_id="rs-20260518202449-386029735",
        deploy_host="rootvpn-rescue-fi",
        journal_lines=120,
    )

    command = " ".join(step.command)
    assert step.command[0] == "ssh"
    assert "BatchMode=yes" in step.command
    assert "rootvpn-rescue-fi" in step.command
    assert "olcrtc-rescue@rs-20260518202449-386029735" in command
    assert "journalctl" in command
    assert "-n 120" in command


def test_build_status_step_rejects_bad_session_id():
    with pytest.raises(ValueError, match="session_id"):
        build_status_step(session_id="bad;id", deploy_host="rootvpn-rescue-fi")


@pytest.mark.asyncio
async def test_run_steps_async_reports_success():
    result = await run_steps_async(
        [CommandStep("ok", [sys.executable, "-c", "print('ok')"])],
        timeout_sec=5,
    )

    assert result.ok is True
    assert "ok" in result.output


@pytest.mark.asyncio
async def test_run_steps_async_reports_failed_step():
    result = await run_steps_async(
        [CommandStep("fail", [sys.executable, "-c", "import sys; print('bad'); sys.exit(7)"])],
        timeout_sec=5,
    )

    assert result.ok is False
    assert result.failed_step == "fail"
    assert "bad" in result.output
