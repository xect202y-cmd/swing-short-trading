@echo off
REM v5 overnight santa scan (15:05 KST, before close auction): settle yesterday,
REM scan full market movers, plan buy-at-close. Paper mode only. ASCII comments only.
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
>> "%~dp0swing.log" echo.
>> "%~dp0swing.log" echo ===== %date% %time% SCALP V5 scan =====
"%~dp0.venv\Scripts\swing-trader.exe" scalp-v5 >> "%~dp0swing.log" 2>&1
REM push state so dashboard/Discord/cloud share same truth
git fetch origin main
git merge --ff-only origin/main
git add -f state
git diff --cached --quiet || ( git commit -m "chore(state): scalp v5 scan state [skip ci]" && git push origin HEAD:main )
echo %date% %time% SCALP V5 DONE> "%~dp0scalp_v5_heartbeat.txt"
