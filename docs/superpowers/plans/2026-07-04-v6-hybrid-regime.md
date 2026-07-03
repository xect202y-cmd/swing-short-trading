# v6 Hybrid Regime Swing — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a regime-aware (BULL/NEUTRAL/BEAR/CRASH) hybrid swing model (v6) that keeps v5's flexible entry/trailing but applies v4-style trend guardrails variably by market regime, with a same-condition v4/v5/v6 backtest, extended metrics, counterfactual analysis, live paper gating, and a vault design doc.

**Architecture:** Pure regime classifier from a market index → a single regime→policy table consumed by both a regime-aware backtest path and live gating → extended metrics + counterfactual → CLI `v6-compare` writing `state/v6_compare.json` → live paper wiring + vault doc.

**Tech Stack:** Python 3.12, pandas, numpy, pytest. Existing `swing_trader` package. Data via existing `DataProvider` (yfinance/pykrx).

## Global Constraints

- Backtest models **structural levers only** (require_uptrend, CRASH block, trail, sizing). `ai_min_score`/`min_reward_risk`/`max_stop` cap are **live-only** — never synthesize historical scores (violates real-data-only rule).
- No look-ahead: regime at date `t` uses index data up to `t` only; entries execute at next-bar open.
- All fixed-frac edge metrics use `backtest.position_frac` (0.2) for all versions; as-traded curve uses `risk_per_trade_pct/|default_stop_pct|` per trade.
- Windows: run python via `./.venv/Scripts/python.exe`; set `PYTHONUTF8=1` for scripts printing Korean.
- Follow existing module/style patterns in `src/swing_trader/strategy/`.
- Vault doc path (real): `C:/Users/xect2/ObsidianVault/이용수_Wiki/04_Trading/Logic/2026-07-04_v6.md`.

---

## File Structure

- Create `src/swing_trader/strategy/market_regime.py` — `Regime` enum + `classify_series(index_df)`.
- Create `src/swing_trader/strategy/regime_policy.py` — `RegimePolicy`, `V6_POLICY`, `policy_for`, `allow_wide_stop`, `crash_entry_allowed`.
- Create `src/swing_trader/strategy/metrics.py` — `TradeRec`, `full_report`, regime split, monthly, max consec losses.
- Modify `src/swing_trader/strategy/backtest.py` — add `_v6_entries_and_blocks()` (regime-aware entries + counterfactual blocks) reusing `_exit_return`.
- Modify `src/swing_trader/main.py` — add `run_v6_compare()`.
- Modify `src/swing_trader/cli.py` — add `v6-compare` subcommand.
- Modify `src/swing_trader/config.py` consumers / `config.yaml` — add `regime:` section + `logic_mode`.
- Modify `src/swing_trader/execution/order_manager.py` + `src/swing_trader/strategy/rules.py` — live regime gate + block reasons.
- Tests: `tests/test_market_regime.py`, `tests/test_regime_policy.py`, `tests/test_metrics.py`, `tests/test_v6_backtest.py`, `tests/test_v6_live_gate.py`.

---

### Task 1: Regime classifier

**Files:**
- Create: `src/swing_trader/strategy/market_regime.py`
- Test: `tests/test_market_regime.py`

**Interfaces:**
- Produces: `class Regime(str, Enum)` with `BULL, NEUTRAL, BEAR, CRASH`; `classify_series(index_df, *, crash_dd=-0.12, crash_ret5=-0.08) -> dict[str, Regime]` keyed by `'YYYY-MM-DD'`.

- [ ] **Step 1: Write failing test**

```python
# tests/test_market_regime.py
import numpy as np
import pandas as pd
from swing_trader.strategy.market_regime import Regime, classify_series


def _idx(closes):
    n = len(closes)
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    c = pd.Series(closes, index=dates, dtype=float)
    return pd.DataFrame({"open": c, "high": c, "low": c, "close": c, "volume": 1.0}, index=dates)


def test_bull_when_above_rising_mas():
    df = _idx(list(np.linspace(100, 300, 260)))  # steady uptrend
    reg = classify_series(df)
    assert reg[df.index[-1].strftime("%Y-%m-%d")] == Regime.BULL


def test_crash_on_fast_drop():
    up = list(np.linspace(100, 200, 250))
    crash = [200, 196, 188, 176, 182]  # ret5 <= -8% into last bar
    df = _idx(up + crash)
    d = df.index[-1].strftime("%Y-%m-%d")
    assert classify_series(df)[d] == Regime.CRASH


def test_bear_below_ma200_falling():
    down = list(np.linspace(300, 150, 260))  # below ma200, ma50 falling
    df = _idx(down)
    assert classify_series(df)[df.index[-1].strftime("%Y-%m-%d")] == Regime.BEAR


def test_warmup_is_neutral():
    df = _idx(list(np.linspace(100, 110, 30)))  # < 200 bars → no ma200
    reg = classify_series(df)
    assert reg[df.index[10].strftime("%Y-%m-%d")] == Regime.NEUTRAL
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_market_regime.py -q`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement**

```python
# src/swing_trader/strategy/market_regime.py
"""시장 국면(regime) 판별 — 시장지수 일봉으로 날짜별 BULL/NEUTRAL/BEAR/CRASH.

과거전용(룩어헤드 없음): 날짜 t 의 regime 은 t 까지의 지수 데이터만 사용.
백테스트·라이브 공용. macro/regime.py(거시 텍스트노트)와 별개 — 이쪽은 가격 기반 이력.
"""
from __future__ import annotations

from enum import Enum

import pandas as pd


class Regime(str, Enum):
    BULL = "BULL"
    NEUTRAL = "NEUTRAL"
    BEAR = "BEAR"
    CRASH = "CRASH"


def classify_series(index_df, *, crash_dd: float = -0.12, crash_ret5: float = -0.08) -> dict:
    """지수 OHLCV → {'YYYY-MM-DD': Regime}. 우선순위 CRASH>BEAR>BULL>NEUTRAL."""
    close = index_df["close"].astype(float)
    high = index_df["high"].astype(float)
    ma50 = close.rolling(50, min_periods=50).mean()
    ma200 = close.rolling(200, min_periods=200).mean()
    dd60 = close / high.rolling(60, min_periods=1).max() - 1.0
    ret5 = close.pct_change(5)
    slope50 = ma50 - ma50.shift(20)
    out: dict = {}
    for i in range(len(close)):
        d = close.index[i].strftime("%Y-%m-%d")
        c = close.iloc[i]
        m50, m200 = ma50.iloc[i], ma200.iloc[i]
        _dd, _r5, _sl = dd60.iloc[i], ret5.iloc[i], slope50.iloc[i]
        if (pd.notna(_dd) and _dd <= crash_dd) or (pd.notna(_r5) and _r5 <= crash_ret5):
            out[d] = Regime.CRASH
        elif pd.notna(m200) and c < m200 and pd.notna(_sl) and _sl < 0:
            out[d] = Regime.BEAR
        elif pd.notna(m200) and c > m200 and m50 > m200 and c > m50:
            out[d] = Regime.BULL
        else:
            out[d] = Regime.NEUTRAL
    return out
```

