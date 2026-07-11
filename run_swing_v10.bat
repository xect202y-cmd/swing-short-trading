@echo off
REM KR EOD run (16:10 KST, after KRX close): refresh full-market panel, then v10
REM (new-high + volume-dry breakout) live cycle -> exit v7 holdings / enter v10 signals / 3-way brief.
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
>> "%~dp0swing.log" echo.
>> "%~dp0swing.log" echo ===== %date% %time% SWING V10 run =====
REM refresh krx_panel.pkl (full KOSPI+KOSDAQ OHLCV cache) so v10 sees today's confirmed candle
"%~dp0.venv\Scripts\python.exe" -c "from pathlib import Path; from swing_trader.scalp.krx_universe import fetch_panel; fetch_panel(Path('state'))" >> "%~dp0swing.log" 2>&1
"%~dp0.venv\Scripts\swing-trader.exe" swing-v10 >> "%~dp0swing.log" 2>&1
REM sync local main to origin so the marker commit fast-forwards (self-heal after a cloud fallback day)
git fetch origin main
git merge --ff-only origin/main
REM push full state (account, position, harness) so dashboard/Discord/cloud share same truth
git add -f state
git diff --cached --quiet || ( git commit -m "chore(state): local run state sync [skip ci]" && git push origin HEAD:main )
echo %date% %time% SWING V10 DONE> "%~dp0swing_heartbeat.txt"
