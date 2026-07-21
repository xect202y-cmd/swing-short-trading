@echo off
REM State sync helper: push local swing/scalp state (state/*.json etc) to origin/main via a
REM dedicated persistent worktree, so a push always lands on main regardless of which branch
REM this repo happens to be checked out on. Called by run_swing_kr.bat / run_swing_us.bat.
REM Pusher-owned .enc (holdings/portfolio_history/portfolio_cashflows/trades) are restored from
REM origin/main after overlay -- this script must never clobber the asset .enc files.
REM Fail-loud: on any failure, writes state_sync_FAILED.txt + state_sync_heartbeat.txt and
REM pings Discord, then exits 1 so the caller does not write its own DONE heartbeat.
setlocal enabledelayedexpansion
cd /d "%~dp0"

set "WT=%~dp0..\swing-state-main"
set "HEARTBEAT=%~dp0state_sync_heartbeat.txt"
set "FAILMARK=%~dp0state_sync_FAILED.txt"
set "MAX_ATTEMPTS=3"
set "FAIL_REASON="

REM -- 전용 영속 worktree(항상 main 추적) 정합화: 없으면 생성, 있으면 그대로 재사용 --
git worktree prune
git worktree list | findstr /i "swing-state-main" >nul || git worktree add -B _state_main "%WT%" origin/main
if errorlevel 1 (
    set "FAIL_REASON=git worktree add failed"
    goto :fail
)

set "ATTEMPTS_LEFT=%MAX_ATTEMPTS%"

:attempt
git -C "%WT%" fetch origin main
if errorlevel 1 (
    set "FAIL_REASON=git fetch origin main failed"
    goto :fail
)

git -C "%WT%" reset --hard origin/main
if errorlevel 1 (
    set "FAIL_REASON=git reset --hard origin/main failed"
    goto :fail
)

REM xcopy(무조건 덮어쓰기)로 스윙 state 전체 오버레이 -- robocopy 는 타임스탬프 skip 함정이 있어 미사용
xcopy "%~dp0state\*" "%WT%\state\" /E /Y /I /Q
if errorlevel 1 (
    set "FAIL_REASON=xcopy state overlay failed"
    goto :fail
)

REM pusher 소유 4개 .enc 는 origin/main 값으로 원복 -- 자산 데이터 클로버 방지
git -C "%WT%" checkout origin/main -- state/holdings.enc state/portfolio_history.enc state/portfolio_cashflows.enc state/trades.enc
if errorlevel 1 (
    set "FAIL_REASON=git checkout origin/main -- pusher .enc restore failed"
    goto :fail
)

git -C "%WT%" add -f state
if errorlevel 1 (
    set "FAIL_REASON=git add -f state failed"
    goto :fail
)

git -C "%WT%" diff --cached --quiet
if not errorlevel 1 goto :nochange

git -C "%WT%" commit -m "chore(state): swing run sync [skip ci]"
if errorlevel 1 (
    set "FAIL_REASON=git commit failed"
    goto :fail
)

git -C "%WT%" push origin _state_main:main
if not errorlevel 1 goto :ok

REM push 거부(pusher 와의 레이스로 non-fast-forward 가능) -- refetch+reset+재오버레이 후 재시도
set /a ATTEMPTS_LEFT-=1
if !ATTEMPTS_LEFT! LEQ 0 (
    set "FAIL_REASON=git push origin _state_main:main rejected after %MAX_ATTEMPTS% attempts"
    goto :fail
)
goto :attempt

:ok
if exist "%FAILMARK%" del "%FAILMARK%"
echo %date% %time% OK> "%HEARTBEAT%"
exit /b 0

:nochange
if exist "%FAILMARK%" del "%FAILMARK%"
echo %date% %time% OK (nochange)> "%HEARTBEAT%"
exit /b 0

:fail
echo %date% %time% FAILURE !FAIL_REASON!> "%HEARTBEAT%"
echo !FAIL_REASON!> "%FAILMARK%"
set "PY=%~dp0.venv\Scripts\python.exe"
if exist "%PY%" (
    "%PY%" -c "from swing_trader.config import load_config; from swing_trader.notify.discord import notify; c=load_config(); notify(c.creds.discord_webhook_url, '[WARN] swing state sync FAILED: !FAIL_REASON!')" >nul 2>&1
)
exit /b 1
