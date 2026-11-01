@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"
if errorlevel 1 goto folder_error
set "PYTHONUTF8=1"
set "PYTHONUNBUFFERED=1"
set "PY_RUN="
where py >nul 2>&1
if errorlevel 1 goto try_python
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)" >nul 2>&1
if errorlevel 1 goto try_python
set "PY_RUN=py -3"
goto python_ready

:try_python
where python >nul 2>&1
if errorlevel 1 goto python_error
python -c "import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)" >nul 2>&1
if errorlevel 1 goto python_error
set "PY_RUN=python"

:python_ready
%PY_RUN% -m py_compile monitor.py
if errorlevel 1 goto syntax_error
if /i "%~1"=="--self-check" goto self_check
if exist "config.json" goto config_ready
copy /y "config.example.json" "config.json" >nul
if errorlevel 1 goto config_error
echo Created config.json. Fill pushplus token, group topic and shop keys first.
echo Open config.json with a text editor, then run this BAT again.
goto failed

:config_ready
%PY_RUN% monitor.py check
if errorlevel 1 goto config_error
if /i "%~1"=="check" goto done
if /i "%~1"=="probe" goto probe
if /i "%~1"=="test-push" goto test_push
if /i "%~1"=="status" goto status
%PY_RUN% monitor.py run
set "RUN_EXIT=%ERRORLEVEL%"
echo Monitor stopped. Exit code: %RUN_EXIT%
pause
exit /b %RUN_EXIT%

:probe
%PY_RUN% monitor.py probe
goto command_done
:test_push
%PY_RUN% monitor.py test-push
goto command_done
:status
%PY_RUN% monitor.py status
:command_done
set "RUN_EXIT=%ERRORLEVEL%"
pause
exit /b %RUN_EXIT%

:self_check
%PY_RUN% monitor.py --help >nul
exit /b %ERRORLEVEL%
:folder_error
echo Cannot access the program directory.
goto failed
:python_error
echo Python 3.9 or newer is required. Install Python and enable Add to PATH.
goto failed
:syntax_error
echo Python syntax check failed. Read the error above.
goto failed
:config_error
echo Configuration check failed. Read the error above.
:failed
pause
exit /b 2
:done
pause
exit /b 0
