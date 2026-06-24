@echo off
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
set PY=C:\Users\admin\AppData\Local\Programs\Python\Python312\python.exe

echo ============================================
echo  AI 面试助手 - 本地离线版 安装脚本
echo ============================================
echo.
echo [1/4] 安装 Python 依赖 (soundcard / numpy / faster-whisper / openai)...
"%PY%" -m pip install -r requirements.txt
echo.
echo [2/4] 安装 Ollama (本地大模型运行时)...
winget install --id Ollama.Ollama -e --silent --accept-package-agreements --accept-source-agreements --disable-interactivity

set OLLAMA=%LOCALAPPDATA%\Programs\Ollama\ollama.exe
if not exist "%OLLAMA%" set OLLAMA=ollama
echo 启动 Ollama 服务...
start "" "%OLLAMA%" serve
timeout /t 5 /nobreak >nul
echo.
echo [3/4] 下载模型文件 (Qwen2.5-3B GGUF + faster-whisper, 约 2.4GB, 首次较慢)...
"%PY%" download_models.py
echo.
echo [4/4] 导入本地大模型到 Ollama...
"%OLLAMA%" create qwen-interview -f Modelfile
echo.
echo 安装完成！现在双击 run.bat 即可启动（全程离线、免费）。
pause
