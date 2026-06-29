# 백테스트 검증 하니스 (Phase 0) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 스윙 백테스트를 "대형 유니버스 + 2년 히스토리 + 기대값/PF/MDD/Sharpe 지표 + 인샘플·아웃오브샘플 분할 + baseline↔candidate A/B 판정"이 가능한 검증 하니스로 확장한다.

**Architecture:** 기존 `strategy/backtest.py`의 per-stock 진입/청산 루프를 날짜가 붙은 `_stock_trades` 헬퍼로 추출(기존 `simulate` 외부 계약 보존). 그 위에 얇은 `strategy/harness.py`(Trade 레코드 → BacktestReport 지표 + OOS 분할 + compare 판정)를 신설. CLI `harness`로 현재 로직의 IS/OOS 성과를 옵시디언+디스코드에 기록.

**Tech Stack:** Python 3.12, pandas/numpy, pytest, 기존 swing_trader 패키지(editable 설치).

## Global Constraints

- 실측·관리 목표: 일 +0.1~0.15% / 연 +20~40%. 1%/일은 북극성, **최적화 목적함수로 쓰지 않음**.
- 판정은 **아웃오브샘플(OOS)** 기준만 인정. OOS 거래 < **100**건이면 "표본부족, 판정보류".
- 페이퍼 트레이딩 전용. 실전 주문/레버리지 도입 금지.
- 백테스트 히스토리는 `backtest.lookback_days`(기본 500)로 fetch — 라이브 지표용 `market_data.lookback_days`(120)와 분리.
- 기존 `simulate(...)`의 반환 계약(rows, summary, take, stop)을 깨지 않는다(weekly 브리핑·run_backtest·run_logic 가 의존).
- 모든 테스트: `cd swing-short-trading && PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest ...` (Windows cp949 회피).
- 커밋 메시지 말미: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`

---

## File Structure

- `src/swing_trader/strategy/backtest.py` (modify) — `_stock_trades` 추출 + `_resolve_params` + `simulate` 리팩터(계약 보존).
- `src/swing_trader/strategy/harness.py` (create) — `Trade`, `BacktestReport`, `report_from_trades`, `split_oos`, `simulate_trades`, `backtest_provider`, `compare`, `ABResult`, `_judge`, `render_report_md`.
- `src/swing_trader/obsidian/writer.py` (modify) — `write_harness(md)`.
- `src/swing_trader/cli.py` (modify) — `harness` 서브커맨드.
- `src/swing_trader/main.py` (modify) — `run_harness(cfg)`.
- `config.yaml` (modify) — `backtest:` 섹션.
- `tests/test_backtest_trades.py` (create) — `_stock_trades` 날짜·계약.
- `tests/test_harness_report.py` (create) — 지표·OOS·판정.

---

### Task 1: per-stock 날짜 거래 추출 (`_stock_trades`) + `simulate` 리팩터

**Files:**
- Modify: `src/swing_trader/strategy/backtest.py`
- Test: `tests/test_backtest_trades.py`

**Interfaces:**
- Produces: `_stock_trades(df, *, take, stop, max_hold, runner, take2, trail, cost, min_tv_eok) -> list[tuple[str, float]]` — (entry_date "YYYY-MM-DD", ret_after_cost decimal).
- Produces: `_resolve_params(cfg, *, take_pct=None, stop_pct=None, runner=False, take2_pct=None, trail_pct=None) -> dict` — keys: take, stop, take2, trail, max_hold, cost, min_tv_eok, runner.
- 기존 `simulate(...)` 시그니처/반환 불변.

- [ ] **Step 1: 실패 테스트 작성** — `tests/test_backtest_trades.py`

```python
import pandas as pd
from swing_trader.strategy import backtest as BT


def _df(closes, vol=5_000_000):
    n = len(closes)
    idx = pd.bdate_range(end="2026-06-26", periods=n)
    return pd.DataFrame(
        {"open": closes, "high": [c * 1.01 for c in closes], "low": [c * 0.99 for c in closes],
         "close": closes, "volume": [vol] * n}, index=idx,
    )