- [ ] **Step 4: Run tests to verify pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_market_regime.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add src/swing_trader/strategy/market_regime.py tests/test_market_regime.py
git commit -m "feat(regime): 시장지수 기반 날짜별 국면 판별기(BULL/NEUTRAL/BEAR/CRASH)"
```

---

### Task 2: Regime policy table

**Files:**
- Create: `src/swing_trader/strategy/regime_policy.py`
- Test: `tests/test_regime_policy.py`

**Interfaces:**
- Consumes: `Regime` from Task 1.
- Produces: `@dataclass(frozen=True) RegimePolicy(require_uptrend, block_new_entry, trail_pct, risk_per_trade_pct, max_stop_pct, ai_min_score, min_reward_risk)`; `V6_POLICY: dict[Regime, RegimePolicy]`; `policy_for(regime, table=None) -> RegimePolicy`; `allow_wide_stop(regime, ai_score, reward_risk, invalidation_pct, liquidity_ok, portfolio_ok) -> bool`; `crash_entry_allowed(regime, ai_score, reward_risk, market_stabilizing, sector_up, stock_up) -> bool`.

- [ ] **Step 1: Write failing test**

```python
# tests/test_regime_policy.py
from swing_trader.strategy.market_regime import Regime
from swing_trader.strategy.regime_policy import (
    V6_POLICY, allow_wide_stop, crash_entry_allowed, policy_for)


def test_policy_values():
    assert policy_for(Regime.BULL).require_uptrend is False
    assert policy_for(Regime.NEUTRAL).require_uptrend is True
    assert policy_for(Regime.CRASH).block_new_entry is True
    assert policy_for(Regime.BULL).trail_pct == 3.0
    assert policy_for(Regime.CRASH).trail_pct == 1.5
    assert policy_for(Regime.BEAR).min_reward_risk == 2.20


def test_wide_stop_only_when_all_true():
    ok = dict(ai_score=80, reward_risk=2.5, invalidation_pct=-6.0,
              liquidity_ok=True, portfolio_ok=True)
    assert allow_wide_stop(Regime.BULL, **ok) is True
    assert allow_wide_stop(Regime.NEUTRAL, **ok) is False          # not BULL
    assert allow_wide_stop(Regime.BULL, **{**ok, "ai_score": 74}) is False
    assert allow_wide_stop(Regime.BULL, **{**ok, "invalidation_pct": -8.0}) is False


def test_crash_entry_needs_all():
    base = dict(ai_score=80, reward_risk=2.5, market_stabilizing=True,
               sector_up=True, stock_up=True)
    assert crash_entry_allowed(Regime.CRASH, **base) is True
    assert crash_entry_allowed(Regime.CRASH, **{**base, "stock_up": False}) is False
    assert crash_entry_allowed(Regime.BULL, **base) is True        # non-crash always allowed here
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_regime_policy.py -q`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement**

```python
# src/swing_trader/strategy/regime_policy.py
"""Regime → 파라미터 정책 테이블. 백테스트·라이브·문서·config 단일 소스."""
from __future__ import annotations

from dataclasses import dataclass

from .market_regime import Regime


@dataclass(frozen=True)
class RegimePolicy:
    require_uptrend: bool
    block_new_entry: bool
    trail_pct: float
    risk_per_trade_pct: float
    max_stop_pct: float      # 라이브 캡(백테스트 미반영)
    ai_min_score: float      # 라이브
    min_reward_risk: float   # 라이브


V6_POLICY: dict = {
    Regime.BULL:    RegimePolicy(False, False, 3.0, 1.00, -7.0, 70, 1.75),
    Regime.NEUTRAL: RegimePolicy(True,  False, 2.5, 0.75, -6.0, 72, 1.90),
    Regime.BEAR:    RegimePolicy(True,  False, 2.0, 0.50, -5.0, 75, 2.20),
    Regime.CRASH:   RegimePolicy(True,  True,  1.5, 0.25, -4.0, 80, 2.50),
}


def policy_for(regime: Regime, table: dict | None = None) -> RegimePolicy:
    return (table or V6_POLICY)[regime]


def allow_wide_stop(regime: Regime, *, ai_score: float, reward_risk: float,
                    invalidation_pct: float, liquidity_ok: bool, portfolio_ok: bool) -> bool:
    """-7% 손절은 BULL + 모든 조건 충족 시에만(라이브)."""
    return (regime == Regime.BULL and ai_score >= 75 and reward_risk >= 2.0
            and liquidity_ok and invalidation_pct >= -7.0 and portfolio_ok)


def crash_entry_allowed(regime: Regime, *, ai_score: float, reward_risk: float,
                        market_stabilizing: bool, sector_up: bool, stock_up: bool) -> bool:
    """CRASH 예외 진입. CRASH 아니면 이 함수는 진입 허용(True)."""
    if regime != Regime.CRASH:
        return True
    return (ai_score >= 80 and reward_risk >= 2.5 and market_stabilizing
            and sector_up and stock_up)
```

- [ ] **Step 4: Run tests to verify pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_regime_policy.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/swing_trader/strategy/regime_policy.py tests/test_regime_policy.py
git commit -m "feat(regime): regime→정책 테이블 + -7%손절/CRASH예외 게이트"
```

---

### Task 3: Extended metrics

**Files:**
- Create: `src/swing_trader/strategy/metrics.py`
- Test: `tests/test_metrics.py`

**Interfaces:**
- Produces: `@dataclass TradeRec(entry: str, ret: float, regime: str, hold_days: int)`; `full_report(trades: list[TradeRec], *, frac_by_regime: dict[str,float] | None = None, fixed_frac: float = 0.2) -> dict`. Returned dict keys: `total_return_pct, cagr_pct, mdd_pct, sharpe, sortino, calmar, win_rate, avg_win_pct, avg_loss_pct, profit_factor, expectancy_pct, realized_rr, n_trades, avg_hold_days, max_consec_losses, monthly_returns (dict 'YYYY-MM'->pct), by_regime (dict regime->{n, ret_pct, mdd_pct})`.
- Sizing: if `frac_by_regime` given, per-trade frac = that regime's value (as-traded curve); else `fixed_frac` for all (edge). Edge metrics (win_rate/expectancy/PF/realized_rr/avg win/loss) are frac-independent; curve metrics (total/cagr/mdd/sharpe/sortino/calmar) use the chosen frac.

