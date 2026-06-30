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
REM sync local main to origin so the marker commit fast-forwards (self-heal after a cloud fallback day)
git fetch origin main
git merge --ff-only origin/main
REM push full state/ (계좌·포지션·하니스 등) so 대시보드/Discord/클라우드가 같은 진실 공유 (클라우드와 동일)
git add -f state
git diff --cached --quiet || ( git commit -m "chore(state): local run state sync [skip ci]" && git push origin HEAD:main )
echo %date% %time% SWING KR DONE> "%~dp0swing_heartbeat.txt"
