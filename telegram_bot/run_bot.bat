@echo off
REM Telegram Bot 启动脚本（Windows Batch）
REM 使用 ComfyUI 自带的 Python

setlocal enabledelayedexpansion

set PYTHON_PATH=D:\ComfyUI_windows_portable\python_embeded\python.exe
set SCRIPT_DIR=%~dp0

REM 检查 Python 是否存在
if not exist "%PYTHON_PATH%" (
    echo ❌ 错误: Python 不存在于 %PYTHON_PATH%
    echo 请检查路径是否正确
    pause
    exit /b 1
)

echo 使用 Python: %PYTHON_PATH%
echo 脚本路径: %SCRIPT_DIR%

REM 解析命令行参数
set TOKEN=
set USER_ID=
set WEBHOOK_URL=
set WEBHOOK_PORT=8765
set MODE=webhook

:parse_args
if "%~1"=="" goto run
if "%~1"=="--token" (
    set TOKEN=%~2
    shift
    shift
    goto parse_args
)
if "%~1"=="--user_id" (
    set USER_ID=%~2
    shift
    shift
    goto parse_args
)
if "%~1"=="--webhook_url" (
    set WEBHOOK_URL=%~2
    shift
    shift
    goto parse_args
)
if "%~1"=="--webhook_port" (
    set WEBHOOK_PORT=%~2
    shift
    shift
    goto parse_args
)
if "%~1"=="--mode" (
    set MODE=%~2
    shift
    shift
    goto parse_args
)
shift
goto parse_args

:run
REM 构建命令
set CMD=%PYTHON_PATH% -m telegram_bot.startup
if not "!TOKEN!"=="" (
    set CMD=!CMD! --token "!TOKEN!"
)
if not "!USER_ID!"=="" (
    set CMD=!CMD! --user_id "!USER_ID!"
)
if not "!WEBHOOK_URL!"=="" (
    set CMD=!CMD! --webhook_url "!WEBHOOK_URL!"
)
set CMD=!CMD! --webhook_port !WEBHOOK_PORT!
set CMD=!CMD! --mode !MODE!

echo 执行命令:
echo !CMD!
echo.

%PYTHON_PATH% -m telegram_bot.startup --webhook_port %WEBHOOK_PORT% --mode %MODE% %*

if errorlevel 1 (
    echo.
    echo ❌ Bot 启动失败
    pause
    exit /b 1
)

endlocal