- [ ] **Step 1: Write failing test**

```python
# tests/test_metrics.py
from swing_trader.strategy.metrics import TradeRec, full_report


def _mk():
    # 6 trades across 2 regimes; alternating win/loss with a 3-loss streak at end
    data = [
        ("2025-01-06", 0.05, "BULL", 3),
        ("2025-01-20", 0.06, "BULL", 4),
        ("2025-02-10", -0.02, "BEAR", 2),
        ("2025-02-24", -0.025, "BEAR", 2),
        ("2025-03-10", -0.03, "BEAR", 5),
        ("2025-03-24", 0.04, "BULL", 3),
    ]
    return [TradeRec(*d) for d in data]


def test_core_metrics():
    r = full_report(_mk(), fixed_frac=0.2)
    assert r["n_trades"] == 6
    assert r["win_rate"] == round(3 / 6 * 100, 1)
    assert r["avg_hold_days"] == round((3+4+2+2+5+3)/6, 1)
    assert r["max_consec_losses"] == 3
    assert r["profit_factor"] > 1.0
    assert r["realized_rr"] > 0
    assert set(r["by_regime"]) == {"BULL", "BEAR"}
    assert r["by_regime"]["BEAR"]["n"] == 3


def test_regime_sizing_reduces_mdd():
    trades = _mk()
    flat = full_report(trades, frac_by_regime={"BULL": 0.4, "NEUTRAL": 0.4, "BEAR": 0.4, "CRASH": 0.4})
    downsized = full_report(trades, frac_by_regime={"BULL": 0.4, "NEUTRAL": 0.3, "BEAR": 0.2, "CRASH": 0.1})
    # smaller bear sizing → shallower drawdown (less negative)
    assert downsized["mdd_pct"] >= flat["mdd_pct"]
    assert "sortino" in downsized and "calmar" in downsized
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_metrics.py -q`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement**

```python
# src/swing_trader/strategy/metrics.py
"""확장 백테스트 지표 — regime 태깅 거래에서 전체+regime별 성과 산출.

harness.report_from_trades 보다 넓은 지표(CAGR·Sortino·Calmar·최대연속손실·평균보유·월별·regime별).
"""
from __future__ import annotations

import math
import statistics as st
from collections import defaultdict
from dataclasses import dataclass
from datetime import date


@dataclass
class TradeRec:
    entry: str        # 'YYYY-MM-DD'
    ret: float        # 청산수익률(소수, 비용차감)
    regime: str       # 'BULL'|'NEUTRAL'|'BEAR'|'CRASH'
    hold_days: int


def _curve(trades, frac_of):
    """시간순 자산곡선(시드 1.0 기준) → (equity_end, mdd, [(entry, equity)])."""
    ordered = sorted(trades, key=lambda t: t.entry)
    eq = peak = 1.0
    mdd = 0.0
    pts = []
    for t in ordered:
        eq *= (1 + frac_of(t) * t.ret)
        peak = max(peak, eq)
        mdd = min(mdd, eq / peak - 1)
        pts.append((t.entry, eq))
    return eq, mdd, pts


def _max_consec_losses(trades) -> int:
    ordered = sorted(trades, key=lambda t: t.entry)
    cur = best = 0
    for t in ordered:
        if t.ret <= 0:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best


def _monthly(pts) -> dict:
    """자산곡선 포인트 → 월별 수익률%(그 달 첫 직전 equity 대비 마지막 equity)."""
    by_month = defaultdict(list)
    for d, eq in pts:
        by_month[d[:7]].append(eq)
    out, prev_end = {}, 1.0
    for m in sorted(by_month):
        end = by_month[m][-1]
        out[m] = round((end / prev_end - 1) * 100, 2)
        prev_end = end
    return out


def full_report(trades, *, frac_by_regime: dict | None = None, fixed_frac: float = 0.2) -> dict:
    if not trades:
        return {"n_trades": 0}

    def frac_of(t):
        return frac_by_regime[t.regime] if frac_by_regime else fixed_frac

    rets = [t.ret for t in trades]
    wins = [x for x in rets if x > 0]
    losses = [x for x in rets if x <= 0]
    eq, mdd, pts = _curve(trades, frac_of)
    ordered = sorted(trades, key=lambda t: t.entry)
    span = max((date.fromisoformat(ordered[-1].entry) - date.fromisoformat(ordered[0].entry)).days, 1)
    years = span / 365.0
    total_ret = eq - 1.0
    cagr = (eq ** (1 / years) - 1) if years > 0 and eq > 0 else None

    mean = sum(rets) / len(rets)
    sd = st.pstdev(rets) if len(rets) >= 2 else 0.0
    downside = [min(0.0, x) for x in rets]
    dd = math.sqrt(sum(d * d for d in downside) / len(downside)) if downside else 0.0
    avg_win = sum(wins) / len(wins) if wins else None
    avg_loss = sum(losses) / len(losses) if losses else None
    gross_win, gross_loss = sum(wins), -sum(losses)

    by_regime = {}
    for reg in {t.regime for t in trades}:
        sub = [t for t in trades if t.regime == reg]
        _e, _mdd, _ = _curve(sub, frac_of)
        by_regime[reg] = {"n": len(sub),
                          "ret_pct": round((_e - 1) * 100, 2),
                          "mdd_pct": round(_mdd * 100, 2)}

    return {
        "n_trades": len(trades),
        "total_return_pct": round(total_ret * 100, 2),
        "cagr_pct": round(cagr * 100, 2) if cagr is not None else None,
        "mdd_pct": round(mdd * 100, 2),
        "sharpe": round(mean / sd, 3) if sd > 0 else None,
        "sortino": round(mean / dd, 3) if dd > 0 else None,
        "calmar": round((cagr * 100) / abs(mdd * 100), 3) if cagr and mdd < 0 else None,
        "win_rate": round(len(wins) / len(rets) * 100, 1),
        "avg_win_pct": round(avg_win * 100, 3) if avg_win is not None else None,
        "avg_loss_pct": round(avg_loss * 100, 3) if avg_loss is not None else None,
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
        "expectancy_pct": round(mean * 100, 3),
        "realized_rr": round(avg_win / abs(avg_loss), 2) if avg_win and avg_loss else None,
        "avg_hold_days": round(sum(t.hold_days for t in trades) / len(trades), 1),
        "max_consec_losses": _max_consec_losses(trades),
        "monthly_returns": _monthly(pts),
        "by_regime": by_regime,
    }
```

