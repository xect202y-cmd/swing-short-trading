# 초단기(단타) 데이트레이딩 카테고리 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 일봉 기반 데이트레이딩 모델 2종(v1 변동성돌파, v2 갭하락반등)을 모델당 가상 300만 페이퍼 계좌로 운용하고, 디스코드 ⚡임베드·대시보드 카테고리 비교·10만원 목표 카드로 노출한다.

**Architecture:** swing repo에 독립 모듈 `scalp/`(전략·계좌·플래너·브리퍼·백테스트) + `market/realtime.py`(네이버/야후 실시간). 기존 아침 런(bat/workflow) 끝에 `scalp-run` 1커맨드 추가 — 이전 계획 정산(확정 일봉이 정본) → 오늘 계획 수립(볼트 시나리오 필터 + 그림자 OFF 병행) → 발송/저장. 대시보드는 `scalp_compare.json`/`scalp_state.json`을 GitHub raw로 읽어 카테고리 비교·목표 카드 렌더.

**Tech Stack:** Python 3.12(pandas·requests·pytest, editable 설치라 src 수정 즉시 반영) / Next.js 15(TS·SWR·vitest) / GitHub Actions / Discord webhook.

**스펙:** `docs/superpowers/specs/2026-07-03-scalp-daytrading-design.md`

## Global Constraints

- 날짜/시각은 전부 KST: `from ..state.daily_marker import today_kst` / `from ..models import now_kst` (절대 `date.today()`/`datetime.now()` 신규 사용 금지)
- `.bat` 파일 주석은 **영어만**(한글+chcp 65001 = 파서 사고 이력)
- 수수료 1.5bps + 슬리피지 5bps(config `paper.fee_bps`/`paper.slippage_bps` 재사용)
- 모델별 시드 300만(`SEED_PER_MODEL = 3_000_000`), 모델당 하루 최대 5종목(`MAX_POS = 5`)
- v1: k=0.5, 손절 -2.0% / v2: 갭 -2% 이상 + 전일 20>60일선, 손절 -2.5% / 전량 당일 종가 청산
- 실시간 시세: KR=네이버 polling(토스와 동일한 실시간 체결가라 토스 포팅은 하지 않음 — 스펙 3b의 "가용하면" 해석), US=야후. 볼트 노트의 price 필드 사용 금지
- 정산은 확정 일봉 OHLC가 정본. look-ahead 금지: 트리거가는 당일 고저 범위 안일 때만 체결, 손절은 보수적으로 우선 판정
- 테스트: `cd /c/Users/xect2/swing-short-trading && .venv/Scripts/python.exe -m pytest tests/ -q` (dashboard는 `npx vitest run` + `npx tsc --noEmit`)
- 커밋 메시지 끝: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`
- swing repo는 main 직푸시 허용(사용자 승인 이력). dashboard prod 배포는 실행 시 사용자에게 확인
- 기존 스윙 로직/state/version_compare.json은 절대 변경하지 않음

---

### Task 1: 실시간 시세 모듈 `market/realtime.py`

**Files:**
- Create: `src/swing_trader/market/realtime.py`
- Test: `tests/test_realtime.py`

**Interfaces:**
- Produces: `RealtimeQuote(price: float, open: float | None, prev_close: float | None, source: str)` (KRW 기준 — US는 fx 환산), `get_quote(ticker: str, fx: float = 1400.0) -> RealtimeQuote | None`
- Consumes: 없음 (requests 직접)

- [ ] **Step 1: 실패하는 테스트 작성** — 파싱 로직은 네트워크 없이 fixture로 검증

```python
# tests/test_realtime.py
"""실시간 시세 파서 — 네이버(KR)/야후(US) 응답 파싱(네트워크 없음)."""
from swing_trader.market.realtime import RealtimeQuote, _parse_naver, _parse_yahoo


def test_parse_naver_comma_strings():
    j = {"datas": [{"closePrice": "61,300", "openPrice": "60,900",
                    "compareToPreviousClosePrice": "400"}]}
    q = _parse_naver(j)
    assert q == RealtimeQuote(price=61300.0, open=60900.0, prev_close=60900.0, source="naver")
    # prev_close = price - compare(400) = 60,900


def test_parse_naver_bad_payload_returns_none():
    assert _parse_naver({}) is None
    assert _parse_naver({"datas": [{"closePrice": "0"}]}) is None


def test_parse_yahoo_converts_fx():
    j = {"chart": {"result": [{"meta": {
        "regularMarketPrice": 100.0, "chartPreviousClose": 98.0, "regularMarketDayHigh": 101.0,
    }, "indicators": {"quote": [{"open": [99.0]}]}}]}}
    q = _parse_yahoo(j, fx=1400.0)
    assert q.price == 140000.0 and q.open == 138600.0 and q.prev_close == 137200.0
    assert q.source == "yahoo"


def test_parse_yahoo_missing_returns_none():
    assert _parse_yahoo({"chart": {"result": []}}, fx=1400.0) is None
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/Scripts/python.exe -m pytest tests/test_realtime.py -q`
Expected: FAIL `ModuleNotFoundError: swing_trader.market.realtime`

- [ ] **Step 3: 구현**

```python
# src/swing_trader/market/realtime.py
"""실시간 시세 — 단타 계획용 당일 시가/현재가. KR=네이버 polling, US=야후.

볼트 노트 가격은 배치 시점 값이라 여기서만 가격을 읽는다(스펙 3b).
US는 KRW 환산(스윙 provider 와 동일 프레임). 정산은 확정 일봉이 정본이므로
여기 값은 '계획 표시/수량 산정'에만 쓴다.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import requests

log = logging.getLogger(__name__)
_UA = {"User-Agent": "Mozilla/5.0"}


@dataclass(frozen=True)
class RealtimeQuote:
    price: float
    open: float | None
    prev_close: float | None
    source: str


def _num(v) -> float | None:
    try:
        f = float(str(v).replace(",", ""))
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None


def _parse_naver(j: dict) -> RealtimeQuote | None:
    try:
        d = (j.get("datas") or [{}])[0]
    except (AttributeError, IndexError):
        return None
    price = _num(d.get("closePrice"))
    if price is None:
        return None
    opn = _num(d.get("openPrice"))
    diff = None
    try:
        diff = float(str(d.get("compareToPreviousClosePrice", "")).replace(",", ""))
    except (TypeError, ValueError):
        pass
    prev = price - diff if diff is not None else None
    return RealtimeQuote(price=price, open=opn, prev_close=prev, source="naver")


def _parse_yahoo(j: dict, fx: float) -> RealtimeQuote | None:
    try:
        r = j["chart"]["result"][0]
        meta = r.get("meta", {})
    except (KeyError, IndexError, TypeError):
        return None
    price = _num(meta.get("regularMarketPrice"))
    if price is None:
        return None
    prev = _num(meta.get("chartPreviousClose"))
    opn = None
    try:
        opn = _num((r["indicators"]["quote"][0].get("open") or [None])[0])
    except (KeyError, IndexError, TypeError):
        pass
    return RealtimeQuote(
        price=price * fx, open=opn * fx if opn else None,
        prev_close=prev * fx if prev else None, source="yahoo")


def _is_kr(ticker: str) -> bool:
    c = (ticker or "").split(".")[0]
    return c.isdigit() and len(c) == 6


def get_quote(ticker: str, fx: float = 1400.0) -> RealtimeQuote | None:
    """실패 시 None(호출자가 스킵/경고 처리 — 조용한 폴백 금지)."""
    try:
        if _is_kr(ticker):
            r = requests.get(
                f"https://polling.finance.naver.com/api/realtime/domestic/stock/{ticker.split('.')[0]}",
                headers=_UA, timeout=7)
            return _parse_naver(r.json()) if r.ok else None
        r = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}",
            headers=_UA, timeout=7)
        return _parse_yahoo(r.json(), fx) if r.ok else None
    except (requests.RequestException, ValueError) as e:
        log.warning("realtime quote 실패 %s: %s", ticker, e)
        return None
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `.venv/Scripts/python.exe -m pytest tests/test_realtime.py -q`
Expected: 4 passed

- [ ] **Step 5: 커밋** — `git add src/swing_trader/market/realtime.py tests/test_realtime.py && git commit -m "feat(scalp): 실시간 시세 모듈(네이버 KR/야후 US)"`

---

### Task 2: 전략 룰 `scalp/strategy.py`

**Files:**
- Create: `src/swing_trader/scalp/__init__.py` (빈 파일)
- Create: `src/swing_trader/scalp/strategy.py`
- Test: `tests/test_scalp_strategy.py`

**Interfaces:**
- Produces:
  - `PlanItem(model, ticker, name, qty, stop_pct, prev_close, prev_range, k=None, trigger=None, why="", shadow=False)` (dataclass, `asdict` 직렬화 가능)
  - `Fill(entry, exit, pnl, ret_pct, reason)`; `settle_item(item: PlanItem, bar, fee_bps: float, slip_bps: float) -> Fill | None` (None=미체결)
  - `V1_K = 0.5`, `V1_STOP = -2.0`, `V2_GAP = -2.0`, `V2_STOP = -2.5`
- Consumes: bar는 pandas Series 유사(`bar["open"|"high"|"low"|"close"]` float 접근만)

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# tests/test_scalp_strategy.py
"""단타 체결 판정 — look-ahead 금지·손절 우선·미체결 케이스."""
import pytest

from swing_trader.scalp.strategy import PlanItem, settle_item, V1_K


def bar(o, h, l, c):
    return {"open": o, "high": h, "low": l, "close": c}


def v1(qty=10, prev_close=10000.0, prev_range=400.0):
    return PlanItem(model="v1", ticker="005930", name="삼성전자", qty=qty,
                    stop_pct=-2.0, prev_close=prev_close, prev_range=prev_range, k=V1_K)


def v2(qty=10, prev_close=10000.0):
    return PlanItem(model="v2", ticker="005930", name="삼성전자", qty=qty,
                    stop_pct=-2.5, prev_close=prev_close, prev_range=300.0)


def test_v1_no_fill_when_high_below_trigger():
    # trigger = 10000 + 0.5*400 = 10200 > high 10150 → 미체결
    assert settle_item(v1(), bar(10000, 10150, 9900, 10100), 1.5, 5.0) is None


def test_v1_fills_at_trigger_and_exits_at_close():
    f = settle_item(v1(), bar(10000, 10400, 9950, 10350), 1.5, 5.0)
    cost = (1.5 + 5.0) / 10000
    assert f.reason == "종가청산"
    assert f.entry == pytest.approx(10200 * (1 + cost))
    assert f.exit == pytest.approx(10350 * (1 - cost))
    assert f.pnl == pytest.approx((f.exit - f.entry) * 10)


def test_v1_gap_above_trigger_enters_at_open():
    # 시가 10300 > 트리거 10200 → 시가 진입
    f = settle_item(v1(), bar(10300, 10500, 10250, 10450), 1.5, 5.0)
    assert f.entry == pytest.approx(10300 * (1 + (1.5 + 5.0) / 10000))


def test_v1_stop_first_when_low_touches():
    # 저가가 손절가 이하 → 보수적으로 손절 체결(같은 날 고저 순서 모름)
    f = settle_item(v1(), bar(10000, 10400, 9800, 10350), 1.5, 5.0)
    assert f.reason == "손절"
    assert f.exit < f.entry


def test_v2_no_fill_without_gap_down():
    # 시가 9900 = -1% 갭 → -2% 미달 → 미체결
    assert settle_item(v2(), bar(9900, 10100, 9850, 10050), 1.5, 5.0) is None


def test_v2_fills_at_open_on_gap_down():
    f = settle_item(v2(), bar(9750, 9950, 9700, 9900), 1.5, 5.0)   # -2.5% 갭
    assert f.reason == "종가청산"
    assert f.entry == pytest.approx(9750 * (1 + (1.5 + 5.0) / 10000))


def test_serialization_roundtrip():
    from dataclasses import asdict
    d = asdict(v1())
    assert d["model"] == "v1" and d["k"] == V1_K
    assert PlanItem(**d) == v1()
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/Scripts/python.exe -m pytest tests/test_scalp_strategy.py -q`
Expected: FAIL (module 없음)

- [ ] **Step 3: 구현**

```python
# src/swing_trader/scalp/__init__.py
```

```python
# src/swing_trader/scalp/strategy.py
"""단타(데이트레이딩) 룰 — v1 변동성돌파(추세형)·v2 갭하락반등(역추세형).

