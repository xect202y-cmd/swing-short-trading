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


def test_resolve_params_runner_derives_from_live_config():
    class _Cfg:
        def __init__(self, pe):
            self._pe = pe

        def get(self, *keys, default=None):
            return self._pe if keys == ("risk", "partial_exit_pct") else default

    # runner 미지정 → 라이브와 동기화: partial_exit_pct>0 이면 on, 0 이면 off
    assert BT._resolve_params(_Cfg(0.5))["runner"] is True
    assert BT._resolve_params(_Cfg(0.0))["runner"] is False
    # 명시 지정은 그대로 존중
    assert BT._resolve_params(_Cfg(0.5), runner=False)["runner"] is False