def test_stock_trades_returns_dated_tuples():
    # 20봉 횡보 후 눌림→반등 패턴이 최소 1건 거래를 만들고, 날짜 문자열이 붙는다.
    closes = [100.0] * 22 + [97.0, 99.0, 104.5, 101.0, 101.0, 101.0]
    trades = BT._stock_trades(_df(closes), take=0.05, stop=-0.03, max_hold=20,
                              runner=False, take2=0.085, trail=3.0, cost=0.0, min_tv_eok=0)
    assert isinstance(trades, list)
    assert trades, "거래가 최소 1건 나와야 함"
    d, r = trades[0]
    assert isinstance(d, str) and len(d) == 10 and d[4] == "-"
    assert isinstance(r, float)
```

- [ ] **Step 2: 실패 확인**

Run: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/test_backtest_trades.py -q`
Expected: FAIL (`AttributeError: module ... has no attribute '_stock_trades'`)

- [ ] **Step 3: `_stock_trades` + `_resolve_params` 추가, `simulate` 리팩터**

`backtest.py`의 `_exit_return` 아래, `simulate` 위에 추가:

```python
def _stock_trades(df, *, take, stop, max_hold, runner, take2, trail, cost, min_tv_eok):
    """단일 종목 df에서 (진입일 'YYYY-MM-DD', 청산수익률 소수, 비용차감) 리스트."""
    close = _col(df, "close").to_numpy(dtype=float)
    open_ = _col(df, "open").to_numpy(dtype=float)
    vol = _col(df, "volume").to_numpy(dtype=float)
    ma20 = _col(df, "close").rolling(20, min_periods=1).mean().to_numpy(dtype=float)
    dates = [d.strftime("%Y-%m-%d") for d in df.index]
    out: list[tuple[str, float]] = []
    i = 20
    while i < len(close) - 2:                    # i+1 체결 가능해야(룩어헤드 방지)
        tv_eok = close[i] * vol[i] / 1e8
        if close[i] <= ma20[i] * 1.01 and close[i] > close[i - 1] and tv_eok >= min_tv_eok:
            entry = float(open_[i + 1])           # 다음 봉 시가 체결
            ret, jend = _exit_return(close, i + 1, entry, take, stop, max_hold,
                                     runner=runner, take2=take2, trail=trail)
            out.append((dates[i + 1], float(ret) - cost))
            i = max(jend, i + 1)
        i += 1
    return out


def _resolve_params(cfg, *, take_pct=None, stop_pct=None, runner: bool = False,
                    take2_pct=None, trail_pct=None) -> dict:
    """config + 오버라이드 → simulate/_stock_trades 공용 파라미터."""
    take = float(take_pct if take_pct is not None else cfg.get("risk", "take1_pct", default=5.0)) / 100
    stop = float(stop_pct if stop_pct is not None else cfg.get("risk", "default_stop_pct", default=-3.0)) / 100
    take2 = float(take2_pct if take2_pct is not None else cfg.get("risk", "take2_pct", default=8.5)) / 100
    trail = float(trail_pct if trail_pct is not None else cfg.get("risk", "trail_pct", default=3.0))
    max_hold = int(cfg.get("risk", "max_hold_days", default=20))
    fee = float(cfg.get("paper", "fee_bps", default=1.5)) / 10000
    slip = float(cfg.get("paper", "slippage_bps", default=5.0)) / 10000
    min_tv_eok = float(cfg.get("risk", "min_trading_value_eok", default=30))
    return {"take": take, "stop": stop, "take2": take2, "trail": trail, "max_hold": max_hold,
            "cost": 2 * (fee + slip), "min_tv_eok": min_tv_eok, "runner": runner}
```

기존 `simulate` 본문의 파라미터 계산부와 per-stock 루프를 아래로 교체(반환은 동일):

