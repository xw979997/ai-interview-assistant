@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
set PYTHONUTF8=1
cd /d "%~dp0"

echo ============================================
echo  AI 面试助手 - 本地离线版 安装脚本
echo ============================================
echo.

rem ---- 自动探测 Python（优先 py 启动器，其次 python，最后常见安装路径）----
set "PY="
where py >nul 2>nul && set "PY=py"
if not defined PY ( where python >nul 2>nul && set "PY=python" )
if not defined PY ( if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe" )
if not defined PY (
  echo [错误] 没找到 Python。请先安装 Python 3.10+（建议 3.12）：
  echo        https://www.python.org/downloads/   或  winget install Python.Python.3.12
  echo        安装时请勾选 "Add Python to PATH"，然后重新运行本脚本。
  pause & exit /b 1
)
echo 使用 Python: %PY%
echo.

echo [1/4] 安装 Python 依赖 (soundcard / numpy / faster-whisper / openai / ddgs)...
%PY% -m pip install -r requirements.txt
if errorlevel 1 ( echo [错误] 依赖安装失败，请检查网络。 & pause & exit /b 1 )
echo.

echo [2/4] 安装并启动 Ollama (本地大模型运行时)...
where ollama >nul 2>nul || winget install --id Ollama.Ollama -e --silent --accept-package-agreements --accept-source-agreements --disable-interactivity
set "OLLAMA=%LOCALAPPDATA%\Programs\Ollama\ollama.exe"
if not exist "%OLLAMA%" set "OLLAMA=ollama"
start "" "%OLLAMA%" serve
timeout /t 5 /nobreak >nul
echo.

echo [3/4] 拉取语言模型 qwen2.5:1.5b-instruct (约 1GB)...
"%OLLAMA%" pull qwen2.5:1.5b-instruct
echo.

echo [4/4] 下载语音识别模型 (faster-whisper base, 约 150MB)...
%PY% download_models.py
echo.

echo ============================================
echo  安装完成！双击 run.bat 即可启动。
echo  （想要答案更有深度可选装更大模型：ollama pull qwen2.5:3b-instruct，
echo    再把 config.json 的 ollama_model 改成 qwen2.5:3b-instruct）
echo ============================================
pause
