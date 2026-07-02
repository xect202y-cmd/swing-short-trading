@echo off
REM US dawn run (06:00 KST, after US market close): scan/trade US names on fresh close data.
REM Skips daily briefing (KR morning run sends one combined brief covering all positions).
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
>> "%~dp0swing.log" echo.
>> "%~dp0swing.log" echo ===== %date% %time% SWING US run =====
"%~dp0.venv\Scripts\swing-trader.exe" run-once --market us --no-brief >> "%~dp0swing.log" 2>&1
REM scalp paper cycle: settle yesterday's plan + build today's plan (Discord orange embed)
"%~dp0.venv\Scripts\swing-trader.exe" scalp-run --market us >> "%~dp0swing.log" 2>&1
REM sync local main to origin so the marker commit fast-forwards (self-heal after a cloud fallback day)
git fetch origin main
git merge --ff-only origin/main
REM push full state (account, position, harness) so dashboard/Discord/cloud share same truth
git add -f state
git diff --cached --quiet || ( git commit -m "chore(state): local run state sync [skip ci]" && git push origin HEAD:main )
echo %date% %time% SWING US DONE> "%~dp0swing_heartbeat.txt"