```python
def simulate(cfg, provider, notes, days: int, take_pct=None, stop_pct=None,
             runner: bool = False, take2_pct=None, trail_pct=None):
    """(rows, summary, take, stop). runner=True면 부분익절+트레일링(승자를 달리게)."""
    p = _resolve_params(cfg, take_pct=take_pct, stop_pct=stop_pct, runner=runner,
                        take2_pct=take2_pct, trail_pct=trail_pct)
    rows, real, all_rets = [], 0, []
    for n in notes:
        if not n.ticker:
            continue
        df, src = provider.get_ohlcv(n.ticker)
        df = df.tail(days)
        trades = _stock_trades(df, take=p["take"], stop=p["stop"], max_hold=p["max_hold"],
                               runner=p["runner"], take2=p["take2"], trail=p["trail"],
                               cost=p["cost"], min_tv_eok=p["min_tv_eok"])
        rets = [r for _, r in trades]
        trades_n = len(rets)
        wr = (sum(1 for r in rets if r > 0) / trades_n * 100) if trades_n else 0
        all_rets += rets
        if src in ("pykrx", "yfinance"):
            real += 1
        rows.append((n.display_name, n.ticker, src, trades_n, wr))

    summary = BacktestSummary(n_stocks=len(rows))
    if rows:
        summary.total_trades = sum(r[3] for r in rows)
        traded = [r[4] for r in rows if r[3] > 0]
        summary.avg_win_rate = round(sum(traded) / len(traded), 1) if traded else None
        summary.avg_return = round(sum(all_rets) / len(all_rets) * 100, 3) if all_rets else None
        summary.real_ratio = round(real / len(rows) * 100, 0)
    return rows, summary, p["take"], p["stop"]
```

- [ ] **Step 4: 통과 확인 + 기존 회귀 없음**

Run: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/test_backtest_trades.py -q && PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest -q`
Expected: 신규 PASS + 기존 전체 PASS(리팩터로 깨진 것 없음)

- [ ] **Step 5: 커밋**

```bash
git add src/swing_trader/strategy/backtest.py tests/test_backtest_trades.py
git commit -m "refactor(backtest): per-stock 날짜 거래 추출(_stock_trades)+_resolve_params, simulate 계약 보존

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: 지표 리포트 (`Trade`, `BacktestReport`, `report_from_trades`)

**Files:**
- Create: `src/swing_trader/strategy/harness.py`
- Test: `tests/test_harness_report.py`

**Interfaces:**
- Produces: `@dataclass Trade(ticker:str, entry:str, ret:float)`.
- Produces: `report_from_trades(trades: list[Trade]) -> BacktestReport`.
- Produces: `@dataclass BacktestReport` — fields: `n_trades:int, win_rate:float|None, avg_win:float|None, avg_loss:float|None, expectancy:float|None, profit_factor:float|None, max_drawdown:float|None, sharpe:float|None, trades_per_year:float|None, start:str|None, end:str|None`. (단위: %는 퍼센트, expectancy/avg_win/avg_loss/MDD = %; sharpe = 거래당 평균/표준편차.)

- [ ] **Step 1: 실패 테스트 작성** — `tests/test_harness_report.py`

```python
from swing_trader.strategy import harness as H


def _t(entry, ret):
    return H.Trade(ticker="X", entry=entry, ret=ret)


def test_report_basic_metrics():
    trades = [_t("2026-01-05", 0.05), _t("2026-02-05", -0.03),
              _t("2026-03-05", 0.05), _t("2026-04-05", -0.03)]
    r = H.report_from_trades(trades)
    assert r.n_trades == 4
    assert r.win_rate == 50.0
    assert r.expectancy == round((0.05 - 0.03 + 0.05 - 0.03) / 4 * 100, 3)  # +1.0%
    assert r.profit_factor == round((0.05 + 0.05) / (0.03 + 0.03), 2)       # 1.67
    assert r.max_drawdown is not None and r.max_drawdown <= 0
    assert r.start == "2026-01-05" and r.end == "2026-04-05"
    assert r.trades_per_year is not None and r.trades_per_year > 0


def test_report_empty_is_safe():
    r = H.report_from_trades([])
    assert r.n_trades == 0 and r.expectancy is None and r.max_drawdown is None
```

- [ ] **Step 2: 실패 확인**

Run: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/test_harness_report.py -q`
Expected: FAIL (`ModuleNotFoundError: ...harness`)

- [ ] **Step 3: `harness.py` 생성(이 Task 범위만)**

```python
"""백테스트 검증 하니스 — 날짜 거래 → 지표 리포트 + 인샘플/아웃오브샘플 분할 + A/B 판정.

simulate(요약·표)와 분리: 여기선 개별 거래(날짜 포함)를 모아 기대값·손익비·PF·MDD·Sharpe를
산출하고, 시간순 홀드아웃(OOS)으로 과최적화를 적발한다.
"""
from __future__ import annotations

