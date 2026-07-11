# 설계: 자가개선 튜닝 루프 (슬라이스 1) — `swing evolve`

- **날짜**: 2026-07-11
- **상태**: 승인됨 (구현 계획 대기)
- **범위**: 마스터 아키텍처 3슬라이스 중 **슬라이스 1**만. 슬라이스 2(국면 라우터)·3(전략 발굴)은 로드맵에만 기록.

---

## 1. 배경 · 문제

백테스트 모델(중/단기 스윙 KR/US, 초단기 스캘핑)의 승률을 AI가 스스로 판단해 개선하는 "똑똑해지는 루프"를 만들고 싶다. 현재 시스템은 **열린 직선**이다:

```
성과 → logic-review(AI 제안) → 끝 (사람이 손으로 config.yaml 편집)
```

`state/logic_review.json`은 이미 `{config_key, current, suggested}` 구조의 수정안을 생성하지만, **검증 → 채택 → 학습**으로 되돌아오지 않는다. `state/learned_rules.json`은 `{}`로 비어 있다 — 루프가 닫히지 않았다는 결정적 신호.

### 핵심 위험 (사용자 이력에서 도출)
`v5 미채택 v4 유지`, `us-overnight 가짜엣지 폐기`, `synthetic 데이터 함정` — 진짜 위험은 "AI가 좋아 보이는 걸 제안하는 것"이 아니라 **과적합된 가짜 개선을 채택하는 것**이다. 따라서 모든 후보는 **같은 심판대(referee)를 반드시 통과**해야 하고, 채택은 **100% 사람 승인**을 거친다.

---

## 2. 마스터 아키텍처 (전체 지도 — 맥락용)

"챔피언/도전자 진화 루프 + 국면 라우터". 야구단 비유: 스카우트(후보생성) → 연습경기장(심판대) → 스카우팅 리포트(학습원장) → 상대별 라인업(국면) → 감독(오케스트레이터).

```
① 후보 생성   [기존 config→튜너]  [부자사냥꾼 고수→발굴]  [금융뉴스/거시]
                     │  후보 = 통일된 config 스펙
② 심판대       walk-forward OOS · 과적합 가드 · 챔피언 A/B → 판정
③ 학습 원장    learned_rules — 통한 것 + "왜 기각됐나"까지 축적 → 다음 후보 제약
④ 국면 라우터  상승/하락/횡보 판별 → 국면별 최고검증모델을 챔피언으로 스위칭
⑤ 오케스트레이터(Agentic) — 언제 돌릴지·사람승인 게이트·"증거 약하면 손대지 마"
```

**빌드 순서**: 슬라이스1(튜닝루프 닫기) → 슬라이스2(국면 라우터) → 슬라이스3(발굴 엔진). 심판대는 셋 다 공유하므로 먼저, 그 위에서 가장 싸고 안전한 튜닝루프부터.

> 참고: ④ 국면(`strategy/market_regime.py`·`regime_policy.py`), 발굴(`strategy/methods.py`)의 스캐폴딩은 이미 존재 — 후속 슬라이스는 신규가 아닌 확장.

---

## 3. 슬라이스 1 범위

### 재사용 (그대로 씀 — 신규 아님)
| 역할 | 기존 자산 | 비고 |
|---|---|---|
| 후보 제안 | `review/logic_reviewer.py` `build_review()` | `{config_key, current, suggested}` 구조 이미 생성 |
| **심판대(referee)** | `strategy/harness.py` `compare()` | OOS 분할·과적합 가드(IS→OOS 격차)·`min_oos_trades=100`·`verdict: improve/neutral/worse/insufficient`. **완성돼 있음** |
| 버전관리 | `strategy/logic_version.py` `snapshot/diff/save_version` | |
| 학습원장 | `review/learning_log.py` `LearningLog.learn()` | 지금 미사용(`{}`) |
| 디스코드 | `notify/discord.py` `notify()` | 단방향 웹훅 |

