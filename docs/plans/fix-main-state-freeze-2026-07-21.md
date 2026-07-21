# Plan — 헤르메스 '성과' 탭 현재가 07-17 동결 버그: 근본원인 수정 + 재발방지

작성: 2026-07-21 · 담당: Opus(오케스트레이터) → teammate(Sonnet) 구현

## 증상
헤르메스 앱 '성과'(모의 스윙트레이딩) 탭에서 보유(페이퍼) 종목 현재가·자산곡선이
며칠째 **2026-07-17 기준**으로 멈춰 갱신되지 않음.

## 근본원인 (조사로 확정)
1. 앱은 `lib/swing/source.ts`의 `readSwingState()`로 **GitHub `origin/main` 의 `state/*.json`**
   (raw.githubusercontent.com/…/main/state/…)을 읽는다. 프로덕션엔 `SWING_STATE_DIR` 미설정.
2. 스윙 일일 파이프라인 배치(`run_swing_kr.bat`, `run_swing_us.bat`)의 git 동기화 꼬리:
   ```
   git fetch origin main
   git merge --ff-only origin/main
   git add -f state
   git diff --cached --quiet || ( git commit -m "chore(state)…" && git push origin HEAD:main )
   ```
   이 로직은 **repo 가 `main` 에 체크아웃돼 있다고 가정**한다.
3. 그러나 작업 repo 가 **`feature/swing-evolve`** 브랜치에 체크아웃된 채 방치됨
   (main 과 22앞/10뒤로 분기). 그 결과:
   - `git merge --ff-only origin/main` → feature 가 분기돼 있어 **실패**(fast-forward 불가).
   - `git push origin HEAD:main` → feature tip 이 origin/main 의 10개 커밋을 포함하지 않아
     **non-fast-forward 로 거부**.
   - 배치는 실패해도 `exit` 안 하고 계속 진행 → state 커밋은 **로컬 feature 에만** 쌓이고,
     `swing_heartbeat.txt` 는 **성공처럼** 기록됨(조용한 실패).
   → `origin/main` 의 스윙 state 가 **07-17 마지막 성공 push 에서 동결**.
4. 반면 `.enc`(토스 잔고/자산)는 **별도 pusher**(메인PC `hermes-holdings-pusher/push-holdings.mjs`)가
   GitHub **Contents API 로 origin/main 에 직접** 30분마다 push → 로컬 repo·브랜치와 무관하게 정상.
   → 그래서 자산 일부만 살아있고 스윙 페이퍼 포지션/현재가/자산곡선만 07-17 고정.

### 파일 소유권 (데이터 손실 방지 — 매우 중요)
- **pusher 소유(4개, 절대 덮으면 안 됨)**: `state/holdings.enc`, `state/portfolio_history.enc`,
  `state/portfolio_cashflows.enc`, `state/trades.enc`.
  (origin/main 이 07-21 최신, feature 트리는 07-11 stale → feature 값으로 덮으면 자산 데이터 롤백)
- **스윙 소유(나머지 전부)**: `open_positions.json`, `paper_state.json`, `equity_history.json`,
  `tracking.json`, `v1_us_state.json`, `harness_latest.json`, `last_run.json`, `learned_rules.json`,
  `scalp_state.json`, `logic_review.json`, `scalp_compare.json`, `version_compare.json`,
  `crosses.json`, `daily_done.json`, `ticker_map.json`, `decision_log/**`, `*.pkl` 등.

## 사용자 결정
운영 브랜치: **"어느 브랜치든 운영 유지" (worktree 격리)**. 즉 dev 가 feature/swing-evolve 로
작업 중이어도 일일 운영이 계속되어야 하며, 상태 push 는 체크아웃 브랜치와 무관하게 main 으로 가야 한다.

---

## Part A — 즉시 복구 (one-time, Opus 직접 수행)
목표: `origin/main` 의 스윙 state 를 07-21 최신으로 올려 앱 동결 해제. **pusher 소유 4개 .enc 는 불가침.**

절차(임시 worktree, origin/main 기반이라 push 는 fast-forward):
1. `git fetch origin main`
2. `git worktree add -B _state_unfreeze <TMP> origin/main`  (origin/main 기반 임시 브랜치)
3. worktree 에 스윙 state 오버레이:
   - `git -C <TMP> checkout feature/swing-evolve -- state/`  (전체 오버레이)
   - `git -C <TMP> checkout origin/main -- state/holdings.enc state/portfolio_history.enc state/portfolio_cashflows.enc state/trades.enc`  (pusher 4개 원복)
4. `git -C <TMP> add -f state`
5. `git -C <TMP> commit -m "chore(state): unfreeze main — 스윙 state 07-17→07-21 동기화 [skip ci]"`
6. `git -C <TMP> push origin _state_unfreeze:main`  (fast-forward)
7. 정리: `git worktree remove <TMP>` + `git branch -D _state_unfreeze`
8. **검증**: `curl raw …/main/state/open_positions.json` → `asOf: 2026-07-20` + positions 3개 확인.
   `.enc` 4개가 07-21 pusher 버전 그대로인지 `git show origin/main:state/holdings.enc | sha` 로 확인.