import statistics as st
from dataclasses import dataclass
from datetime import date, timedelta


@dataclass
class Trade:
    ticker: str
    entry: str   # 'YYYY-MM-DD' (다음봉 시가 체결일)
    ret: float   # 청산수익률(소수, 비용차감)


@dataclass
class BacktestReport:
    n_trades: int = 0
    win_rate: float | None = None        # %
    avg_win: float | None = None         # % (양수)
    avg_loss: float | None = None        # % (음수)
    expectancy: float | None = None      # 거래당 평균 %
    profit_factor: float | None = None
    max_drawdown: float | None = None    # % (음수)
    sharpe: float | None = None          # 거래당 평균/표준편차
    trades_per_year: float | None = None
    start: str | None = None
    end: str | None = None


def report_from_trades(trades: list[Trade]) -> BacktestReport:
    r = BacktestReport(n_trades=len(trades))
    if not trades:
        return r
    rets = [t.ret for t in trades]
    wins = [x for x in rets if x > 0]
    losses = [x for x in rets if x <= 0]
    r.win_rate = round(len(wins) / len(rets) * 100, 1)
    r.avg_win = round(sum(wins) / len(wins) * 100, 3) if wins else None
    r.avg_loss = round(sum(losses) / len(losses) * 100, 3) if losses else None
    r.expectancy = round(sum(rets) / len(rets) * 100, 3)
    gross_win, gross_loss = sum(wins), -sum(losses)
    r.profit_factor = round(gross_win / gross_loss, 2) if gross_loss > 0 else None
    ordered = sorted(trades, key=lambda t: t.entry)
    eq = peak = 1.0
    mdd = 0.0
    for t in ordered:
        eq *= (1 + t.ret)
        peak = max(peak, eq)
        mdd = min(mdd, eq / peak - 1)
    r.max_drawdown = round(mdd * 100, 2)
    if len(rets) >= 2:
        sd = st.pstdev(rets)
        r.sharpe = round((sum(rets) / len(rets)) / sd, 3) if sd > 0 else None
    r.start, r.end = ordered[0].entry, ordered[-1].entry
    span = (date.fromisoformat(r.end) - date.fromisoformat(r.start)).days
    if span > 0:
        r.trades_per_year = round(len(trades) / span * 365, 1)
    return r
```

- [ ] **Step 4: 통과 확인**

Run: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/test_harness_report.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: 커밋**

```bash
git add src/swing_trader/strategy/harness.py tests/test_harness_report.py
git commit -m "feat(harness): Trade/BacktestReport + report_from_trades(기대값·PF·MDD·Sharpe)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: OOS 시간분할 + config `backtest:` + `backtest_provider`

**Files:**
- Modify: `src/swing_trader/strategy/harness.py`
- Modify: `config.yaml`
- Test: `tests/test_harness_report.py` (추가)

**Interfaces:**
- Consumes: `Trade`, `BacktestReport` (Task 2).
- Produces: `split_oos(trades: list[Trade], frac: float = 0.3) -> tuple[list[Trade], list[Trade]]` — entry **날짜** 기준 시간순 분할(뒤 frac이 OOS).
- Produces: `backtest_provider(cfg) -> DataProvider` — lookback = `backtest.lookback_days`.

- [ ] **Step 1: 실패 테스트 추가** (`tests/test_harness_report.py` 하단에)

```python
def test_split_oos_by_date():
    trades = [H.Trade("X", f"2026-{m:02d}-05", 0.01) for m in range(1, 11)]  # 1~10월
    is_, oos = H.split_oos(trades, frac=0.3)
    assert is_ and oos
    # 분할 경계는 날짜 스팬 기준 — OOS 첫 거래가 IS 마지막보다 늦다
    assert is_[-1].entry < oos[0].entry
    # 전체 보존
    assert len(is_) + len(oos) == 10


def test_split_oos_empty():
    assert H.split_oos([], 0.3) == ([], [])
```

- [ ] **Step 2: 실패 확인**

