# 스윙 v10 (신고가 거감짜름) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 영상(보컬 김영준) "신고가 상승음봉/거감짜름" 기법을 v9 스윙 베이스로 코드화한 v10 후보 모델을 만들고, OOS A/B(v10 vs v9)로 채택 여부를 판정할 수 있게 한다.

**Architecture:** 접근법 A — 코스피+코스닥 전시장 패널(기존 `krx_universe.fetch_panel`)에서 값싼 가격·신고가·거감짜름 셋업으로 후보를 좁힌 뒤, **후보 종목에만** 네이버 기관 순매매를 조회해 하드 게이트를 건다. 청산·손익·OOS 하니스는 v9(v7 청산) 것을 재사용한다. v10 로직은 신규 2파일(`market/supply.py`, `strategy/v10_new_high.py`)에 격리한다.

**Tech Stack:** Python 3.12, pandas/numpy, FinanceDataReader(패널·지수), 네이버 금융 HTML(`pandas.read_html`, euc-kr), pytest. 기존 `swing_trader` 패키지.

## Global Constraints

- 대상 시장: **코스피 + 코스닥만** (US 제외 — 수급 데이터 부재). 미국주 티커는 v10 대상 아님.
- 데이터 정직성: **synthetic/합성 데이터를 실거래 성과로 쓰지 않는다.** 패널 없으면 명확히 에러.
- 룩어헤드 금지: 진입 판정·체결가는 **진입일 D의 종가까지 확정된 값만** 사용.
- 기관 수급 소스: **네이버 `finance.naver.com/item/frgn.naver`**(무로그인). pykrx는 KRX 로그인 필요→미사용. euc-kr 디코드, `기관 순매매량`(주식수, +=순매수) 컬럼.
- 수급 게이트: 백테스트=**하드**(데이터 없으면 거래 드롭), 라이브=**페일오픈**(경고 후 허용).
- 진입 체결가 = 거감짜름일 D의 **종가**. 청산은 **D+1**부터.
- 청산·손절·비용은 기존 `risk.*` / `paper.*` 재사용(신규 손절값 도입 금지). 비용 = `2*(fee_bps+slip_bps)/10000`.
- 거래 산출물 타입 = `swing_trader.strategy.harness.Trade(ticker, entry, ret)` (OOS/metrics 재사용 위해).
- 커밋 자주. 새 코드가 만든 미사용분만 정리. 기존 스타일(한글 주석·docstring) 따름.
- 브랜치: 현재 `feature/swing-evolve`. 실행 시 워크트리/브랜치 격리는 실행 스킬이 처리.

---

## File Structure

- **Create** `src/swing_trader/market/supply.py` — 기관 순매매 조회(네이버 스크레이프)+디스크 캐시+파싱. 순수 파서 `parse_frgn_html`와 `SupplyProvider` 클래스.
- **Create** `src/swing_trader/strategy/v10_new_high.py` — v10 전 로직: 신고가/돌파 검출, 거감짜름 검출, 후보 스캔, 수급/시황 게이트, per-ticker 거래 생성, 전시장 오케스트레이션(`v10_market_trades`).
- **Create** `tests/test_v10_supply.py` — 파서·수급 게이트·캐시 단위테스트.
- **Create** `tests/test_v10_detection.py` — 신고가·돌파·거감짜름·후보스캔·룩어헤드 단위테스트.
- **Create** `tests/test_v10_market.py` — 시황 게이트·per-ticker·전시장 오케스트레이션 단위테스트.
- **Create** `tests/fixtures/frgn_005930.html` — 네이버 frgn 페이지 저장본(네트워크 없는 파서 테스트용).
- **Modify** `config.yaml` — `v10:` 블록 추가.
- **Modify** `src/swing_trader/strategy/logic_version.py` — `snapshot()`에 v10 키 포함.
- **Modify** `src/swing_trader/cli.py` — `swing-v10-backtest` 서브커맨드 등록.
- **Modify** `src/swing_trader/main.py` — `run_v10_backtest()` (패널→v10 거래→OOS→v9 A/B→state/v10_compare.json + 볼트 문서).

라이브 배선(`v10_live.py`, 디스코드/앱/옵시디언 3면)은 **OOS 채택 후 별도 플랜** — 본 플랜 범위 밖.

---

## Task 1: config `v10` 블록 + 로직 스냅샷

**Files:**
- Modify: `config.yaml` (backtest 블록 뒤에 v10 추가)
- Modify: `src/swing_trader/strategy/logic_version.py:16-41` (`snapshot()`)
- Test: `tests/test_v10_detection.py`

**Interfaces:**
- Produces: config 키 `v10.{high_n,vol_x,body_min,window,vol_dry,body_max,supply_days,supply_required,regime_gate,regime_ma,min_tv_eok,supply_max_pages}` — 이후 모든 태스크가 `cfg.get("v10","<k>")`로 읽음.

- [ ] **Step 1: Write the failing test**

`tests/test_v10_detection.py` (신규 파일 시작):
```python
"""v10 신고가 거감짜름 — 검출 로직 단위테스트."""
import numpy as np
import pandas as pd

from swing_trader.config import load_config


def test_v10_config_defaults():
    cfg = load_config()
    assert cfg.get("v10", "high_n") == 252
    assert cfg.get("v10", "vol_x") == 2.0
    assert cfg.get("v10", "window") == 3
    assert cfg.get("v10", "vol_dry") == 0.7
    assert cfg.get("v10", "supply_days") == 3
    assert cfg.get("v10", "supply_required") is True
    assert cfg.get("v10", "regime_gate") is True
    assert cfg.get("v10", "regime_ma") == 50
    assert cfg.get("v10", "min_tv_eok") == 50
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_v10_detection.py::test_v10_config_defaults -v`
Expected: FAIL (KeyError/None — `v10` 블록 없음). *(load_config 시그니처가 인자를 요구하면 기존 테스트의 호출법을 따를 것 — `tests/test_scoring.py` 등 참고.)*

- [ ] **Step 3: Add the config block**

`config.yaml` 의 `backtest:` 블록 바로 뒤에 추가:
```yaml
v10:
  # 신고가 거감짜름(보컬 김영준 기법). 진입=돌파 후 첫 거래량-마름 짧은음봉 종가매수, 청산=risk.* 재사용(5일선 이탈).
  high_n: 252            # 52주 신고가 판정 구간(거래일)
  vol_x: 2.0             # 돌파 거래량 배수(직전 20일 평균 대비)
  body_min: 0.03         # 장대양봉 최소 몸통
  window: 3              # 돌파 후 거감짜름 탐색 봉수
  vol_dry: 0.7           # 거감짜름 거래량 상한(돌파일 대비)
  body_max: 0.03         # 거감짜름 최대 몸통(짧은 음봉)
  supply_days: 3         # 기관 연속 순매수 판정일수
  supply_required: true  # 백테스트 하드게이트(라이브는 코드에서 페일오픈)
  regime_gate: true      # 시황 게이트(코스닥/코스피 50일선)
  regime_ma: 50
  min_tv_eok: 50         # 거래대금 최소(억)
  supply_max_pages: 60   # 후보 종목당 네이버 frgn 최대 조회 page(≈600거래일)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_v10_detection.py::test_v10_config_defaults -v`
Expected: PASS

- [ ] **Step 5: Extend logic snapshot**