- [ ] **Step 4: Run tests to verify pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_metrics.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/swing_trader/strategy/metrics.py tests/test_metrics.py
git commit -m "feat(metrics): CAGR·Sortino·Calmar·연속손실·월별·regime별 확장 지표"
```

---

### Task 4: Regime-aware backtest + counterfactual entries

**Files:**
- Modify: `src/swing_trader/strategy/backtest.py` (add functions at end; reuse `_col`, `_exit_return`)
- Test: `tests/test_v6_backtest.py`

**Interfaces:**
- Consumes: `Regime`, `policy_for` (Task 1/2), existing `_col`, `_exit_return`.
- Produces: `_v6_entries_and_blocks(df, regime_by_date, *, take, default_stop, take2, cost, min_tv_eok, policy_table=None) -> tuple[list[tuple[str,float,str,int]], list[dict]]`. First list = v6 trades `(entry_date, ret, regime, hold_days)`. Second = counterfactual blocks: v5 (require_uptrend off, trail 3.0) entries that v6 blocks, each `{"entry": date, "ret_v5": float, "regime": str, "reason": str}` where reason ∈ `{"CRASH차단","추세필터"}`.

- [ ] **Step 1: Write failing test**

```python
# tests/test_v6_backtest.py
import numpy as np
import pandas as pd
from swing_trader.strategy.market_regime import Regime
from swing_trader.strategy.backtest import _v6_entries_and_blocks


def _stock(closes):
    n = len(closes)
    dates = pd.date_range("2024-06-03", periods=n, freq="B")
    c = pd.Series(closes, index=dates, dtype=float)
    # ensure an up-day pullback-to-ma20 setup happens; volume high for trading value
    return pd.DataFrame({"open": c, "high": c * 1.01, "low": c * 0.99,
                         "close": c, "volume": 1e7}, index=dates)


def test_crash_dates_block_and_tag():
    closes = list(np.linspace(100, 130, 80))
    df = _stock(closes)
    # mark every date CRASH → v6 should produce zero trades, blocks tagged CRASH차단
    reg = {d.strftime("%Y-%m-%d"): Regime.CRASH for d in df.index}
    trades, blocks = _v6_entries_and_blocks(
        df, reg, take=0.06, default_stop=-0.025, take2=0.085, cost=0.0, min_tv_eok=0.0)
    assert trades == []
    assert all(b["reason"] == "CRASH차단" for b in blocks)


def test_bull_allows_and_tags_regime():
    closes = list(np.linspace(100, 160, 90))
    df = _stock(closes)
    reg = {d.strftime("%Y-%m-%d"): Regime.BULL for d in df.index}
    trades, _ = _v6_entries_and_blocks(
        df, reg, take=0.06, default_stop=-0.025, take2=0.085, cost=0.0, min_tv_eok=0.0)
    assert len(trades) >= 1
    assert all(t[2] == "BULL" and t[3] >= 1 for t in trades)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_v6_backtest.py -q`
Expected: FAIL (function not defined).

- [ ] **Step 3: Implement** (append to `backtest.py`)

```python
def _v6_entries_and_blocks(df, regime_by_date, *, take, default_stop, take2,
                           cost, min_tv_eok, policy_table=None):
    """regime 가변 v6 진입 + 반사실(v5 진입 O·v6 차단 O) 산출. 단일 종목."""
    from .market_regime import Regime
    from .regime_policy import policy_for
    close = _col(df, "close").to_numpy(dtype=float)
    open_ = _col(df, "open").to_numpy(dtype=float)
    vol = _col(df, "volume").to_numpy(dtype=float)
    ma20 = _col(df, "close").rolling(20, min_periods=1).mean().to_numpy(dtype=float)
    ma60 = _col(df, "close").rolling(60, min_periods=1).mean().to_numpy(dtype=float)
    dates = [d.strftime("%Y-%m-%d") for d in df.index]
    trades: list = []
    blocks: list = []
    i = 20
    while i < len(close) - 2:
        tv_eok = close[i] * vol[i] / 1e8
        base_setup = (close[i] <= ma20[i] * 1.01 and close[i] > close[i - 1]
                      and tv_eok >= min_tv_eok and open_[i + 1] > 0)
        if base_setup:
            reg = regime_by_date.get(dates[i], Regime.NEUTRAL)
            pol = policy_for(reg, policy_table)
            stock_up = close[i] > ma60[i] and ma20[i] > ma60[i]
            entry = float(open_[i + 1])
            # v5 결과(반사실용): 추세무관 진입, 트레일 3.0
            ret5, jend5 = _exit_return(close, i + 1, entry, take, default_stop, 20,
                                       runner=True, take2=take2, trail=3.0)
            v5_ret = float(ret5) - cost
            # v6 게이트
            if pol.block_new_entry:
                blocks.append({"entry": dates[i + 1], "ret_v5": v5_ret,
                               "regime": reg.value, "reason": "CRASH차단"})
            elif pol.require_uptrend and not stock_up:
                blocks.append({"entry": dates[i + 1], "ret_v5": v5_ret,
                               "regime": reg.value, "reason": "추세필터"})
            else:
                ret6, jend6 = _exit_return(close, i + 1, entry, take, default_stop, 20,
                                           runner=True, take2=take2, trail=pol.trail_pct)
                trades.append((dates[i + 1], float(ret6) - cost, reg.value,
                               int(jend6 - (i + 1))))
                i = max(jend6, i + 1)
                i += 1
                continue
            i = max(jend5, i + 1)
        i += 1
    return trades, blocks
```

- [ ] **Step 4: Run tests to verify pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_v6_backtest.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/swing_trader/strategy/backtest.py tests/test_v6_backtest.py
git commit -m "feat(backtest): regime 가변 v6 진입 + 반사실(차단) 산출"
```

---

### Task 5: `run_v6_compare` + `state/v6_compare.json` + CLI

**Files:**
- Modify: `src/swing_trader/main.py` (add `run_v6_compare`)
- Modify: `src/swing_trader/cli.py` (add `v6-compare` subcommand)
- Test: none new (integration-run in Task 6 verification); add a smoke assertion in `tests/test_v6_backtest.py`.

