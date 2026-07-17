@echo off
REM Dung han bot: phai giet VONG LAP (cmd chay run_bot.bat) TRUOC,
REM roi moi giet python - neu giet python truoc, vong lap se tu
REM khoi dong lai bot sau 10 giay.
powershell -NoProfile -Command "Get-WmiObject Win32_Process -Filter \"Name='cmd.exe'\" | Where-Object { $_.CommandLine -like '*run_bot.bat*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }; Get-WmiObject Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -like '*telegram_ai_bot*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
echo Bot da dung han. Chay lai bang: schtasks /run /tn TelegramAIBot
pause