`src/swing_trader/strategy/logic_version.py` `snapshot()` 안, `rg = cfg.get("regime", ...)` 블록 뒤에 추가:
```python
    v10 = cfg.get("v10", default={})
    for k in ("high_n", "vol_x", "body_min", "window", "vol_dry", "body_max",
              "supply_days", "supply_required", "regime_gate", "regime_ma", "min_tv_eok"):
        flat[f"v10.{k}"] = v10.get(k)
```

- [ ] **Step 6: Commit**

```bash
git add config.yaml src/swing_trader/strategy/logic_version.py tests/test_v10_detection.py
git commit -m "feat(v10): config v10 블록 + 로직 스냅샷 확장"
```

---

## Task 2: 신고가 돌파 캔들 검출 (순수)

**Files:**
- Create: `src/swing_trader/strategy/v10_new_high.py`
- Test: `tests/test_v10_detection.py`

**Interfaces:**
- Produces:
  - `_arr(df, col) -> np.ndarray` (1D float 강제)
  - `breakout_mask(df, *, high_n, vol_x, body_min, min_tv_eok) -> np.ndarray[bool]` — 각 봉이 52주 신고가 돌파+장대양봉+거래량급증+유동성 셋업인지.
  - `all_time_high_mask(df) -> np.ndarray[bool]`, `hist_vol_mask(df, ratio=0.9) -> np.ndarray[bool]` — 가점 플래그.

- [ ] **Step 1: Write the failing test**

`tests/test_v10_detection.py` 에 추가:
```python
from swing_trader.strategy import v10_new_high as v10


def _df(closes, opens=None, vols=None):
    n = len(closes)
    idx = pd.date_range("2024-01-02", periods=n, freq="B")
    c = np.array(closes, dtype=float)
    o = np.array(opens, dtype=float) if opens is not None else np.r_[c[0], c[:-1]]
    v = np.array(vols, dtype=float) if vols is not None else np.full(n, 1e6)
    hi = np.maximum(o, c) * 1.001
    lo = np.minimum(o, c) * 0.999
    return pd.DataFrame({"open": o, "high": hi, "low": lo, "close": c, "volume": v}, index=idx)


def test_breakout_mask_flags_new_high_bullish_volume():
    # 260봉 완만한 박스(100 근처) 뒤, 마지막 봉에서 신고가 장대양봉 + 대량거래.
    base = list(100 + np.sin(np.linspace(0, 12, 260)) * 2)      # 98~102 박스
    closes = base + [110.0]                                     # 신고가 돌파
    opens = [c for c in closes]
    opens[-1] = 105.0                                           # 장대양봉(+4.8% 몸통)
    vols = [1e6] * 260 + [3e6]                                  # 돌파일 3배
    df = _df(closes, opens, vols)
    m = v10.breakout_mask(df, high_n=252, vol_x=2.0, body_min=0.03, min_tv_eok=0)
    assert m[-1]                     # 마지막 봉 = 돌파
    assert not m[:-1].any()          # 박스 구간은 돌파 아님


def test_breakout_mask_rejects_low_volume():
    base = list(100 + np.sin(np.linspace(0, 12, 260)) * 2)
    closes = base + [110.0]
    opens = list(closes); opens[-1] = 105.0
    vols = [1e6] * 261                                          # 돌파일 거래량 증가 없음
    df = _df(closes, opens, vols)
    m = v10.breakout_mask(df, high_n=252, vol_x=2.0, body_min=0.03, min_tv_eok=0)
    assert not m[-1]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_v10_detection.py -k breakout -v`
Expected: FAIL (ModuleNotFoundError: v10_new_high)

- [ ] **Step 3: Write the implementation**

`src/swing_trader/strategy/v10_new_high.py` (신규):
```python
"""v10 — 신고가 거감짜름 기법(보컬 김영준). 순수 검출 + 전시장 오케스트레이션.

진입: 52주 신고가 장대양봉 대량거래 돌파(B) → 다음 window봉 내 첫 '거감짜름'(거래량 마름 짧은음봉 D)
      종가 매수. 기관 연속 순매수 하드게이트 + 코스닥/코스피 50일선 시황게이트.
청산: v7 재사용(5일선 이탈/대량음봉/손절/max_hold).
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _arr(df: pd.DataFrame, col: str) -> np.ndarray:
    s = df[col]
    if getattr(s, "ndim", 1) > 1:
        s = s.iloc[:, 0]
    return s.to_numpy(dtype=float)


def breakout_mask(df: pd.DataFrame, *, high_n: int, vol_x: float,
                  body_min: float, min_tv_eok: float) -> np.ndarray:
    """각 봉이 신고가 돌파 셋업(B)인지 bool 배열. 전일까지의 최고가를 당일 종가가 돌파."""
    c, o, v = _arr(df, "close"), _arr(df, "open"), _arr(df, "volume")
    prev_high = pd.Series(c).shift(1).rolling(high_n, min_periods=max(20, high_n // 2)).max().to_numpy()
    va20_prev = pd.Series(v).shift(1).rolling(20, min_periods=5).mean().to_numpy()
    with np.errstate(invalid="ignore", divide="ignore"):
        body = np.where(o > 0, (c - o) / o, 0.0)
        tv_eok = c * v / 1e8
        m = ((c >= prev_high) & (c > o) & (body >= body_min)
             & (v >= vol_x * va20_prev) & (tv_eok >= min_tv_eok))
    return np.nan_to_num(m, nan=0.0).astype(bool)


def all_time_high_mask(df: pd.DataFrame) -> np.ndarray:
    """종가가 확보 이력 전체의 신고가(역사적 신고가 근사) — 가점 플래그."""
    c = _arr(df, "close")
    prev_max = pd.Series(c).shift(1).cummax().to_numpy()
    return np.nan_to_num(c >= prev_max, nan=0.0).astype(bool)


def hist_vol_mask(df: pd.DataFrame, ratio: float = 0.9) -> np.ndarray:
    """거래량이 확보 이력 전체 최고의 ratio 이상(역사적 거래량 근사) — 가점 플래그."""
    v = _arr(df, "volume")
    prev_vmax = pd.Series(v).shift(1).cummax().to_numpy()
    with np.errstate(invalid="ignore"):
        m = v >= prev_vmax * ratio
    return np.nan_to_num(m, nan=0.0).astype(bool)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_v10_detection.py -k breakout -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/swing_trader/strategy/v10_new_high.py tests/test_v10_detection.py
git commit -m "feat(v10): 신고가 돌파 캔들 검출 + 역사적 신고가/거래량 가점 플래그"
```

---

## Task 3: 거감짜름 검출 (순수)

**Files:**
- Modify: `src/swing_trader/strategy/v10_new_high.py`
- Test: `tests/test_v10_detection.py`

**Interfaces:**
- Consumes: `_arr`
- Produces: `find_geogamjjareum(df, breakout_idx, *, window, vol_dry, body_max) -> int | None` — 돌파봉 B 다음 window봉 내 첫 거감짜름 봉 인덱스(없으면 None).

- [ ] **Step 1: Write the failing test**

