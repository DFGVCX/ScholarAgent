param(
    [string]$HostAddress = "127.0.0.1",
    [int]$Port = 8002
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    throw "未找到项目虚拟环境：$python"
}

Set-Location -LiteralPath $projectRoot
& $python -m uvicorn browser_worker.server:app --host $HostAddress --port $Port
