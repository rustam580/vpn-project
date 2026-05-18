from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.vpnbot.olcrtc_rescue import (  # noqa: E402
    DEFAULT_LABEL,
    DEFAULT_OUT_ROOT,
    DEFAULT_REMOTE_ROOT,
    CommandStep,
    RescueCommandArgs,
    RescueSession,
    build_deploy_steps,
    build_rescue_admin_summary,
    build_rescue_user_message,
    create_local_session,
    default_client_id,
    make_session_id,
    parse_rescue_command_args,
    run_steps,
    run_steps_async,
    shq,
    validate_session_id,
)

__all__ = [
    "CommandStep",
    "RescueCommandArgs",
    "RescueSession",
    "build_deploy_steps",
    "build_rescue_admin_summary",
    "build_rescue_user_message",
    "create_local_session",
    "default_client_id",
    "make_session_id",
    "parse_rescue_command_args",
    "run_steps",
    "run_steps_async",
    "shq",
    "validate_session_id",
]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create and optionally deploy a RootVPN olcRTC Rescue session")
    sub = parser.add_subparsers(dest="cmd", required=True)

    create = sub.add_parser("create", help="create session artifacts from a WB Stream room URL")
    create.add_argument("room", help="WB Stream room URL or room id")
    create.add_argument("--tg-id", default="", help="Telegram id for operator metadata/client id")
    create.add_argument("--session-id", default="", help="override generated session id")
    create.add_argument("--client-id", default="", help="override olcRTC URI client id")
    create.add_argument("--out-root", default=str(DEFAULT_OUT_ROOT))
    create.add_argument("--label", default=DEFAULT_LABEL)
    create.add_argument("--key", default="", help="64-hex shared key; generated when omitted")
    create.add_argument("--debug", action="store_true")
    create.add_argument("--deploy-host", default="", help="SSH target, e.g. root@104.238.29.239")
    create.add_argument("--remote-root", default=DEFAULT_REMOTE_ROOT)
    create.add_argument("--install-service", action="store_true")
    create.add_argument("--no-start", action="store_true")
    create.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args.cmd == "create":
        session = create_local_session(
            room=args.room,
            tg_id=args.tg_id,
            session_id=args.session_id,
            client_id=args.client_id,
            out_root=Path(args.out_root),
            label=args.label,
            key_hex=args.key,
            debug=args.debug,
        )
        print(f"Created Rescue session: {session.session_id}")
        print(f"Artifacts: {session.out_dir}")
        print(session.uri)
        if args.deploy_host:
            steps = build_deploy_steps(
                session_id=session.session_id,
                local_dir=session.out_dir,
                deploy_host=args.deploy_host,
                remote_root=args.remote_root,
                install_service=args.install_service,
                start_service=not args.no_start,
            )
            run_steps(steps, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