`tests/test_v10_detection.py` 에 추가:
```python
def test_find_geogamjjareum_first_dry_down_candle():
    # 돌파봉(B) 다음: [양봉, 거래량마름 짧은음봉(D), ...]. D를 잡아야.
    closes = [100, 110, 111, 109.5, 112]     # idx1 돌파 가정
    opens  = [100, 105, 110, 110.0, 109.5]   # idx3: open110>close109.5 → 음봉(-0.45%)
    vols   = [1e6, 3e6, 2e6, 5e5, 2e6]       # idx3: 거래량 5e5 << 돌파봉 3e6
    df = _df(closes, opens, vols)
    j = v10.find_geogamjjareum(df, 1, window=3, vol_dry=0.7, body_max=0.03)
    assert j == 3


def test_find_geogamjjareum_none_when_no_dry_candle():
    closes = [100, 110, 112, 114, 116]        # 계속 양봉
    opens  = [100, 105, 110, 112, 114]
    vols   = [1e6, 3e6, 3e6, 3e6, 3e6]        # 거래량 안 마름
    df = _df(closes, opens, vols)
    assert v10.find_geogamjjareum(df, 1, window=3, vol_dry=0.7, body_max=0.03) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_v10_detection.py -k geogamjjareum -v`
Expected: FAIL (AttributeError: find_geogamjjareum)

- [ ] **Step 3: Write the implementation**

`v10_new_high.py` 에 추가:
```python
def find_geogamjjareum(df: pd.DataFrame, breakout_idx: int, *,
                       window: int, vol_dry: float, body_max: float) -> int | None:
    """돌파봉 B 다음 window봉 내 '거감짜름'(음봉+거래량마름+짧은몸통+5일선유지) 첫 봉 인덱스."""
    c, o, v = _arr(df, "close"), _arr(df, "open"), _arr(df, "volume")
    ma5 = pd.Series(c).rolling(5, min_periods=1).mean().to_numpy()
    va20 = pd.Series(v).rolling(20, min_periods=5).mean().to_numpy()
    vol_b = v[breakout_idx]
    n = len(c)
    for j in range(breakout_idx + 1, min(breakout_idx + window, n - 1) + 1):
        if o[j] <= 0:
            continue
        down = c[j] < o[j]
        dry = v[j] < va20[j] and v[j] < vol_b * vol_dry
        short = abs(c[j] - o[j]) / o[j] <= body_max
        trend = c[j] >= ma5[j]
        if down and dry and short and trend:
            return j
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_v10_detection.py -k geogamjjareum -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/swing_trader/strategy/v10_new_high.py tests/test_v10_detection.py
git commit -m "feat(v10): 거감짜름(거래량 마름 짧은음봉) 검출"
```

---

## Task 4: 후보 스캔 + 룩어헤드 없음 (순수)

**Files:**
- Modify: `src/swing_trader/strategy/v10_new_high.py`
- Test: `tests/test_v10_detection.py`

**Interfaces:**
- Consumes: `breakout_mask`, `find_geogamjjareum`, `all_time_high_mask`, `hist_vol_mask`
- Produces:
  - `@dataclass Candidate(ticker:str, breakout:str, entry_date:str, entry_idx:int, entry_price:float, all_time:bool, hist_vol:bool)`
  - `scan_candidates(df, ticker, *, high_n, vol_x, body_min, min_tv_eok, window, vol_dry, body_max) -> list[Candidate]` — 각 돌파봉마다 거감짜름 진입봉을 찾아 후보 생성(다음 돌파 스캔은 진입봉 이후로 진행).

- [ ] **Step 1: Write the failing test**

```python
def test_scan_candidates_builds_entry_at_dry_candle_close():
    base = list(100 + np.sin(np.linspace(0, 12, 260)) * 2)
    tail_c = [110.0, 111.0, 109.5]            # 260돌파, 261양봉, 262거감짜름
    tail_o = [105.0, 110.0, 110.0]
    tail_v = [3e6, 2e6, 5e5]
    closes = base + tail_c
    opens = list(closes[:260]) + tail_o
    vols = [1e6] * 260 + tail_v
    df = _df(closes, opens, vols)
    cands = v10.scan_candidates(df, "005930", high_n=252, vol_x=2.0, body_min=0.03,
                                min_tv_eok=0, window=3, vol_dry=0.7, body_max=0.03)
    assert len(cands) == 1
    c0 = cands[0]
    assert c0.ticker == "005930"
    assert c0.entry_idx == 262
    assert c0.entry_price == 109.5           # 거감짜름일 종가
    assert c0.entry_date == df.index[262].strftime("%Y-%m-%d")


def test_scan_candidates_no_lookahead():
    # 진입봉 인덱스까지의 데이터만으로 후보가 결정되어야 — 뒤 데이터를 바꿔도 후보 동일.
    base = list(100 + np.sin(np.linspace(0, 12, 260)) * 2)
    closes = base + [110.0, 111.0, 109.5, 999.0]
    opens = list(closes[:260]) + [105.0, 110.0, 110.0, 500.0]
    vols = [1e6] * 260 + [3e6, 2e6, 5e5, 9e6]
    df = _df(closes, opens, vols)
    a = v10.scan_candidates(df, "T", high_n=252, vol_x=2.0, body_min=0.03,
                            min_tv_eok=0, window=3, vol_dry=0.7, body_max=0.03)
    df2 = df.copy(); df2.iloc[263] = df2.iloc[263] * 0.001    # 미래봉 변형
    b = v10.scan_candidates(df2, "T", high_n=252, vol_x=2.0, body_min=0.03,
                            min_tv_eok=0, window=3, vol_dry=0.7, body_max=0.03)
    assert [(x.entry_idx, x.entry_price) for x in a] == [(x.entry_idx, x.entry_price) for x in b]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_v10_detection.py -k scan -v`
Expected: FAIL (AttributeError: scan_candidates / Candidate)

- [ ] **Step 3: Write the implementation**

`v10_new_high.py` 상단 import에 `from dataclasses import dataclass` 추가 후:
```python
@dataclass
class Candidate:
    ticker: str
    breakout: str        # 돌파일 'YYYY-MM-DD'
    entry_date: str      # 거감짜름 진입일 'YYYY-MM-DD'
    entry_idx: int
    entry_price: float   # 진입일 종가
    all_time: bool       # 역사적 신고가 가점
    hist_vol: bool       # 역사적 거래량 가점


def scan_candidates(df: pd.DataFrame, ticker: str, *, high_n: int, vol_x: float,
                    body_min: float, min_tv_eok: float, window: int,
                    vol_dry: float, body_max: float) -> list[Candidate]:
    """돌파봉마다 거감짜름 진입봉을 찾아 Candidate 생성. 룩어헤드 없음(≤진입봉 데이터만)."""
    bmask = breakout_mask(df, high_n=high_n, vol_x=vol_x, body_min=body_min, min_tv_eok=min_tv_eok)
    ath = all_time_high_mask(df)
    hvol = hist_vol_mask(df)
    c = _arr(df, "close")
    dates = [d.strftime("%Y-%m-%d") for d in df.index]
    out: list[Candidate] = []
    i = 0
    n = len(c)
    while i < n:
        if bmask[i]:
            j = find_geogamjjareum(df, i, window=window, vol_dry=vol_dry, body_max=body_max)
            if j is not None:
                out.append(Candidate(ticker, dates[i], dates[j], j, float(c[j]),
                                     bool(ath[i]), bool(hvol[i])))
                i = j + 1                    # 진입 후 다음 돌파부터 재스캔
                continue
        i += 1
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_v10_detection.py -k scan -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/swing_trader/strategy/v10_new_high.py tests/test_v10_detection.py
git commit -m "feat(v10): 후보 스캔(돌파→거감짜름 진입) + 룩어헤드 회귀"
```

