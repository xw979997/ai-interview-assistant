@echo off
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
set "PY="
where py >nul 2>nul && set "PY=py"
if not defined PY ( where python >nul 2>nul && set "PY=python" )
if not defined PY ( if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe" )
if not defined PY ( echo [错误] 没找到 Python，请先运行 setup.bat。 & pause & exit /b 1 )
%PY% test_questions.py
pause
