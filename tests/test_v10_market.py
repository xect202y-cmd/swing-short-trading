"""v10 시황 게이트 + per-ticker/전시장 오케스트레이션."""
import numpy as np
import pandas as pd

from swing_trader.strategy import v10_new_high as v10
from swing_trader.strategy.harness import Trade


def test_regime_ok_by_market():
    kospi_up = {"2026-07-08", "2026-07-09"}
    kosdaq_up = {"2026-07-09"}
    assert v10.regime_ok("KOSPI", "2026-07-09", kospi_up, kosdaq_up) is True
    assert v10.regime_ok("KOSPI", "2026-07-07", kospi_up, kosdaq_up) is False
    assert v10.regime_ok("KOSDAQ", "2026-07-08", kospi_up, kosdaq_up) is False
    assert v10.regime_ok("KOSDAQ", "2026-07-09", kospi_up, kosdaq_up) is True


def test_regime_ok_fail_open_when_no_data():
    assert v10.regime_ok("KOSDAQ", "2026-07-09", None, None) is True


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
