@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
if errorlevel 1 goto folder_error
set "SSH_KEY=%USERPROFILE%\.ssh\lvyoumap_deploy_ed25519"
set "REMOTE_MODE="
if "%~1"=="" goto check_tools
if /i "%~1"=="--check" set "REMOTE_MODE=--check"
if not defined REMOTE_MODE goto argument_error
:check_tools
where ssh.exe >nul 2>&1
if errorlevel 1 goto ssh_error
if not exist "%SSH_KEY%" goto key_error
echo OZON cloud service launcher
echo Public URL: https://voice.xzbest.site/ozon-profit/
echo Public IP: 8.138.252.219
echo Local computer network addresses:
ipconfig | findstr /I "IPv4"
echo Connecting and checking server paths and dependencies...
ssh.exe -i "%SSH_KEY%" -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=2 root@8.138.252.219 "if command -v python3.11 >/dev/null 2>&1; then python3.11 -B /opt/ozon-push-monitor/cloud_service.py %REMOTE_MODE%; else echo Python3.11_missing >&2; exit 2; fi"
set "RUN_EXIT=%ERRORLEVEL%"
if not "%RUN_EXIT%"=="0" echo Failed. Read the error above; check SSH access and server dependencies.
if "%RUN_EXIT%"=="0" echo Success. Both services are running and the HTTPS page passed its check.
echo This window will stay open until you press a key.
pause
exit /b %RUN_EXIT%
:folder_error
echo Cannot access the launcher directory.
goto failed
:ssh_error
echo Windows OpenSSH client is required. Install it and run this BAT again.
goto failed
:key_error
echo SSH private key was not found at the configured path:
echo %SSH_KEY%
echo Set SSH_KEY in this BAT to an existing authorized key path.
goto failed
:argument_error
echo Usage: start-cloud-services.bat [--check]
:failed
pause
exit /b 2