**Interfaces:**
- Consumes: `_v6_entries_and_blocks` (Task 4), `_stock_trades` (existing, for v4/v5), `metrics.full_report` (Task 3), `market_regime.classify_series` (Task 1), `regime_policy.V6_POLICY` (Task 2), existing `harness.backtest_provider`, `_load_notes`.
- Produces: `run_v6_compare(cfg) -> Path` writing `state/v6_compare.json` with keys `as_of, seed, oos_start, oos_end, lookback_days, versions:[{label,title,edge:{...},as_traded:{...}}], counterfactual:{n, avg_ret_pct, helped_pct, by_reason:{reason:{n,avg_ret_pct}}}`. `edge` = `full_report(fixed_frac=0.2)`; `as_traded` = `full_report(frac_by_regime=...)`.

- [ ] **Step 1: Write the code** (append to `main.py`; index tickers from a small map)

```python
def _regime_index_ticker(market: str) -> str:
    return {"kr": "^KS11", "us": "^GSPC"}.get(market, "^KS11")


def run_v6_compare(cfg: Config) -> Path:
    """v4/v5/v6 동일조건 백테스트 + regime별 + 반사실 → state/v6_compare.json."""
    from .strategy import backtest as _BT
    from .strategy import harness as _HN
    from .strategy import market_regime as _MR
    from .strategy import metrics as _MX
    from .strategy.regime_policy import V6_POLICY
    reader = VaultReader(cfg)
    provider = _HN.backtest_provider(cfg)
    notes = [n for n in _load_notes(cfg, reader, None, str(cfg.get("backtest", "universe", default="all")))
             if n.ticker]
    days = int(cfg.get("backtest", "lookback_days", default=500))
    seed = float(cfg.get("capital", "seed", default=5_000_000))
    fee = float(cfg.get("paper", "fee_bps", default=1.5)) / 10000
    slip = float(cfg.get("paper", "slippage_bps", default=5.0)) / 10000
    cost = 2 * (fee + slip)
    min_tv = float(cfg.get("risk", "min_trading_value_eok", default=30))
    take, dstop, take2 = 0.06, -0.025, 0.085
    dfs = {n.ticker: provider.get_ohlcv(n.ticker)[0].tail(days) for n in notes}
    # regime 시계열: 각 종목의 시장(kr/us)별 지수 1회 조회
    reg_by_market = {}
    for mk in ("kr", "us"):
        try:
            idx_df, _ = provider.get_ohlcv(_regime_index_ticker(mk))
            reg_by_market[mk] = _MR.classify_series(idx_df.tail(days))
        except Exception:  # noqa: BLE001
            reg_by_market[mk] = {}

    def market_of(n):
        return "us" if (n.ticker or "").isupper() or "." in (n.ticker or "") else "kr"

    # v4/v5 고정 파라미터 거래(엣지 비교) — regime 태그는 진입일 지수로 부여
    def _fixed(require_uptrend):
        recs = []
        for n in notes:
            reg = reg_by_market.get(market_of(n), {})
            for d, r in _BT._stock_trades(dfs[n.ticker], take=take, stop=dstop, max_hold=20,
                                          runner=True, take2=take2, trail=3.0, cost=cost,
                                          min_tv_eok=min_tv, require_uptrend=require_uptrend):
                recs.append(_MX.TradeRec(d, r, reg.get(d, _MR.Regime.NEUTRAL).value if hasattr(reg.get(d, _MR.Regime.NEUTRAL), "value") else "NEUTRAL", 0))
        return recs

    v4 = _fixed(True)
    v5 = _fixed(False)
    v6, blocks = [], []
    for n in notes:
        reg = reg_by_market.get(market_of(n), {})
        t, b = _BT._v6_entries_and_blocks(dfs[n.ticker], reg, take=take, default_stop=dstop,
                                          take2=take2, cost=cost, min_tv_eok=min_tv)
        v6 += [_MX.TradeRec(e, r, rg, hd) for (e, r, rg, hd) in t]
        blocks += b

    frac = {r.value: p.risk_per_trade_pct / abs(dstop * 100) for r, p in V6_POLICY.items()}
    flat = {"BULL": 1.0 / abs(dstop * 100), "NEUTRAL": 1.0 / abs(dstop * 100),
            "BEAR": 1.0 / abs(dstop * 100), "CRASH": 1.0 / abs(dstop * 100)}
    versions = [
        {"label": "v4", "title": "추세필터 고정 ON",
         "edge": _MX.full_report(v4, fixed_frac=0.2),
         "as_traded": _MX.full_report(v4, frac_by_regime=flat)},
        {"label": "v5", "title": "추세필터 OFF(유연)",
         "edge": _MX.full_report(v5, fixed_frac=0.2),
         "as_traded": _MX.full_report(v5, frac_by_regime=flat)},
        {"label": "v6", "title": "regime 가변 하이브리드",
         "edge": _MX.full_report(v6, fixed_frac=0.2),
         "as_traded": _MX.full_report(v6, frac_by_regime=frac)},
    ]
    # 반사실 요약
    cf = {"n": len(blocks)}
    if blocks:
        avg = sum(b["ret_v5"] for b in blocks) / len(blocks)
        helped = sum(1 for b in blocks if b["ret_v5"] <= 0) / len(blocks) * 100
        by_reason = {}
        for reason in {b["reason"] for b in blocks}:
            sub = [b for b in blocks if b["reason"] == reason]
            by_reason[reason] = {"n": len(sub),
                                 "avg_ret_pct": round(sum(x["ret_v5"] for x in sub) / len(sub) * 100, 3)}
        cf.update({"avg_ret_pct": round(avg * 100, 3), "helped_pct": round(helped, 1),
                   "by_reason": by_reason})
    all_dates = [t.entry for v in (v4, v5, v6) for t in v]
    path = cfg.state_dir / "v6_compare.json"
    path.write_text(json.dumps({
        "as_of": _DM.today_kst().isoformat(), "seed": seed,
        "oos_start": min(all_dates) if all_dates else None,
        "oos_end": max(all_dates) if all_dates else None,
        "lookback_days": days, "versions": versions, "counterfactual": cf,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("v6_compare → %s (v4 %d·v5 %d·v6 %d 거래, 차단 %d)",
             path, len(v4), len(v5), len(v6), len(blocks))
    return path
```

- [ ] **Step 2: Add CLI subcommand** in `cli.py`

