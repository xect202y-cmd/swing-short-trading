@echo off
REM US dawn run (06:00 KST, after US market close): scan/trade US names on fresh close data.
REM Skips daily briefing (KR morning run sends one combined brief covering all positions).
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
>> "%~dp0swing.log" echo.
>> "%~dp0swing.log" echo ===== %date% %time% SWING US run =====
"%~dp0.venv\Scripts\swing-trader.exe" run-once --market us --no-brief >> "%~dp0swing.log" 2>&1
echo %date% %time% SWING US DONE> "%~dp0swing_heartbeat.txt"
