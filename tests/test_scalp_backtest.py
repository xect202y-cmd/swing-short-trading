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