### 신규 (작음)
1. **오케스트레이터** — 신규 모듈 `review/evolve.py` + cli 명령 `evolve`, `adopt <id>`, `reject <id>`.
2. **상태파일** `state/pending_proposals.json` — 대기 중 제안 목록.
3. **config 단일 키 쓰기** — `adopt` 시 `config.yaml`에 키 하나 적용. 주석/포맷 보존 위해 `ruamel.yaml` 라운드트립 권장(대안: 타깃 라인 편집).
4. **학습원장 배선** — `LearningLog`를 실제로 채움(채택/기각 사유).

---

## 4. 데이터 흐름 (닫힌 루프 한 바퀴)

```
① swing evolve   (스케줄러 주기 실행)
   └ build_review(cfg) → suggestions[]
   └ 각 suggestion 분류:
       ├ 이미 learned_rules에 "기각"으로 있으면 → 스킵(재제안 금지)
       ├ T1 (백테가능 키)
       │    → candidate params 구성(baseline=현재 snapshot, candidate=suggested 반영)
       │    → harness.compare(cfg, provider, notes, days, baseline, candidate)
       │    → verdict:
       │        improve      → pending_proposals.json 등록 + 🧠 Discord 발송(ID·OOS수치)
       │        worse/neutral→ learned_rules.learn(reject, "이 키 이 방향=OOS악화")
       │        insufficient → 보류(표본부족 기록만, 재시도 여지)
       └ T2 (백테불가 키) → "정량검증 불가·페이퍼 관찰 필요" Discord 안내만(등록·채택 안 함)

② (사람) Discord 확인 후 판단

③ swing adopt <id>
   └ pending에서 로드 → config.yaml 해당 키 적용
   └ logic_version.save_version(snapshot, note) → v↑
   └ learned_rules.learn(accepted, "OOS개선 검증 후 채택")
   └ pending 상태=adopted, ✅ Discord 확인 발송

   swing reject <id>
   └ pending 상태=rejected + learned_rules.learn(manual_reject, 사람 거절)
```

---

## 5. T1/T2 경계 (정직성 제약)

`harness.simulate_trades` 시그니처가 백테 가능한 파라미터를 규정한다: `take, stop, max_hold, runner, take2, trail, cost, min_tv_eok, require_uptrend`.

### T1 화이트리스트 (config_key → harness param)
| config_key | harness param |
|---|---|
| `risk.take1_pct` | `take` |
| `risk.default_stop_pct` | `stop` |
| `risk.max_hold_days` | `max_hold` |
| `risk.take2_pct` | `take2` |
| `risk.trail_pct` | `trail` |
| `risk.min_trading_value_eok` | `min_tv_eok` |
| `risk.require_uptrend` | `require_uptrend` |

> 구현 시 `strategy/backtest.py._resolve_params`로 정확 매핑 최종 확인.

### T2 (백테 불가 → 페이퍼 관찰만)
`risk.min_reward_risk`, `scoring.weights.*`, `scoring.thresholds.*`, `ai.*`, `capital.max_positions`, `risk.momentum_min_pct` 등. **개선이라 우기지 않고** "관찰 필요"로만 표시. 예시로 나왔던 `min_reward_risk`가 여기에 속함.

---

## 6. 자료구조

### `state/pending_proposals.json`
```json
{
  "proposals": [
    {
      "id": "A3",
      "created": "2026-07-11T14:00:00+09:00",
      "config_key": "risk.take1_pct",
      "current": 6.0,
      "suggested": 6.5,
      "tier": "T1",
      "title": "익절 상향으로 손익비 개선",
      "insight": "…(logic-review 근거)",
      "verdict": "improve",
      "oos": {"base_expectancy": 0.62, "cand_expectancy": 0.70, "n_oos": 143,
              "base_sharpe": 0.11, "cand_sharpe": 0.14},
      "status": "pending"
    }
  ]
}
```
- `id`: 짧은 사람친화 ID(예: A3). 결정론적 생성(날짜+config_key 해시 앞자리) — `Math.random`/시간난수 금지.
- `status`: `pending | adopted | rejected`.