## Part B — 재발방지 (teammate 구현): worktree 격리 + fail-loud
공유 헬퍼 `sync_state_to_main.bat` 를 신설하고 `run_swing_kr.bat`/`run_swing_us.bat` 의 기존 git 꼬리
(`git fetch … merge --ff-only … add … commit && push HEAD:main`)를 **이 헬퍼 호출로 교체**한다.

### `sync_state_to_main.bat` 설계 (어드버서리얼 리뷰 반영본)
전용 영속 worktree(`%~dp0..\swing-state-main`, 항상 main)를 통해 브랜치 무관하게 push.
**overlay 는 robocopy 대신 `xcopy /E /Y`(무조건 덮어쓰기) + 4개 .enc 는 `git checkout origin/main --` 원복**
으로 하여 Part A 와 로직을 통일한다(robocopy 타임스탬프 skip 함정 회피).
```
0. worktree 정합화:
   git worktree prune
   git worktree list | findstr /i "swing-state-main" >nul || git worktree add -B _state_main "<WT>" origin/main
1. (재시도 루프, 최대 3회) :attempt
2.   git -C "<WT>" fetch origin main            || goto :fail
3.   git -C "<WT>" reset --hard origin/main       || goto :fail   (pusher 최신 .enc 흡수)
4.   xcopy "%~dp0state\*" "<WT>\state\" /E /Y /I /Q                (스윙 state 전체 오버레이)
5.   git -C "<WT>" checkout origin/main -- state/holdings.enc state/portfolio_history.enc ^
        state/portfolio_cashflows.enc state/trades.enc            (pusher 4개 원복 — 클로버 방지)
6.   git -C "<WT>" add -f state
7.   git -C "<WT>" diff --cached --quiet && goto :nochange
8.   git -C "<WT>" commit -m "chore(state): swing run sync [skip ci]" || goto :fail
9.   git -C "<WT>" push origin _state_main:main && goto :ok
     REM push 거부(pusher 와 레이스로 non-fast-forward) → 재시도. 3회 초과면 :fail
     감소 카운터; goto :attempt
:ok / :nochange → state_sync_heartbeat.txt "OK <KST시각>"; (실패마커 있으면 삭제)
:fail          → state_sync_heartbeat.txt "FAILURE <KST시각> <사유>" + state_sync_FAILED.txt 마커
                 + Discord 웹훅 경고; exit /b 1
```
- `reset --hard origin/main` 이 매 실행 pusher 최신 .enc 를 흡수 + step5 가 4개를 origin/main 값으로 원복 →
  자산 .enc 클로버 절대 불가(Part A 와 동일 불변식).
- **push 레이스 재시도**: pusher 가 fetch~push 사이 main 에 push 하면 non-fast-forward 거부되므로,
  refetch+reset+재오버레이 후 재시도(최대 3회). 이래야 정상 레이스가 거짓 FAILURE 경고를 안 냄.
- **fail-loud + heartbeat 게이팅**: 헬퍼가 전용 heartbeat/실패마커를 소유. runner 는 sync 실패 시
  기존의 무조건 "DONE" heartbeat 를 쓰지 않도록 `call … && (DONE heartbeat)` 로 게이팅
  → 며칠짜리 조용한 동결이 재발 불가(첫 실패 즉시 Discord 경고 + FAILED 마커).
- Discord 웹훅 URL 은 기존 스윙 훅 재사용(teammate 가 코드/env/.bat 에서 위치 확인; 없으면
  최소한 FAILED 마커+heartbeat 로 폴백하고 그 사실을 보고).
- worktree 함정 방지: `git worktree prune`(스테일 정리) 선행, 경로 존재 여부로 중복 add 회피,
  매 실행 `reset --hard` 로 `_state_main` 브랜치 드리프트 자동 교정.

### 변경 파일
- 신설: `sync_state_to_main.bat`
- 수정: `run_swing_kr.bat`, `run_swing_us.bat` — git 꼬리 6줄을 `call "%~dp0sync_state_to_main.bat"` 로 교체.
- 그 외 파일·로직은 손대지 않음(외과적 변경).

## 검증 기준 (Part B)
1. feature/swing-evolve 체크아웃 상태에서 `sync_state_to_main.bat` 수동 실행 → origin/main 에 state 커밋 push 성공.
2. push 강제 실패 상황(예: 잘못된 원격) → heartbeat 에 FAILURE + Discord 경고 발생, exit code 1.
3. `.enc` 4개가 push 후에도 origin/main 최신(pusher) 값 유지(해시 불변) 확인.
4. worktree 재사용(2회 실행) 시 중복 생성 오류 없이 동작.

## 미채택/보류
- `.pkl`(krx 50MB·us 10MB) 를 main 에 커밋하는 기존 동작은 유지(외과적 범위 밖). 추후 별도 정리 후보.
- feature/swing-evolve 의 evolve 코드 커밋(7926317 등)을 main 으로 병합하는 것은 이 버그와 별개 → 범위 밖.
