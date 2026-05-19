from __future__ import annotations

import asyncio
import json
import re
import secrets
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from scripts.generate_olcrtc_rescue_configs import (
    OlcRtcRescueConfig,
    build_uri,
    new_key_hex,
    normalize_room_id,
    write_outputs,
)

DEFAULT_OUT_ROOT = Path("out/olcrtc-sessions")
DEFAULT_REMOTE_ROOT = "/etc/rootvpn/rescue"
DEFAULT_SERVICE_NAME = "olcrtc-rescue@.service"
DEFAULT_LABEL = "RootVPN Rescue Beta"
SERVICE_TEMPLATE_PATH = Path("experiments/olcrtc-rescue/systemd/olcrtc-rescue@.service")
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


@dataclass(frozen=True)
class RescueSession:
    session_id: str
    room_id: str
    room_url: str
    client_id: str
    key_hex: str
    out_dir: Path
    uri: str


@dataclass(frozen=True)
class CommandStep:
    description: str
    command: list[str]


@dataclass(frozen=True)
class RescueDeployResult:
    ok: bool
    output: str
    failed_step: str = ""


@dataclass(frozen=True)
class RescueCommandArgs:
    target_tg_id: int
    room: str


def make_session_id(*, tg_id: str = "", room_id: str = "", now: datetime | None = None) -> str:
    now = now or datetime.now(tz=UTC)
    suffix_source = tg_id or room_id or secrets.token_hex(3)
    suffix = re.sub(r"[^A-Za-z0-9_.-]+", "-", suffix_source).strip("-_.")[:24] or secrets.token_hex(3)
    return f"rs-{now.strftime('%Y%m%d%H%M%S')}-{suffix}"


def validate_session_id(session_id: str) -> str:
    if not SAFE_ID_RE.fullmatch(session_id):
        raise ValueError("session_id must be 1..64 chars of A-Z, a-z, 0-9, _, ., -")
    if session_id in {".", ".."}:
        raise ValueError("session_id cannot be . or ..")
    return session_id


def default_client_id(*, tg_id: str = "") -> str:
    if tg_id:
        return f"tg_{re.sub(r'[^0-9A-Za-z_.-]+', '_', tg_id)}"
    return "olcbox"


def parse_rescue_command_args(text: str) -> RescueCommandArgs:
    parts = (text or "").split(maxsplit=2)
    if len(parts) != 3:
        raise ValueError("usage")
    command = parts[0].split("@", 1)[0]
    if command != "/rescue":
        raise ValueError("usage")
    try:
        target_tg_id = int(parts[1])
    except ValueError as exc:
        raise ValueError("bad_tg_id") from exc
    if target_tg_id <= 0:
        raise ValueError("bad_tg_id")
    room = parts[2].strip()
    if not room:
        raise ValueError("usage")
    return RescueCommandArgs(target_tg_id=target_tg_id, room=room)


def create_local_session(
    *,
    room: str,
    tg_id: str = "",
    session_id: str = "",
    client_id: str = "",
    out_root: Path = DEFAULT_OUT_ROOT,
    label: str = DEFAULT_LABEL,
    key_hex: str = "",
    debug: bool = False,
) -> RescueSession:
    room_id = normalize_room_id(room, carrier="wbstream")
    session_id = validate_session_id(session_id or make_session_id(tg_id=tg_id, room_id=room_id))
    client_id = client_id or default_client_id(tg_id=tg_id)
    key_hex = key_hex or new_key_hex()
    config = OlcRtcRescueConfig(room_id=room_id, key_hex=key_hex, debug=debug)
    out_dir = out_root / session_id
    write_outputs(config, out_dir, label=label, client_id=client_id, created_room=False)
    uri = build_uri(config, label=label, client_id=client_id)
    _write_user_message(out_dir, uri=uri)
    _write_operator_summary(
        out_dir,
        session_id=session_id,
        room_id=room_id,
        room_url=config.normalized().room_url,
        client_id=client_id,
        tg_id=tg_id,
    )
    return RescueSession(
        session_id=session_id,
        room_id=room_id,
        room_url=config.normalized().room_url,
        client_id=client_id,
        key_hex=key_hex,
        out_dir=out_dir,
        uri=uri,
    )


def build_rescue_user_message(uri: str) -> str:
    return (
        "🆘 RootVPN Rescue Beta\n\n"
        "Это аварийный режим для мобильного интернета, когда работают только белые списки.\n\n"
        "Что сделать:\n"
        "1. Дождитесь сообщения от поддержки, что канал активирован.\n"
        "2. Отключите другие VPN-приложения.\n"
        "3. Откройте RootVPN Rescue / Olcbox.\n"
        "4. Добавьте эту ссылку как custom location:\n"
        f"{uri}\n"
        "5. Нажмите START.\n"
        "6. Проверьте 2ip.ru или любой сайт, который без Rescue не открывался.\n\n"
        "Если комната сбросится, напишите в поддержку: мы выдадим новую ссылку."
    )


def build_rescue_admin_summary(
    session: RescueSession,
    *,
    deploy_host: str = "root@104.238.29.239",
) -> str:
    return (
        "Rescue session prepared.\n\n"
        f"session_id: {session.session_id}\n"
        f"client_id: {session.client_id}\n"
        f"room: {session.room_url}\n"
        f"artifacts: {session.out_dir}\n\n"
        "Deploy command:\n"
        "python scripts/manage_olcrtc_rescue_session.py create "
        f"\"{session.room_url}\" --session-id {session.session_id} "
        f"--client-id {session.client_id} --key {session.key_hex} "
        f"--deploy-host {deploy_host} --install-service\n\n"
        "User URI:\n"
        f"{session.uri}"
    )


