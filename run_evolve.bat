@echo off
REM Self-improving tuning loop (20:00 KST weekdays, after close): AI proposes config
REM tweaks -> harness walk-forward OOS A/B -> improving ones go to Discord for human
REM `swing adopt <id>`. Proposal-only: NEVER auto-applies. Paper/backtest only. ASCII comments only.
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
>> "%~dp0swing.log" echo.
>> "%~dp0swing.log" echo ===== %date% %time% EVOLVE run =====
"%~dp0.venv\Scripts\swing-trader.exe" evolve >> "%~dp0swing.log" 2>&1
REM push state (pending_proposals, learned_rules) so dashboard/Discord/cloud share same truth.
REM GUARDED: only push when on main, so an unmerged feature branch is never pushed to main.
for /f %%b in ('git rev-parse --abbrev-ref HEAD') do set BRANCH=%%b
if "%BRANCH%"=="main" (
  git fetch origin main
  git merge --ff-only origin/main
  git add -f state
  git diff --cached --quiet || ( git commit -m "chore(state): evolve proposals [skip ci]" && git push origin HEAD:main )
)
echo %date% %time% EVOLVE DONE> "%~dp0evolve_heartbeat.txt"
