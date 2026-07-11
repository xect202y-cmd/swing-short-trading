---
type: 설계스펙
이니셔티브: v10 라이브 채택(3면) + 앱 52주 신고가 매수 알림
날짜: 2026-07-11
repos: swing-short-trading(A), hermes-dashboard(B)
관련: 2026-07-11-swing-v10-new-high-institutional-design.md (v10 백테스트)
tags: [v10, 라이브, 채택, 알림, 웹푸시, 3면]
---

# 🚀 v10 라이브 채택 + 52주 신고가 매수 알림 — 설계

## 0. 배경 · 결정

v10(신고가 거감짜름)이 OOS A/B에서 v9를 이겼다(부록: v10 OOS exp+3.34%/PF3.0 vs
v9 +0.75%/1.37; 단일 5개월 국면 유의). 사용자 결정:
- **v10을 채택 모델로 전환** — `regime.adopted_version: v9→v10`. 앞으로 스윙 = v10.
- v9 라이브 루프는 **은퇴**(백테/이력 코드는 유지, 페이퍼 매매 중단).
- **기존 KR 스윙 페이퍼 계정·보유종목을 v10이 그대로 인수** — v9/v10은 청산 규칙
  (5일선 이탈/대량음봉/손절)이 동일하므로 보유종목은 같은 규칙으로 관리되고,
  **신규 진입만 v10(거감짜름)**로 바뀐다. 자산곡선 연속·인위적 청산 없음.
- 앱에 **"52주 신고가 감시→매수가능" 알림** 신규(돌파 감시 신호, 인앱+실제 웹푸시).

두 리포·두 서브프로젝트 → **실행 플랜 2개**(A swing 백엔드, B hermes 앱)로 분리.

---

## A. v10 라이브 채택 (swing-short-trading, 3면)

### A1. 라이브 루프 — `strategy/v10_live.py :: run_v10_live(cfg) -> dict`
`v1_us_live.py::run_v1_us` 골격을 클론한 EOD 사이클(멱등):
```
1. 패널 로드(코스피+코스닥, krx_universe.load_cache) + 최신 확정봉 날짜 d(멱등 키)
2. 기존 KR 스윙 페이퍼 계정 인수 — open_positions.json 의 positions/cash 를 v10 이 운용
   (v9 이 쓰던 그 계정. 별도 v10 시드 안 만듦. asOf!=d 일 때만 아래 실행)
   (a) 청산 — 인수한 보유 포함 open 포지션을 v7 규칙(rules.decide_exit mode="v7":
       5일선 이탈/대량거래량 음봉/손절/max_hold)으로 청산 → 실현손익
   (b) 신규 진입 — 오늘(d) 거감짜름 진입 후보(scan_candidates 에서 entry_date==d)
       → 수급 게이트(SupplyProvider, 라이브=페일오픈) + 시황 게이트(regime_ok)
       → 랭킹 상위 N(모멘텀/신고가 강도) 매수, 시드 대비 사이징
3. 시가평가(마크투마켓) 항상 갱신(가격/포지션 최신)
4. 저장 — 인수 계정에 반영. 대시보드 성과탭이 읽는 open_positions.json 갱신
   (briefer._save_open_positions 규약 준수) + v10 자체 이력(trades/equity)
5. 디스코드 embed(🚀 스윙 V10 · d, 청산/신규진입/보유 3필드) via notify_embeds(discord_webhook_url)
6. 옵시디언 VaultWriter(cfg).append_swing_v10(md) — signals_dir, 접미사 SwingV10
7. daily_marker.record_done(state_dir, "swing_v10", now)
반환 {"exited","entered","held","realized"}
```
- **계정**: 신규 시드 없이 **기존 KR 스윙 페이퍼 계정 인수**(open_positions.json). 보유종목 그대로.
- **엔트리 재사용**: `scan_candidates`(거감짜름) + `SupplyProvider`(라이브 페일오픈) +
  `regime_ok` + `_v7_exit`/`decide_exit(mode="v7")` — v10 백테스트에서 만든 순수 로직 재사용.

### A2. config
- `regime.adopted_version: v9 → v10` (앱 성과탭 "채택됨" 배지 기준).
- `v10` 블록에 라이브 노브 추가: `alloc_pct`(신규 진입 사이징), `rank`(신규 진입 랭킹 키).
  보유/현금은 인수 계정 사용(신규 시드 없음), 동시보유 상한은 `capital.max_positions`(=3) 재사용.
- `regime.logic_mode`/`enabled`는 v10 청산이 v7과 동일하므로 현행 유지.

### A3. 앱 노출 — version_compare.json
- 대시보드 성과탭·모델시트는 `state/version_compare.json`의 `versions[]` + `adopted`를
  데이터-구동으로 읽음(앱 코드 변경 0). **v10 엔트리 추가 + `adopted="v10"`**.
- version_compare.json 생성 경로(CLI `versions` 리플레이) 확인 후 v10 포함시킴 —
  없으면 v10 항목을 스냅샷에 추가(라벨 v10·core_logic·OOS 지표는 v10_compare.json에서 인용).

### A4. 스케줄 — CLI + bat + 예약작업
- CLI `swing-v10`(라이브; backtest 명령 `swing-v10-backtest`와 구분) 등록·디스패치(→run_v10_live).
- `run_swing_v10.bat`(state git add -f/commit/push 동기화 포함, 기존 run_swing_us.bat 패턴).
- `_register_swing_task.ps1`에 KR EOD 슬롯 예약작업 추가. v9 KR 스윙 예약작업은 은퇴(비활성/제거).

