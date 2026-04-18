from __future__ import annotations

import asyncio
import importlib
import sys


_runtime = importlib.import_module("src.vpnbot.bot_runtime")

if __name__ == "__main__":
    asyncio.run(_runtime.main())
else:
    # Keep backwards-compatible import surface: `import bot` now returns
    # the runtime module directly, so monkeypatch/tests mutate live globals.
    sys.modules[__name__] = _runtime
