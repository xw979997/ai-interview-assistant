@echo off
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
set "PY="
where py >nul 2>nul && set "PY=py"
if not defined PY ( where python >nul 2>nul && set "PY=python" )
if not defined PY ( if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe" )
if not defined PY ( echo [错误] 没找到 Python，请先运行 setup.bat。 & pause & exit /b 1 )
echo 语音考官：将用系统声音朗读面试问题，供 AI 面试助手抓取作答。
echo 请先确保 run.bat 已启动且助手显示「聆听中…（模型已就绪）」。
echo.
%PY% speak_test.py %*
pause