일봉 OHLC 만으로 정직하게 판정한다:
- 체결 인정 = 트리거가가 당일 고저 범위 안일 때만 (look-ahead 금지)
- 손절 우선 = 당일 저가가 손절가 이하이면 손절 체결로 보수 판정(장중 순서 미상)
- 전량 당일 종가 청산(오버나잇 없음)
"""
from __future__ import annotations

from dataclasses import dataclass

V1_K = 0.5        # 돌파 계수: 시가 + k×전일레인지
V1_STOP = -2.0    # %
V2_GAP = -2.0     # 시가 갭하락 임계(%)
V2_STOP = -2.5    # %


@dataclass(frozen=True)
class PlanItem:
    model: str                 # "v1" | "v2"
    ticker: str
    name: str
    qty: int
    stop_pct: float
    prev_close: float
    prev_range: float          # 전일 고가-저가
    k: float | None = None     # v1 전용
    trigger: float | None = None   # 표시용(KR은 실시간 시가로 해석) — 정산은 확정 시가로 재계산
    why: str = ""
    shadow: bool = False       # 시나리오 필터 OFF(그림자 A/B) 항목


@dataclass(frozen=True)
class Fill:
    entry: float
    exit: float
    pnl: float
    ret_pct: float
    reason: str    # "손절" | "종가청산"


def settle_item(item: PlanItem, bar, fee_bps: float, slip_bps: float) -> Fill | None:
    """확정 일봉으로 체결/청산 판정. None=미체결."""
    o, h, l, c = (float(bar["open"]), float(bar["high"]),
                  float(bar["low"]), float(bar["close"]))
    if o <= 0 or h <= 0:
        return None
    cost = (fee_bps + slip_bps) / 10000
    if item.model == "v1":
        trigger = o + (item.k or V1_K) * item.prev_range
        if h < trigger:
            return None
        entry = max(o, trigger) * (1 + cost)   # 갭 상방 출발이면 시가 진입
    else:  # v2 — 시가 갭하락 재확인(계획 시점 실시간 시가와 무관하게 확정 시가가 정본)
        if item.prev_close <= 0 or o > item.prev_close * (1 + V2_GAP / 100):
            return None
        entry = o * (1 + cost)
    stop_price = entry * (1 + item.stop_pct / 100)
    if l <= stop_price:
        exit_px, reason = stop_price * (1 - cost), "손절"
    else:
        exit_px, reason = c * (1 - cost), "종가청산"
    pnl = (exit_px - entry) * item.qty
    return Fill(entry=entry, exit=exit_px, pnl=pnl,
                ret_pct=round((exit_px / entry - 1) * 100, 2), reason=reason)
```

- [ ] **Step 4: 통과 확인** — Run: `.venv/Scripts/python.exe -m pytest tests/test_scalp_strategy.py -q` → 7 passed
- [ ] **Step 5: 커밋** — `git add src/swing_trader/scalp tests/test_scalp_strategy.py && git commit -m "feat(scalp): v1 돌파·v2 갭반등 체결 판정 룰"`

---

### Task 3: 가상 계좌 `scalp/account.py`

**Files:**
- Create: `src/swing_trader/scalp/account.py`
- Test: `tests/test_scalp_account.py`

**Interfaces:**
- Produces: `ScalpState.load(state_dir) -> ScalpState`, `.save(state_dir)`, `.apply_day(date, market, results)` — results는 `{"v1": {"pnl": float, "shadow_pnl": float, "trades": [dict]}, "v2": {...}}`
- 파일 스키마 `state/scalp_state.json`:
  ```json
  { "asOf": "YYYY-MM-DD", "seed_per_model": 3000000,
    "models": { "v1": {"cash": 3000000.0, "realized": 0.0, "shadow_realized": 0.0},
                "v2": {"cash": 3000000.0, "realized": 0.0, "shadow_realized": 0.0} },
    "daily": [ {"date": "YYYY-MM-DD", "market": "kr", "v1_pnl": 0.0, "v2_pnl": 0.0,
                "v1_shadow": 0.0, "v2_shadow": 0.0} ],
    "trades": [ {"date","market","model","ticker","name","qty","entry","exit","pnl","ret_pct","reason","why"} ] }
  ```
- Consumes: Task 2의 Fill/PlanItem은 dict 로 변환되어 전달됨

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# tests/test_scalp_account.py
"""단타 가상계좌 — 시드 300만×2, 일별 정산 적재, 같은 날 재정산 멱등."""
from swing_trader.scalp.account import SEED_PER_MODEL, ScalpState


def test_fresh_state(tmp_path):
    st = ScalpState.load(tmp_path)
    assert st.models["v1"]["cash"] == SEED_PER_MODEL
    assert st.models["v2"]["cash"] == SEED_PER_MODEL
    assert st.daily == [] and st.trades == []


def test_apply_day_updates_cash_and_daily(tmp_path):
    st = ScalpState.load(tmp_path)
    st.apply_day("2026-07-03", "kr", {
        "v1": {"pnl": 15000.0, "shadow_pnl": 12000.0,
               "trades": [{"ticker": "005930", "pnl": 15000.0}]},
        "v2": {"pnl": -8000.0, "shadow_pnl": -8000.0, "trades": []},
    })
    assert st.models["v1"]["cash"] == SEED_PER_MODEL + 15000.0
    assert st.models["v2"]["realized"] == -8000.0
    assert st.models["v1"]["shadow_realized"] == 12000.0
    assert st.daily[-1] == {"date": "2026-07-03", "market": "kr",
                            "v1_pnl": 15000.0, "v2_pnl": -8000.0,
                            "v1_shadow": 12000.0, "v2_shadow": -8000.0}
    assert st.trades[-1]["date"] == "2026-07-03" and st.trades[-1]["model"] == "v1"


def test_apply_same_day_market_is_idempotent(tmp_path):
    st = ScalpState.load(tmp_path)
    day = {"v1": {"pnl": 1000.0, "shadow_pnl": 0.0, "trades": []},
           "v2": {"pnl": 0.0, "shadow_pnl": 0.0, "trades": []}}
    st.apply_day("2026-07-03", "kr", day)
    st.apply_day("2026-07-03", "kr", day)   # 재실행(failover 중복) → 덮어쓰기
    assert st.models["v1"]["cash"] == SEED_PER_MODEL + 1000.0
    assert len(st.daily) == 1


def test_save_load_roundtrip(tmp_path):
    st = ScalpState.load(tmp_path)
    st.apply_day("2026-07-03", "us", {
        "v1": {"pnl": 500.0, "shadow_pnl": 500.0, "trades": []},
        "v2": {"pnl": 0.0, "shadow_pnl": 0.0, "trades": []}})
    st.save(tmp_path)
    again = ScalpState.load(tmp_path)
    assert again.models["v1"]["cash"] == SEED_PER_MODEL + 500.0
    assert again.daily == st.daily
```

- [ ] **Step 2: 실패 확인** — Run: `.venv/Scripts/python.exe -m pytest tests/test_scalp_account.py -q` → FAIL

- [ ] **Step 3: 구현**

```python
# src/swing_trader/scalp/account.py
"""단타 가상계좌 — 모델별 독립 300만. 같은 (date, market) 재정산은 덮어쓰기(멱등).

멱등이 필요한 이유: 로컬 지각 실행/클라우드 failover 로 같은 날이 두 번 정산될 수 있다
(스윙 last_run 덮어쓰기 사고의 교훈 — 여기서는 병합 대신 '같은 키 교체'가 정답:
 정산 소스가 확정 일봉이라 몇 번을 계산해도 같은 값이어야 하므로).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

SEED_PER_MODEL = 3_000_000
_FILE = "scalp_state.json"
_MODELS = ("v1", "v2")


@dataclass
class ScalpState:
    models: dict = field(default_factory=dict)
    daily: list = field(default_factory=list)
    trades: list = field(default_factory=list)
    asOf: str = ""

    @classmethod
    def load(cls, state_dir: Path) -> "ScalpState":
        p = Path(state_dir) / _FILE
        raw: dict = {}
        if p.exists():
            try:
                raw = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                raw = {}
        models = raw.get("models") or {}
        for m in _MODELS:
            models.setdefault(m, {"cash": float(SEED_PER_MODEL), "realized": 0.0,
                                  "shadow_realized": 0.0})
        return cls(models=models, daily=list(raw.get("daily") or []),
                   trades=list(raw.get("trades") or []), asOf=str(raw.get("asOf") or ""))

    def apply_day(self, d: str, market: str, results: dict) -> None:
        # 같은 (date, market) 기존 기록 제거(재정산 멱등) — 현금/실현도 되돌린 뒤 재적용
        prev = next((r for r in self.daily if r["date"] == d and r["market"] == market), None)
        if prev:
            for m in _MODELS:
                self.models[m]["cash"] -= prev[f"{m}_pnl"]
                self.models[m]["realized"] -= prev[f"{m}_pnl"]
                self.models[m]["shadow_realized"] -= prev[f"{m}_shadow"]
            self.daily = [r for r in self.daily if not (r["date"] == d and r["market"] == market)]
            self.trades = [t for t in self.trades if not (t["date"] == d and t["market"] == market)]
        row = {"date": d, "market": market}
        for m in _MODELS:
            res = results.get(m) or {"pnl": 0.0, "shadow_pnl": 0.0, "trades": []}
            self.models[m]["cash"] += res["pnl"]
            self.models[m]["realized"] += res["pnl"]
            self.models[m]["shadow_realized"] += res["shadow_pnl"]
            row[f"{m}_pnl"] = res["pnl"]
            row[f"{m}_shadow"] = res["shadow_pnl"]
            for t in res.get("trades", []):
                self.trades.append({"date": d, "market": market, "model": m, **t})
        self.daily.append(row)
        self.daily.sort(key=lambda r: (r["date"], r["market"]))
        self.asOf = d

    def save(self, state_dir: Path) -> None:
        p = Path(state_dir) / _FILE
        p.write_text(json.dumps({
            "asOf": self.asOf, "seed_per_model": SEED_PER_MODEL,
            "models": self.models, "daily": self.daily, "trades": self.trades[-400:],
        }, ensure_ascii=False, indent=2), encoding="utf-8")
