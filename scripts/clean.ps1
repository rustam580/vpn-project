$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $ProjectRoot
try {
    foreach ($dir in @(".pytest_cache", ".ruff_cache", "test_tmp", "__pycache__")) {
        if (Test-Path $dir) {
            Remove-Item -Recurse -Force $dir
        }
    }

    Get-ChildItem -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -notlike "*\.venv\*" } |
        Remove-Item -Recurse -Force

    Get-ChildItem -Recurse -File -Filter "*.pyc" -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -notlike "*\.venv\*" } |
        Remove-Item -Force

    Write-Output "Cleanup complete."
}
finally {
    Pop-Location
}