def _write_user_message(out_dir: Path, *, uri: str) -> None:
    (out_dir / "user-message.txt").write_text(build_rescue_user_message(uri) + "\n", encoding="utf-8")


def _write_operator_summary(
    out_dir: Path,
    *,
    session_id: str,
    room_id: str,
    room_url: str,
    client_id: str,
    tg_id: str,
) -> None:
    summary = {
        "session_id": session_id,
        "room_id": room_id,
        "room_url": room_url,
        "client_id": client_id,
        "telegram_id": tg_id,
        "local_files": {
            "server_yaml": "server.yaml",
            "uri": "uri.txt",
            "user_message": "user-message.txt",
            "session_json": "session.json",
        },
    }
    (out_dir / "operator-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def build_deploy_steps(
    *,
    session_id: str,
    local_dir: Path,
    deploy_host: str,
    remote_root: str = DEFAULT_REMOTE_ROOT,
    install_service: bool = False,
    start_service: bool = True,
    safe_ssh: bool = False,
) -> list[CommandStep]:
    validate_session_id(session_id)
    remote_dir = f"{remote_root.rstrip('/')}/{session_id}"
    steps: list[CommandStep] = []
    ssh_prefix = _ssh_prefix() if safe_ssh else ["ssh"]
    scp_prefix = _scp_prefix() if safe_ssh else ["scp"]
    if install_service:
        steps.append(
            CommandStep(
                "install systemd service template",
                [
                    *scp_prefix,
                    str(SERVICE_TEMPLATE_PATH),
                    f"{deploy_host}:/etc/systemd/system/{DEFAULT_SERVICE_NAME}",
                ],
            )
        )
    steps.append(
        CommandStep(
            "create remote session directory",
            [*ssh_prefix, deploy_host, f"install -d -m 700 {shq(remote_dir)}"],
        )
    )
    for name in ("server.yaml", "session.json", "room-url.txt", "operator-summary.json"):
        steps.append(
            CommandStep(
                f"upload {name}",
                [*scp_prefix, str(local_dir / name), f"{deploy_host}:{remote_dir}/{name}"],
            )
        )
    if install_service or start_service:
        steps.append(CommandStep("reload systemd", [*ssh_prefix, deploy_host, "systemctl daemon-reload"]))
    if start_service:
        steps.append(
            CommandStep(
                "start rescue session",
                [*ssh_prefix, deploy_host, f"systemctl enable --now olcrtc-rescue@{session_id}"],
            )
        )
    return steps


def build_status_step(
    *,
    session_id: str,
    deploy_host: str,
    safe_ssh: bool = True,
    journal_lines: int = 80,
) -> CommandStep:
    session_id = validate_session_id(session_id)
    unit = f"olcrtc-rescue@{session_id}"
    lines = max(10, min(300, int(journal_lines)))
    remote_command = (
        f"printf 'service: %s\\n' {shq(unit)}; "
        f"printf 'active: '; systemctl is-active {shq(unit)} || true; "
        f"systemctl status --no-pager -l {shq(unit)} || true; "
        f"journalctl -u {shq(unit)} -n {lines} --no-pager || true"
    )
    ssh_prefix = _ssh_prefix() if safe_ssh else ["ssh"]
    return CommandStep(
        f"status {unit}",
        [*ssh_prefix, deploy_host, remote_command],
    )


async def fetch_rescue_status(
    *,
    session_id: str,
    deploy_host: str,
    timeout_sec: int = 30,
    journal_lines: int = 80,
) -> RescueDeployResult:
    step = build_status_step(
        session_id=session_id,
        deploy_host=deploy_host,
        safe_ssh=True,
        journal_lines=journal_lines,
    )
    return await run_steps_async([step], timeout_sec=timeout_sec)


def _ssh_prefix() -> list[str]:
    return [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=8",
        "-o",
        "StrictHostKeyChecking=accept-new",
    ]


def _scp_prefix() -> list[str]:
    return [
        "scp",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=8",
        "-o",
        "StrictHostKeyChecking=accept-new",
    ]


def run_steps(steps: list[CommandStep], *, dry_run: bool = False) -> None:
    for step in steps:
        printable = " ".join(step.command)
        print(f"[{step.description}] {printable}")
        if not dry_run:
            subprocess.run(step.command, check=True)  # noqa: S603 - explicit operator-controlled SSH/SCP commands.


async def run_steps_async(
    steps: list[CommandStep],
    *,
    timeout_sec: int = 60,
) -> RescueDeployResult:
    output_parts: list[str] = []
    for step in steps:
        output_parts.append(f"$ {' '.join(step.command)}")
        try:
            proc = await asyncio.create_subprocess_exec(
                *step.command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            try:
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)
            except TimeoutError:
                proc.kill()
                await proc.communicate()
                output_parts.append(f"TIMEOUT after {timeout_sec}s")
                return RescueDeployResult(False, _trim_output("\n".join(output_parts)), step.description)
        except Exception as exc:
            output_parts.append(f"FAILED TO START: {exc}")
            return RescueDeployResult(False, _trim_output("\n".join(output_parts)), step.description)

        text = (stdout or b"").decode("utf-8", errors="replace").strip()
        if text:
            output_parts.append(text)
        if proc.returncode != 0:
            output_parts.append(f"EXIT CODE: {proc.returncode}")
            return RescueDeployResult(False, _trim_output("\n".join(output_parts)), step.description)

    return RescueDeployResult(True, _trim_output("\n".join(output_parts)))


def _trim_output(text: str, limit: int = 3000) -> str:
    if len(text) <= limit:
        return text
    return text[-limit:]


def shq(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"