```

- [ ] **Step 4: 통과 확인** — 4 passed
- [ ] **Step 5: 커밋** — `git commit -m "feat(scalp): 모델별 300만 가상계좌(멱등 일별 정산)"`

---

### Task 4: 플랜 빌더 `scalp/planner.py`

**Files:**
- Create: `src/swing_trader/scalp/planner.py`
- Test: `tests/test_scalp_planner.py`

**Interfaces:**
- Consumes: `assess_macro`(macro/regime.py, `MacroState.risk: RiskLevel`), `PlanItem`(Task 2), `get_quote`(Task 1), `RiskLevel`(models)
- Produces:
  - `build_scenario(cfg, reader) -> dict` — `{"risk": "낮음|중간|높음", "notes": [...], "focus_text": str}` (focus_text = 지침로그 최신 evening 노트 원문, 없으면 "")
  - `build_plan(candidates, cash_by_model, scenario, quotes) -> dict` — `{"v1": [PlanItem], "v2": [PlanItem], "v1_shadow": [...], "v2_shadow": [...]}`
    - candidates: `[{"ticker","name","prev_close","prev_range","prev_tv_eok","uptrend": bool}]` (호출자가 일봉에서 산출)
    - 규칙: 거래대금 내림차순, focus_text에 name 포함 시 최상단 가점, v1은 risk "높음"이면 상한 5→2(shadow는 항상 5), v2는 uptrend 필수, qty = (cash/5)//기준가(KR=실시간가, 없으면 prev_close), qty<1 스킵
  - `save_plan(state_dir, market, plan_dict)` / `load_plans(state_dir) -> dict` — `state/scalp_plan.json` `{ "<market>": {"date","scenario","items":[asdict(PlanItem)...]}}` (shadow 포함 단일 리스트, `shadow` 플래그로 구분)

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# tests/test_scalp_planner.py
"""플랜 빌더 — 시나리오 가중/상한·qty 산정·직렬화 왕복."""
from swing_trader.scalp.planner import build_plan, load_plans, save_plan
from swing_trader.scalp.strategy import PlanItem


def cand(t, name, tv=100.0, uptrend=True, prev_close=10000.0):
    return {"ticker": t, "name": name, "prev_close": prev_close,
            "prev_range": 300.0, "prev_tv_eok": tv, "uptrend": uptrend}


def _scen(risk="낮음", focus=""):
    return {"risk": risk, "notes": [], "focus_text": focus}


def test_v1_caps_at_5_and_sorts_by_trading_value():
    cands = [cand(f"00000{i}", f"종목{i}", tv=float(i)) for i in range(1, 8)]
    plan = build_plan(cands, {"v1": 3_000_000, "v2": 3_000_000}, _scen(), quotes={})
    assert len(plan["v1"]) == 5
    assert plan["v1"][0].ticker == "000007"   # 거래대금 최대 우선


def test_high_risk_caps_v1_at_2_but_shadow_keeps_5():
    cands = [cand(f"00000{i}", f"종목{i}", tv=float(i)) for i in range(1, 8)]
    plan = build_plan(cands, {"v1": 3_000_000, "v2": 3_000_000}, _scen(risk="높음"), quotes={})
    assert len(plan["v1"]) == 2
    assert len(plan["v1_shadow"]) == 5
    assert all(it.shadow for it in plan["v1_shadow"])


def test_focus_name_boosts_to_front():
    cands = [cand("000001", "삼성전자", tv=1.0), cand("000002", "포스코", tv=99.0)]
    plan = build_plan(cands, {"v1": 3_000_000, "v2": 3_000_000},
                      _scen(focus="오늘은 삼성전자 반도체 모멘텀 주목"), quotes={})
    assert plan["v1"][0].name == "삼성전자"


def test_v2_requires_uptrend():
    cands = [cand("000001", "A", uptrend=False), cand("000002", "B", uptrend=True)]
    plan = build_plan(cands, {"v1": 3_000_000, "v2": 3_000_000}, _scen(), quotes={})
    assert [i.ticker for i in plan["v2"]] == ["000002"]


def test_qty_from_budget_and_quote_price():
    cands = [cand("000001", "A", prev_close=100_000.0)]
    plan = build_plan(cands, {"v1": 3_000_000, "v2": 3_000_000}, _scen(),
                      quotes={"000001": 120_000.0})   # 실시간가 우선
    assert plan["v1"][0].qty == 5   # (3_000_000/5)//120_000


def test_plan_save_load_roundtrip(tmp_path):
    items = [PlanItem(model="v1", ticker="000001", name="A", qty=3, stop_pct=-2.0,
                      prev_close=100.0, prev_range=5.0, k=0.5)]
    save_plan(tmp_path, "kr", {"date": "2026-07-03",
                               "scenario": {"risk": "낮음", "notes": [], "focus_text": ""},
                               "items": items})
    plans = load_plans(tmp_path)
    assert plans["kr"]["date"] == "2026-07-03"
    assert plans["kr"]["items"][0].ticker == "000001"   # PlanItem 으로 복원
```

- [ ] **Step 2: 실패 확인** — FAIL(module 없음)

- [ ] **Step 3: 구현**

```python
# src/swing_trader/scalp/planner.py
"""전일 리서치 기반 단타 플랜 — 시나리오(거시+지침로그) → 종목 선정 → PlanItem.

사실/판단 분리: 가격·수량은 실측(실시간가/전일봉)만, 볼트는 선별 가중에만.
그림자 A/B: 시나리오 필터 OFF 리스트(shadow)를 항상 병행 산출해 필터의
부가가치 자체를 검증한다(스펙 3). 백테스트는 기계룰만 쓰므로 이 모듈과 무관.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from ..macro.regime import assess_macro
from .strategy import V1_K, V1_STOP, V2_STOP, PlanItem

MAX_POS = 5
_PLAN_FILE = "scalp_plan.json"


def build_scenario(cfg, reader) -> dict:
    macro = assess_macro(reader.macro_dashboard(), reader.macro_regime(),
                         vix_caution=float(cfg.get("event_filter", "vix_caution", default=20.0)))
    focus_text = ""
    gdir = cfg.vault_root / "금융뉴스" / "지침로그"
    if gdir.exists():
        evenings = sorted(gdir.glob("*-evening.md"))
        if evenings:
            try:
                focus_text = evenings[-1].read_text(encoding="utf-8")
            except OSError:
                focus_text = ""
    return {"risk": macro.risk.value, "notes": macro.notes, "focus_text": focus_text}


def _rank(cands: list[dict], focus_text: str) -> list[dict]:
    def key(c):
        boost = 1 if (c["name"] and c["name"] in focus_text) else 0
        return (boost, c.get("prev_tv_eok") or 0.0)
    return sorted(cands, key=key, reverse=True)


def _qty(budget: float, ref_price: float) -> int:
    return int(budget // ref_price) if ref_price > 0 else 0


def _items(model: str, cands: list[dict], cash: float, quotes: dict,
           cap: int, shadow: bool) -> list[PlanItem]:
    out: list[PlanItem] = []
    budget = cash / MAX_POS
    for c in cands:
        if len(out) >= cap:
            break
        ref = quotes.get(c["ticker"]) or c["prev_close"]
        q = _qty(budget, ref)
        if q < 1:
            continue
        if model == "v1":
            out.append(PlanItem(model="v1", ticker=c["ticker"], name=c["name"], qty=q,
                                stop_pct=V1_STOP, prev_close=c["prev_close"],
                                prev_range=c["prev_range"], k=V1_K,
                                why=c.get("why", ""), shadow=shadow))
        else:
            out.append(PlanItem(model="v2", ticker=c["ticker"], name=c["name"], qty=q,
                                stop_pct=V2_STOP, prev_close=c["prev_close"],
                                prev_range=c["prev_range"],
                                why=c.get("why", ""), shadow=shadow))
    return out


def build_plan(candidates: list[dict], cash_by_model: dict, scenario: dict,
               quotes: dict) -> dict:
    ranked = _rank(candidates, scenario.get("focus_text", ""))
    base = _rank(candidates, "")                      # 그림자 = 시나리오 무가중
    v1_cap = 2 if scenario.get("risk") == "높음" else MAX_POS
    up = [c for c in ranked if c.get("uptrend")]
    up_base = [c for c in base if c.get("uptrend")]
    return {
        "v1": _items("v1", ranked, cash_by_model["v1"], quotes, v1_cap, False),
        "v2": _items("v2", up, cash_by_model["v2"], quotes, MAX_POS, False),
        "v1_shadow": _items("v1", base, cash_by_model["v1"], quotes, MAX_POS, True),
        "v2_shadow": _items("v2", up_base, cash_by_model["v2"], quotes, MAX_POS, True),
    }


def save_plan(state_dir: Path, market: str, plan: dict) -> None:
    p = Path(state_dir) / _PLAN_FILE
    data: dict = {}
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
    items = plan["items"]
    data[market] = {"date": plan["date"], "scenario": plan["scenario"],
                    "items": [asdict(i) if isinstance(i, PlanItem) else i for i in items]}
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_plans(state_dir: Path) -> dict:
    p = Path(state_dir) / _PLAN_FILE
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    for mk, entry in data.items():
        entry["items"] = [PlanItem(**it) for it in entry.get("items", [])]
    return data
```

- [ ] **Step 4: 통과 확인** — `pytest tests/test_scalp_planner.py -q` → 7 passed
- [ ] **Step 5: 전체 회귀** — `pytest tests/ -q` → 전부 통과
- [ ] **Step 6: 커밋** — `git commit -m "feat(scalp): 시나리오 플랜 빌더(+그림자 A/B 리스트)"`

---

### Task 5: 디스코드/볼트 브리퍼 `scalp/briefer.py`

**Files:**
- Create: `src/swing_trader/scalp/briefer.py`
- Test: `tests/test_scalp_briefer.py`

**Interfaces:**
- Consumes: `PlanItem`(Task 2), `ScalpState`(Task 3)
- Produces: `scalp_brief(market, settled, plan, state, plan_date) -> tuple[dict, str]`
  - settled: `{"v1": [{"name","entry","exit","pnl","ret_pct","reason"}], "v2": [...]}` (그림자 제외 실계좌 체결만)
  - 반환 = (디스코드 embed(오렌지 0xE67E22), 옵시디언 markdown)
  - embed title `⚡ 단타 페이퍼 · KR|US`, footer에 `가상 300만 ×2모델 · 당일청산`

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# tests/test_scalp_briefer.py
"""단타 브리핑 — 스윙과 구분되는 임베드(오렌지·⚡)와 md 렌더."""
from swing_trader.scalp.account import ScalpState
from swing_trader.scalp.briefer import ORANGE, scalp_brief
from swing_trader.scalp.strategy import PlanItem


