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
