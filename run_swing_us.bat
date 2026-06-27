@echo off
REM US dawn run (06:00 KST, after US market close): scan/trade US names on fresh close data.
REM Skips daily briefing (KR morning run sends one combined brief covering all positions).
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
>> "%~dp0swing.log" echo.
>> "%~dp0swing.log" echo ===== %date% %time% SWING US run =====
"%~dp0.venv\Scripts\swing-trader.exe" run-once --market us --no-brief >> "%~dp0swing.log" 2>&1
REM push daily-done marker to swing repo so cloud failover can detect local ran
git add state\daily_done.json
git diff --cached --quiet || ( git commit -m "chore(state): local daily marker [skip ci]" && git push origin HEAD )
echo %date% %time% SWING US DONE> "%~dp0swing_heartbeat.txt"
