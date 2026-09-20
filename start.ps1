#!/usr/bin/env pwsh
# Arranca el servidor de desarrollo en Windows.
# Uso:  .\start.ps1

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$venv = Join-Path $root ".venv"
$python = Join-Path $venv "Scripts\python.exe"

if (-not (Test-Path $python)) {
    Write-Host "Creando entorno virtual..." -ForegroundColor Cyan
    python -m venv $venv
    & $python -m pip install --upgrade pip --quiet
    & $python -m pip install -r (Join-Path $root "backend\requirements.txt")
}

Write-Host "Servidor en http://127.0.0.1:8000" -ForegroundColor Green
Set-Location (Join-Path $root "backend")
& $python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
