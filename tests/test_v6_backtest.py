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


def test_bull_allows_and_tags_regime():
    # 상승추세 후 눌림→반등(20일선 눌림 setup 발생) — 검증된 진입 패턴.
    closes = [100 + i for i in range(60)] + [158, 154, 150, 146, 144, 147, 150, 152, 154, 156]
    df = _stock(closes)
    reg = {d.strftime("%Y-%m-%d"): Regime.BULL for d in df.index}
    trades, _ = _v6_entries_and_blocks(
        df, reg, take=0.06, default_stop=-0.025, take2=0.085, cost=0.0, min_tv_eok=0.0)
    assert len(trades) >= 1
    assert all(t[2] == "BULL" and t[3] >= 1 for t in trades)
