"""v5 오버나잇 상따 라이브 — 스캔 선별·정산(순수 함수) 검증."""
import numpy as np
import pandas as pd

from swing_trader.scalp.v5_live import V5Item, pick_candidates, settle_items


def _hist(days=100, base=10000.0, vol=1_000_000.0):
    idx = pd.bdate_range("2026-01-05", periods=days)
    return pd.DataFrame({"open": np.full(days, base), "high": np.full(days, base * 1.01),
                         "low": np.full(days, base * 0.99), "close": np.full(days, base),
                         "volume": np.full(days, vol)}, index=idx)


def _snapshot(rows):
    return pd.DataFrame(rows, columns=["Code", "Name", "Market", "Close",
                                       "ChagesRatio", "Volume", "Amount"])


def test_pick_candidates_filters_and_ranks():
    snap = _snapshot([
        ["000010", "대장주", "KOSPI", 12000.0, 20.0, 10_000_000.0, 100e8],
        ["000020", "이등주", "KOSDAQ", 11800.0, 18.0, 8_000_000.0, 80e8],
        ["000030", "거래량부족", "KOSPI", 11700.0, 17.0, 2_000_000.0, 70e8],   # 5배 미만
        ["000045", "우선주", "KOSPI", 11600.0, 19.0, 9_000_000.0, 90e8],       # 코드 끝 0 아님
        ["000050", "잠금임박", "KOSPI", 12900.0, 29.3, 9_000_000.0, 90e8],     # 29%↑ 제외
        ["000060", "대금부족", "KOSDAQ", 11500.0, 16.0, 9_000_000.0, 10e8],    # 50억 미만
    ])
    got = pick_candidates(snap, lambda c: _hist(), min_tv_eok=50)
    assert [g["ticker"] for g in got] == ["000010", "000020"]   # 등락률 내림차순 상한 2
    assert got[0]["vol_x"] == 10.0


def test_pick_candidates_rejects_non_new_high():
    hist = _hist()
    hist.iloc[-10, hist.columns.get_loc("close")] = 20000.0   # 60일 창 안 매물대
    snap = _snapshot([["000010", "매물대", "KOSPI", 12000.0, 20.0, 10_000_000.0, 100e8]])
    assert pick_candidates(snap, lambda c: hist, min_tv_eok=50) == []


def _bars_for_settle(limit_locked=False):
    # D-1(10000) → D(12000 = +20% 또는 +30% 잠금) → D+1(시가 12600)
    idx = pd.to_datetime(["2026-07-01", "2026-07-02", "2026-07-03"])
    d_close = 13000.0 if limit_locked else 12000.0
    return pd.DataFrame({"open": [10000.0, 10500.0, 12600.0],
                         "high": [10100.0, d_close, 12700.0],
                         "low": [9900.0, 10400.0, 12500.0],
                         "close": [10000.0, d_close, 12650.0],
                         "volume": [1e6, 1e7, 5e6]}, index=idx)


def test_settle_overnight_close_to_next_open():
    items = [V5Item(ticker="000010", name="대장주", qty=10, ref_price=12000.0, chg_pct=20.0)]
    rows, pnl, missing = settle_items(items, lambda c: _bars_for_settle(), "2026-07-02", cost=0.0)
    assert missing == []
    assert rows[0]["reason"] == "익일시가청산"
    assert rows[0]["entry"] == 12000.0 and rows[0]["exit"] == 12600.0
    assert pnl == (12600.0 - 12000.0) * 10


def test_settle_skips_limit_locked_close():
    items = [V5Item(ticker="000010", name="잠금", qty=10, ref_price=12900.0, chg_pct=29.3)]
    rows, pnl, missing = settle_items(items, lambda c: _bars_for_settle(limit_locked=True),
                                      "2026-07-02", cost=0.0)
    assert rows[0]["reason"].startswith("미체결") and pnl == 0.0


def test_settle_holds_until_next_open_arrives():
    bars = _bars_for_settle().iloc[:2]   # D+1 시가 미도착
    items = [V5Item(ticker="000010", name="대장주", qty=10, ref_price=12000.0, chg_pct=20.0)]
    rows, pnl, missing = settle_items(items, lambda c: bars, "2026-07-02", cost=0.0)
    assert missing == ["000010"] and rows == []
