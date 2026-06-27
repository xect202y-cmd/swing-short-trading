# 스윙 액티브-패시브 페일오버 + 로컬 장애 디스코드 알림 — 설계

작성일: 2026-06-27
대상 레포: `swing-short-trading`(주), `obsidian-automation`(워치독), `obsidian-automation/gpt-api`(cron)

## 1. 목표 / 문제

현재 **로컬 노트북 Swing 작업(Swing-KR/US)** 과 **클라우드 GitHub Actions(`swing.yml`)** 가 같은 페이퍼 트레이딩을 **둘 다** 수행한다. 결과:
- 같은 디스코드 채널에 **중복 Swing Hook**(서로 다른 페이퍼 계좌라 내용도 어긋남)
- 같은 날짜의 `04_Trading/Signals|Logs/<date>.md`를 양쪽이 써서 vault repo **add/add 충돌** → `vault_sync` rebase abort → push 영구 실패

사용자 의도: **로컬을 주(primary), 클라우드를 페일오버(fallback)** 로. 로컬이 정상이면 클라우드는 no-op. 로컬이 안 돌면 클라우드가 보충하고 **디스코드로 점검 알림**. 추가로 **데이터 수집 파이프라인(`run_pipeline.bat`)** 이 멈추면 디스코드 알림.

성공 기준:
- 정상일: 하루에 **정확히 한 번** 거래/리포트/Hook. 클라우드는 작업·커밋·발송 0.
- 로컬 장애일: 클라우드가 빠진 시장만 보충 + **페일오버 경고 Discord 1건**.
- `04_Trading` 같은날 add/add 충돌 소멸.
- 데이터 파이프라인 5h+ 정지 시 **Discord 경고**(텔레그램과 병행), 24h마다 재알림, 복구 시 복구 알림.

## 2. 핵심 메커니즘 — 날짜 마커

swing repo의 `state/daily_done.json`:
```json
{ "2026-06-29": { "us": "2026-06-29T06:00:12+09:00", "kr": "2026-06-29T09:05:33+09:00" } }
```
- **`swing-trader run-once --market <m>` 가 해당 시장 성공 완료 시** 오늘 날짜 아래 `<m>` 타임스탬프를 기록. KR은 디스코드 brief 발송까지 성공해야 기록(부분 실패 시 미기록 → 클라우드가 보충).
- 최근 7일만 보관(오래된 키 prune).
- 마커 기록은 로컬·클라우드 공통(`swing-trader` 내부). **푸시**만 주체별:
  - 로컬: `run_swing_*.bat`이 run 후 `state/daily_done.json`만 `git add/commit/push` → swing repo.
  - 클라우드: `swing.yml`의 기존 `state/` 커밋백 스텝에 자동 포함.

## 3. 타이밍 (페일오버 순서)

| 시각(KST) | 주체 | 동작 |
|---|---|---|
| 06:00 | 로컬 US 작업 | `run-once --market us --no-brief` → us 마커 push |
| 09:05 | 로컬 KR 작업 | `run-once --market kr`(+Discord brief), review, brief → kr 마커 push |
| **09:35** | 클라우드 | (변경: vercel cron `5 0`→`35 0`) swing repo pull → 마커 확인 |

클라우드 09:35 로직:
- 오늘 us·kr 둘 다 마커 있음 → **즉시 종료**(run-once/review/brief/커밋/Discord 전부 skip).
- 빠진 시장만 `run-once` 실행. 마지막 빠진 시장이 kr이면 review+brief도 실행.
- 하나라도 보충했으면 **페일오버 경고 Discord**(§5) 발송.

유예 30분: 로컬 KR 09:05 실행 + push 여유. (트레이드오프: 로컬 장애일엔 Hook이 ~30분 늦음 — 페일오버라 허용.)

## 4. 구현 위치 (테스트 가능 단위)

- `swing-trader` CLI:
  - 마커 기록 헬퍼: `record_done(market, ts)` — run-once 성공 경로 끝에서 호출.
  - `swing-trader check-done --market <kr|us>`: 오늘 마커 있으면 exit 0, 없으면 exit 1. (순수 함수 + 얇은 CLI 래퍼, 단위 테스트 대상)
  - prune 헬퍼: 7일 초과 키 제거.
