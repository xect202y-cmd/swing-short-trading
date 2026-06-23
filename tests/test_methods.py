"""매매 기법 신호규칙 정규화/매칭 테스트."""
from __future__ import annotations

import numpy as np
import pandas as pd

from swing_trader.strategy.methods import MethodMatcher, _normalize


def _df(n: int = 60) -> pd.DataFrame:
    close = np.linspace(100, 130, n)
    return pd.DataFrame({
        "open": close, "high": close * 1.01, "low": close * 0.99,
        "close": close, "volume": np.full(n, 1000.0),
    })


def test_normalize_skips_position_and_intraday_rules():
    df = _df()
    assert _normalize("if (price <= buy_price * 0.95 or price <= support_level) sell()", df) is None
    assert _normalize("price <= support_level & rsi < 30 & vol < ma_vol_20", df) is None
    assert _normalize("time > 14:30 & close > prev_high & volume > avg_vol_5min & rsi < 70", df) is None


def test_normalize_converts_operators_and_functions():
    df = _df()
    e = _normalize("close > ma20 & ma20 > ma60 & vol < vol_ma20 & low <= ma20 * 1.02", df)
    assert e is not None and " and " in e and "&" not in e and "VOL_MA20" in e
    e2 = _normalize("close > highest(high, 52) & volume > sma(volume, 20) & close > open", df)
    assert e2 is not None and "highest(" not in e2 and "sma(" not in e2  # 함수 → 수치 치환


def test_matcher_empty_is_safe():
    assert MethodMatcher([]).match(_df(), None) == []
