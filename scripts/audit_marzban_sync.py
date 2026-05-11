from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, cast


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import Settings  # noqa: E402
from src.vpnbot.db.bot_repo import Repo  # noqa: E402
from src.vpnbot.marzban_sync import audit_marzban_sync  # noqa: E402
from src.vpnbot.services.bot_marzban import MarzbanClient  # noqa: E402


def _print_section(title: str, items: list[str], *, limit: int) -> None:
    print(f"\n== {title}: {len(items)} ==")
    for item in items[:limit]:
        print(item)
    if len(items) > limit:
        print(f"... {len(items) - limit} more")


async def main() -> int:
    parser = argparse.ArgumentParser(description="Audit bot SQLite references against Marzban users.")
    parser.add_argument("--limit", type=int, default=100, help="Marzban API page size.")
    parser.add_argument("--show", type=int, default=80, help="Max rows per report section.")
    parser.add_argument("--json", action="store_true", help="Emit JSON report.")
    args = parser.parse_args()

    settings = Settings.load()
    repo = Repo(settings.db_path)
    marzban = MarzbanClient(cast(Any, settings))
    await repo.open()
    try:
        report = await audit_marzban_sync(repo, marzban, limit=max(1, args.limit))

        if args.json:
            print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
            return 0

        print("RootVPN Marzban/DB sync audit")
        print(f"DB path: {settings.db_path}")
        print(f"DB refs: {report.db_refs}")
        print(f"DB unique usernames: {report.db_unique_usernames}")
        print(f"Marzban users seen: {report.marzban_users_seen}")
        if report.marzban_list_error:
            print(
                "WARN: Marzban full user list unavailable "
                f"({report.marzban_list_error}); checked DB usernames individually."
            )

        _print_section("missing_in_marzban", report.missing_in_marzban, limit=args.show)
        _print_section("unknown_in_db_tg_or_web", report.unknown_in_db, limit=args.show)
        _print_section("web_orders_without_access", report.web_orders_without_access, limit=args.show)
        _print_section("non_standard_device_names", report.non_standard_device_names, limit=args.show)
        _print_section("shared_db_refs", report.shared_db_refs, limit=args.show)
        _print_section("db_known_summary", report.db_known_summary, limit=args.show)

        print("\nResult:", "CHECK_FINDINGS" if report.has_findings() else "OK")
        return 1 if report.has_critical_findings() else 0
    finally:
        await marzban.close()
        await repo.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