- `swing.yml` 실행 스텝(bash):
  ```bash
  ALERT_MARKETS=""
  for M in us kr; do
    if swing-trader check-done --market $M; then echo "$M: 로컬 완료 — skip";
    else swing-trader run-once --market $M $( [ $M = us ] && echo --no-brief ); ALERT_MARKETS="$ALERT_MARKETS $M"; fi
  done
  # kr을 보충 실행했으면 review/brief도
  # ALERT_MARKETS 비어있지 않으면 §5 경고 발송
  ```
  둘 다 skip이면 이후 커밋백/리포트 스텝도 변경 없음(자연히 no-op).
- `run_swing_kr.bat` / `run_swing_us.bat`: 마지막에 마커 push 스텝 추가(아래 push 블록). **PURE ASCII 주석 유지**(기존 bat 인코딩 함정 회피).

## 5. 페일오버 경고 (Discord)

클라우드가 1개 이상 시장 보충 시 1건:
> ⚠️ **로컬 스윙 미실행 감지** — 클라우드가 `{보충 시장}` 대체 처리함. 노트북(로컬 Swing 작업) 점검 요망. (yyyy-mm-dd hh:mm KST)

→ `SWING_DISCORD_WEBHOOK_URL`(swing 채널). 기존 `notify/discord.py` 재사용.

## 6. 데이터 파이프라인 장애 알림 (Discord)

`obsidian-automation/pipeline_watchdog.py` 확장:
- 기존 `send_telegram` 옆에 `send_discord(text)` 추가. stale/복구 분기에서 **둘 다** 호출(텔레그램 유지 + Discord 추가).
- Discord webhook = `SWING_DISCORD_WEBHOOK_URL`(gpt-api/.env). 메시지는 swing 채널로.
- 기존 `STALE_HOURS=5`, `RENOTIFY_HOURS=24`, 상태파일 dedup 로직 그대로 재사용.

## 7. 부수 효과 — 충돌 해소

하루 한쪽만 `04_Trading` 오늘자 파일 생성 → add/add 충돌 구조적 소멸. (추가 안전장치 불필요. 단, 과거 잔여 충돌은 §10 수동 1회 정리 완료된 상태.)

## 8. 엣지 케이스

- 로컬 US만 돌고 KR 못 돔 → 클라우드 KR만 보충 + 경고. (의도대로)
- 로컬 돌았으나 마커 push 실패 → 클라우드가 중복 실행(안전측: Hook 한 번 더 > 놓침). 드묾.
- 로컬 run-once 절반 실패 → 마커 미기록 → 클라우드 보충.
- 주말/공휴일: cron 평일(1-5)만. 로컬 작업도 평일.
- 클라우드 마커 읽기 전 pull 실패 → 보충 안 하고 종료(다음날 정상). 또는 보수적으로 실행. → **pull 실패 시 실행(놓침 방지)** 채택.

## 9. Prerequisites (구현 전 1회 설정)

- **로컬 swing repo push 토큰**: `run_swing_*.bat`이 swing repo로 push할 수 있도록 자격증명(PAT/gh) 구성. (로컬 obsidian-automation의 `.gh-token` 패턴 참고)
- **`SWING_DISCORD_WEBHOOK_URL`을 `gpt-api/.env`에 추가**(값은 `swing-short-trading/.env`에서 복사) — 워치독이 swing 채널로 발송하기 위함.

## 10. 변경 파일 요약

- `swing-short-trading/src/swing_trader/...`: 마커 record/check/prune + CLI `check-done`.
- `swing-short-trading/run_swing_kr.bat`, `run_swing_us.bat`: 마커 push 스텝.
- `swing-short-trading/.github/workflows/swing.yml`: check-done 가드 + 페일오버 Discord 경고.
- `obsidian-automation/pipeline_watchdog.py`: `send_discord` 추가, stale/복구에 병행 호출.
- `obsidian-automation/gpt-api/vercel.json`: swing-trigger cron `5 0 * * 1-5` → `35 0 * * 1-5`.
- `obsidian-automation/gpt-api/.env`: `SWING_DISCORD_WEBHOOK_URL` 추가.
- 테스트: `tests/test_failover.py`(check-done/record/prune 단위), 가능하면 swing.yml 가드 로직 스모크.

## 11. 테스트 / 검증 기준

- 단위: 오늘 마커 있음→check-done exit 0; 없음→exit 1; 기록 후 재확인; 8일 지난 키 prune.
- 통합(수동/스모크): 마커에 us·kr 채운 상태로 swing.yml 실행 로직 → 전부 skip. kr 마커 제거 → kr만 실행 + ALERT 세팅.
- 워치독: heartbeat 가짜 노후 → 텔레그램+Discord 둘 다 발송, 24h 내 재호출 시 dedup.
