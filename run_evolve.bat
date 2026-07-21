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
REM push state (pending_proposals, learned_rules) to origin/main via dedicated worktree.
REM Was guarded to main-only (feature branch skipped push); the worktree helper pushes state
REM to main regardless of checked-out branch, so the guard is no longer needed. Fail-loud.
call "%~dp0sync_state_to_main.bat"
if not errorlevel 1 (
    echo %date% %time% EVOLVE DONE> "%~dp0evolve_heartbeat.txt"
)