---

## Task 5: 네이버 기관 순매매 파서 (순수)

**Files:**
- Create: `src/swing_trader/market/supply.py`
- Create: `tests/fixtures/frgn_005930.html`
- Test: `tests/test_v10_supply.py`

**Interfaces:**
- Produces: `parse_frgn_html(html: str) -> pd.Series` — index='YYYY-MM-DD'(오름차순), value=기관 순매매량(float, +=순매수). 빈 표면 빈 Series.

- [ ] **Step 1: Create the fixture**

Run (실제 페이지 1장 저장 — 네트워크 1회, 테스트는 이후 오프라인):
```bash
mkdir -p tests/fixtures
./.venv/Scripts/python.exe -c "import urllib.request; \
req=urllib.request.Request('https://finance.naver.com/item/frgn.naver?code=005930&page=1', headers={'User-Agent':'Mozilla/5.0'}); \
open('tests/fixtures/frgn_005930.html','wb').write(urllib.request.urlopen(req, timeout=10).read())"
```
Expected: 파일 생성(수 KB~수십 KB).

- [ ] **Step 2: Write the failing test**

`tests/test_v10_supply.py` (신규):
```python
"""v10 기관 수급 — 네이버 frgn 파서 + 수급 게이트."""
from pathlib import Path

import pandas as pd

from swing_trader.market.supply import parse_frgn_html

FIX = Path(__file__).parent / "fixtures" / "frgn_005930.html"


def test_parse_frgn_html_returns_institution_series():
    html = FIX.read_bytes().decode("euc-kr", "replace")
    s = parse_frgn_html(html)
    assert isinstance(s, pd.Series)
    assert len(s) >= 5                         # 페이지당 ~10거래일
    assert list(s.index) == sorted(s.index)    # 오름차순
    assert s.index[0].count("-") == 2          # 'YYYY-MM-DD'
    assert s.dtype == float


def test_parse_frgn_html_empty_on_garbage():
    assert parse_frgn_html("<html><body>no table</body></html>").empty
```

- [ ] **Step 3: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_v10_supply.py -k parse -v`
Expected: FAIL (ModuleNotFoundError: supply)

- [ ] **Step 4: Write the implementation**

`src/swing_trader/market/supply.py` (신규):
```python
"""기관 수급 — 네이버 금융 frgn(외국인·기관 순매매) 무로그인 스크레이프 + 디스크 캐시.

pykrx 투자자별 순매수는 2025~ KRX 로그인 필요(빈 결과) → 네이버 frgn 폴백.
표: 날짜/종가/전일비/등락률/거래량/기관 순매매량/외국인 순매매량/... (기관=위치 5, +=순매수, 주식수).
"""
from __future__ import annotations

import io
import logging
import re

import pandas as pd

log = logging.getLogger(__name__)
_DATE = re.compile(r"^\d{4}\.\d{2}\.\d{2}$")


def parse_frgn_html(html: str) -> pd.Series:
    """네이버 frgn HTML → 기관 순매매량 Series(index 'YYYY-MM-DD' 오름차순)."""
    try:
        tables = pd.read_html(io.StringIO(html))
    except ValueError:
        return pd.Series(dtype=float)
    for t in tables:
        if t.shape[1] < 9:
            continue
        rows: dict[str, float] = {}
        for _, row in t.dropna(how="all").iterrows():
            d = str(row.iloc[0]).strip()
            if not _DATE.match(d):
                continue
            try:
                inst = float(str(row.iloc[5]).replace(",", "").strip())
            except (ValueError, TypeError):
                continue
            rows[d.replace(".", "-")] = inst
        if rows:
            return pd.Series(rows).sort_index()
    return pd.Series(dtype=float)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_v10_supply.py -k parse -v`
Expected: PASS (2 passed)

- [ ] **Step 6: Commit**

```bash
git add src/swing_trader/market/supply.py tests/test_v10_supply.py tests/fixtures/frgn_005930.html
git commit -m "feat(v10): 네이버 기관 순매매 파서 + 픽스처"
```

---

## Task 6: 수급 게이트 (순수)

**Files:**
- Modify: `src/swing_trader/market/supply.py`
- Test: `tests/test_v10_supply.py`

**Interfaces:**
- Produces: `supply_ok(netbuy: pd.Series | None, entry_date: str, supply_days: int) -> bool | None`
  — 진입일 이전(포함) 최근 supply_days 기관 순매수: 합>0 AND ≥(supply_days-1)일 양(+)이면 True. 데이터 부족/None이면 **None**(호출측이 백테=드롭/라이브=허용 판단).

- [ ] **Step 1: Write the failing test**

`tests/test_v10_supply.py` 에 추가:
```python
from swing_trader.market.supply import supply_ok


def _series(pairs):
    return pd.Series({d: float(v) for d, v in pairs})


def test_supply_ok_true_on_consecutive_buying():
    s = _series([("2026-07-06", 100), ("2026-07-07", 200), ("2026-07-08", 300),
                 ("2026-07-09", 400)])
    assert supply_ok(s, "2026-07-09", 3) is True


def test_supply_ok_false_on_net_selling():
    s = _series([("2026-07-06", 100), ("2026-07-07", -500), ("2026-07-08", -600),
                 ("2026-07-09", -700)])
    assert supply_ok(s, "2026-07-09", 3) is False


def test_supply_ok_none_when_missing():
    assert supply_ok(None, "2026-07-09", 3) is None
    assert supply_ok(_series([("2026-07-09", 100)]), "2026-07-09", 3) is None   # 표본부족
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_v10_supply.py -k supply_ok -v`
Expected: FAIL (ImportError: supply_ok)

- [ ] **Step 3: Write the implementation**

`supply.py` 에 추가:
```python
def supply_ok(netbuy: "pd.Series | None", entry_date: str, supply_days: int) -> "bool | None":
    """진입일까지 최근 supply_days 기관 순매수 게이트. 데이터 부족/None → None(판단 위임)."""
    if netbuy is None or len(netbuy) == 0:
        return None
    s = netbuy[netbuy.index <= entry_date].tail(supply_days)
    if len(s) < supply_days:
        return None
    positives = int((s > 0).sum())
    return bool(s.sum() > 0 and positives >= supply_days - 1)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_v10_supply.py -k supply_ok -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/swing_trader/market/supply.py tests/test_v10_supply.py
git commit -m "feat(v10): 기관 연속 순매수 게이트(데이터 없음=None 위임)"
```

---

## Task 7: SupplyProvider (조회+캐시)

**Files:**
- Modify: `src/swing_trader/market/supply.py`
- Test: `tests/test_v10_supply.py`

**Interfaces:**
- Consumes: `parse_frgn_html`
- Produces: `class SupplyProvider(state_dir: Path, max_pages: int = 60, fetcher=None)` with `institution_netbuy(ticker: str) -> pd.Series | None`.
  - `fetcher(ticker, page) -> str | None` 주입 가능(테스트/네트워크 분리). 기본 fetcher는 네이버 HTTP.
  - 디스크 캐시 `state_dir/supply_cache/{ticker}.pkl`. 조회 실패 전체 → None.

- [ ] **Step 1: Write the failing test**

`tests/test_v10_supply.py` 에 추가:
```python
from swing_trader.market.supply import SupplyProvider