Run: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/test_harness_report.py -k split -q`
Expected: FAIL (`AttributeError: ...split_oos`)

- [ ] **Step 3: `split_oos` + `backtest_provider` 추가**

`harness.py` 상단 import에 추가: `from ..market.data_provider import DataProvider`. 파일 끝에 추가:

```python
def split_oos(trades: list[Trade], frac: float = 0.3) -> tuple[list[Trade], list[Trade]]:
    """entry 날짜 기준 시간순 분할 → (인샘플, 아웃오브샘플). 뒤 frac 기간이 OOS 홀드아웃."""
    if not trades:
        return [], []
    ordered = sorted(trades, key=lambda t: t.entry)
    start = date.fromisoformat(ordered[0].entry)
    end = date.fromisoformat(ordered[-1].entry)
    span = (end - start).days
    if span <= 0:
        return ordered, []
    cut = (start + timedelta(days=int(span * (1 - frac)))).isoformat()
    is_ = [t for t in ordered if t.entry < cut]
    oos = [t for t in ordered if t.entry >= cut]
    return is_, oos


def backtest_provider(cfg):
    """백테스트 전용 provider — backtest.lookback_days(기본 500)로 더 긴 히스토리 fetch."""
    from ..market.fx import get_usdkrw
    md = cfg.get("market_data", default={})
    look = int(cfg.get("backtest", "lookback_days", default=500))
    fx = get_usdkrw(float(md.get("fx_usdkrw", 1400)))
    return DataProvider(provider=md.get("provider", "auto"), lookback_days=look, fx_usdkrw=fx)
```

`config.yaml`의 `market_data:` 섹션 바로 위(또는 아래)에 추가:

```yaml
backtest:
  lookback_days: 500     # 백테스트 전용 히스토리(약 2년). 라이브 지표용 market_data.lookback_days(120)와 분리
  oos_fraction: 0.3      # 뒤 30%를 아웃오브샘플 홀드아웃
  min_oos_trades: 100    # OOS 거래 < 이 값이면 판정 보류(표본부족)
  universe: all          # all | kr | us
```

- [ ] **Step 4: 통과 확인**

Run: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/test_harness_report.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: 커밋**

```bash
git add src/swing_trader/strategy/harness.py config.yaml tests/test_harness_report.py
git commit -m "feat(harness): split_oos 시간분할 + backtest_provider + config backtest 섹션

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: `simulate_trades` + A/B 비교·판정 (`compare`, `ABResult`, `_judge`)

**Files:**
- Modify: `src/swing_trader/strategy/harness.py`
- Test: `tests/test_harness_report.py` (추가)

**Interfaces:**
- Consumes: `backtest._resolve_params`, `backtest._stock_trades` (Task 1); `split_oos`, `report_from_trades` (Task 2/3).
- Produces: `simulate_trades(cfg, provider, notes, days, **params) -> list[Trade]`.
- Produces: `@dataclass ABResult(sample_ok:bool, n_oos:int, base_is, base_oos, cand_is, cand_oos, verdict:str)` (report 4개 = BacktestReport, verdict ∈ "improve"|"neutral"|"worse"|"insufficient").
- Produces: `compare(cfg, provider, notes, days, baseline:dict, candidate:dict, oos_fraction=None, min_oos=None) -> ABResult`.
- Produces: `_judge(base: BacktestReport, cand: BacktestReport) -> str`.

- [ ] **Step 1: 실패 테스트 추가**

```python
def test_judge_improve_and_worse():
    base = H.BacktestReport(n_trades=200, expectancy=0.50, sharpe=0.10)
    better = H.BacktestReport(n_trades=200, expectancy=0.80, sharpe=0.20)
    worse = H.BacktestReport(n_trades=200, expectancy=0.30, sharpe=0.02)
    same = H.BacktestReport(n_trades=200, expectancy=0.505, sharpe=0.101)
    assert H._judge(base, better) == "improve"
    assert H._judge(base, worse) == "worse"
    assert H._judge(base, same) == "neutral"


def test_compare_sample_guard(monkeypatch):
    # 거래 수가 적으면 insufficient. simulate_trades를 가짜로 대체.
    few = [H.Trade("X", "2026-01-05", 0.01), H.Trade("X", "2026-06-05", -0.01)]
    monkeypatch.setattr(H, "simulate_trades", lambda *a, **k: few)
    ab = H.compare(cfg=None, provider=None, notes=[], days=500,
                   baseline={}, candidate={"runner": True}, oos_fraction=0.3, min_oos=100)
    assert ab.verdict == "insufficient" and ab.sample_ok is False
```