def test_brief_contains_results_and_plan(tmp_path):
    state = ScalpState.load(tmp_path)
    settled = {"v1": [{"name": "삼성전자", "entry": 61000.0, "exit": 61600.0,
                       "pnl": 5900.0, "ret_pct": 0.98, "reason": "종가청산"}], "v2": []}
    plan = {"date": "2026-07-03",
            "scenario": {"risk": "낮음", "notes": ["VIX 15 안정"], "focus_text": ""},
            "items": [PlanItem(model="v1", ticker="005930", name="삼성전자", qty=9,
                               stop_pct=-2.0, prev_close=61000.0, prev_range=800.0,
                               k=0.5, trigger=61400.0, why="거래대금 상위")]}
    embed, md = scalp_brief("kr", settled, plan, state, "2026-07-02")
    assert embed["title"].startswith("⚡ 단타 페이퍼 · KR")
    assert embed["color"] == ORANGE
    joined = str(embed) + md
    assert "삼성전자" in joined and "61,400" in joined   # 계획 트리거가 노출
    assert "+5,900" in joined or "5,900" in joined       # 결과 손익 노출
    assert "300만" in str(embed["footer"])


def test_brief_no_results_says_so(tmp_path):
    state = ScalpState.load(tmp_path)
    plan = {"date": "2026-07-03", "scenario": {"risk": "낮음", "notes": [], "focus_text": ""},
            "items": []}
    embed, md = scalp_brief("us", {"v1": [], "v2": []}, plan, state, "2026-07-02")
    assert "체결 없음" in str(embed) + md
```

- [ ] **Step 2: 실패 확인** — FAIL

- [ ] **Step 3: 구현**

```python
# src/swing_trader/scalp/briefer.py
"""단타 브리핑 — 디스코드 임베드(오렌지 ⚡, 스윙과 시각 분리) + 옵시디언 md."""
from __future__ import annotations

from .account import SEED_PER_MODEL, ScalpState
from .strategy import PlanItem

ORANGE = 0xE67E22
_MK = {"kr": "KR", "us": "US"}


def _won(v) -> str:
    return "—" if v is None else f"{round(v):,}"


def _results_lines(settled: dict) -> list[str]:
    out: list[str] = []
    for m in ("v1", "v2"):
        rows = settled.get(m) or []
        tag = "v1 돌파" if m == "v1" else "v2 갭반등"
        if not rows:
            out.append(f"[{tag}] 체결 없음")
            continue
        day = sum(r["pnl"] for r in rows)
        out.append(f"[{tag}] {len(rows)}건 · 일손익 {'+' if day >= 0 else ''}{_won(day)}원")
        for r in rows:
            sign = "+" if r["pnl"] >= 0 else ""
            out.append(f"  · {r['name']} {_won(r['entry'])}→{_won(r['exit'])} "
                       f"{sign}{_won(r['pnl'])}원({r['ret_pct']:+.1f}%) {r['reason']}")
    return out


def _plan_lines(plan: dict) -> list[str]:
    items = [i for i in plan.get("items", []) if isinstance(i, PlanItem) and not i.shadow] or \
            [i for i in plan.get("items", []) if not getattr(i, "shadow", False)]
    if not items:
        return ["(오늘 계획 없음 — 조건 충족 후보 없음)"]
    out: list[str] = []
    for m in ("v1", "v2"):
        mine = [i for i in items if i.model == m]
        if not mine:
            continue
        out.append("[v1 돌파 — 트리거 터치 시 매수]" if m == "v1" else "[v2 갭반등 — 시가 진입]")
        for i in mine:
            trig = f"트리거 {_won(i.trigger)}원 · " if i.trigger else ""
            out.append(f"  · {i.name} {i.qty}주 · {trig}손절 {i.stop_pct:+.1f}% · {i.why}")
    return out


def scalp_brief(market: str, settled: dict, plan: dict, state: ScalpState,
                settled_date: str) -> tuple[dict, str]:
    mk = _MK.get(market, market.upper())
    scen = plan.get("scenario", {})
    total = sum(state.models[m]["cash"] for m in ("v1", "v2"))
    res = _results_lines(settled)
    pl = _plan_lines(plan)
    day_total = sum(r["pnl"] for m in ("v1", "v2") for r in (settled.get(m) or []))
    fields = [
        {"name": f"📊 {settled_date} 결과 · 일손익 {'+' if day_total >= 0 else ''}{_won(day_total)}원",
         "value": "\n".join(res)[:1024], "inline": False},
        {"name": f"🗺️ {plan.get('date')} 계획 · 시나리오 리스크 {scen.get('risk', '—')}",
         "value": ("\n".join(scen.get("notes", [])[:2] + pl))[:1024], "inline": False},
    ]
    embed = {"title": f"⚡ 단타 페이퍼 · {mk}", "color": ORANGE, "fields": fields,
             "footer": {"text": f"가상 {SEED_PER_MODEL // 10000}만 ×2모델 · 당일청산 · "
                                f"합산 {_won(total)}원"}}
    md = (f"### ⚡ 단타 · {mk} · {plan.get('date')}\n"
          f"**{settled_date} 결과**\n" + "\n".join(res) +
          f"\n\n**계획(리스크 {scen.get('risk', '—')})**\n" + "\n".join(pl) + "\n")
    return embed, md
```

- [ ] **Step 4: 통과 확인** — 2 passed
- [ ] **Step 5: 커밋** — `git commit -m "feat(scalp): 디스코드 오렌지 임베드+볼트 md 브리퍼"`

---

### Task 6: 오케스트레이션 `main.run_scalp` + CLI `scalp-run`

**Files:**
- Modify: `src/swing_trader/main.py` (파일 끝, `__all__` 갱신)
- Modify: `src/swing_trader/cli.py` (서브커맨드 추가)
- Modify: `src/swing_trader/obsidian/writer.py` (append_scalp 추가)
- Modify: `config.yaml` — `paths.write` 에 `scalp_dir: "04_Trading/Scalp"` 한 줄
- Test: `tests/test_scalp_run.py`

**Interfaces:**
- Consumes: Task 1~5 전부, `_provider`/`_load_notes`/`VaultReader`/`notify_embeds`/`daily_marker(_DM)` (main.py 기존)
- Produces: `run_scalp(cfg, market: str) -> dict` — `{"settled": int, "planned": int, "warned": bool}`; CLI `swing-trader scalp-run --market kr|us`; `VaultWriter.append_scalp(md, d)` → `04_Trading/Scalp/YYYY-MM-DD_Scalp.md` append; 마커 `scalp_kr|scalp_us` 기록

- [ ] **Step 1: 실패하는 테스트 작성** — 정산 유닛만(오케스트레이션 전체는 Task 11 수동 E2E)

```python
# tests/test_scalp_run.py
"""run_scalp 정산 헬퍼 — 확정 일봉 매칭·미체결·그림자 분리."""
import pandas as pd

from swing_trader.main import _settle_scalp_plan
from swing_trader.scalp.strategy import PlanItem


def _df(d: str, o, h, l, c):
    idx = pd.DatetimeIndex([pd.Timestamp(d)])
    return pd.DataFrame({"open": [o], "high": [h], "low": [l], "close": [c]}, index=idx)


def _item(shadow=False):
    return PlanItem(model="v1", ticker="005930", name="삼성전자", qty=10,
                    stop_pct=-2.0, prev_close=10000.0, prev_range=400.0, k=0.5,
                    shadow=shadow)


def test_settle_matches_bar_by_date():
    plan = {"date": "2026-07-02", "items": [_item(), _item(shadow=True)]}
    dfs = {"005930": _df("2026-07-02", 10000, 10400, 9950, 10350)}
    results, rows = _settle_scalp_plan(plan, dfs, fee_bps=1.5, slip_bps=5.0)
    assert results["v1"]["pnl"] != 0.0
    assert results["v1"]["shadow_pnl"] != 0.0        # 그림자는 별도 합산
    assert len(results["v1"]["trades"]) == 1          # 실계좌 체결만 원장 기록
    assert rows["v1"][0]["name"] == "삼성전자"


def test_settle_no_bar_returns_none():
    plan = {"date": "2026-07-02", "items": [_item()]}
    results, rows = _settle_scalp_plan(plan, {}, fee_bps=1.5, slip_bps=5.0)
    assert results is None and rows is None           # 데이터 미도착 → 보류 신호
```

- [ ] **Step 2: 실패 확인** — FAIL(`_settle_scalp_plan` 없음)

- [ ] **Step 3: main.py 에 추가** (파일 끝 `__all__` 위)

```python
# ── 단타(데이트레이딩) ──
def _scalp_bar(df, d: str):
    sub = df[df.index.normalize() == __import__("pandas").Timestamp(d)]
    return None if sub.empty else sub.iloc[-1]


def _settle_scalp_plan(plan: dict, dfs: dict, fee_bps: float, slip_bps: float):
    """저장된 계획을 확정 일봉으로 정산. 계획 종목의 봉이 하나도 없으면 (None, None)=보류."""
    from .scalp.strategy import settle_item
    items = plan.get("items", [])
    if not items:
        return ({m: {"pnl": 0.0, "shadow_pnl": 0.0, "trades": []} for m in ("v1", "v2")},
                {"v1": [], "v2": []})
    bars = {i.ticker: _scalp_bar(dfs[i.ticker], plan["date"]) for i in items if i.ticker in dfs}
    if not any(b is not None for b in bars.values()):
        return None, None
    results = {m: {"pnl": 0.0, "shadow_pnl": 0.0, "trades": []} for m in ("v1", "v2")}
    rows: dict = {"v1": [], "v2": []}
    for i in items:
        bar = bars.get(i.ticker)
        if bar is None:
            continue
        f = settle_item(i, bar, fee_bps, slip_bps)
        if f is None:
            continue
        if i.shadow:
            results[i.model]["shadow_pnl"] += f.pnl
            continue
        results[i.model]["pnl"] += f.pnl
        row = {"ticker": i.ticker, "name": i.name, "qty": i.qty, "entry": round(f.entry, 2),
               "exit": round(f.exit, 2), "pnl": round(f.pnl, 0), "ret_pct": f.ret_pct,
               "reason": f.reason, "why": i.why}
        results[i.model]["trades"].append(row)
        rows[i.model].append(row)
    return results, rows