def test_supply_provider_uses_injected_fetcher_and_caches(tmp_path):
    html = FIX.read_bytes().decode("euc-kr", "replace")
    calls = {"n": 0}

    def fake_fetch(ticker, page):
        calls["n"] += 1
        return html if page == 1 else ""      # page2는 빈 표 → 조회 종료

    sp = SupplyProvider(tmp_path, max_pages=5, fetcher=fake_fetch)
    s1 = sp.institution_netbuy("005930")
    assert s1 is not None and len(s1) >= 5
    n_after_first = calls["n"]
    s2 = sp.institution_netbuy("005930")       # 두 번째 호출은 캐시 → fetch 안 함
    assert calls["n"] == n_after_first
    assert list(s2.index) == list(s1.index)


def test_supply_provider_none_on_total_failure(tmp_path):
    sp = SupplyProvider(tmp_path, fetcher=lambda t, p: None)
    assert sp.institution_netbuy("000000") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_v10_supply.py -k provider -v`
Expected: FAIL (ImportError: SupplyProvider)

- [ ] **Step 3: Write the implementation**

`supply.py` 상단 import에 `import pickle`, `from pathlib import Path` 추가 후:
```python
def _naver_fetch(ticker: str, page: int) -> "str | None":
    import urllib.request
    url = f"https://finance.naver.com/item/frgn.naver?code={ticker}&page={page}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        return urllib.request.urlopen(req, timeout=10).read().decode("euc-kr", "replace")
    except Exception as e:  # noqa: BLE001 — 네트워크 실패는 None
        log.debug("frgn 조회 실패(%s p%d): %s", ticker, page, e)
        return None


class SupplyProvider:
    """후보 종목의 기관 순매매 시계열 — 네이버 frgn 페이지 누적 + 디스크 캐시."""

    def __init__(self, state_dir, max_pages: int = 60, fetcher=None):
        self.dir = Path(state_dir) / "supply_cache"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.max_pages = max_pages
        self._fetch = fetcher or _naver_fetch

    def institution_netbuy(self, ticker):
        cache = self.dir / f"{ticker}.pkl"
        if cache.exists():
            try:
                return pickle.loads(cache.read_bytes())
            except Exception:  # noqa: BLE001 — 손상 캐시는 재수집
                pass
        frames: list[pd.Series] = []
        for page in range(1, self.max_pages + 1):
            html = self._fetch(ticker, page)
            if not html:
                break
            s = parse_frgn_html(html)
            if s.empty:
                break
            frames.append(s)
        if not frames:
            return None
        out = pd.concat(frames)
        out = out[~out.index.duplicated(keep="first")].sort_index()
        cache.write_bytes(pickle.dumps(out))
        return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_v10_supply.py -k provider -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/swing_trader/market/supply.py tests/test_v10_supply.py
git commit -m "feat(v10): SupplyProvider 네이버 frgn 누적 조회 + 디스크 캐시"
```

---

## Task 8: 시황 게이트 (지수 50일선)

**Files:**
- Modify: `src/swing_trader/strategy/v10_new_high.py`
- Test: `tests/test_v10_market.py`

**Interfaces:**
- Produces:
  - `index_up_days(index_code: str, ma: int, reader=None) -> set[str] | None` — 지수 종가≥ma일선인 'YYYY-MM-DD' 집합(실패 None). reader 주입 가능.
  - `regime_ok(market: str, date: str, kospi_up: set|None, kosdaq_up: set|None) -> bool` — 시장별 국면. up집합 None(데이터 없음)이면 **페일오픈 True**.

- [ ] **Step 1: Write the failing test**

`tests/test_v10_market.py` (신규):
```python
"""v10 시황 게이트 + per-ticker/전시장 오케스트레이션."""
from swing_trader.strategy import v10_new_high as v10


def test_regime_ok_by_market():
    kospi_up = {"2026-07-08", "2026-07-09"}
    kosdaq_up = {"2026-07-09"}
    assert v10.regime_ok("KOSPI", "2026-07-09", kospi_up, kosdaq_up) is True
    assert v10.regime_ok("KOSPI", "2026-07-07", kospi_up, kosdaq_up) is False
    assert v10.regime_ok("KOSDAQ", "2026-07-08", kospi_up, kosdaq_up) is False
    assert v10.regime_ok("KOSDAQ", "2026-07-09", kospi_up, kosdaq_up) is True


def test_regime_ok_fail_open_when_no_data():
    assert v10.regime_ok("KOSDAQ", "2026-07-09", None, None) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_v10_market.py -k regime -v`
Expected: FAIL (AttributeError: regime_ok)

- [ ] **Step 3: Write the implementation**

`v10_new_high.py` 에 추가:
```python
def index_up_days(index_code: str, ma: int, reader=None) -> "set[str] | None":
    """지수(KS11/KQ11) 종가 ≥ ma일선인 날짜 집합. 실패 시 None(페일오픈)."""
    try:
        if reader is None:
            import FinanceDataReader as fdr
            reader = lambda code: fdr.DataReader(code, "2023-01-01")["Close"].astype(float)
        s = reader(index_code)
        up = s >= s.rolling(ma).mean()
        return {d.strftime("%Y-%m-%d") for d, u in up.items() if bool(u)}
    except Exception:  # noqa: BLE001
        return None


def regime_ok(market: str, date: str, kospi_up, kosdaq_up) -> bool:
    """시장별 국면 게이트. up집합 None(데이터 없음)이면 페일오픈(True)."""
    up = kosdaq_up if str(market).upper() == "KOSDAQ" else kospi_up
    if up is None:
        return True
    return date in up
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_v10_market.py -k regime -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/swing_trader/strategy/v10_new_high.py tests/test_v10_market.py
git commit -m "feat(v10): 코스닥/코스피 50일선 시황 게이트(데이터 없음=페일오픈)"
```

---

## Task 9: per-ticker 거래 생성 (게이트 + v7 청산)

**Files:**
- Modify: `src/swing_trader/strategy/v10_new_high.py`
- Test: `tests/test_v10_market.py`

**Interfaces:**
- Consumes: `scan_candidates`, `supply_ok`(supply.py), `regime_ok`, `backtest._v7_exit`, `harness.Trade`
- Produces: `ticker_trades(df, ticker, market, netbuy, kospi_up, kosdaq_up, *, params: dict, mode: str, cost: float) -> list[Trade]`
  - `params` 키: high_n,vol_x,body_min,min_tv_eok,window,vol_dry,body_max,supply_days + 청산용 stop,take1,volspike,max_hold.
  - `mode="backtest"`: 수급 None/False → 드롭. `mode="live"`: None → 허용(페일오픈), False → 드롭.
  - 진입=Candidate.entry_price(종가), 청산=`_v7_exit(entry_idx=entry_idx)`. ret = exit_ret - cost.

- [ ] **Step 1: Write the failing test**

`tests/test_v10_market.py` 에 추가:
```python
import numpy as np
import pandas as pd
from swing_trader.strategy.harness import Trade


def _df(closes, opens, vols):
    idx = pd.date_range("2024-01-02", periods=len(closes), freq="B")
    c = np.array(closes, float); o = np.array(opens, float); v = np.array(vols, float)
    hi = np.maximum(o, c) * 1.001; lo = np.minimum(o, c) * 0.999
    return pd.DataFrame({"open": o, "high": hi, "low": lo, "close": c, "volume": v}, index=idx)


