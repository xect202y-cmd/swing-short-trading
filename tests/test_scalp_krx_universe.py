"""v5 오버나잇 상따(전시장) — 종가 매수→익일 시가 매도, 상한가 잠금 제외 검증."""
import numpy as np
import pandas as pd
import pytest

from swing_trader.scalp.krx_universe import v5_market_trades


def _panel_one(chg_pct=20.0, vol_ok=True, next_open_gap=5.0):
    n = 100
    close = np.full(n, 10000.0)
    vol = np.full(n, 1_000_000.0)
    close[-2] = 10000.0 * (1 + chg_pct / 100)   # D일 폭등(=신고가)
    if vol_ok:
        vol[-2] = 10_000_000.0                  # 20일 평균 ×10
    o = close * 1.0
    o[-1] = close[-2] * (1 + next_open_gap / 100)   # D+1 시가(청산가)
    close[-1] = o[-1]
    h = np.maximum(close, o) * 1.005
    l = np.minimum(close, o) * 0.995
    idx = pd.bdate_range("2026-01-05", periods=n)
    return {"000010": pd.DataFrame({"open": o, "high": h, "low": l,
                                    "close": close, "volume": vol}, index=idx)}


def test_v5_overnight_close_to_next_open():
    trades = v5_market_trades(_panel_one(), min_tv_eok=0)
    assert len(trades) == 1                     # 폭등 당일(D) 1건 — 진입일 기록
    t = trades[0]
    cost = 6.5 / 10000
    expected = (12000 * 1.05 * (1 - cost)) / (12000 * (1 + cost)) - 1
    assert t.ret == pytest.approx(expected, abs=1e-6)   # 종가→익일 시가 갭 수익


def test_v5_overnight_skips_limit_locked_close():
    # 상한가 잠금(+30%) 마감은 종가 매수 불가 → 제외
    assert v5_market_trades(_panel_one(chg_pct=30.0), min_tv_eok=0) == []


def test_v5_overnight_skips_without_volume_surge():
    assert v5_market_trades(_panel_one(vol_ok=False), min_tv_eok=0) == []


def test_v5_overnight_liquidity_floor():
    assert v5_market_trades(_panel_one(), min_tv_eok=1e9) == []