def run_scalp(cfg: Config, market: str) -> dict:
    """단타 페이퍼 1사이클: 이전 계획 정산 → 오늘 계획 수립 → 발송/저장/마커."""
    from .market.realtime import get_quote
    from .notify import health as _H
    from .notify.discord import notify_embeds
    from .scalp import planner as _P
    from .scalp.account import ScalpState
    from .scalp.briefer import scalp_brief
    reader = VaultReader(cfg)
    provider = _provider(cfg)
    notes = [n for n in _load_notes(cfg, reader, None, market) if n.ticker]
    fee = float(cfg.get("paper", "fee_bps", default=1.5))
    slip = float(cfg.get("paper", "slippage_bps", default=5.0))
    today = _DM.today_kst().isoformat()
    state = ScalpState.load(cfg.state_dir)
    warned = False

    # 후보 지표(전일 확정봉) — 유동성 하한: scalp.min_tv_eok(기본 50억)
    min_tv = float(cfg.get("scalp", "min_tv_eok", default=50))
    cands, dfs = [], {}
    for n in notes:
        try:
            df, _src = provider.get_ohlcv(n.ticker)
        except Exception:  # noqa: BLE001 — 종목 하나 실패가 전체를 못 막게
            continue
        if df is None or len(df) < 61:
            continue
        dfs[n.ticker] = df
        prev = df.iloc[-1]
        tv_eok = float(prev["close"]) * float(prev.get("volume", 0)) / 1e8
        if tv_eok < min_tv:
            continue
        ma20 = float(df["close"].tail(20).mean())
        ma60 = float(df["close"].tail(60).mean())
        cands.append({"ticker": n.ticker, "name": n.name or n.ticker,
                      "prev_close": float(prev["close"]),
                      "prev_range": float(prev["high"]) - float(prev["low"]),
                      "prev_tv_eok": round(tv_eok, 1), "uptrend": ma20 > ma60,
                      "why": f"거래대금 {tv_eok:,.0f}억"})

    # 1) 이전 계획 정산(확정 일봉이 정본)
    plans = _P.load_plans(cfg.state_dir)
    prev_plan = plans.get(market)
    settled_rows = {"v1": [], "v2": []}
    settled_date = "—"
    n_settled = 0
    if prev_plan and prev_plan["date"] < today:
        results, rows = _settle_scalp_plan(prev_plan, dfs, fee, slip)
        if results is None:
            warned = True
            _H.alert(cfg.creds.scalp_webhook, f"단타 정산[{market}]",
                     f"{prev_plan['date']} 확정 일봉 미도착 — 정산 보류(다음 런 재시도)")
        else:
            state.apply_day(prev_plan["date"], market, results)
            state.save(cfg.state_dir)
            settled_rows, settled_date = rows, prev_plan["date"]
            n_settled = sum(len(v) for v in rows.values())

    # 2) 오늘 계획(실시간가로 수량/트리거 표시 — 정산은 어차피 확정봉)
    from .market.fx import get_usdkrw
    fx = get_usdkrw(float(cfg.get("market_data", "fx_usdkrw", default=1400)))
    scenario = _P.build_scenario(cfg, reader)
    quotes: dict = {}
    for c in cands[:20]:
        q = get_quote(c["ticker"], fx)
        if q:
            quotes[c["ticker"]] = q.price
    if cands and not quotes:
        warned = True
        _H.alert(cfg.creds.scalp_webhook, f"단타 계획[{market}]",
                 "실시간 시세 전부 실패 — 전일 종가로 수량 산정(트리거 표시 생략)")
    cash_by = {m: state.models[m]["cash"] for m in ("v1", "v2")}
    plan_lists = _P.build_plan(cands, cash_by, scenario, quotes)
    items = plan_lists["v1"] + plan_lists["v2"] + plan_lists["v1_shadow"] + plan_lists["v2_shadow"]
    # KR 표시용 트리거(v1) = 실시간 시가 + k×전일레인지
    from dataclasses import replace
    disp = []
    for i in items:
        q = quotes.get(i.ticker)
        if i.model == "v1" and q and not i.shadow:
            qq = get_quote(i.ticker, fx)
            if qq and qq.open:
                i = replace(i, trigger=round(qq.open + (i.k or 0.5) * i.prev_range, 0))
        disp.append(i)
    plan = {"date": today, "scenario": scenario, "items": disp}
    _P.save_plan(cfg.state_dir, market, plan)

    # 3) 브리핑(디스코드+볼트) + 마커
    embed, md = scalp_brief(market, settled_rows, plan, state, settled_date)
    notify_embeds(cfg.creds.scalp_webhook, [embed], md)
    VaultWriter(cfg).append_scalp(md)
    _DM.record_done(cfg.state_dir, f"scalp_{market}", datetime.now(_DM.KST))
    n_planned = sum(1 for i in disp if not i.shadow)
    log.info("scalp-run[%s]: 정산 %d건 · 계획 %d건", market, n_settled, n_planned)
    return {"settled": n_settled, "planned": n_planned, "warned": warned}
```

그리고 `config.py`의 `Creds`에 스칼프 웹훅 폴백 프로퍼티를 추가하는 대신, **간단히** `config.py:49` 근처 `Creds`에 필드 추가:

```python
# config.py Creds dataclass 에 필드 추가:
    scalp_webhook: str | None = None
# _load 부분(117행 근처)에:
    scalp_webhook=os.getenv("SCALP_DISCORD_WEBHOOK_URL")
                  or os.getenv("SWING_DISCORD_WEBHOOK_URL") or os.getenv("DISCORD_WEBHOOK_URL") or None,
```

`writer.py` 끝에 추가:

```python
    def append_scalp(self, content: str, d: date | None = None) -> Path:
        d = d or today_kst()
        path = self._path("scalp_dir", "Scalp", d)
        new = not path.exists()
        with path.open("a", encoding="utf-8") as f:
            if new:
                f.write(f"---\ntype: 스윙단타\n날짜: {d.isoformat()}\ntags: [단타, 페이퍼]\n---\n"
                        f"# ⚡ 단타 페이퍼 · {d.isoformat()}\n\n")
            f.write(content + "\n")
        return path
```

`config.yaml` `paths: write:` 블록에 `scalp_dir: "04_Trading/Scalp"` 추가.

`main.py` `__all__` 에 `"run_scalp"` 추가.

`cli.py` — subparser 와 디스패치 추가(`versions` 파서 뒤):

```python
    sr = sub.add_parser("scalp-run", help="단타 페이퍼 1사이클(이전 계획 정산+오늘 계획) → 디스코드 ⚡")
    sr.add_argument("--market", choices=["kr", "us"], required=True)
```

```python
    if args.cmd == "scalp-run":
        r = M.run_scalp(cfg, args.market)
        print(f"✅ scalp-run[{args.market}]: 정산 {r['settled']}건 · 계획 {r['planned']}건"
              + (" ⚠️경고 발생" if r["warned"] else ""))
        return 0
```

`check-done` 확장 — `cd.add_argument` 의 choices 를 `["kr", "us", "scalp_kr", "scalp_us"]` 로 변경.

- [ ] **Step 4: 테스트+회귀** — `pytest tests/test_scalp_run.py tests/ -q` → 전부 통과. `swing-trader scalp-run --help` 출력 확인.
- [ ] **Step 5: 커밋** — `git commit -m "feat(scalp): run_scalp 오케스트레이션 + scalp-run CLI + 볼트 기록"`

---

### Task 7: 백테스트 리플레이 `scalp/backtest.py` → `scalp_compare.json`

**Files:**
- Create: `src/swing_trader/scalp/backtest.py`
- Modify: `src/swing_trader/main.py` — `run_scalp_compare` 추가, `run_brief` weekly 블록에서 호출
- Modify: `src/swing_trader/cli.py` — `scalp-compare` 서브커맨드
- Modify: `src/swing_trader/obsidian/writer.py` — `write_scalp_backtest` 추가(로직+결과를 볼트에 영구 기록)
- Test: `tests/test_scalp_backtest.py`

**Interfaces:**
- Consumes: `harness.Trade(ticker, entry, ret)`/`split_oos`/`report_from_trades`/`backtest_provider`(strategy/harness.py), `settle_item`/`PlanItem`(Task 2)
- Produces:
  - `simulate_stock(ticker, df, min_tv_eok) -> dict[str, list[Trade]]` — `{"v1": [...], "v2": [...]}` (ret은 **소수**, 수수료+슬리피지 차감)
  - `main.run_scalp_compare(cfg) -> Path` — `state/scalp_compare.json` (version_compare.json 과 동일 스키마 + `"seed": 3000000`, label `단타 v1|단타 v2`)
  - `VaultWriter.write_scalp_backtest(content) -> Path` — `04_Trading/Scalp/YYYY-MM-DD_단타백테스트.md` (로직 정의+OOS 성과 영구 기록)
  - 디스코드 브리핑: scalp_webhook 으로 `⚡ 단타 백테스트 리플레이 — v1/v2 기대값·PF·승률` 한 줄 요약(주간 자동 + scalp-compare 수동 실행 시)

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# tests/test_scalp_backtest.py
"""단타 백테스트 — 합성 일봉에서 v1/v2 거래 생성·look-ahead 없음."""
import numpy as np
import pandas as pd

from swing_trader.scalp.backtest import simulate_stock


def _df(n=80, seed=7):
    rng = np.random.default_rng(seed)
    close = 10000 * np.cumprod(1 + rng.normal(0.001, 0.02, n))
    op = close * (1 + rng.normal(0, 0.01, n))
    hi = np.maximum(op, close) * (1 + abs(rng.normal(0, 0.01, n)))
    lo = np.minimum(op, close) * (1 - abs(rng.normal(0, 0.01, n)))
    vol = np.full(n, 1_000_000.0)
    idx = pd.bdate_range("2026-01-05", periods=n)
    return pd.DataFrame({"open": op, "high": hi, "low": lo, "close": close,
                         "volume": vol}, index=idx)


def test_simulate_produces_trades_with_dates():
    trades = simulate_stock("005930", _df(), min_tv_eok=0)
    assert set(trades) == {"v1", "v2"}
    all_t = trades["v1"] + trades["v2"]
    assert len(trades["v1"]) > 0                       # 돌파는 변동장에서 반드시 발생
    for t in all_t:
        assert t.ticker == "005930"
        assert abs(t.ret) < 0.2                        # 당일 청산이라 ±20% 밖은 버그
        pd.Timestamp(t.entry)                          # ISO 날짜 파싱 가능


def test_liquidity_filter_blocks_all():
    trades = simulate_stock("005930", _df(), min_tv_eok=1e9)
    assert trades == {"v1": [], "v2": []}


def test_needs_61_bars():
    assert simulate_stock("005930", _df(n=50), min_tv_eok=0) == {"v1": [], "v2": []}
```

- [ ] **Step 2: 실패 확인** — FAIL

- [ ] **Step 3: 구현**