def _setup_df():
    base = list(100 + np.sin(np.linspace(0, 12, 260)) * 2)
    closes = base + [110.0, 111.0, 109.5, 112.0, 108.0]     # 260돌파,262거감짜름 진입
    opens = list(closes[:260]) + [105.0, 110.0, 110.0, 109.5, 111.5]
    vols = [1e6] * 260 + [3e6, 2e6, 5e5, 2e6, 2e6]
    return _df(closes, opens, vols)


_PARAMS = dict(high_n=252, vol_x=2.0, body_min=0.03, min_tv_eok=0, window=3,
               vol_dry=0.7, body_max=0.03, supply_days=3,
               stop=-0.03, take1=None, volspike=2.5, max_hold=40)


def test_ticker_trades_backtest_drops_when_no_supply():
    df = _setup_df()
    trades = v10.ticker_trades(df, "T", "KOSPI", None, None, None,
                               params=_PARAMS, mode="backtest", cost=0.0)
    assert trades == []                       # 수급 None → 백테스트 드롭


def test_ticker_trades_live_failopen_when_no_supply():
    df = _setup_df()
    trades = v10.ticker_trades(df, "T", "KOSPI", None, None, None,
                               params=_PARAMS, mode="live", cost=0.0)
    assert len(trades) == 1
    assert isinstance(trades[0], Trade)
    assert trades[0].entry == df.index[262].strftime("%Y-%m-%d")


def test_ticker_trades_supply_gate_passes_with_buying():
    df = _setup_df()
    dates = [d.strftime("%Y-%m-%d") for d in df.index]
    netbuy = pd.Series({dates[260]: 100.0, dates[261]: 200.0, dates[262]: 300.0})
    trades = v10.ticker_trades(df, "T", "KOSPI", netbuy, None, None,
                               params=_PARAMS, mode="backtest", cost=0.0)
    assert len(trades) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_v10_market.py -k ticker_trades -v`
Expected: FAIL (AttributeError: ticker_trades)

- [ ] **Step 3: Write the implementation**

`v10_new_high.py` 에 추가(상단 import에 `from .backtest import _v7_exit`, `from .harness import Trade`, `from ..market.supply import supply_ok` 추가):
```python
def ticker_trades(df, ticker, market, netbuy, kospi_up, kosdaq_up, *,
                  params: dict, mode: str, cost: float) -> list[Trade]:
    """한 종목의 v10 거래 목록. 수급/시황 게이트 후 v7 청산으로 수익률 산출."""
    cands = scan_candidates(
        df, ticker, high_n=params["high_n"], vol_x=params["vol_x"],
        body_min=params["body_min"], min_tv_eok=params["min_tv_eok"],
        window=params["window"], vol_dry=params["vol_dry"], body_max=params["body_max"])
    if not cands:
        return []
    close = _arr(df, "close"); open_ = _arr(df, "open"); vol = _arr(df, "volume")
    ma5 = pd.Series(close).rolling(5, min_periods=1).mean().to_numpy()
    va20 = pd.Series(vol).rolling(20, min_periods=5).mean().to_numpy()
    out: list[Trade] = []
    for cand in cands:
        if not regime_ok(market, cand.entry_date, kospi_up, kosdaq_up):
            continue
        ok = supply_ok(netbuy, cand.entry_date, params["supply_days"])
        if ok is False:
            continue
        if ok is None and mode == "backtest":     # 하드게이트: 미검증 드롭
            continue
        # ok is True, 또는 (ok is None and mode=="live") → 진입(페일오픈)
        ret, _jend = _v7_exit(close, open_, vol, ma5, va20, cand.entry_idx, cand.entry_price,
                              stop=params["stop"], take1=params["take1"],
                              volspike=params["volspike"], max_hold=params["max_hold"])
        out.append(Trade(ticker, cand.entry_date, float(ret) - cost))
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_v10_market.py -k ticker_trades -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/swing_trader/strategy/v10_new_high.py tests/test_v10_market.py
git commit -m "feat(v10): per-ticker 거래 생성(수급 하드/페일오픈 + 시황 + v7 청산)"
```

---

## Task 10: 전시장 오케스트레이션 (후보-only 수급조회)

**Files:**
- Modify: `src/swing_trader/strategy/v10_new_high.py`
- Test: `tests/test_v10_market.py`

**Interfaces:**
- Consumes: `ticker_trades`, `SupplyProvider`(주입), `index_up_days`
- Produces: `v10_market_trades(panel: dict, market_of: dict, supply, cfg, *, mode="backtest", kospi_up=None, kosdaq_up=None) -> list[Trade]`
  - `panel`: {ticker: DataFrame}. `market_of`: {ticker: "KOSPI"|"KOSDAQ"}. `supply`: `.institution_netbuy(ticker)` 있는 객체.
  - **후보 있는 종목에만** 수급 조회(접근법 A). cfg에서 v10/risk/paper 파라미터·cost 조립.

- [ ] **Step 1: Write the failing test**

`tests/test_v10_market.py` 에 추가:
```python
class _FakeCfg:
    def get(self, *keys, default=None):
        table = {
            ("v10",): dict(high_n=252, vol_x=2.0, body_min=0.03, min_tv_eok=0, window=3,
                           vol_dry=0.7, body_max=0.03, supply_days=3),
            ("risk", "default_stop_pct"): -3.0, ("risk", "max_hold_days"): 40,
            ("paper", "fee_bps"): 0.0, ("paper", "slippage_bps"): 0.0,
        }
        return table.get(tuple(keys), default)


class _FakeSupply:
    def __init__(self, series_by_ticker): self.d = series_by_ticker; self.calls = []
    def institution_netbuy(self, ticker):
        self.calls.append(ticker); return self.d.get(ticker)


def test_v10_market_trades_fetches_supply_only_for_candidates():
    df = _setup_df()
    flat = _df([100] * 60, [100] * 60, [1e6] * 60)          # 후보 없는 종목
    dates = [d.strftime("%Y-%m-%d") for d in df.index]
    netbuy = pd.Series({dates[260]: 100.0, dates[261]: 200.0, dates[262]: 300.0})
    supply = _FakeSupply({"CAND": netbuy})
    panel = {"CAND": df, "FLAT": flat}
    market_of = {"CAND": "KOSPI", "FLAT": "KOSDAQ"}
    trades = v10.v10_market_trades(panel, market_of, supply, _FakeCfg(),
                                   mode="backtest", kospi_up=None, kosdaq_up=None)
    assert len(trades) == 1 and trades[0].ticker == "CAND"
    assert supply.calls == ["CAND"]                          # FLAT은 후보 없어 수급 미조회
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_v10_market.py -k market_trades -v`
Expected: FAIL (AttributeError: v10_market_trades)

- [ ] **Step 3: Write the implementation**

`v10_new_high.py` 에 추가:
```python
def _params_from_cfg(cfg) -> dict:
    v = cfg.get("v10", default={})
    return dict(
        high_n=int(v.get("high_n", 252)), vol_x=float(v.get("vol_x", 2.0)),
        body_min=float(v.get("body_min", 0.03)), min_tv_eok=float(v.get("min_tv_eok", 50)),
        window=int(v.get("window", 3)), vol_dry=float(v.get("vol_dry", 0.7)),
        body_max=float(v.get("body_max", 0.03)), supply_days=int(v.get("supply_days", 3)),
        stop=float(cfg.get("risk", "default_stop_pct", default=-3.0)) / 100,
        take1=None, volspike=2.5,
        max_hold=int(cfg.get("risk", "max_hold_days", default=40)))


