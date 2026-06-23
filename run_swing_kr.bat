@echo off
REM KR morning run (09:05 KST, after KRX open): decide on prev-day confirmed candle,
REM enter at today's open. Then review + weekly/monthly briefings. Paper mode only.
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
>> "%~dp0swing.log" echo.
>> "%~dp0swing.log" echo ===== %date% %time% SWING KR run =====
"%~dp0.venv\Scripts\swing-trader.exe" run-once --market kr >> "%~dp0swing.log" 2>&1
"%~dp0.venv\Scripts\swing-trader.exe" review >> "%~dp0swing.log" 2>&1
REM weekly(Fri, incl. backtest) / monthly(last Fri) briefings auto-fire by date
"%~dp0.venv\Scripts\swing-trader.exe" brief --period auto >> "%~dp0swing.log" 2>&1
echo %date% %time% SWING KR DONE> "%~dp0swing_heartbeat.txt"