```python
# src/swing_trader/scalp/backtest.py
"""단타 백테스트 리플레이 — 각 거래일을 '전일 지표 → 당일 settle_item' 으로 재현.

기계룰만 검증한다(시나리오 필터는 과거 뉴스 재현 불가 → 라이브 그림자 A/B 담당).
ret 은 소수(harness.Trade 규약) — 대시보드 복리 곡선은 eq *= 1 + pfrac*ret.
"""
from __future__ import annotations

from ..strategy.harness import Trade
from .strategy import V1_K, V1_STOP, V2_GAP, V2_STOP, PlanItem, settle_item

_FEE, _SLIP = 1.5, 5.0    # config paper 기본과 동일(리플레이 고정 — 재현성)


def simulate_stock(ticker: str, df, min_tv_eok: float = 50.0) -> dict:
    out: dict = {"v1": [], "v2": []}
    if df is None or len(df) < 61:
        return out
    closes = df["close"]
    for i in range(60, len(df)):
        prev, bar = df.iloc[i - 1], df.iloc[i]
        tv_eok = float(prev["close"]) * float(prev.get("volume", 0)) / 1e8
        if tv_eok < min_tv_eok:
            continue
        d = df.index[i].strftime("%Y-%m-%d")
        prev_range = float(prev["high"]) - float(prev["low"])
        base = dict(ticker=ticker, name=ticker, qty=1, prev_close=float(prev["close"]),
                    prev_range=prev_range)
        # v1 돌파 — 매일 시도(체결은 settle 이 판정)
        f = settle_item(PlanItem(model="v1", stop_pct=V1_STOP, k=V1_K, **base),
                        bar, _FEE, _SLIP)
        if f:
            out["v1"].append(Trade(ticker, d, f.ret_pct / 100))
        # v2 갭반등 — 전일 기준 20>60일선일 때만 후보(전일까지 데이터만 사용)
        ma20 = float(closes.iloc[i - 20:i].mean())
        ma60 = float(closes.iloc[i - 60:i].mean())
        if ma20 > ma60:
            f = settle_item(PlanItem(model="v2", stop_pct=V2_STOP, **base),
                            bar, _FEE, _SLIP)
            if f:
                out["v2"].append(Trade(ticker, d, f.ret_pct / 100))
    return out
```

`main.py` 에 추가(`run_version_compare` 아래) — 기존 함수와 같은 출력 스키마:

```python
def run_scalp_compare(cfg: Config) -> Path:
    """단타 v1/v2 백테스트 리플레이 → state/scalp_compare.json (대시보드 카테고리 비교)."""
    from .scalp import backtest as _SB
    from .scalp.account import SEED_PER_MODEL
    from .strategy import harness as _HN
    reader = VaultReader(cfg)
    provider = _HN.backtest_provider(cfg)
    notes = [n for n in _load_notes(cfg, reader, None,
                                    str(cfg.get("backtest", "universe", default="all"))) if n.ticker]
    days = int(cfg.get("backtest", "lookback_days", default=500))
    frac = float(cfg.get("backtest", "oos_fraction", default=0.3))
    min_tv = float(cfg.get("scalp", "min_tv_eok", default=50))
    pfrac = 1.0 / 5   # 모델당 5분할 사이징과 동일 프레임
    seed = float(SEED_PER_MODEL)

    trades: dict = {"v1": [], "v2": []}
    for n in notes:
        try:
            df, _src = provider.get_ohlcv(n.ticker)
        except Exception:  # noqa: BLE001
            continue
        r = _SB.simulate_stock(n.ticker, df.tail(days), min_tv_eok=min_tv)
        trades["v1"] += r["v1"]
        trades["v2"] += r["v2"]

    meta = {"v1": ("단타 v1", "변동성 돌파(추세형)",
                   ["당일 시가+0.5×전일레인지 돌파 시 매수", "당일 종가 전량 청산(오버나잇 없음)",
                    "장중 손절 -2.0%(저가 터치 보수 판정)", "거래대금 하한 필터"]),
            "v2": ("단타 v2", "갭하락 과매도 반등(역추세형)",
                   ["시가 -2% 이상 갭하락 + 전일 20>60일선 종목 시가 매수",
                    "당일 종가 전량 청산(오버나잇 없음)", "장중 손절 -2.5%(보수 판정)"])}
    out = []
    for m in ("v1", "v2"):
        _is, oos = _HN.split_oos(trades[m], frac)
        rep = _HN.report_from_trades(oos, pfrac)
        eq, curve = seed, []
        for t in sorted(oos, key=lambda t: t.entry):
            eq *= (1 + pfrac * t.ret)
            curve.append({"date": t.entry, "equity": round(eq)})
        label, title, core = meta[m]
        out.append({"label": label, "title": title, "core_logic": core,
                    "oos": {"expectancy": rep.expectancy, "profit_factor": rep.profit_factor,
                            "max_drawdown": rep.max_drawdown, "sharpe": rep.sharpe,
                            "win_rate": rep.win_rate, "n_trades": rep.n_trades,
                            "cum_return_pct": round((eq / seed - 1) * 100, 2) if oos else None},
                    "equity": curve})
    dates = [pt["date"] for v in out for pt in v["equity"]]
    oos_start, oos_end = (min(dates), max(dates)) if dates else (None, None)
    oos_days = ((date.fromisoformat(oos_end) - date.fromisoformat(oos_start)).days
                if dates else None)
    path = cfg.state_dir / "scalp_compare.json"
    path.write_text(json.dumps({
        "as_of": _DM.today_kst().isoformat(), "seed": seed,
        "oos_start": oos_start, "oos_end": oos_end, "oos_days": oos_days,
        "lookback_days": days, "versions": out,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    # 볼트 영구 기록(로직 정의 + 백테스트 결과) + 디스코드 브리핑 — 스윙 하니스와 동일 패턴
    def _f(v, dp=2):
        return "—" if v is None else f"{v:+.{dp}f}%" if dp else f"{v}"
    md_lines = [f"# ⚡ 단타 백테스트 · {_DM.today_kst().isoformat()}",
                f"> 유니버스 {len(notes)}종목 · {days}일 · OOS {oos_start}~{oos_end} · 가상 {seed:,.0f}원", ""]
    brief_bits = []
    for v in out:
        o = v["oos"]
        md_lines += [f"## {v['label']} — {v['title']}", "**핵심 로직**"]
        md_lines += [f"- {c}" for c in v["core_logic"]]
        md_lines += ["", f"**OOS 성과**: 기대값 {_f(o['expectancy'])} · PF {o['profit_factor'] or '—'} · "
                     f"MDD {_f(o['max_drawdown'], 1)} · 승률 {o['win_rate'] or '—'}% · "
                     f"{o['n_trades']}거래 · 누적 {_f(o['cum_return_pct'], 1)}", ""]
        brief_bits.append(f"{v['label']} 기대값 {_f(o['expectancy'])}·PF {o['profit_factor'] or '—'}"
                          f"·승률 {o['win_rate'] or '—'}%({o['n_trades']}건)")
    vpath = VaultWriter(cfg).write_scalp_backtest("\n".join(md_lines))
    from .notify.discord import notify
    notify(cfg.creds.scalp_webhook,
           f"⚡ **단타 백테스트 리플레이** — " + " / ".join(brief_bits) +
           f"\n볼트: {vpath.name} · 대시보드 '버전 비교 > 초단기 단타' 갱신됨")
    log.info("scalp_compare → %s (v1 %d·v2 %d OOS거래)", path,
             out[0]["oos"]["n_trades"], out[1]["oos"]["n_trades"])
    return path
```

`writer.py` 끝에 추가(append_scalp 아래):

```python
    def write_scalp_backtest(self, content: str, d: date | None = None) -> Path:
        """단타 로직 정의+백테스트 결과 영구 기록 — 나중에 결과 브리핑/회고의 근거 문서."""
        d = d or today_kst()
        path = self._path("scalp_dir", "단타백테스트", d)
        path.write_text(content, encoding="utf-8")
        return path
```

`run_brief` weekly 블록의 `run_version_compare(cfg)` 호출 다음 줄에:

```python
            run_scalp_compare(cfg)     # 단타 카테고리 비교 데이터도 주간 갱신
```

`cli.py`:

```python
    sub.add_parser("scalp-compare", help="단타 v1/v2 백테스트 리플레이 → state/scalp_compare.json")
```
```python
    if args.cmd == "scalp-compare":
        path = M.run_scalp_compare(cfg)
        print(f"✅ 단타 비교 데이터 → {path}")
        return 0
```

`main.py` `__all__` 에 `"run_scalp_compare"` 추가.

- [ ] **Step 4: 테스트+회귀** — `pytest tests/ -q` 전부 통과
- [ ] **Step 5: 커밋** — `git commit -m "feat(scalp): 500일 백테스트 리플레이 → scalp_compare.json"`

---

### Task 8: 스케줄 배선 — bat + swing.yml failover

**Files:**
- Modify: `run_swing_kr.bat` / `run_swing_us.bat`
- Modify: `.github/workflows/swing.yml`

**Interfaces:**
- Consumes: Task 6의 `scalp-run`, `check-done --market scalp_kr|scalp_us`

- [ ] **Step 1: bat 수정** — 두 파일 모두 `run-once` 줄 **다음**에 (주석 영어!):

`run_swing_kr.bat`:
```bat
REM scalp paper cycle: settle yesterday's plan + build today's plan (Discord orange embed)
"%~dp0.venv\Scripts\swing-trader.exe" scalp-run --market kr >> "%~dp0swing.log" 2>&1
```
`run_swing_us.bat` 동일하게 `--market us`.

- [ ] **Step 2: swing.yml 수정** — "마커 확인" step 의 `for M in us kr` 루프 안, run-once 블록 뒤에 단타 보충 추가(같은 step 내 ALERT 패턴 재사용 없이 독립 게이트):

```yaml
          for M in us kr; do
            if swing-trader check-done --market "scalp_$M"; then
              echo "::notice::scalp_$M 로컬 완료 마커 존재 — skip"
            else
              echo "::group::scalp-run $M (클라우드 보충)"
              swing-trader scalp-run --market "$M"; echo "exit=$?"
              echo "::endgroup::"
            fi
          done
```
위 블록을 기존 `if [ -n "$ALERT" ]; then` 직전에 삽입한다(스윙 마커와 독립 — 스윙이 로컬 완료여도 단타가 미완료면 보충).

- [ ] **Step 3: bat 비ASCII 검사** — Run: `grep -nP "[^\x00-\x7F]" run_swing_kr.bat run_swing_us.bat` → **출력 0줄** 필수
- [ ] **Step 4: yml 문법 검사** — `python -c "import yaml; yaml.safe_load(open('.github/workflows/swing.yml', encoding='utf-8'))" && echo YAML-OK`
- [ ] **Step 5: 커밋** — `git commit -m "feat(scalp): 아침 런 배선(bat)+클라우드 failover(swing.yml)"`

---

### Task 9: 대시보드 — 비교 화면 카테고리 분리

**Files:**
- Create: `C:\Users\xect2\hermes-dashboard\lib\swing\compareCategories.ts`
- Modify: `C:\Users\xect2\hermes-dashboard\app\api\compare\route.ts`
- Modify: `C:\Users\xect2\hermes-dashboard\lib\useCompare.ts`
- Modify: `C:\Users\xect2\hermes-dashboard\components\compare\CompareView.tsx`
- Test: `C:\Users\xect2\hermes-dashboard\lib\swing\compareCategories.test.ts`

**Interfaces:**
- Consumes: `readSwingState(file)` (lib/swing/source.ts), `cdnCached` (lib/cdnCache.ts), 기존 `VersionEntry`
- Produces:
  - `CompareCategory = { key: "swing"|"scalp"; label: string; seed: number; oos_start/oos_end/oos_days/lookback_days; versions: VersionEntry[] }`
  - `buildCategories(swingRaw: any, scalpRaw: any) -> CompareCategory[]` (없는 쪽은 제외)
  - API 응답 = 기존 최상위 필드(스윙, 하위호환) + `categories`