- [ ] **Step 2: 실패 확인**

Run: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/test_harness_report.py -k "judge or sample_guard" -q`
Expected: FAIL (`AttributeError: ...simulate_trades/_judge/compare`)

- [ ] **Step 3: `simulate_trades` + `compare` + `ABResult` + `_judge` 추가**

`harness.py` 끝에 추가:

```python
def simulate_trades(cfg, provider, notes, days: int, **params) -> list[Trade]:
    """전 종목 백테스트 → 날짜 붙은 Trade 리스트. params 는 _resolve_params 오버라이드
    (take_pct/stop_pct/runner/take2_pct/trail_pct)."""
    from . import backtest as _BT
    p = _BT._resolve_params(cfg, **params)
    trades: list[Trade] = []
    for n in notes:
        if not n.ticker:
            continue
        df, _src = provider.get_ohlcv(n.ticker)
        df = df.tail(days)
        for d, r in _BT._stock_trades(df, take=p["take"], stop=p["stop"], max_hold=p["max_hold"],
                                      runner=p["runner"], take2=p["take2"], trail=p["trail"],
                                      cost=p["cost"], min_tv_eok=p["min_tv_eok"]):
            trades.append(Trade(n.ticker, d, r))
    return trades


@dataclass
class ABResult:
    sample_ok: bool
    n_oos: int
    base_is: BacktestReport
    base_oos: BacktestReport
    cand_is: BacktestReport
    cand_oos: BacktestReport
    verdict: str   # improve | neutral | worse | insufficient


def _judge(base: BacktestReport, cand: BacktestReport) -> str:
    """OOS 기준 판정. 미세차이는 노이즈로 간주(기대값 ±0.02%p, Sharpe ±0.05 마진)."""
    be, ce = base.expectancy or 0.0, cand.expectancy or 0.0
    bs, cs = base.sharpe or 0.0, cand.sharpe or 0.0
    better = (ce > be + 0.02) or (cs > bs + 0.05)
    worse = (ce < be - 0.02) and (cs < bs - 0.05)
    if worse:
        return "worse"
    if better:
        return "improve"
    return "neutral"


def compare(cfg, provider, notes, days: int, baseline: dict, candidate: dict,
            oos_fraction=None, min_oos=None) -> ABResult:
    frac = oos_fraction if oos_fraction is not None else float(cfg.get("backtest", "oos_fraction", default=0.3))
    floor = min_oos if min_oos is not None else int(cfg.get("backtest", "min_oos_trades", default=100))
    b_is, b_oos = split_oos(simulate_trades(cfg, provider, notes, days, **baseline), frac)
    c_is, c_oos = split_oos(simulate_trades(cfg, provider, notes, days, **candidate), frac)
    base_is, base_oos = report_from_trades(b_is), report_from_trades(b_oos)
    cand_is, cand_oos = report_from_trades(c_is), report_from_trades(c_oos)
    n_oos = min(base_oos.n_trades, cand_oos.n_trades)
    if n_oos < floor:
        return ABResult(False, n_oos, base_is, base_oos, cand_is, cand_oos, "insufficient")
    return ABResult(True, n_oos, base_is, base_oos, cand_is, cand_oos, _judge(base_oos, cand_oos))
```

`compare`가 `cfg.get(...)` 폴백을 타도록, 테스트에서 `oos_fraction`/`min_oos`를 명시로 넘긴다(위 테스트는 그렇게 함). `cfg=None`이어도 명시 인자면 동작.

- [ ] **Step 4: 통과 확인**

Run: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/test_harness_report.py -q`
Expected: PASS (6 passed)

- [ ] **Step 5: 커밋**

