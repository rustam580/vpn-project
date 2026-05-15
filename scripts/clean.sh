#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

rm -rf .mypy_cache .pytest_cache .ruff_cache test_tmp __pycache__

# Remove local Python bytecode caches (excluding virtual env).
find . -path "./.venv" -prune -o -type d -name "__pycache__" -exec rm -rf {} +
find . -path "./.venv" -prune -o -type f -name "*.pyc" -delete

echo "Cleanup complete."
