from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

DEFAULT_PROFILE = Path.home() / "AppData" / "Roaming" / "WB Stream"
TOKEN_KEY_RE = re.compile(r"(access[_-]?token|token|auth|authorization|jwt|bearer)", re.I)
JWT_RE = re.compile(rb"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")
LONG_TOKEN_RE = re.compile(rb"[A-Za-z0-9_-]{32,}\.[A-Za-z0-9_.-]{20,}|[A-Fa-f0-9]{64,}|[A-Za-z0-9_-]{80,}")
ROOM_URL_RE = re.compile(rb"https://stream\.wb\.ru/room/[A-Za-z0-9_.-]+")


def _safe_text(data: bytes) -> str:
    return data.decode("utf-8", errors="ignore")


def _fingerprint(value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()[:16]
    return f"len={len(value)} sha256[:16]={digest}"


def _extract_jsonish_tokens(text: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    # Common localStorage/JSON shapes: "accessToken":"...", access_token=..., Authorization: Bearer ...
    patterns = [
        re.compile(r'"(?P<key>[^"]*(?:access[_-]?token|token|auth|authorization|jwt)[^"]*)"\s*:\s*"(?P<val>[^"]{20,})"', re.I),
        re.compile(r"(?P<key>access[_-]?token|token|auth|authorization|jwt)\s*[=:]\s*(?P<val>[A-Za-z0-9_.:-]{20,})", re.I),
        re.compile(r"(?P<key>authorization)\s*[=:]\s*Bearer\s+(?P<val>[A-Za-z0-9_.-]{20,})", re.I),
    ]
    for pattern in patterns:
        for m in pattern.finditer(text):
            key = m.group("key")[:120]
            val = m.group("val").strip()
            if len(val) >= 20:
                out.append((key, val))
    return out


def find_candidates(profile: Path) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen_values: set[str] = set()
    roots = [
        profile / "Local Storage",
        profile / "Session Storage",
        profile / "IndexedDB",
        profile / "WebStorage",
        profile / "Network",
    ]
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if size <= 0 or size > 20_000_000:
                continue
            try:
                data = path.read_bytes()
            except OSError:
                continue
            text = _safe_text(data)
            for key, value in _extract_jsonish_tokens(text):
                if value in seen_values:
                    continue
                seen_values.add(value)
                candidates.append({"source": str(path), "kind": "jsonish", "key": key, "value": value})
            for match in JWT_RE.finditer(data):
                value = match.group(0).decode("ascii", errors="ignore")
                if value and value not in seen_values:
                    seen_values.add(value)
                    candidates.append({"source": str(path), "kind": "jwt", "key": "jwt", "value": value})
            # Keep this as fallback only when nearby text contains token/auth.
            lower = data.lower()
            if b"token" in lower or b"auth" in lower or b"access" in lower:
                for match in LONG_TOKEN_RE.finditer(data):
                    value = match.group(0).decode("ascii", errors="ignore")
                    if value and value not in seen_values:
                        seen_values.add(value)
                        candidates.append({"source": str(path), "kind": "opaque", "key": "unknown", "value": value})
    return candidates


def load_token_candidate(profile: Path, *, index: int = 0) -> str:
    candidates = find_candidates(profile)
    if not candidates:
        raise ValueError("no WB Stream token candidates found")
    if index < 0 or index >= len(candidates):
        raise ValueError(f"candidate index out of range: {index}")
    return str(candidates[index]["value"])


def main() -> int:
    parser = argparse.ArgumentParser(description="Find WB Stream auth token candidates without printing secrets")
    parser.add_argument("--profile", default=str(DEFAULT_PROFILE))
    parser.add_argument("--write-token", default="", help="write selected token candidate to this file")
    parser.add_argument("--index", type=int, default=0, help="candidate index to write")
    parser.add_argument("--print-summary-json", action="store_true")
    args = parser.parse_args()

    profile = Path(args.profile)
    candidates = find_candidates(profile)
    summary = []
    for idx, item in enumerate(candidates):
        summary.append({
            "index": idx,
            "source": item["source"],
            "kind": item["kind"],
            "key": item["key"],
            "fingerprint": _fingerprint(item["value"]),
        })

    if args.print_summary_json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(f"profile: {profile}")
        print(f"candidates: {len(candidates)}")
        for item in summary:
            print(f"[{item['index']}] {item['kind']} key={item['key']} {item['fingerprint']}")
            print(f"    source={item['source']}")

    if args.write_token:
        out = Path(args.write_token)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(load_token_candidate(profile, index=args.index) + "\n", encoding="utf-8")
        print(f"wrote candidate #{args.index} to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
