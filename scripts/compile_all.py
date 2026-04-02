#!/usr/bin/env python3
from __future__ import annotations

import os
import py_compile
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXCLUDE_DIRS = {
    ".git",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "test_tmp",
    "data",
}


def _git_tracked_python_files() -> list[Path]:
    try:
        proc = subprocess.run(
            ["git", "ls-files", "*.py"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
    except Exception:
        return []
    files: list[Path] = []
    for line in proc.stdout.splitlines():
        rel = line.strip()
        if not rel:
            continue
        files.append(ROOT / rel)
    return files


def _scan_python_files() -> list[Path]:
    files: list[Path] = []
    for cur_root, dirs, names in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        for name in names:
            if not name.endswith(".py"):
                continue
            files.append(Path(cur_root) / name)
    return files


def collect_python_files() -> list[Path]:
    files = _git_tracked_python_files()
    if not files:
        files = _scan_python_files()
    uniq = sorted({p.resolve() for p in files})
    return uniq


def main() -> int:
    files = collect_python_files()
    if not files:
        print("No Python files found.")
        return 0

    failed = False
    for path in files:
        try:
            py_compile.compile(str(path), doraise=True)
        except py_compile.PyCompileError as exc:
            failed = True
            print(f"COMPILE ERROR: {path}")
            print(exc.msg)

    if failed:
        return 1

    print(f"Compiled OK: {len(files)} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
