@echo off
REM US dawn run (06:00 KST, after US market close): scan/trade US names on fresh close data.
REM Skips daily briefing (KR morning run sends one combined brief covering all positions).
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
>> "%~dp0swing.log" echo.
>> "%~dp0swing.log" echo ===== %date% %time% SWING US run =====
"%~dp0.venv\Scripts\swing-trader.exe" run-once --market us --no-brief >> "%~dp0swing.log" 2>&1
REM sync local main to origin so the marker commit fast-forwards (self-heal after a cloud fallback day)
git fetch origin main
git merge --ff-only origin/main
REM push daily-done marker to swing repo so cloud failover can detect local ran
git add -f state\daily_done.json state\harness_latest.json state\logic_versions.json state\version_compare.json
git diff --cached --quiet || ( git commit -m "chore(state): local daily marker + harness/logic [skip ci]" && git push origin HEAD:main )
echo %date% %time% SWING US DONE> "%~dp0swing_heartbeat.txt"