- [ ] **Step 1: 실패하는 테스트 작성**

```typescript
// lib/swing/compareCategories.test.ts
import { describe, expect, it } from "vitest";
import { buildCategories } from "./compareCategories";

const raw = (label: string) => ({
  as_of: "2026-07-03", seed: 5000000, oos_start: "2026-01-01", oos_end: "2026-06-01",
  oos_days: 150, lookback_days: 500,
  versions: [{ label, title: "t", core_logic: [], oos: { expectancy: 0.1, profit_factor: 1.2,
    max_drawdown: -5, sharpe: 1, win_rate: 50, n_trades: 10, cum_return_pct: 3 },
    equity: [{ date: "2026-01-02", equity: 5050000 }] }],
});

describe("buildCategories", () => {
  it("두 카테고리를 스윙 먼저 순서로 만든다", () => {
    const cats = buildCategories(raw("v3"), { ...raw("단타 v1"), seed: 3000000 });
    expect(cats.map((c) => c.key)).toEqual(["swing", "scalp"]);
    expect(cats[0].label).toBe("중·단기 스윙");
    expect(cats[1].label).toBe("초단기 단타");
    expect(cats[1].seed).toBe(3000000);
  });
  it("scalp 파일이 없으면 스윙만", () => {
    const cats = buildCategories(raw("v3"), null);
    expect(cats).toHaveLength(1);
    expect(cats[0].key).toBe("swing");
  });
  it("versions 비정상이면 해당 카테고리 제외", () => {
    expect(buildCategories(null, { versions: "bad" })).toHaveLength(0);
  });
});
```

- [ ] **Step 2: 실패 확인** — Run: `cd C:\Users\xect2\hermes-dashboard && npx vitest run lib/swing/compareCategories.test.ts` → FAIL

- [ ] **Step 3: 구현**

```typescript
// lib/swing/compareCategories.ts
import type { VersionEntry } from "@/lib/useCompare";

export type CompareCategory = {
  key: "swing" | "scalp"; label: string; seed: number;
  oos_start: string | null; oos_end: string | null; oos_days: number | null;
  lookback_days: number | null; versions: VersionEntry[];
};

function toCategory(key: "swing" | "scalp", label: string, raw: any,
                    fallbackSeed: number): CompareCategory | null {
  if (!raw || !Array.isArray(raw.versions) || raw.versions.length === 0) return null;
  return {
    key, label, seed: Number(raw.seed) || fallbackSeed,
    oos_start: raw.oos_start ?? null, oos_end: raw.oos_end ?? null,
    oos_days: raw.oos_days ?? null, lookback_days: raw.lookback_days ?? null,
    versions: raw.versions,
  };
}

export function buildCategories(swingRaw: any, scalpRaw: any): CompareCategory[] {
  const out: CompareCategory[] = [];
  const sw = toCategory("swing", "중·단기 스윙", swingRaw, 5_000_000);
  if (sw) out.push(sw);
  const sc = toCategory("scalp", "초단기 단타", scalpRaw, 3_000_000);
  if (sc) out.push(sc);
  return out;
}
```

`app/api/compare/route.ts` 전체 교체:

```typescript
import { NextResponse } from "next/server";
import { cdnCached } from "@/lib/cdnCache";
import { buildCategories } from "@/lib/swing/compareCategories";
import { readSwingState } from "@/lib/swing/source";

export const dynamic = "force-dynamic";

// 버전별 백테스트 리플레이 — 스윙(version_compare)+단타(scalp_compare) 카테고리 응답.
// 최상위 필드는 스윙 값 유지(하위호환).
export async function GET() {
  const [raw, scalpRaw] = await Promise.all([
    readSwingState("version_compare.json"), readSwingState("scalp_compare.json"),
  ]);
  const categories = buildCategories(raw, scalpRaw);
  if (categories.length === 0) {
    return NextResponse.json({ ok: false, seed: 5000000, versions: [], categories: [] });
  }
  const sw = categories.find((c) => c.key === "swing") ?? categories[0];
  return cdnCached({
    ok: true, asOf: raw?.as_of ?? scalpRaw?.as_of ?? null, seed: sw.seed,
    oos_start: sw.oos_start, oos_end: sw.oos_end,
    oos_days: sw.oos_days, lookback_days: sw.lookback_days,
    versions: sw.versions, categories,
  }, 300);
}
```

`lib/useCompare.ts` — 타입 추가(기존 유지):

```typescript
import type { CompareCategory } from "@/lib/swing/compareCategories";
// CompareData 에 필드 추가:
export type CompareData = {
  ok: boolean; asOf: string | null; seed: number; versions: VersionEntry[];
  oos_start?: string | null; oos_end?: string | null; oos_days?: number | null; lookback_days?: number | null;
  categories?: CompareCategory[];
};
```
(주의: compareCategories.ts 가 useCompare 의 `VersionEntry` 를 import 하므로 **순환 import** — `VersionEntry`/`CompareData` 타입 정의를 `lib/swing/compareCategories.ts` 로 옮기고 `lib/useCompare.ts` 는 `export type { VersionEntry, CompareCategory, CompareData } from "@/lib/swing/compareCategories";` 재수출 + `useCompare()` 훅만 유지한다. CompareView/기존 import 는 그대로 동작.)

`components/compare/CompareView.tsx` — 카테고리 토글. `CompareView` 함수 시작부를 다음으로 교체:

```typescript
export default function CompareView({ data }: { data: CompareData }) {
  const cats = data.categories?.length
    ? data.categories
    : [{ key: "swing" as const, label: "중·단기 스윙", seed: data.seed,
         oos_start: data.oos_start ?? null, oos_end: data.oos_end ?? null,
         oos_days: data.oos_days ?? null, lookback_days: data.lookback_days ?? null,
         versions: data.versions }];
  const [catKey, setCatKey] = useState(cats[0].key);
  const cat = cats.find((c) => c.key === catKey) ?? cats[0];
  const vs = cat.versions;
  const [ai, setAi] = useState(Math.max(0, vs.length - 2));
  const [bi, setBi] = useState(Math.max(0, vs.length - 1));
  const [mode, setMode] = useState<CompareMode>("date");
  const [proj, setProj] = useState(false);
  const pickCat = (k: typeof catKey) => {   // 카테고리 전환 시 A/B 인덱스 리셋
    const nv = (cats.find((c) => c.key === k) ?? cats[0]).versions;
    setCatKey(k); setAi(Math.max(0, nv.length - 2)); setBi(Math.max(0, nv.length - 1));
  };
```

이후 본문에서 `data.seed`→`cat.seed`, `data.oos_start/oos_end/oos_days`→`cat.oos_*` 로 치환하고,
`projectFwd` 의 `data.oos_days`/`data.oos_end`/`data.seed` 도 `cat.*` 사용으로 변경.
겹쳐보기 Card 위(안내 배너 아래)에 토글 삽입:

```tsx
      {cats.length > 1 && (
        <Segmented items={cats.map((c) => ({ key: c.key, label: c.label }))}
          value={catKey} onChange={(k) => pickCat(k as typeof catKey)} />
      )}
```

겹쳐보기 헤더 문구 `겹쳐보기 · 가상 500만` → `` `겹쳐보기 · 가상 ${Math.round(cat.seed / 10000)}만` ``,
VersionCard 내 `500만 →` 도 seed 프롭 기반이므로 `{won(seed / 10000)}만 →` 형태로 교체:
`<div className="mt-1 text-right text-[11px] text-sub">{Math.round(seed / 10000)}만 → {won(last)}원</div>`.
`vs.length < 2` 빈 상태 분기는 `cat.versions.length < 2` 로 유지(단타 초기엔 v1/v2 두 개라 통과).

- [ ] **Step 4: 검증** — `npx vitest run && npx tsc --noEmit && npx next build` 전부 통과
- [ ] **Step 5: 커밋** — `git commit -m "feat(compare): 카테고리 분리(중·단기 스윙 | 초단기 단타)"`

---

### Task 10: 대시보드 — 10만원 목표 카드 2행 + `/api/scalp`

**Files:**
- Create: `C:\Users\xect2\hermes-dashboard\lib\swing\scalpSummary.ts`
- Create: `C:\Users\xect2\hermes-dashboard\app\api\scalp\route.ts`
- Create: `C:\Users\xect2\hermes-dashboard\lib\useScalp.ts`
- Modify: `C:\Users\xect2\hermes-dashboard\components\performance\PerformanceView.tsx:217-250` (DailyPnlCard 확장)
- Test: `C:\Users\xect2\hermes-dashboard\lib\swing\scalpSummary.test.ts`

**Interfaces:**
- Consumes: `readSwingState("scalp_state.json")`, `cdnCached`
- Produces:
  - `summarizeScalp(state: any) -> ScalpSummary | null` — `{ asOf, seedTotal: 6000000, cashTotal, realizedTotal, today: { date, pnl } | null, avgDaily: number | null, days: number, capitalNeeded: number | null }`
    - `avgDaily` = 최근 20개 daily 행의 `(v1_pnl+v2_pnl)` 평균(일 단위 합산 — 같은 날 kr/us 합침), 표본<10일이면 null
    - `capitalNeeded` = `avgDaily > 0 ? round(100000 / (avgDaily / seedTotal)) : null` (10만원 = 일수익률 × 자본)
  - `/api/scalp` GET → `{ ok: true, ...ScalpSummary }` (cdnCached 120s) / state 없으면 `{ ok: false }`
  - `useScalp(): ScalpSummary & { ok } | null`

- [ ] **Step 1: 실패하는 테스트 작성**

```typescript
// lib/swing/scalpSummary.test.ts
import { describe, expect, it } from "vitest";
import { summarizeScalp } from "./scalpSummary";

const daily = (date: string, pnl: number, market = "kr") =>
  ({ date, market, v1_pnl: pnl / 2, v2_pnl: pnl / 2, v1_shadow: 0, v2_shadow: 0 });

const state = (rows: any[]) => ({
  asOf: "2026-07-03", seed_per_model: 3000000,
  models: { v1: { cash: 3010000, realized: 10000, shadow_realized: 0 },
            v2: { cash: 2995000, realized: -5000, shadow_realized: 0 } },
  daily: rows, trades: [],
});

describe("summarizeScalp", () => {
  it("null/빈 state 는 null", () => {
    expect(summarizeScalp(null)).toBeNull();
    expect(summarizeScalp({})).toBeNull();
  });
  it("같은 날 kr/us 를 하루로 합산하고 오늘 손익을 낸다", () => {
    const s = summarizeScalp(state([daily("2026-07-02", 4000, "us"),
                                    daily("2026-07-02", 6000, "kr")]))!;
    expect(s.today).toEqual({ date: "2026-07-02", pnl: 10000 });
    expect(s.cashTotal).toBe(6005000);
    expect(s.days).toBe(1);
    expect(s.avgDaily).toBeNull();          // 표본 < 10일
    expect(s.capitalNeeded).toBeNull();
  });
  it("표본 10일 이상이면 avgDaily·필요자본 계산", () => {
    const rows = Array.from({ length: 12 }, (_, i) =>
      daily(`2026-06-${String(10 + i).padStart(2, "0")}`, 6000));
    const s = summarizeScalp(state(rows))!;
    expect(s.avgDaily).toBe(6000);
    // 일수익률 = 6000/6000000 = 0.1% → 10만원엔 1억
    expect(s.capitalNeeded).toBe(100_000_000);
  });
  it("평균이 음수면 필요자본 null", () => {
    const rows = Array.from({ length: 12 }, (_, i) =>
      daily(`2026-06-${String(10 + i).padStart(2, "0")}`, -1000));
    expect(summarizeScalp(state(rows))!.capitalNeeded).toBeNull();
  });
});
```

