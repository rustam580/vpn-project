$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $ProjectRoot
try {
    $Python = if (Test-Path ".venv\Scripts\python.exe") { ".venv\Scripts\python.exe" } else { "python" }

    & $Python -B -m py_compile bot.py app_texts.py payments_service.py
    & $Python -m ruff check --no-cache bot.py app_texts.py payments_service.py tests
    & $Python -m pytest -q -p no:cacheprovider
}
finally {
    Pop-Location
}
