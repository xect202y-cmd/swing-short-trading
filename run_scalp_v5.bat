@echo off
REM v6 overnight santa scan + KOSDAQ regime gate (15:05 KST, before close auction):
REM settle yesterday, if KOSDAQ above 50d MA scan full-market movers + plan buy-at-close.
REM Paper mode only. ASCII comments only. (filename kept as v5 so the scheduled task is unchanged)
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
>> "%~dp0swing.log" echo.
>> "%~dp0swing.log" echo ===== %date% %time% SCALP V6 scan =====
"%~dp0.venv\Scripts\swing-trader.exe" scalp-v6 >> "%~dp0swing.log" 2>&1
REM push state so dashboard/Discord/cloud share same truth
git fetch origin main
git merge --ff-only origin/main
git add -f state
git diff --cached --quiet || ( git commit -m "chore(state): scalp v6 scan state [skip ci]" && git push origin HEAD:main )
echo %date% %time% SCALP V6 DONE> "%~dp0scalp_v5_heartbeat.txt"
