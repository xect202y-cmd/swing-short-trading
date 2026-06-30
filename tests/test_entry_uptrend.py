"""Phase3 진입 추세 필터 — 백테스트(_stock_trades) + 라이브(rules.buy_blocks) 일관성."""
import pandas as pd

from swing_trader.models import RiskLevel
from swing_trader.strategy import backtest as BT
from swing_trader.strategy import rules


def _df(closes, vol=5_000_000):
    n = len(closes)
    idx = pd.bdate_range(end="2026-06-26", periods=n)
    return pd.DataFrame(
        {"open": closes, "high": [c * 1.01 for c in closes], "low": [c * 0.99 for c in closes],
         "close": closes, "volume": [vol] * n}, index=idx,
    )


def _sawtooth(start, step_down, step_up, n):
    out, v = [], float(start)
    for k in range(n):
        v += -step_down if k % 2 == 0 else step_up
        out.append(round(v, 2))
    return out


def _trades(df, require_uptrend):
    return BT._stock_trades(df, take=0.06, stop=-0.025, max_hold=20, runner=True,
                            take2=0.085, trail=3.0, cost=0.0, min_tv_eok=0,
                            require_uptrend=require_uptrend)


def test_uptrend_filter_blocks_downtrend_entries():
    # 하락추세(net 하락) 톱니 — 반등 진입 후보는 있으나 종가<60일선이라 필터가 전부 제거.
    down = _df(_sawtooth(100, 2.0, 1.0, 90))
    assert len(_trades(down, require_uptrend=False)) > 0
    assert len(_trades(down, require_uptrend=True)) == 0


def test_uptrend_filter_keeps_uptrend_entries():
    # 상승추세 후 눌림→반등(종가>60일선 AND 20>60) — 필터가 베이스 진입을 그대로 유지.
    closes = [100 + i for i in range(60)] + [158, 154, 150, 146, 144, 147, 150, 152, 154, 156]
    up = _df(closes)
    base = _trades(up, require_uptrend=False)
    filt = _trades(up, require_uptrend=True)
    assert len(base) > 0
    assert len(filt) == len(base)


class _Tech:
    trading_value_eok = 100.0
    price = 110.0
    ma = {20: 105.0, 60: 100.0}   # 상승배열: price>ma60 AND ma20>ma60


class _Note:
    risk = ""
    recommendable = ""


def test_buy_blocks_uptrend_gate():
    lo = RiskLevel.LOW
    # 상승배열 충족 → 추세 차단 없음
    ok = rules.buy_blocks(_Note(), _Tech(), lo, lo, 30, require_uptrend=True)
    assert not any("추세" in x for x in ok)

    bad = _Tech()
    bad.price = 90.0             # 60일선(100) 하회 → 차단
    blocked = rules.buy_blocks(_Note(), bad, lo, lo, 30, require_uptrend=True)
    assert any("추세" in x for x in blocked)

    # 필터 off 면 추세 무관 통과
    assert not any("추세" in x for x in rules.buy_blocks(_Note(), bad, lo, lo, 30))