```bash
git add src/swing_trader/strategy/harness.py tests/test_harness_report.py
git commit -m "feat(harness): simulate_trades + compare/ABResult/_judge(OOS A/B + 표본가드)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: 리포트 렌더 + `run_harness` + CLI `harness`

**Files:**
- Modify: `src/swing_trader/strategy/harness.py` (`render_report_md`)
- Modify: `src/swing_trader/obsidian/writer.py` (`write_harness`)
- Modify: `src/swing_trader/main.py` (`run_harness`)
- Modify: `src/swing_trader/cli.py` (`harness` 서브커맨드)
- Test: `tests/test_harness_report.py` (render 추가)

**Interfaces:**
- Consumes: `report_from_trades`, `split_oos`, `simulate_trades`, `backtest_provider` (Task 2~4); `_load_notes` (main.py).
- Produces: `render_report_md(title:str, is_rep:BacktestReport, oos_rep:BacktestReport, d:str) -> str`.
- Produces: `VaultWriter.write_harness(content:str, d=None) -> Path` (→ `04_Trading/Backtests/<date>_Harness.md`).
- Produces: `main.run_harness(cfg) -> Path` — 현재 로직 baseline 을 full 유니버스·장기 히스토리로 IS/OOS 측정 → 볼트+디스코드.

- [ ] **Step 1: 실패 테스트 추가(render 순수함수만)**

```python
def test_render_report_md_has_both_windows():
    is_rep = H.report_from_trades([H.Trade("X", "2026-01-05", 0.05), H.Trade("X", "2026-02-05", -0.03)])
    oos_rep = H.report_from_trades([H.Trade("X", "2026-05-05", 0.05), H.Trade("X", "2026-06-05", -0.03)])
    md = H.render_report_md("기준 로직 측정", is_rep, oos_rep, "2026-06-30")
    assert "인샘플" in md and "아웃오브샘플" in md
    assert "기대값" in md and "MDD" in md
    assert "type: 스윙백테스트하니스" in md
```

- [ ] **Step 2: 실패 확인**

Run: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/test_harness_report.py -k render -q`
Expected: FAIL (`AttributeError: ...render_report_md`)

- [ ] **Step 3: `render_report_md` 추가**

`harness.py` 끝에 추가:

```python
def _fmt(v, suffix="%"):
    return "—" if v is None else f"{v:g}{suffix}"


def _report_row(label: str, r: BacktestReport) -> str:
    return (f"| {label} | {r.n_trades} | {_fmt(r.win_rate)} | {_fmt(r.expectancy)} | "
            f"{_fmt(r.profit_factor, '')} | {_fmt(r.max_drawdown)} | {_fmt(r.sharpe, '')} | "
            f"{_fmt(r.trades_per_year, '')} |")


def render_report_md(title: str, is_rep: BacktestReport, oos_rep: BacktestReport, d: str) -> str:
    gap = None
    if is_rep.expectancy is not None and oos_rep.expectancy is not None:
        gap = round(oos_rep.expectancy - is_rep.expectancy, 3)
    lines = [
        "---", "type: 스윙백테스트하니스", f"날짜: {d}", "tags: [스윙, 백테스트, 검증]", "---",
        f"# 🧪 {title} · {d}",
        "> 규칙: 20일선 눌림 후 반등 진입 → 익절/손절·트레일링. 비용(수수료+슬리피지) 차감.",
        "> 판정은 **아웃오브샘플(OOS)** 기준. IS↔OOS 기대값 격차가 크면 과최적화 신호.", "",
        "| 구간 | 거래수 | 승률 | 기대값 | PF | MDD | Sharpe | 연거래 |",
        "|---|---|---|---|---|---|---|---|",
        _report_row("인샘플(IS)", is_rep),
        _report_row("아웃오브샘플(OOS)", oos_rep),
    ]
    if gap is not None:
        sign = "✅ 견고" if gap >= -0.2 else "⚠️ 과최적화 의심"
        lines += ["", f"**IS→OOS 기대값 격차**: {gap:+g}%p · {sign}"]
    return "\n".join(lines)
```

- [ ] **Step 4: render 통과 확인**

Run: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/test_harness_report.py -k render -q`
Expected: PASS

- [ ] **Step 5: `write_harness` + `run_harness` + CLI 추가**

`writer.py`의 `write_backtest` 바로 아래에 추가:

```python
    def write_harness(self, content: str, d: date | None = None) -> Path:
        d = d or date.today()
        path = self._path("backtests_dir", "Harness", d)
        path.write_text(content, encoding="utf-8")
        return path
