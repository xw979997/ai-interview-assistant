@echo off
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
"C:\Users\admin\AppData\Local\Programs\Python\Python312\python.exe" test_questions.py
pause
