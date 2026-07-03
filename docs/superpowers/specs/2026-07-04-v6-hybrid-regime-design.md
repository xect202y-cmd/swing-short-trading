# v6 하이브리드 (regime 가변) 스윙 트레이딩 — 설계 스펙

- 날짜: 2026-07-04
- 상태: 승인됨 (사용자 2026-07-04)
- 관련: [[swing-logic-refinement]] v4(추세필터)·v5(진입유연) 계열의 하이브리드

## 1. 목표 / 한 줄 정의

> **v6는 v5의 유연한 진입·우수한 손익비·부분익절+트레일링을 유지하면서, v4의 시장/섹터/종목 추세 가드레일을 market regime별로 가변 적용하여 약세장 리스크를 줄이는 하이브리드 스윙 모델이다.**

- 강세장(BULL): v5처럼 유연하게 진입.
- 약세장/급락장(BEAR/CRASH): v4처럼(또는 그 이상) 보수적으로 차단.

## 2. 핵심 설계 결정 (사용자 승인)

1. **Regime 판별원 = 시장지수** (KR=코스피 `^KS11`, US=S&P500 `^GSPC`). 지수 일봉으로 과거 매 거래일 regime을 결정론적으로 산출 → 백테스트 가능. (거시노트 VIX/금리/환율은 이력이 없어 백테스트 불가 → 라이브 보조로만.)
2. **백테스트는 구조 레버만.** 엔진(`_stock_trades`)에 과거 AI점수/reward_risk가 없음 → `ai.min_score`·`min_reward_risk`·구조적 손절캡은 **라이브 전용 게이트**로만 적용하고 문서화. 백테스트는 "추세게이팅 + CRASH차단 + 트레일링 + 사이징"의 약세장 방어 효과를 측정. (과거 점수/RR 합성은 `no-guessing-real-data-only` 원칙 위반이라 배제.)
3. **사이징은 두 관점 모두 보고.** ①고정분율 거래당 엣지(기대값·PF·승률, 1:1 비교) + ②regime별 가변 risk_per_trade 자산곡선(CAGR·Calmar·MDD).

### 백테스트 반영 경계 (정직성 핵심)

| 레버 | 백테스트 | 라이브 |
|---|---|---|
| stock 추세필터(require_uptrend) | ✅ | ✅ |
| CRASH 신규진입 차단 | ✅ | ✅ |
| 트레일링 % (regime별) | ✅ | ✅ |
| risk_per_trade % (regime별, 자산곡선) | ✅ | ✅ |
| max_stop % 캡 (-7/-6/-5/-4) | ⚠️ 불가(구조적 손절 없음) | ✅ |
| ai.min_score (regime별) | ⚠️ 불가(과거 점수 없음) | ✅ |
| min_reward_risk (regime별) | ⚠️ 불가 | ✅ |
| 섹터 추세/집중도, 이벤트리스크, 과열추격 | ⚠️ 라이브 신호엔진 | ✅ |

## 3. Regime 판별 규칙 (`strategy/market_regime.py`)

입력: 시장지수 OHLCV DataFrame(일봉). 출력: `{ 'YYYY-MM-DD': Regime }` (룩어헤드 없음, t 시점까지 데이터만).

파생값 (지수 종가 기준):
- `ma50`, `ma200` = 단순이동평균
- `dd60` = `close / (최근 60봉 고가 최대) - 1` (60일 고점 대비 낙폭)
- `ret5` = 5일 수익률, `slope50` = `ma50[t] - ma50[t-20]`

판정(우선순위 순, 초안 임계값 — 튜닝 대상):
1. **CRASH**: `dd60 ≤ -0.12` 또는 `ret5 ≤ -0.08`
2. **BEAR**: `close < ma200` 그리고 `slope50 < 0`
3. **BULL**: `close > ma200` 그리고 `ma50 > ma200` 그리고 `close > ma50`
4. **NEUTRAL**: 그 외

워밍업: ma200 위해 지수 200봉 이상 선행 필요. `backtest.lookback_days=500`(~2년)이면 충분. 부족 구간은 NEUTRAL로 처리.

`Regime` = Enum(BULL, NEUTRAL, BEAR, CRASH). 결정론적·순수함수 → 단위테스트 대상.

## 4. Regime 정책 테이블 (`strategy/regime_policy.py`)

`RegimePolicy` 데이터클래스 + v6 기본 정책 dict. **백테스트·라이브·문서·config 단일 소스.**

| 레버 | BULL | NEUTRAL | BEAR | CRASH |
|---|---|---|---|---|
| require_uptrend(stock) | false | true | true | true |
| block_new_entry | false | false | false | **true**(예외만) |
| trail_pct | 3.0 | 2.5 | 2.0 | 1.5 |
| risk_per_trade_pct | 1.0 | 0.75 | 0.5 | 0.25 |
| max_stop_pct(캡, 라이브) | -7(조건부) | -6 | -5 | -4 |
| ai_min_score(라이브) | 70 | 72 | 75 | 80 |
| min_reward_risk(라이브) | 1.75 | 1.90 | 2.20 | 2.50 |

공통 유지(v5 계열): default_stop -2.5, take1 6.0, take2 8.5, partial 0.5, max_hold 20.

### -7% 손절 조건부 허용 (라이브, BULL 전용)

다음을 **모두** 충족할 때만 -7%까지 허용, 아니면 regime 캡:
- `regime == BULL`
- `ai_score ≥ 75`
- `reward_risk ≥ 2.0`
- 유동성 충분(거래대금 하한 통과)
- 기술적 무효화 지점이 진입가 대비 -7% 이내
- 포트폴리오 총리스크 한도 내