### A5. 에러 · 정직성
- 패널 없음→명확 에러(synthetic 성과 금지). 수급 조회 실패→라이브 페일오픈(경고).
- 시황 실패→페일오픈. 인수 계정 파싱 실패→중단(임의 초기화 금지).
- 단일 국면 검증 한계는 v10 백테 스펙 부록에 기록됨(채택은 사용자 결정).

---

## B. 앱 52주 신고가 매수 알림 (hermes-dashboard)

### B1. 매수 판정 — `lib/swing/breakoutBuyJudge.ts :: judgeBuyBreakout(info: BreakoutInfo)`
- 반환 `{actionable: boolean, reasons: string[]}`. 매수 지향(보유/손익 로직 없음 — 기존
  sell 지향 breakoutJudge.ts 와 형제).
- 게이트: `info.brokeOut && info.strongVol && info.topClose && info.rsStrong`
  (기존 `breakoutCompute.ts::computeBreakoutInfo` 필드 재사용 — 52주 신고가·대량거래·종가강세·RS).

### B2. 알림 데이터
- `lib/swing/alerts.ts` 의 `AlertKind`에 `"newhigh"` 추가, `AlertCategory`에 `"buy"` 추가
  (기존 target1/target2/prevhigh 추가 선례와 동일 확장).
- 라이브 빌더 `lib/swing/newHighBuyAlerts.ts :: buildNewHighBuyAlerts(picks, highs, today) -> AlertEvent[]`
  — 관심종목(picks) 티커 → 기존 `/api/highs`(POST, 리스트-무관) → `judgeBuyBreakout` →
  actionable 종목을 `kind:"newhigh", category:"buy"` AlertEvent로.
- `app/(app)/alerts/page.tsx`: 기존 `usePicks` + `useHighs(picks 티커)` 로 조회해
  `liveAlerts`(client-side)에 합류(기존 priorHighAlerts/sellTargetAlerts 패턴 동일).

### B3. UI — `components/alerts/AlertsView.tsx`
- **"🚀 지금 매수 가능"** 라이브 카드(기존 "🎯 지금 매도 고민" 카드 패턴 미러).
- `BAR`/`TAG` 맵에 `newhigh` 색/라벨 추가(디자인 토큰: up 계열 강조).
- Badge/PriceLadder로 신고가 라인·돌파% 표시(기존 PositionRow 패턴).
- 벨 배지·읽음처리는 `lib/readAlerts.ts`(kind/ticker/date 키)로 **자동 동작**(신규 kind 자동 지원).

### B4. 웹푸시 (OS 알림, 앱 닫혀도)
- 신규 cron `app/api/cron/newhigh-watch/route.ts`:
  - CRON_SECRET 베어러 인증(기존 warm-scenario 패턴).
  - picks → `/api/highs` → `judgeBuyBreakout` → 신규 매수가능 종목 산출.
  - **중복방지**: KV set `push:newhigh:{YYYY-MM-DD}`에 그날 이미 푸시한 ticker 기록 →
    1일 1회만 발송(client localStorage 읽음처리는 푸시엔 무용 → 서버 dedup 필수).
  - 발송: 기존 `POST /api/push/send`(web-push·VAPID·lib/push/store KV 구독) 재사용 —
    `{title:"🚀 매수 가능", body:"{종목} 52주 신고가 돌파", url:"/alerts"}`.
- `vercel.json`에 cron 엔트리 추가(KR 장마감 후 시각, 예: 07:00 UTC 근처 — 실제 KST 장마감 반영).

### B5. 에러 · 데이터
- picks 실패→빈 알림(기존 SWR null 패턴). highs 실패→해당 종목 스킵.
- KV 미설정→dedup 없이도 cron 동작(경고 로그) — 단 중복 푸시 가능성 감수(기존 store.ts fallback 정신).
- 기관수급·거감짜름 정밀화(백엔드 전용 데이터)는 후속 — v1은 돌파 감시 프록시.

---

## 테스트

**A (swing, pytest)**
- run_v10_live 멱등(asOf==d 재실행 무변화)·인수 계정 보존(기존 positions 유지)·신규 진입은
  오늘 거감짜름만·수급 라이브 페일오픈·청산 v7 규칙. 합성/모킹 데이터, 실 네트워크 없이.
- version_compare.json v10 엔트리 생성 단위.

**B (hermes, 앱 테스트 관례 따름)**
- judgeBuyBreakout — 각 게이트 조합(brokeOut/strongVol/topClose/rsStrong)별 actionable 판정.
- buildNewHighBuyAlerts — 픽스처 BreakoutInfo에서 AlertEvent 생성(actionable만·필드 정확).
- cron dedup — KV 모킹, 같은 날 2회 호출 시 2번째 발송 스킵.
- AlertsView newhigh 렌더(BAR/TAG·카드).

## 범위 밖 (YAGNI)
- v9 라이브 코드 삭제(은퇴만 — 예약작업 비활성, 코드/백테는 유지).
- 기관수급·거감짜름 정밀 알림(앱엔 수급 데이터 없음 → 후속).
- 사용자 커스텀 관심종목 편집 UI(관심종목=picks 그대로).
- 알림 도착 서버 이력/계정 동기화(읽음처리는 기존 client localStorage 유지).
