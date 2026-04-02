# Development Workflow

This file defines the default local workflow for fast and predictable changes.

## 1) Setup

Linux/macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
```

Windows PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt -r requirements-dev.txt
```

## 2) Standard Commands

Lint only:

```bash
python -m ruff check --no-cache .
```

Tests only:

```bash
python -m pytest -q -p no:cacheprovider
```

Full local check:

Linux/macOS:

```bash
bash scripts/check.sh
```

Windows PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/check.ps1
```

Cleanup generated caches/tmp:

Linux/macOS:

```bash
bash scripts/clean.sh
```

Windows PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/clean.ps1
```

## 3) Done Criteria

Before deploy, every change should pass:

1. `python scripts/compile_all.py`
2. `python -m ruff check --no-cache .`
3. `python -m pytest -q -p no:cacheprovider`

## 4) CI

GitHub Actions workflow: `.github/workflows/ci.yml`

The workflow runs the same checks as local `check`.

## 5) Context Handoff Discipline

To avoid context loss between sessions:

1. Always read `docs/STATE.md` first.
2. Then read `docs/open-issues.md`.
3. After any meaningful change, update both files:
   - update date
   - move completed items to closed
   - add new blockers with priority.
