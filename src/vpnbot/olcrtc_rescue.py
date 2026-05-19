from __future__ import annotations

import asyncio
import json
import re
import shlex
import secrets
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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


@dataclass(frozen=True)
class RemoteRescueSession:
    session_id: str
    active: str
    room_url: str
    since: str = ""


@dataclass(frozen=True)
class RescuePoolCapacity:
    warm_active: int
    warm_stale: int
    free: int
    assigned: int
    total: int


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


def normalize_rescue_room_url(room: str) -> str:
    room_id = normalize_room_id(room, carrier="wbstream")
    return OlcRtcRescueConfig(room_id=room_id, key_hex="0" * 64).normalized().room_url


def build_rescue_uri_for_room(
    *,
    room: str,
    key_hex: str,
    client_id: str = "",
    label: str = DEFAULT_LABEL,
) -> str:
    room_id = normalize_room_id(room, carrier="wbstream")
    config = OlcRtcRescueConfig(room_id=room_id, key_hex=key_hex)
    return build_uri(config, label=label, client_id=client_id)


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


def build_restart_step(
    *,
    session_id: str,
    deploy_host: str,
    safe_ssh: bool = True,
) -> CommandStep:
    session_id = validate_session_id(session_id)
    unit = f"olcrtc-rescue@{session_id}"
    remote_command = (
        f"systemctl restart {shq(unit)}; "
        "sleep 3; "
        f"printf 'service: %s\\n' {shq(unit)}; "
        f"printf 'active: '; systemctl is-active {shq(unit)} || true"
    )
    ssh_prefix = _ssh_prefix() if safe_ssh else ["ssh"]
    return CommandStep(
        f"restart {unit}",
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


async def restart_rescue_session(
    *,
    session_id: str,
    deploy_host: str,
    timeout_sec: int = 30,
) -> RescueDeployResult:
    step = build_restart_step(session_id=session_id, deploy_host=deploy_host, safe_ssh=True)
    return await run_steps_async([step], timeout_sec=timeout_sec)


def diagnose_rescue_status_output(output: str) -> str:
    text = (output or "").lower()
    notes: list[str] = []
    if "guests cannot create rooms" in text or '"code":7' in text:
        notes.append(
            "WB auth 403: relay could not get a room token. Most likely the WB room was asleep/closed "
            "or had no active host presence when the service restarted. Rejoin the same WB room as host "
            "and wait 10-20 seconds; if it does not recover, create/warm a new room."
        )
    if "link connected" in text:
        notes.append("Link connected: the relay managed to join the carrier again; existing user URI/key can recover.")
    if "scheduled restart job" in text or "restart counter" in text:
        notes.append("Systemd restart loop was observed; the user's tunnel will drop while olcRTC restarts.")
    if "read/write on closed pipe" in text:
        notes.append("Closed pipe: olcRTC smux/control stream was reinstalled; short client reconnects are expected.")
    if "payload exceeds max_payload_size" in text or "frame too large" in text:
        notes.append("Payload framing issue seen; check traffic.max_payload_size and client/server version match.")
    if not notes:
        return ""
    return "Diagnosis:\n" + "\n".join(f"- {note}" for note in notes)


def build_list_step(
    *,
    deploy_host: str,
    remote_root: str = DEFAULT_REMOTE_ROOT,
    safe_ssh: bool = True,
) -> CommandStep:
    root = remote_root.rstrip("/")
    remote_command = (
        "printf 'session_id|active|room|since\\n'; "
        "systemctl list-units --all --plain --no-legend 'olcrtc-rescue@*.service' "
        "| awk '{print $1}' "
        "| while read -r unit; do "
        "[ -z \"$unit\" ] && continue; "
        "sid=${unit#olcrtc-rescue@}; sid=${sid%.service}; "
        "active=$(systemctl is-active \"$unit\" 2>/dev/null || true); "
        "since=$(systemctl show \"$unit\" -p ActiveEnterTimestamp --value 2>/dev/null || true); "
        f"room_file={shq(root)}/\"$sid\"/room-url.txt; "
        "room=''; [ -f \"$room_file\" ] && room=$(tr -d '\\r\\n' < \"$room_file\"); "
        "printf '%s|%s|%s|%s\\n' \"$sid\" \"$active\" \"$room\" \"$since\"; "
        "done"
    )
    ssh_prefix = _ssh_prefix() if safe_ssh else ["ssh"]
    return CommandStep("list rescue sessions", [*ssh_prefix, deploy_host, remote_command])


async def fetch_rescue_list(
    *,
    deploy_host: str,
    remote_root: str = DEFAULT_REMOTE_ROOT,
    timeout_sec: int = 30,
) -> RescueDeployResult:
    step = build_list_step(deploy_host=deploy_host, remote_root=remote_root, safe_ssh=True)
    return await run_steps_async([step], timeout_sec=timeout_sec)


def parse_rescue_list_output(output: str) -> list[RemoteRescueSession]:
    sessions: list[RemoteRescueSession] = []
    for raw_line in (output or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("$ ") or line == "session_id|active|room|since":
            continue
        parts = line.split("|", maxsplit=3)
        if len(parts) < 3:
            continue
        session_id, active, room_url = (part.strip() for part in parts[:3])
        if not session_id or not room_url:
            continue
        try:
            validate_session_id(session_id)
        except ValueError:
            continue
        since = parts[3].strip() if len(parts) == 4 else ""
        sessions.append(
            RemoteRescueSession(
                session_id=session_id,
                active=active,
                room_url=normalize_rescue_room_url(room_url),
                since=since,
            )
        )
    return sessions


def active_rescue_sessions_for_room(room: str, output: str) -> list[RemoteRescueSession]:
    expected_room = normalize_rescue_room_url(room)
    return [
        session
        for session in parse_rescue_list_output(output)
        if session.active == "active" and session.room_url == expected_room
    ]


def rescue_pool_capacity(
    rooms: list[dict[str, Any]],
    remote_sessions: list[RemoteRescueSession],
) -> RescuePoolCapacity:
    active_session_ids = {session.session_id for session in remote_sessions if session.active == "active"}
    warm_active = 0
    warm_stale = 0
    free = 0
    assigned = 0
    for room in rooms:
        status = str(room.get("status") or "")
        if status == "warm":
            if str(room.get("session_id") or "") in active_session_ids:
                warm_active += 1
            else:
                warm_stale += 1
        elif status == "free":
            free += 1
        elif status == "assigned":
            assigned += 1
    return RescuePoolCapacity(
        warm_active=warm_active,
        warm_stale=warm_stale,
        free=free,
        assigned=assigned,
        total=len(rooms),
    )


def rescue_room_broker_request_count(
    rooms: list[dict[str, Any]],
    remote_sessions: list[RemoteRescueSession],
    *,
    min_warm: int,
    min_free: int,
    max_rooms: int,
) -> int:
    capacity = rescue_pool_capacity(rooms, remote_sessions)
    warm_shortage = max(0, int(min_warm) - capacity.warm_active)
    free_after_warm = max(0, capacity.free - warm_shortage)
    free_shortage = max(0, int(min_free) - free_after_warm)
    return max(0, min(int(max_rooms), warm_shortage + free_shortage))


def rescue_pool_warm_candidates(
    rooms: list[dict[str, Any]],
    remote_sessions: list[RemoteRescueSession],
    *,
    min_warm: int,
    max_to_warm: int,
) -> list[dict[str, Any]]:
    needed = max(0, int(min_warm) - rescue_pool_capacity(rooms, remote_sessions).warm_active)
    limit = max(0, min(needed, int(max_to_warm)))
    if limit <= 0:
        return []

    candidates = [
        room
        for room in rooms
        if str(room.get("status") or "") == "free" and str(room.get("room_url") or "").strip()
    ]
    candidates.sort(
        key=lambda room: (
            int(room.get("fail_count") or 0),
            int(room.get("updated_at") or 0),
            int(room.get("id") or 0),
        )
    )
    return candidates[:limit]


def build_room_broker_step(
    *,
    command_template: str,
    count: int = 1,
) -> CommandStep:
    command = command_template.strip()
    if not command:
        raise ValueError("room broker command is empty")
    count = max(1, int(count))
    if "{count}" in command:
        command = command.replace("{count}", str(count))
    else:
        command = f"{command} --count {count}"
    return CommandStep("create WB room via broker", shlex.split(command))


async def run_room_broker(
    *,
    command_template: str,
    count: int = 1,
    timeout_sec: int = 45,
) -> RescueDeployResult:
    step = build_room_broker_step(command_template=command_template, count=count)
    return await run_steps_async([step], timeout_sec=timeout_sec)


def parse_room_broker_output(output: str) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()

    def add(value: Any) -> None:
        if not isinstance(value, str):
            return
        for match in re.findall(r"https://stream\.wb\.ru/room/[A-Za-z0-9_.-]+", value):
            normalized = normalize_rescue_room_url(match)
            if normalized not in seen:
                seen.add(normalized)
                urls.append(normalized)

    text = output or ""
    try:
        body = json.loads(text)
    except json.JSONDecodeError:
        body = None

    if isinstance(body, dict):
        add(body.get("room_url"))
        add(body.get("url"))
        for item in body.get("rooms") or []:
            if isinstance(item, dict):
                add(item.get("room_url"))
                add(item.get("url"))
            else:
                add(item)
    elif isinstance(body, list):
        for item in body:
            if isinstance(item, dict):
                add(item.get("room_url"))
                add(item.get("url"))
            else:
                add(item)

    if not urls:
        add(text)
    return urls


def format_rescue_dashboard(
    output: str,
    *,
    deploy_host: str = "",
) -> str:
    sessions = parse_rescue_list_output(output)
    active_count = sum(1 for session in sessions if session.active == "active")
    inactive_count = len(sessions) - active_count
    header = [
        "🆘 Rescue Dashboard",
        f"host: {deploy_host or '-'}",
        f"sessions: {len(sessions)} total / {active_count} active / {inactive_count} inactive",
    ]
    if not sessions:
        return "\n".join([*header, "", "No Rescue sessions found."])

    lines = [*header, ""]
    for idx, session in enumerate(sessions, start=1):
        icon = "🟢" if session.active == "active" else "⚪"
        lines.extend(
            [
                f"{idx}. {icon} {session.session_id}",
                f"   status: {session.active}",
                f"   room: {session.room_url}",
                f"   since: {session.since or '-'}",
                f"   status cmd: /rescue_status {session.session_id}",
                f"   stop cmd: /rescue_stop {session.session_id}",
                "",
            ]
        )
    return "\n".join(lines).rstrip()


def rescue_watchdog_findings(output: str) -> list[RemoteRescueSession]:
    return [
        session
        for session in parse_rescue_list_output(output)
        if session.active != "active"
    ]


def format_rescue_watchdog_alert(
    findings: list[RemoteRescueSession],
    *,
    deploy_host: str = "",
) -> str:
    if not findings:
        return "Rescue watchdog: OK"
    lines = [
        "Rescue watchdog findings",
        f"host: {deploy_host or '-'}",
        f"problem sessions: {len(findings)}",
        "",
    ]
    for session in findings[:10]:
        lines.extend(
            [
                f"- {session.session_id}",
                f"  status: {session.active or '-'}",
                f"  room: {session.room_url}",
                f"  since: {session.since or '-'}",
                f"  check: /rescue_status {session.session_id}",
                f"  stop: /rescue_stop {session.session_id}",
            ]
        )
    if len(findings) > 10:
        lines.append(f"... and {len(findings) - 10} more")
    return "\n".join(lines)


def build_stop_step(
    *,
    session_id: str,
    deploy_host: str,
    safe_ssh: bool = True,
) -> CommandStep:
    session_id = validate_session_id(session_id)
    unit = f"olcrtc-rescue@{session_id}"
    remote_command = (
        f"systemctl disable --now {shq(unit)}; "
        f"systemctl reset-failed {shq(unit)} || true; "
        f"printf 'service: %s\\n' {shq(unit)}; "
        f"printf 'active: '; systemctl is-active {shq(unit)} || true"
    )
    ssh_prefix = _ssh_prefix() if safe_ssh else ["ssh"]
    return CommandStep(f"stop {unit}", [*ssh_prefix, deploy_host, remote_command])


async def stop_rescue_session(
    *,
    session_id: str,
    deploy_host: str,
    timeout_sec: int = 30,
) -> RescueDeployResult:
    step = build_stop_step(session_id=session_id, deploy_host=deploy_host, safe_ssh=True)
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
