import numpy as np
import pandas as pd
from swing_trader.strategy.market_regime import Regime
from swing_trader.strategy.backtest import _v6_entries_and_blocks


def _stock(closes):
    n = len(closes)
    dates = pd.date_range("2024-06-03", periods=n, freq="B")
    c = pd.Series(closes, index=dates, dtype=float)
    return pd.DataFrame({"open": c, "high": c * 1.01, "low": c * 0.99,
                         "close": c, "volume": 1e7}, index=dates)


def test_crash_dates_block_and_tag():
    closes = list(np.linspace(100, 130, 80))
    df = _stock(closes)
    reg = {d.strftime("%Y-%m-%d"): Regime.CRASH for d in df.index}
    trades, blocks = _v6_entries_and_blocks(
        df, reg, take=0.06, default_stop=-0.025, take2=0.085, cost=0.0, min_tv_eok=0.0)
    assert trades == []
    assert all(b["reason"] == "CRASH차단" for b in blocks)


def _v5_entry_dates(df):
    from swing_trader.strategy.backtest import _stock_trades
    return [e for e, _ in _stock_trades(df, take=0.06, stop=-0.025, max_hold=20, runner=True,
                                        take2=0.085, trail=3.0, cost=0.0, min_tv_eok=0.0,
                                        require_uptrend=False)]


def test_blocks_are_clean_subset_and_v6_scans_past_crash():
    # v5 진입은 bar 66, 116. 앞(CRASH)은 차단, 뒤(BULL)는 v6 진입 → blind-window 없이 계속 스캔.
    seg = [158, 154, 150, 146, 144, 147, 150, 152, 154, 156]
    closes = ([100 + i for i in range(60)] + seg + [156 + i for i in range(40)]
              + [194, 190, 186, 182, 180, 183, 186, 188, 190, 192])
    df = _stock(closes)
    reg = {d.strftime("%Y-%m-%d"): (Regime.CRASH if k < 90 else Regime.BULL)
           for k, d in enumerate(df.index)}
    trades, blocks = _v6_entries_and_blocks(
        df, reg, take=0.06, default_stop=-0.025, take2=0.085, cost=0.0, min_tv_eok=0.0)
    v5_dates = set(_v5_entry_dates(df))
    # 핵심(I4): 반사실 차단은 v5 진입 집합의 정확한 부분집합.
    assert {b["entry"] for b in blocks} <= v5_dates
    assert any(b["reason"] == "CRASH차단" for b in blocks)   # 앞 CRASH 진입 차단 기록
    assert all(t[2] != "CRASH" for t in trades)              # CRASH 진입 없음
    assert any(t[2] == "BULL" for t in trades)               # 뒤 BULL setup 을 실제로 잡음(스캔 지속)


def test_bull_allows_and_tags_regime():
    # 상승추세 후 눌림→반등(20일선 눌림 setup 발생) — 검증된 진입 패턴.
    closes = [100 + i for i in range(60)] + [158, 154, 150, 146, 144, 147, 150, 152, 154, 156]
    df = _stock(closes)
    reg = {d.strftime("%Y-%m-%d"): Regime.BULL for d in df.index}
    trades, _ = _v6_entries_and_blocks(
        df, reg, take=0.06, default_stop=-0.025, take2=0.085, cost=0.0, min_tv_eok=0.0)
    assert len(trades) >= 1
    assert all(t[2] == "BULL" and t[3] >= 1 for t in trades)
