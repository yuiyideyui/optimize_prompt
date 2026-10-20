# Telegram Bot Startup Script (Windows PowerShell)
# Using ComfyUI embedded Python

param(
    [string]$Token = "8770104786:AAE2gEq4FygHqnBNNJkKF70ETKWOsjVOhWw",
    [string]$UserId = "8916152730",
    [string]$WebhookUrl = "",
    [int]$WebhookPort = 8765,
    [string]$Mode = "webhook"
)

$PythonPath = "D:\ComfyUI_windows_portable\python_embeded\python.exe"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# Check if Python exists
if (-not (Test-Path $PythonPath)) {
    Write-Host "Error: Python not found at $PythonPath" -ForegroundColor Red
    Write-Host "Please check the path is correct" -ForegroundColor Red
    exit 1
}

Write-Host "OK - Python: $PythonPath" -ForegroundColor Green
Write-Host "OK - Script path: $ScriptDir" -ForegroundColor Cyan
Write-Host ""

# Build command
$cmdArgs = @("-m", "telegram_bot.startup")

if ($Token) {
    $cmdArgs += "--token", $Token
}
if ($UserId) {
    $cmdArgs += "--user_id", $UserId
}
if ($WebhookUrl) {
    $cmdArgs += "--webhook_url", $WebhookUrl
}
$cmdArgs += "--webhook_port", $WebhookPort.ToString()
$cmdArgs += "--mode", $Mode

Write-Host "Starting bot..." -ForegroundColor Cyan
Write-Host "Python: $PythonPath" -ForegroundColor Gray
Write-Host "Args: $($cmdArgs -join ' ')" -ForegroundColor Gray
Write-Host ""

# Execute
& $PythonPath $cmdArgs

if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "Bot startup failed" -ForegroundColor Red
    exit $LASTEXITCODE
}