### `state/learned_rules.json` (기존 `LearningLog` 스키마 사용)
`rule_id` 키 규칙: `reject:{config_key}:{up|down}` / `accept:{config_key}:{up|down}`.
```json
{
  "reject:risk.default_stop_pct:down": {
    "note": "손절 -3→-3.5 방향은 OOS 기대값 악화(-0.05%p)",
    "hits": 2, "cases": ["2026-07-11"]
  }
}
```
`evolve`는 후보 분류 시 `reject:{key}:{dir}`가 있으면 재제안하지 않는다.

---

## 7. 컴포넌트 경계

- **`review/evolve.py`** (신규) — 오케스트레이션. 입력: `cfg`. 하는 일: 제안 생성 위임(`logic_reviewer`) → 분류 → 심판(`harness`) → pending 기록 → Discord. `adopt/reject`도 여기.
  - 의존: `logic_reviewer`, `harness`, `logic_version`, `learning_log`, `notify.discord`, 신규 `config_writer`.
- **`config_writer`** (신규, 작음) — `config.yaml`에 점표기 키 하나 쓰기. 순수하고 독립 테스트 가능. 의존: `ruamel.yaml`(또는 라인편집).
- **cli.py** — 서브파서 3개 추가(`evolve`, `adopt`, `reject`). 기존 패턴 그대로.

각 유닛은 "무엇을/어떻게 쓰나/무엇에 의존"이 한 문장으로 답해져야 한다.

---

## 8. 에러 처리 (실제 발생하는 것만)

- AI 키 없음/파싱 실패 → `build_review`가 이미 `ok:False` 반환. `evolve`는 조용히 종료(제안 0).
- OOS 표본 부족(`insufficient`) → 채택 금지, 다음 런 재시도(기각으로 학습하지 않음).
- 웹훅 없음 → `notify()`가 콘솔 출력으로 폴백(기존 동작).
- `adopt <id>` 대상 없음/이미 처리됨 → 명확한 에러 메시지, config 미변경.
- config.yaml 쓰기 실패 → 원본 보존, 버전기록/학습 안 함(부분적용 금지 — 원자적으로).

> 일어날 수 없는 시나리오용 방어코드는 넣지 않는다(Karpathy 단순함).

---

## 9. 테스트 (검증 가능한 성공 기준)

기존 `tests/test_logic_review.py` 패턴 재사용.

1. **OOS 개선 제안** → `evolve`가 pending 1건 등록 + Discord 텍스트에 ID 포함. *(harness.compare를 improve로 스텁)*
2. **OOS 악화 제안** → pending 0건 + `learned_rules`에 `reject:*` 항목 생김 + **재실행 시 그 제안 재생성 안 됨**.
3. **`adopt <id>`** → config.yaml의 해당 값 실제 변경 + `logic_versions.json` v↑ + `learned_rules`에 `accept:*`.
4. **T2 제안**(`min_reward_risk`) → pending에 안 들어감, "관찰 필요"로만 표시(자동채택 절대 없음).
5. **config_writer 단위** → 점표기 키 하나만 바뀌고 나머지 라인/주석 보존.

---

## 10. 안전장치 (불변)

- 페이퍼/백테만 대상 — 실전 주문 로직과 무관.
- 채택은 100% 사람 승인(`adopt` 명령). 자동 채택 없음.
- 심판대 미통과(`worse/neutral/insufficient`) 제안은 발송조차 안 됨.
- 백테 불가(T2)는 "검증됨"이라 표기 금지.

---

## 11. 명시적 비범위 (YAGNI)

- 국면별 라우팅(슬라이스 2).
- 고수 기법 발굴(슬라이스 3).
- 디스코드 버튼/봇 상호작용(로컬 명령 승인으로 충분).
- 다중 키 동시 변경 제안(1제안=1키. 조합 탐색은 후속).
- 스캘핑(v6)·US(V1) 로직으로의 확장(스윙 KR에서 먼저 검증 후).