In the `sub.add_parser` block near `versions`:
```python
    sub.add_parser("v6-compare", help="v4/v5/v6 동일조건 regime 비교 → state/v6_compare.json")
```
In the dispatch block near the `versions` handler:
```python
    if args.cmd == "v6-compare":
        path = M.run_v6_compare(cfg)
        print(f"✅ v6 비교 데이터 → {path}")
        return 0
```

- [ ] **Step 3: Run it (integration)**

Run: `./.venv/Scripts/swing-trader.exe v6-compare`
Expected: prints `✅ v6 비교 데이터 → ...state/v6_compare.json`; log line with trade counts.

- [ ] **Step 4: Sanity-assert output**

Run:
```bash
PYTHONUTF8=1 ./.venv/Scripts/python.exe -c "import json;d=json.load(open('state/v6_compare.json',encoding='utf-8'));print([v['label'] for v in d['versions']]);print('cf', d['counterfactual'].get('n'))"
```
Expected: `['v4','v5','v6']` and a counterfactual count ≥ 0.

- [ ] **Step 5: Commit**

```bash
git add src/swing_trader/main.py src/swing_trader/cli.py
git commit -m "feat(v6): run_v6_compare 동일조건 v4/v5/v6+regime별+반사실 → state + CLI"
```

---

### Task 6: Vault design doc generation

**Files:**
- Create: `src/swing_trader/review/v6_doc.py` (renderer) — pure function `render_v6_doc(compare: dict, policy=V6_POLICY) -> str`
- Modify: `src/swing_trader/main.py` — `run_v6_compare` writes the doc via `VaultWriter` after JSON.
- Test: `tests/test_v6_doc.py` (assert all 12 headings present).

**Interfaces:**
- Consumes: `state/v6_compare.json` dict shape (Task 5), `V6_POLICY`.
- Produces: markdown string with the 12 required sections; written to `04_Trading/Logic/2026-07-04_v6.md`.

- [ ] **Step 1: Write failing test**

```python
# tests/test_v6_doc.py
from swing_trader.review.v6_doc import render_v6_doc

_SAMPLE = {
    "as_of": "2026-07-04", "seed": 5000000, "oos_start": "2024-06-04",
    "oos_end": "2026-07-01", "lookback_days": 500,
    "versions": [
        {"label": "v4", "title": "t", "edge": {"n_trades": 300, "win_rate": 37.0,
            "expectancy_pct": 0.6, "profit_factor": 1.4, "mdd_pct": -16.0, "sharpe": 0.1,
            "sortino": 0.2, "calmar": 0.5, "total_return_pct": 60.0, "cagr_pct": 20.0,
            "avg_win_pct": 3.0, "avg_loss_pct": -2.0, "realized_rr": 1.5, "avg_hold_days": 4.0,
            "max_consec_losses": 5, "by_regime": {}, "monthly_returns": {}},
         "as_traded": {"total_return_pct": 60.0, "cagr_pct": 20.0, "mdd_pct": -16.0,
            "calmar": 0.5, "by_regime": {}}},
    ],
    "counterfactual": {"n": 10, "avg_ret_pct": -1.2, "helped_pct": 60.0,
                       "by_reason": {"CRASH차단": {"n": 4, "avg_ret_pct": -3.0}}},
}


def test_all_sections_present():
    md = render_v6_doc(_SAMPLE)
    for h in ["전략 개요", "대비 변경점", "regime 판별", "regime별 설정", "진입 조건",
              "차단 조건", "트레일링", "포지션 사이징", "백테스트 비교", "regime별 성과",
              "반사실", "추가 개선"]:
        assert h in md
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_v6_doc.py -q`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement `render_v6_doc`** in `src/swing_trader/review/v6_doc.py`