```

`main.py` 끝(`__all__` 위)에 추가:

```python
def run_harness(cfg: Config) -> Path:
    """현재 로직 baseline 을 full 유니버스·장기 히스토리로 IS/OOS 측정 → 볼트+디스코드."""
    from .strategy import harness as _HN
    reader = VaultReader(cfg)
    provider = _HN.backtest_provider(cfg)
    market = str(cfg.get("backtest", "universe", default="all"))
    notes = [n for n in _load_notes(cfg, reader, None, market) if n.ticker]
    days = int(cfg.get("backtest", "lookback_days", default=500))
    trades = _HN.simulate_trades(cfg, provider, notes, days)
    is_t, oos_t = _HN.split_oos(trades, float(cfg.get("backtest", "oos_fraction", default=0.3)))
    is_rep, oos_rep = _HN.report_from_trades(is_t), _HN.report_from_trades(oos_t)
    writer = VaultWriter(cfg)
    md = _HN.render_report_md("기준 로직 성과 측정", is_rep, oos_rep, date.today().isoformat())
    path = writer.write_harness(md)
    from .notify import health as _H
    hz = _H.assess([provider.sources.get(n.ticker) for n in notes])
    if not hz.ok:
        _H.alert(cfg.creds.discord_webhook_url, "하니스 측정", hz.reason)
    from .notify.discord import notify
    floor = int(cfg.get("backtest", "min_oos_trades", default=100))
    guard = "" if oos_rep.n_trades >= floor else f" ⚠️표본부족(OOS {oos_rep.n_trades}<{floor})"
    notify(cfg.creds.discord_webhook_url,
           f"🧪 하니스 측정 — 종목 {len(notes)} · OOS 거래 {oos_rep.n_trades} · "
           f"기대값 IS {_HN._fmt(is_rep.expectancy)}→OOS {_HN._fmt(oos_rep.expectancy)} · "
           f"MDD {_HN._fmt(oos_rep.max_drawdown)}{guard}")
    log.info("harness → %s (종목 %d · OOS거래 %d · OOS기대값 %s)",
             path, len(notes), oos_rep.n_trades, oos_rep.expectancy)
    return path
```

`main.py`의 `__all__` 리스트에 `"run_harness"` 추가.

`cli.py`에서 서브파서 등록부에 추가(다른 `sub.add_parser(...)` 옆):

```python
    sub.add_parser("harness", help="현재 로직 IS/OOS 성과 측정(검증 하니스) → 볼트+디스코드")
```

그리고 디스패치부(`if args.cmd == "backtest":` 블록 아래)에 추가:

```python
    if args.cmd == "harness":
        path = M.run_harness(cfg)
        print(f"✅ 하니스 측정 → {path}")
        return 0
```

- [ ] **Step 6: 전체 테스트 + CLI 스모크(synthetic으로 fetch 회피)**

Run: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest -q`
Expected: 전체 PASS

Run(스모크, 볼트 안 건드리게 임시 출력 확인은 dry 점검 — 실제 fetch/쓰기는 사용자 합의 후):
`PYTHONUTF8=1 .venv/Scripts/python.exe -c "import swing_trader.main as M; print(hasattr(M,'run_harness'))"`
Expected: `True`

- [ ] **Step 7: 커밋**

```bash
git add src/swing_trader/strategy/harness.py src/swing_trader/obsidian/writer.py src/swing_trader/main.py src/swing_trader/cli.py tests/test_harness_report.py
git commit -m "feat(harness): render_report_md + run_harness + CLI harness(IS/OOS 측정 리포트)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review 결과

- **스펙 커버리지:** Phase 0 전 항목 매핑 — 데이터확장(Task3 backtest_provider+config·Task5 universe), 지표(Task2), OOS분할(Task3), A/B비교·표본가드(Task4), 옵시디언+디스코드 출력(Task5). Phase 1~4는 본 하니스를 **운영**하는 후속(별도 진행), Phase 3 진입 plug-in은 그때 소계획.
- **플레이스홀더:** 없음(모든 step에 실제 코드/명령).
- **타입 일관성:** `Trade`/`BacktestReport`/`ABResult` 필드명, `_resolve_params` 키, `_stock_trades` 시그니처가 Task 간 일치.
- **주의:** Task5 CLI 스모크는 실제 네트워크 fetch·볼트 쓰기를 **하지 않음**(존재 확인만). 실제 `swing-trader harness` 첫 실행은 사용자 합의 후(2년 fetch 레이트리밋·off-schedule 볼트 파일 생성 고려).
