@echo off
REM ============================================================
REM Vong lap chay bot 24/7: bot crash (loi mang, bug...) thi
REM tu dong chay lai sau 10 giay. Log don ve logs\bot.log.
REM File nay duoc Task Scheduler goi (qua run_bot_hidden.vbs)
REM moi khi ban dang nhap Windows - khong can chay tay.
REM ============================================================
cd /d "%~dp0"
if not exist logs mkdir logs

:loop
echo [%date% %time%] === Khoi dong bot === >> logs\bot.log
venv\Scripts\python.exe telegram_ai_bot.py >> logs\bot.log 2>&1
echo [%date% %time%] === Bot thoat (ma loi %errorlevel%), chay lai sau 10 giay === >> logs\bot.log
timeout /t 10 /nobreak > nul
goto loop