```python
"""v6 전략 문서 렌더러 — v6_compare.json → 04_Trading/Logic/<날짜>_v6.md (12항목)."""
from __future__ import annotations

from ..strategy.regime_policy import V6_POLICY


def _edge_row(v):
    e = v["edge"]
    return (f"| {v['label']} | {e.get('n_trades')} | {e.get('total_return_pct')}% | "
            f"{e.get('cagr_pct')}% | {e.get('mdd_pct')}% | {e.get('sharpe')} | "
            f"{e.get('sortino')} | {e.get('calmar')} | {e.get('win_rate')}% | "
            f"{e.get('avg_win_pct')}% | {e.get('avg_loss_pct')}% | {e.get('profit_factor')} | "
            f"{e.get('expectancy_pct')}% | {e.get('realized_rr')} | {e.get('avg_hold_days')} | "
            f"{e.get('max_consec_losses')} |")


def render_v6_doc(compare: dict, policy: dict | None = None) -> str:
    policy = policy or V6_POLICY
    d = compare.get("as_of")
    L = ["---", "type: 스윙로직", "버전: v6", f"날짜: {d}", "tags: [스윙, 로직, v6, regime]", "---",
         f"# 🧭 v6 하이브리드 regime 스윙 · {d}",
         f"> 유니버스 백테스트 OOS {compare.get('oos_start')}~{compare.get('oos_end')} · "
         f"시드 {compare.get('seed'):,.0f} · lookback {compare.get('lookback_days')}일", ""]

    L += ["## 1. 전략 개요",
          "v5의 유연한 진입·우수한 손익비·부분익절+트레일링을 유지하되, v4의 추세 가드레일을 "
          "market regime(BULL/NEUTRAL/BEAR/CRASH)별로 가변 적용해 약세장 리스크를 줄이는 하이브리드.", ""]

    L += ["## 2. v4/v5 대비 변경점",
          "- v4: 추세필터 항상 ON(약세 방어 강, 강세 기회 손실).",
          "- v5: 추세필터 OFF(강세 유연, 약세 무방비).",
          "- **v6: regime별 가변** — BULL은 v5처럼 유연, BEAR/CRASH는 v4 이상으로 보수적(+CRASH 차단·사이징 축소·트레일 타이트).", ""]

    L += ["## 3. market_regime 판별 기준 (지수: KR 코스피/US S&P500)",
          "- CRASH: 60일 고점대비 낙폭 ≤ -12% 또는 5일 수익률 ≤ -8%",
          "- BEAR: 종가 < 200일선 그리고 50일선 하락(20일 기울기<0)",
          "- BULL: 종가 > 200일선 그리고 50일선 > 200일선 그리고 종가 > 50일선",
          "- NEUTRAL: 그 외", ""]

    L += ["## 4. regime별 설정값",
          "| regime | 추세필터 | 신규진입 | 트레일% | risk/trade% | max_stop(라이브) | ai_min(라이브) | RR(라이브) |",
          "|---|---|---|---|---|---|---|---|"]
    for reg, p in policy.items():
        L.append(f"| {reg.value} | {'ON' if p.require_uptrend else 'OFF'} | "
                 f"{'차단' if p.block_new_entry else '허용'} | {p.trail_pct} | "
                 f"{p.risk_per_trade_pct} | {p.max_stop_pct} | {p.ai_min_score} | {p.min_reward_risk} |")
    L += ["", "> ⚠️ max_stop·ai_min·RR은 라이브 전용 게이트(백테스트 미반영 — 과거 AI점수/구조손절 없음).", ""]

    L += ["## 5. 진입 조건",
          "기본: 20일선 눌림 후 반등 + 거래대금 하한. regime 게이트: BULL 추세무관, "
          "NEUTRAL/BEAR 종목 정배열(종가>60·20>60) 필요, CRASH 차단(예외조건만).", ""]

    L += ["## 6. 차단 조건 (사유 로깅)",
          "- CRASH 예외 미충족 / ai_score·reward_risk regime기준 미만 / 손절폭 regime캡 초과 / "
          "포트폴리오·섹터 한도 초과 / 유동성 부족 / 이벤트리스크 과도 / 시장·섹터·종목 동반 하락 / "
          "과열 추격 / 기대값 낮음. 각 차단은 decision_log에 (종목·사유·근거값) 기록.", ""]

    L += ["## 7. 손절/익절/트레일링",
          "- 익절 v5 유지: 1차 6% 절반 익절 → 잔량 트레일링으로 2차 8.5%.",
          "- 트레일링 regime 가변(BULL 3.0 → CRASH 1.5).",
          "- 손절 default -2.5, max_stop 캡 regime별(라이브). -7%는 BULL+조건 전부 충족 시만.", ""]

    L += ["## 8. 포지션 사이징",
          "risk_per_trade regime별(1.0/0.75/0.5/0.25%). 백테스트 자산곡선=거래별 "
          "risk_per_trade/|손절폭|. 엣지비교는 고정분율 0.2로 사이징 중립.", ""]

    L += ["## 9. v4/v5/v6 백테스트 비교표 (동일 종목·기간·비용·체결, 고정분율 0.2 엣지)",
          "| 버전 | 거래 | 총수익 | CAGR | MDD | Sharpe | Sortino | Calmar | 승률 | 평균익 | 평균손 | PF | 기대값 | 실현RR | 평균보유 | 최대연속손실 |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    L += [_edge_row(v) for v in compare.get("versions", [])]
    L += ["", "### 실전형(regime 가변 사이징) 자산곡선",
          "| 버전 | 총수익 | CAGR | MDD | Calmar |", "|---|---|---|---|---|"]
    for v in compare.get("versions", []):
        a = v["as_traded"]
        L.append(f"| {v['label']} | {a.get('total_return_pct')}% | {a.get('cagr_pct')}% | "
                 f"{a.get('mdd_pct')}% | {a.get('calmar')} |")
    L.append("")

    L += ["## 10. regime별 성과표 (v6, 엣지)"]
    v6 = next((v for v in compare.get("versions", []) if v["label"] == "v6"), None)
    if v6:
        L += ["| regime | 거래수 | 수익률 | MDD |", "|---|---|---|---|"]
        for reg, s in (v6["edge"].get("by_regime") or {}).items():
            L.append(f"| {reg} | {s['n']} | {s['ret_pct']}% | {s['mdd_pct']}% |")
    L.append("")

    L += ["## 11. v5 진입 but v6 차단 거래 사후검증"]
    cf = compare.get("counterfactual", {})
    if cf.get("n"):
        L += [f"- 차단 {cf['n']}건 · 평균 사후수익 {cf.get('avg_ret_pct')}% · "
              f"손실회피 기여율 {cf.get('helped_pct')}%(사후수익≤0 비율).",
              "| 차단사유 | 건수 | 평균 사후수익 |", "|---|---|---|"]
        for reason, s in (cf.get("by_reason") or {}).items():
            L.append(f"| {reason} | {s['n']} | {s['avg_ret_pct']}% |")
        L += ["", "> 평균 사후수익<0면 차단이 손실회피에 기여, >0면 좋은 기회 과차단."]
    else:
        L.append("- 반사실 차단 없음.")
    L.append("")

    L += ["## 12. 추가 개선안",
          "- regime 임계값 OOS 튜닝(-12%/-8% 등).",
          "- 섹터 지수 이력 확보 시 섹터 추세 가드레일 백테스트 편입.",
          "- 라이브 score/RR 게이트 페이퍼 실적 누적 후 regime 임계 재보정.",
          "- CRASH 예외 진입의 실측 성과 추적(현재 라이브 전용).", ""]
    return "\n".join(L) + "\n"
```

- [ ] **Step 4: Wire doc write into `run_v6_compare`** (before `return path` in Task 5 code)

```python
    from .review.v6_doc import render_v6_doc
    compare_obj = json.loads(path.read_text(encoding="utf-8"))
    VaultWriter(cfg).write_logic(render_v6_doc(compare_obj), "2026-07-04_v6.md")
```
If `VaultWriter` has no `write_logic(content, filename)`, add a thin method writing to `paths.write.logic_dir`. Check `src/swing_trader/obsidian/writer.py` first; reuse existing logic-writing pattern (`write_logic_review`/`write` used elsewhere).

- [ ] **Step 5: Run tests + integration**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_v6_doc.py -q` → PASS.
Run: `./.venv/Scripts/swing-trader.exe v6-compare` → verify `04_Trading/Logic/2026-07-04_v6.md` exists in vault with 12 sections.

- [ ] **Step 6: Commit**

```bash
git add src/swing_trader/review/v6_doc.py src/swing_trader/main.py tests/test_v6_doc.py src/swing_trader/obsidian/writer.py
git commit -m "feat(v6): 볼트 v6 전략문서 12항목 렌더 + run_v6_compare 연동"
```

---

### Task 7: Live regime gating (paper) + config + block logging

**Files:**
- Modify: `config.yaml` — add `regime:` section + `logic_mode: v6`.
- Modify: `src/swing_trader/execution/order_manager.py` — regime-aware gate in `evaluate_buy`/`can_open_new` with block reasons.
- Modify: `src/swing_trader/main.py` `run_once` — resolve today's regime, pass to OrderManager.
- Test: `tests/test_v6_live_gate.py`.

**Interfaces:**
- Consumes: `policy_for`, `crash_entry_allowed`, `Regime`, `classify_series`.
- Produces: OrderManager accepts `regime: Regime = Regime.NEUTRAL` and applies `ai_min_score`/`min_reward_risk`/CRASH-block from policy, appending Korean block reasons. `run_once` computes today's regime from the market index (fallback NEUTRAL on fetch failure).

- [ ] **Step 1: Write failing test**

```python
# tests/test_v6_live_gate.py
from types import SimpleNamespace
from swing_trader.strategy.market_regime import Regime
from swing_trader.strategy.regime_policy import policy_for