def v10_market_trades(panel: dict, market_of: dict, supply, cfg, *,
                      mode: str = "backtest", kospi_up=None, kosdaq_up=None) -> list[Trade]:
    """전시장 패널 v10 리플레이. 후보 있는 종목에만 수급 조회(접근법 A)."""
    params = _params_from_cfg(cfg)
    fee = float(cfg.get("paper", "fee_bps", default=1.5)) / 10000
    slip = float(cfg.get("paper", "slippage_bps", default=5.0)) / 10000
    cost = 2 * (fee + slip)
    out: list[Trade] = []
    for ticker, df in panel.items():
        if df is None or len(df) < params["high_n"] + params["window"] + 5:
            continue
        # 1단계: 값싼 가격 셋업으로 후보 유무만 확인(수급 조회 전)
        cands = scan_candidates(
            df, ticker, high_n=params["high_n"], vol_x=params["vol_x"],
            body_min=params["body_min"], min_tv_eok=params["min_tv_eok"],
            window=params["window"], vol_dry=params["vol_dry"], body_max=params["body_max"])
        if not cands:
            continue
        # 2단계: 후보 종목만 수급 조회
        netbuy = supply.institution_netbuy(ticker) if supply is not None else None
        out += ticker_trades(df, ticker, market_of.get(ticker, "KOSPI"),
                             netbuy, kospi_up, kosdaq_up,
                             params=params, mode=mode, cost=cost)
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_v10_market.py -v`
Expected: PASS (전체 v10_market 테스트 통과)

- [ ] **Step 5: Full v10 suite green**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_v10_detection.py tests/test_v10_supply.py tests/test_v10_market.py -v`
Expected: PASS (all)

- [ ] **Step 6: Commit**

```bash
git add src/swing_trader/strategy/v10_new_high.py tests/test_v10_market.py
git commit -m "feat(v10): 전시장 오케스트레이션(후보-only 수급조회) — v10_market_trades"
```

---

## Task 11: CLI `swing-v10-backtest` + v9 OOS A/B 리포트

**Files:**
- Modify: `src/swing_trader/cli.py` (서브파서 등록 + 디스패치)
- Modify: `src/swing_trader/main.py` (`run_v10_backtest`)
- Test: `tests/test_v10_market.py` (compare 조립 순수함수)

**Interfaces:**
- Consumes: `v10_market_trades`, `harness.report_from_trades`, `harness.split_oos`, `krx_universe.{list_universe,load_cache}`, `SupplyProvider`
- Produces:
  - `main.build_v10_compare(v10_trades, v9_trades, oos_frac, min_oos) -> dict` — {v10:{is,oos}, v9:{is,oos}, verdict}.
  - CLI `swing-v10-backtest` → `state/v10_compare.json` + `04_Trading/Backtests/` 문서.

- [ ] **Step 1: Write the failing test (compare 조립은 네트워크 없이 검증)**

`tests/test_v10_market.py` 에 추가:
```python
from swing_trader.strategy.harness import Trade as T
from swing_trader import main as m


def test_build_v10_compare_picks_winner_by_oos_expectancy():
    # v10: OOS 기대값 양(+), v9: OOS 기대값 음(-) → v10 승. 표본은 min_oos 충족.
    v10_tr = [T("A", f"2026-01-{i:02d}", 0.02) for i in range(1, 28)]
    v9_tr = [T("B", f"2026-01-{i:02d}", -0.01) for i in range(1, 28)]
    out = m.build_v10_compare(v10_tr, v9_tr, oos_frac=0.3, min_oos=5)
    assert out["verdict"]["winner"] == "v10"
    assert out["v10"]["oos"]["n_trades"] >= 5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_v10_market.py -k compare -v`
Expected: FAIL (AttributeError: build_v10_compare)

- [ ] **Step 3: Implement `build_v10_compare` in main.py**

`src/swing_trader/main.py` 에 함수 추가(파일 상단에 이미 harness import 있으면 재사용):
```python
def build_v10_compare(v10_trades, v9_trades, oos_frac: float = 0.3, min_oos: int = 100) -> dict:
    """v10 vs v9 IS/OOS 성과 비교 dict. 승자는 OOS 기대값(표본 충족 시)으로 판정."""
    from .strategy.harness import report_from_trades, split_oos

    def side(trades):
        is_t, oos_t = split_oos(trades, oos_frac)
        r_is, r_oos = report_from_trades(is_t), report_from_trades(oos_t)
        return {"is": r_is.__dict__, "oos": r_oos.__dict__}

    a, b = side(v10_trades), side(v9_trades)
    av, bv = a["oos"]["expectancy"], b["oos"]["expectancy"]
    enough = (a["oos"]["n_trades"] >= min_oos and b["oos"]["n_trades"] >= min_oos)
    if av is None or bv is None:
        winner = "표본부족"
    elif not enough:
        winner = f"표본부족(OOS<{min_oos}) — 참고: {'v10' if av > bv else 'v9'} 우세"
    else:
        winner = "v10" if av > bv else ("v9" if bv > av else "동일")
    return {"v10": a, "v9": b, "verdict": {"winner": winner,
            "v10_oos_expectancy": av, "v9_oos_expectancy": bv}}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_v10_market.py -k compare -v`
Expected: PASS

- [ ] **Step 5: Implement `run_v10_backtest` in main.py**

`src/swing_trader/main.py` 에 추가(패널·v9 거래·리포트 작성 — 기존 `run_backtest`/v6 비교 함수의 볼트쓰기 헬퍼 패턴을 따를 것):
```python
def run_v10_backtest(cfg) -> dict:
    """전시장 패널로 v10 거래 생성 → v9와 OOS A/B → state/v10_compare.json + 볼트 문서."""
    import json
    from pathlib import Path

    from .scalp.krx_universe import list_universe, load_cache
    from .market.supply import SupplyProvider
    from .strategy.v10_new_high import v10_market_trades, index_up_days
    from .strategy.backtest import _v7_stock_trades

    state_dir = Path(cfg.get("paths", "state_dir", default="state"))
    panel = load_cache(state_dir)
    panel = {k: v for k, v in panel.items() if v is not None}
    if not panel:
        raise RuntimeError("전시장 패널 없음 — 먼저 크로스/단타 패널 수집(krx_universe.fetch_panel) 필요. "
                           "synthetic 폴백은 성과로 쓰지 않음.")
    market_of = {u["code"]: u["market"] for u in list_universe()}
    ma = int(cfg.get("v10", "regime_ma", default=50))
    gate = bool(cfg.get("v10", "regime_gate", default=True))
    kospi_up = index_up_days("KS11", ma) if gate else None
    kosdaq_up = index_up_days("KQ11", ma) if gate else None
    max_pages = int(cfg.get("v10", "supply_max_pages", default=60))
    supply = SupplyProvider(state_dir, max_pages=max_pages)

    v10_trades = v10_market_trades(panel, market_of, supply, cfg, mode="backtest",
                                   kospi_up=kospi_up, kosdaq_up=kosdaq_up)
    # v9 비교군: 동일 패널에 v7/모멘텀 진입(코스닥 포함) — per-ticker.
    mom = float(cfg.get("risk", "momentum_min_pct", default=5.0))
    min_tv = float(cfg.get("risk", "min_trading_value_eok", default=30))
    fee = float(cfg.get("paper", "fee_bps", default=1.5)) / 10000
    slip = float(cfg.get("paper", "slippage_bps", default=5.0)) / 10000
    cost = 2 * (fee + slip)
    from .strategy.harness import Trade
    v9_trades: list[Trade] = []
    for tk, df in panel.items():
        for e, r in _v7_stock_trades(df, stop=-0.03, take1=None, volspike=2.5, max_hold=40,
                                     cost=cost, min_tv_eok=min_tv, require_uptrend=True,
                                     momentum_min_pct=mom):
            v9_trades.append(Trade(tk, e, r))

    oos_frac = float(cfg.get("backtest", "oos_fraction", default=0.3))
    min_oos = int(cfg.get("backtest", "min_oos_trades", default=100))
    compare = build_v10_compare(v10_trades, v9_trades, oos_frac, min_oos)
    compare["counts"] = {"panel": len(panel), "v10_trades": len(v10_trades),
                         "v9_trades": len(v9_trades)}
    (state_dir / "v10_compare.json").write_text(
        json.dumps(compare, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return compare
```

