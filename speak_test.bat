@echo off
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
echo 语音考官：将用系统声音朗读面试问题，供 AI 面试助手抓取作答。
echo 请先确保 run.bat 已启动且助手显示「聆听中…（模型已就绪）」。
echo.
"C:\Users\admin\AppData\Local\Programs\Python\Python312\python.exe" speak_test.py %*
pause
