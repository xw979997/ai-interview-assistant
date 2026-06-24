@echo off
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"

rem ---- 自动探测 Python ----
set "PY="
where py >nul 2>nul && set "PY=py"
if not defined PY ( where python >nul 2>nul && set "PY=python" )
if not defined PY ( if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe" )
if not defined PY (
  echo [错误] 没找到 Python，请先运行 setup.bat 完成安装。
  pause & exit /b 1
)

rem ---- 确保 Ollama 服务在运行 ----
set "OLLAMA=%LOCALAPPDATA%\Programs\Ollama\ollama.exe"
if not exist "%OLLAMA%" set "OLLAMA=ollama"
tasklist /fi "imagename eq ollama.exe" 2>nul | find /i "ollama.exe" >nul || start "" "%OLLAMA%" serve

%PY% interview_assistant.py
pause