- [ ] **Step 6: Register CLI subcommand**

`src/swing_trader/cli.py`, 다른 `sub.add_parser` 근처(예: line 62 부근)에 추가:
```python
    sub.add_parser("swing-v10-backtest", help="스윙 v10(신고가 거감짜름) 전시장 백테스트 → v9 OOS A/B → state/v10_compare.json 📈")
```
그리고 디스패치(예: `if args.cmd in ("scalp-v6", ...)` 근처)에 추가:
```python
    if args.cmd == "swing-v10-backtest":
        from swing_trader.main import run_v10_backtest
        r = run_v10_backtest(cfg)
        c = r["counts"]
        print(f"✅ v10 백테스트: 패널 {c['panel']} · v10 {c['v10_trades']}건 · v9 {c['v9_trades']}건 "
              f"· OOS 승자: {r['verdict']['winner']}")
        return
```
*(실제 디스패치 구조는 파일의 기존 분기 스타일을 따를 것 — `args.cmd`/`args.command` 명칭 확인.)*

- [ ] **Step 7: Lint + full test suite**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_v10_detection.py tests/test_v10_supply.py tests/test_v10_market.py -v && ./.venv/Scripts/ruff.exe check src/swing_trader/market/supply.py src/swing_trader/strategy/v10_new_high.py src/swing_trader/main.py`
Expected: 테스트 PASS, ruff clean

- [ ] **Step 8: Commit**

```bash
git add src/swing_trader/cli.py src/swing_trader/main.py tests/test_v10_market.py
git commit -m "feat(v10): swing-v10-backtest CLI + v9 OOS A/B 비교 리포트"
```

---

## Task 12: 실제 백테스트 실행 + 정직한 채택 판정

**Files:**
- Modify: `docs/superpowers/specs/2026-07-11-swing-v10-new-high-institutional-design.md` (결과 부록 추가)
- (조건부) Modify: `config.yaml` (`regime.adopted_version` — v10 승리 시에만)

**Interfaces:**
- Consumes: `swing-v10-backtest` CLI, `state/v10_compare.json`

- [ ] **Step 1: 패널 확보 확인**

Run: `./.venv/Scripts/python.exe -c "from pathlib import Path; from swing_trader.scalp.krx_universe import load_cache; p=load_cache(Path('state')); print('panel', len([1 for v in p.values() if v is not None]))"`
Expected: 수백~2000+ 종목. **0이면** 먼저 패널 수집:
`./.venv/Scripts/python.exe -c "from pathlib import Path; from swing_trader.scalp.krx_universe import fetch_panel; fetch_panel(Path('state'))"` (수 분~수십 분, 네트워크).

- [ ] **Step 2: v10 백테스트 실행 (네트워크 — 후보 종목 수급 조회 포함)**

Run: `./.venv/Scripts/python.exe -m swing_trader swing-v10-backtest`
Expected: `✅ v10 백테스트: 패널 N · v10 X건 · v9 Y건 · OOS 승자: ...` 출력, `state/v10_compare.json` 생성.

- [ ] **Step 3: 결과 확인**

Run: `./.venv/Scripts/python.exe -c "import json; d=json.load(open('state/v10_compare.json', encoding='utf-8')); print('verdict', d['verdict']); print('v10 OOS', d['v10']['oos']['n_trades'], d['v10']['oos']['expectancy'], d['v10']['oos']['profit_factor']); print('v9 OOS', d['v9']['oos']['n_trades'], d['v9']['oos']['expectancy'], d['v9']['oos']['profit_factor'])"`
Expected: v10/v9 OOS 지표 출력.

- [ ] **Step 4: 스펙에 결과 부록 기록 (정직)**

스펙 문서 끝에 `## 부록 — OOS A/B 결과(2026-07-11 실행)` 섹션 추가: 패널 규모, v10/v9 거래수, IS/OOS 기대값·PF·MDD·승률, 표본 충족 여부, 승자. **표본부족이면 그대로 기록**(판정 보류).

- [ ] **Step 5: 채택 결정 (조건부)**

- v10이 OOS에서 **표본 충족 + 기대값·PF 우위** → `config.yaml` `regime.adopted_version: v10` 로 변경, 커밋. 라이브 3면 배선은 별도 플랜.
- 그렇지 않으면 → **v9 유지.** config 변경 없음. 스펙 부록에 "미채택 사유" 기록.

- [ ] **Step 6: Commit**

```bash
git add docs/superpowers/specs/2026-07-11-swing-v10-new-high-institutional-design.md config.yaml state/v10_compare.json
git commit -m "test(v10): 전시장 OOS A/B 실행 + 채택 판정 기록"
```

*(참고: `state/`가 .gitignore면 v10_compare.json은 커밋에서 빠짐 — 정상. 그 경우 스펙 부록에 수치를 남긴다.)*

---

## Self-Review 메모

- **스펙 커버리지:** §3 접근법A→T10, §4 진입식→T2·T3·T4, §5 수급→T5·T6·T7, §6 시황→T8, §7 청산(v7)→T9, §8 컴포넌트→전 태스크, §10 테스트→각 태스크 TDD, §11 채택→T11·T12, §12 YAGNI(월봉 근사=hist_vol_mask, US 제외=Global Constraint) 반영. 라이브 배선(§8 2단계)은 명시적으로 범위 밖.
- **플레이스홀더:** 없음(모든 코드 스텝에 실제 코드). CLI 디스패치·load_config 호출부만 "기존 스타일 확인" 주석 — 파일 상태 의존이라 의도적.
- **타입 일관성:** `Candidate.entry_idx/entry_price/entry_date` T4 정의 → T9에서 동일 사용. `supply_ok`/`institution_netbuy`가 `None` 반환 규약 T6·T7·T9 일관. 거래 타입 `harness.Trade(ticker,entry,ret)` 전 구간 통일. `_v7_exit` 시그니처(backtest.py:92) 그대로 소비.
