$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $ProjectRoot
try {
    $Python = if (Test-Path ".venv\Scripts\python.exe") { ".venv\Scripts\python.exe" } else { "python" }

    & $Python scripts/compile_all.py
    & $Python -m ruff check --no-cache .
    & $Python -m pytest -q -p no:cacheprovider
}
finally {
    Pop-Location
}