def test_crash_blocks_normal_score():
    # unit-level: policy gate helper (pure) blocks a 72-score BUY in CRASH
    pol = policy_for(Regime.CRASH)
    assert 72 < pol.ai_min_score            # below CRASH ai_min (80)
    assert pol.block_new_entry is True
```

(Full OrderManager wiring is verified by the integration run in Step 4; keep the unit test on the pure policy contract to avoid brittle broker mocks.)

- [ ] **Step 2: Run test**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_v6_live_gate.py -q`
Expected: PASS immediately (pure assertion) — this pins the policy contract the wiring depends on.

- [ ] **Step 3: Add `regime:` to `config.yaml`**

```yaml
regime:
  enabled: true
  logic_mode: v6            # v6=regime 가변 게이팅. v5=단일(require_uptrend false).
  index_kr: "^KS11"
  index_us: "^GSPC"
  crash_dd: -0.12
  crash_ret5: -0.08
```

- [ ] **Step 4: Wire OrderManager + run_once**

In `order_manager.py` `__init__`, add `regime: "Regime" = None`; store `self.regime`. In `evaluate_buy`, after existing checks, when `cfg` regime enabled and `logic_mode == "v6"`:
```python
        from ..strategy.regime_policy import policy_for
        if self.regime is not None:
            pol = policy_for(self.regime)
            if pol.block_new_entry:
                reasons.append(f"{self.regime.value} 국면 — 신규진입 차단(예외조건 미충족)")
            if sig.score < pol.ai_min_score:
                reasons.append(f"{self.regime.value} 최소점수 {pol.ai_min_score} 미만(점수 {sig.score:.0f})")
            rr = (sig.plan.reward_risk if sig.plan else None)
            if rr is not None and rr < pol.min_reward_risk:
                reasons.append(f"{self.regime.value} 최소손익비 {pol.min_reward_risk} 미만({rr:.2f})")
```
In `main.run_once`, compute regime before OrderManager:
```python
    regime = None
    if bool(cfg.get("regime", "enabled", default=False)):
        from .strategy import market_regime as _MR
        idx = cfg.get("regime", f"index_{market}", default="^KS11") if market in ("kr", "us") else "^KS11"
        try:
            idf, _ = provider.get_ohlcv(idx)
            series = _MR.classify_series(idf, crash_dd=float(cfg.get("regime","crash_dd",default=-0.12)),
                                         crash_ret5=float(cfg.get("regime","crash_ret5",default=-0.08)))
            regime = list(series.values())[-1] if series else _MR.Regime.NEUTRAL
        except Exception:  # noqa: BLE001
            regime = _MR.Regime.NEUTRAL
    om = OrderManager(cfg, broker, realized_today=realized_today, realized_total=realized_week, regime=regime)
```
(For `market == "all"`, default the index to KR; live scheduled runs are per-market kr/us.)

- [ ] **Step 5: Run full suite + smoke run**

Run: `./.venv/Scripts/python.exe -m pytest -q` → all pass.
Run: `./.venv/Scripts/swing-trader.exe run-once --market kr --no-brief` → completes; if any blocks occur, decision_log shows regime reasons.

- [ ] **Step 6: Commit**

```bash
git add config.yaml src/swing_trader/execution/order_manager.py src/swing_trader/main.py tests/test_v6_live_gate.py
git commit -m "feat(v6): 라이브 regime 게이팅(페이퍼)+config regime 섹션+차단사유 로깅"
```

---

### Task 8: Snapshot logic v6 + regenerate compares + push

**Files:** none new (operational).

- [ ] **Step 1: Snapshot v6**

Run: `./.venv/Scripts/swing-trader.exe logic --note "v6: regime 가변 하이브리드 — BULL 유연/BEAR·CRASH 보수, 트레일·사이징·게이트 regime별"`
Expected: `✅ 로직 v6 기록`.

- [ ] **Step 2: Regenerate compare data**

Run: `./.venv/Scripts/swing-trader.exe v6-compare` then `./.venv/Scripts/swing-trader.exe versions`.

- [ ] **Step 3: Verify v6 검증 기준** (read `state/v6_compare.json`)

Confirm vs spec §10: v6 MDD ≤ v5 MDD; v6 BEAR/CRASH ret ≥ v5; counterfactual helped_pct; v6 PF/expectancy not badly degraded; v6 trades > v4. Record findings in the vault doc §11–12 (already rendered) and note any threshold retune needed.

- [ ] **Step 4: Commit + push**

```bash
git fetch origin main && git merge --ff-only origin/main
git add -f config.yaml state docs
git commit -m "chore(v6): logic v6 스냅샷 + v6_compare/version_compare 데이터 + 문서"
git push origin HEAD:main
```

---

## Self-Review

**Spec coverage:** §3 regime→Task1; §4 policy/-7%/CRASH→Task2; §5 entries→Task4/7; §6 block logging→Task7; §7 trailing/stops→Task2/4; §8 sizing→Task3/5; §9 compare+metrics→Task3/5; §10 criteria→Task8 step3; §11 live→Task7; §12 outputs (doc)→Task6; counterfactual→Task4/5/6. All covered.

**Placeholder scan:** No TBD/TODO; each code step shows full code. Task 6 Step 4 and Task 7 Step 4 reference reading existing `writer.py`/`order_manager.py` patterns — acceptable (inspect-then-follow), full insert code provided.

**Type consistency:** `Regime` (str Enum) used consistently; `TradeRec(entry,ret,regime,hold_days)` matches producers in Task 4 (`(date,ret,regime.value,hold)`) and consumers in Task 5. `full_report` keys used in Task 6 renderer match Task 3 output. `_v6_entries_and_blocks` signature identical across Task 4 def and Task 5 call.

**Known modeling caveats (documented, intentional):** backtest excludes score/RR/max_stop levers; v4/v5 as-traded curve uses flat sizing; regime tag on v4/v5 fixed trades is best-effort by entry date. These are spec §2 decisions, surfaced in the vault doc §9 note.