- [ ] **Step 2: 실패 확인** — `npx vitest run lib/swing/scalpSummary.test.ts` → FAIL

- [ ] **Step 3: 구현**

```typescript
// lib/swing/scalpSummary.ts
// 단타 페이퍼 요약 — 성과탭 '10만원 목표' 카드의 단기 행 데이터.
// 정직 원칙: 10만원 도달 경로 = 검증된 일수익률 × 자본. 표본<10일이면 평균을 내지 않는다.
export type ScalpSummary = {
  asOf: string | null; seedTotal: number; cashTotal: number; realizedTotal: number;
  today: { date: string; pnl: number } | null;
  avgDaily: number | null; days: number; capitalNeeded: number | null;
};

const DAILY_GOAL = 100_000;
const MIN_SAMPLE = 10;

export function summarizeScalp(state: any): ScalpSummary | null {
  if (!state || typeof state !== "object" || !state.models) return null;
  const seedTotal = (Number(state.seed_per_model) || 3_000_000) * 2;
  const models = state.models;
  const cashTotal = (Number(models?.v1?.cash) || 0) + (Number(models?.v2?.cash) || 0);
  const realizedTotal = (Number(models?.v1?.realized) || 0) + (Number(models?.v2?.realized) || 0);
  // 같은 날 kr/us 합산 → 일별 손익 시계열
  const byDate = new Map<string, number>();
  for (const r of Array.isArray(state.daily) ? state.daily : []) {
    const pnl = (Number(r.v1_pnl) || 0) + (Number(r.v2_pnl) || 0);
    byDate.set(r.date, (byDate.get(r.date) ?? 0) + pnl);
  }
  const dates = [...byDate.keys()].sort();
  const days = dates.length;
  const last = dates[dates.length - 1];
  const today = last ? { date: last, pnl: byDate.get(last)! } : null;
  const recent = dates.slice(-20).map((d) => byDate.get(d)!);
  const avgDaily = days >= MIN_SAMPLE
    ? recent.reduce((a, b) => a + b, 0) / recent.length : null;
  const capitalNeeded = avgDaily != null && avgDaily > 0
    ? Math.round(DAILY_GOAL / (avgDaily / seedTotal)) : null;
  return { asOf: state.asOf ?? null, seedTotal, cashTotal, realizedTotal,
           today, avgDaily, days, capitalNeeded };
}
```

```typescript
// app/api/scalp/route.ts
import { NextResponse } from "next/server";
import { cdnCached } from "@/lib/cdnCache";
import { summarizeScalp } from "@/lib/swing/scalpSummary";
import { readSwingState } from "@/lib/swing/source";

export const dynamic = "force-dynamic";

export async function GET() {
  const state = await readSwingState("scalp_state.json");
  const s = summarizeScalp(state);
  if (!s) return NextResponse.json({ ok: false });   // 미가동/읽기 실패는 캐시 안 함
  return cdnCached({ ok: true, ...s }, 120, 600);
}
```

```typescript
// lib/useScalp.ts
import useSWR from "swr";
import type { ScalpSummary } from "@/lib/swing/scalpSummary";

export function useScalp(): (ScalpSummary & { ok: boolean }) | null {
  const { data } = useSWR<{ ok: boolean } & ScalpSummary>("/api/scalp");
  return data ?? null;
}
```

`PerformanceView.tsx` — `DailyPnlCard`(228-250행)를 2행 카드로 교체하고 호출부에서 scalp 훅 사용:

```tsx
// 상단 import 에 추가:
import { useScalp } from "@/lib/useScalp";

// DailyPnlCard 를 아래로 교체:
function GoalRow({ tag, date, profit, sub }: { tag: string; date: string | null;
                                               profit: number | null; sub: string }) {
  const c = profit == null ? "#888" : pnlColor(profit);
  const progress = profit == null ? 0 : Math.max(0, Math.min(100, (profit / DAILY_GOAL) * 100));
  return (
    <div className="py-1.5">
      <div className="flex items-end justify-between gap-2">
        <div className="min-w-0">
          <div className="text-[11px] text-sub">{tag}{date ? ` · ${md(date)}` : ""} (모의)</div>
          <div className="tnum text-[20px] font-bold leading-tight" style={{ color: c }}>
            {profit == null ? "—" : `${profit >= 0 ? "+" : ""}${won(Math.round(profit))}원`}
          </div>
        </div>
        <div className="shrink-0 text-right">
          <div className="text-[11px] text-sub">일 목표 10만원</div>
          <div className="tnum text-[15px] font-bold text-ink">{profit == null ? "—" : `${Math.round(progress)}%`}</div>
        </div>
      </div>
      <div className="mt-1.5 h-[6px] w-full overflow-hidden rounded-full bg-[#F0F0F0]">
        <div className="h-full rounded-full" style={{ width: `${progress}%`, background: c }} />
      </div>
      <div className="mt-1 text-[10.5px] text-sub">{sub}</div>
    </div>
  );
}

function DailyGoalCard({ swing }: { swing: { date: string; profit: number; pct: number } | null }) {
  const scalp = useScalp();
  const scalpSub = !scalp?.ok
    ? "⚡ 단타 페이퍼 가동 대기 중"
    : scalp.avgDaily == null
      ? `⚡ 가상 300만×2 · 검증 표본 ${scalp.days}/10일 수집 중`
      : scalp.capitalNeeded != null
        ? `⚡ 일평균 ${scalp.avgDaily >= 0 ? "+" : ""}${won(Math.round(scalp.avgDaily))}원 — 이 페이스로 10만원엔 자본 ${won(scalp.capitalNeeded)}원 필요`
        : "⚡ 일평균 음수 — 로직 개선 필요(자본 스케일업 불가)";
  return (
    <Card>
      <div className="divide-y divide-line">
        <GoalRow tag="중·단기 스윙 · 오늘 1일 손익" date={swing?.date ?? null}
                 profit={swing?.profit ?? null}
                 sub="🎯 자동매매로 하루 10만원이 목표 — 모의 검증 중" />
        <GoalRow tag="초단기 단타 · 최근 일손익" date={scalp?.ok ? scalp.today?.date ?? null : null}
                 profit={scalp?.ok ? scalp.today?.pnl ?? null : null} sub={scalpSub} />
      </div>
    </Card>
  );
}
```

호출부: `PerformanceView` 본문에서 기존 `daily && <DailyPnlCard d={daily} />` 형태(정확한 기존 JSX를 찾아 유지)를 `<DailyGoalCard swing={daily} />` 로 교체하고, `!backtest.ok` 분기에도 `<DailyGoalCard swing={null} />` 를 최상단에 추가(단타만 가동 중일 때도 보이게). `dailyPnl` 함수와 `DAILY_GOAL` 은 그대로 재사용.

- [ ] **Step 4: 검증** — `npx vitest run && npx tsc --noEmit && npx next build` 통과
- [ ] **Step 5: 커밋** — `git commit -m "feat(perf): 10만원 목표 카드 단기/중·단기 2행 + /api/scalp"`

---

### Task 11: E2E 검증 + 배포

**Files:** 없음(실행/검증만)

- [ ] **Step 1: swing 전체 회귀+푸시** — `pytest tests/ -q` 전부 통과 → `git push origin HEAD:main`
- [ ] **Step 2: 백테스트 리플레이 실사** — `swing-trader scalp-compare` 실행 → ①`state/scalp_compare.json` 생성, v1/v2 각 `oos.n_trades > 0`, `max_drawdown < 0`, equity 곡선 길이 > 10 ②볼트 `04_Trading/Scalp/*_단타백테스트.md` 생성(로직+성과) ③디스코드 ⚡백테스트 브리핑 도착. 결과 수치(기대값·PF·승률)를 사용자에게 보고.
- [ ] **Step 3: 페이퍼 1사이클 실사** — `swing-trader scalp-run --market kr` 실행 → ①디스코드 ⚡오렌지 임베드 도착 ②`state/scalp_plan.json` kr 계획 생성(트리거가 숫자 표시) ③볼트 `04_Trading/Scalp/` md 생성 ④`daily_done.json` 에 `scalp_kr` 마커. 첫 실행은 정산 0건(계획만)이 정상.
- [ ] **Step 4: state 푸시** — bat 패턴대로 `git add -f state && git commit -m "chore(state): scalp 초기 상태" && git push origin HEAD:main`
- [ ] **Step 5: 대시보드 배포(사용자 확인 후)** — `node -r ./patch-hostname.cjs C:/Users/xect2/AppData/Roaming/npm/node_modules/vercel/dist/index.js --prod --yes`
- [ ] **Step 6: 프로덕션 실사** — ①`/compare` 접속: 세그먼트 [중·단기 스윙|초단기 단타] 표시, 단타 선택 시 단타 v1/v2 A/B + "가상 300만" 라벨 ②성과탭 최상단 목표 카드 2행 표시(단타는 "표본 수집 중") ③`/api/scalp`·`/api/compare` Cookie 요청 MISS→HIT
- [ ] **Step 7: 다음날 아침 확인 항목을 사용자에게 안내** — 09:05 런 후 ⚡임베드에 "어제 결과" 정산 표시, 성과탭 단타 행에 일손익 반영

---

## Self-Review 결과

- **스펙 커버리지**: §1 알고리즘(Task 2), §2 계좌/CLI/failover(Task 3·6·8), §3 플랜빌더+그림자(Task 4·6), §3b 실시간 가격(Task 1·6), §4 디스코드(Task 5, SCALP_DISCORD_WEBHOOK_URL env는 Task 6 Creds), §5 리플레이(Task 7), §6a 비교 UI(Task 9), §6b 목표 카드(Task 10), §7 검증(각 Task 테스트 + Task 11) — 누락 없음.
- **플레이스홀더**: 없음(전 단계 실코드).
- **타입 일관성**: `PlanItem`/`Fill`/`ScalpState.apply_day`/`Trade(ticker, entry, ret[소수])`/`ScalpSummary`/`CompareCategory` — Task 간 시그니처 교차 확인 완료. `_settle_scalp_plan` 반환 `(results, rows)` 를 Task 6 run_scalp 과 Task 6 테스트가 동일 사용.
- 주의사항: Task 9의 `VersionEntry` 타입 이동(순환 import 방지)은 기존 import 경로를 재수출로 보존할 것.