### CRASH 예외 진입 (라이브)

`ai_score ≥ 80 AND reward_risk ≥ 2.5 AND 시장 안정화(regime이 CRASH이나 ret5 반등) AND sector/stock uptrend` 전부일 때만. 아니면 차단.

## 5. 진입 조건 (요약)

기본 진입 트리거는 v5 유지(20일선 눌림 후 반등 + 거래대금). 여기에 regime 게이트:
- BULL: 추세필터 off → 유연.
- NEUTRAL/BEAR: stock 추세필터 on(종가>60일선 AND 20>60).
- CRASH: 차단(예외조건만).

## 6. 차단 조건 (전부 사유 로깅 — `state/decision_log`)

- CRASH regime에서 예외조건 미충족
- ai_score < regime 기준
- reward_risk < regime 기준
- 손절폭 > regime 허용캡
- 포트폴리오 총리스크 한도 초과
- 섹터 집중도 초과
- 유동성 부족(거래대금 미달)
- 이벤트 리스크 과도
- 시장·섹터·종목 모두 하락추세
- 과열 추격매수 구간(예: 20일선 대비 과도 이격)
- 기대값 낮은 거래

각 차단은 `(종목, 사유코드, 계산근거값)` 로 기록. 반사실 분석의 키.

## 7. 손절/익절/트레일링

- 익절: v5 유지 — 1차 6%에서 절반 익절, 잔량 트레일링으로 2차 8.5%까지.
- 트레일링: regime별 가변(BULL 3.0 → CRASH 1.5) — 약세장일수록 타이트.
- 손절: default -2.5 유지, max_stop 캡만 regime별(라이브).

## 8. 포지션 사이징

regime별 risk_per_trade(1.0/0.75/0.5/0.25%). 백테스트 자산곡선은 이 값으로 거래별 사이징하여 CAGR/Calmar/MDD 산출. 별도로 고정분율(0.2) 엣지 비교도 병행.

## 9. 비교 검증 (`main.run_v6_compare` → `state/v6_compare.json`, CLI `v6-compare`)

- v4·v5·v6를 **동일 종목군·기간·수수료(fee_bps)·슬리피지(slippage_bps)·체결(next_open)·lookback**으로 재백테스트. 기존 노트 수치 무시, 1:1 재산출.
- v4/v5 = 고정 파라미터(`_stock_trades`), v6 = regime 가변(`_stock_trades_regime`).
- 각 거래 태깅: `(entry_date, ret, regime, hold_days)`.

### 지표 (`strategy/metrics.py`) — 전량

총수익률, CAGR, MDD, Sharpe, Sortino, Calmar, 승률, 평균수익, 평균손실, Profit Factor, Expectancy, 실현손익비(평균수익/|평균손실|), 거래수, 평균보유기간, 최대연속손실, 월별수익률, **regime별(수익률·MDD·거래수)**.

### 반사실 추적 (v5 진입 O · v6 차단 O)

1. v5 진입 집합 산출.
2. 각 진입에 대해 그날 regime + stock추세 + CRASH차단 규칙으로 v6 차단 여부 판정.
3. v6가 차단한 v5거래 = 반사실 집합. 이들의 실제 사후 수익률(v5 exit ret) 집계:
   - 평균 ret < 0 → 차단이 손실회피에 기여(좋음).
   - 평균 ret > 0 → 좋은 기회 과차단(비용).
4. **차단사유별** 평균 ret·건수 요약.

## 10. v6 검증 기준 (합격선)

- v5 대비 MDD 감소.
- v5 대비 약세장(BEAR/CRASH) 손실 감소.
- v5 대비 CRASH 구간 신규진입 감소/차단.
- v5 대비 PF·Expectancy 큰 훼손 없음.
- v4 대비 거래기회·총수익률 개선.
- BULL에서 v5 진입 유연성 상당부분 유지.

## 11. 라이브 반영 (월요일 페이퍼)

- `config.yaml`에 `regime:` 섹션(정책 테이블) + `logic_mode: v6`.
- 라이브 신호엔진/오더매니저가 오늘 regime(지수 최신봉 or 거시노트 폴백) 조회 → regime 게이트 적용, 차단사유 로깅.
- `swing-trader logic`으로 v6 스냅샷. **실거래 자동매매 미가동(페이퍼 유지).**

## 12. 산출물

- 코드: `market_regime.py`, `regime_policy.py`, `metrics.py`, `backtest._stock_trades_regime`, `main.run_v6_compare`, CLI `v6-compare`, 라이브 게이팅, config `regime:` 섹션.
- 테스트: regime 판별, 정책 조회, 지표(Sortino/Calmar/연속손실), CRASH 차단, 반사실.
- 문서: 볼트 `04_Trading/Logic/2026-07-04_v6.md` (12항목: 개요·v4/v5 대비 변경점·regime 판별·regime별 설정·진입·차단·손절익절트레일링·사이징·v4/v5/v6 비교표·regime별 성과표·반사실 사후검증·추가개선안).

## 13. 단계

- **A (필수, 이번 주말):** §3~§10 = regime 판별·정책·백테스트·지표·반사실·볼트문서.
- **B (월요일 페이퍼):** §11 라이브 게이팅+config+로깅, logic v6 스냅샷.
- **C (후순위):** 대시보드 v6 카드(version_compare 자동 포함 or 전용 뷰).

## 14. 비목표 (주의)

- 특정 종목 추천 없음 — 전략 로직만.
- 실거래 자동매매 즉시 가동 금지 — 백테스트+페이퍼 검증 우선.
- 모든 차단 사유·계산근거를 기록에 남김.
