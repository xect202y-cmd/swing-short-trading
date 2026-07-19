"""v5 오버나잇 상따 라이브 — 스캔 선별·정산(순수 함수) 검증."""
from datetime import date

import numpy as np
import pandas as pd

from swing_trader.scalp import v5_live
from swing_trader.scalp.v5_live import V5Item, _idle_status, pick_candidates, settle_items


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


# --- is_trading_day (P3-2c) — pykrx 캘린더 → FDR 직전영업일 폴백 → 평일 페일세이프 ---

def _bar_on(d: str) -> pd.DataFrame:
    return pd.DataFrame({"close": [100.0]}, index=pd.to_datetime([d]))


def test_is_trading_day_weekend_short_circuits_before_any_lookup(monkeypatch):
    def _boom(_d):
        raise AssertionError("주말은 캘린더 조회를 하면 안 된다")
    monkeypatch.setattr(v5_live, "_krx_business_day", _boom)
    assert v5_live.is_trading_day(date(2026, 7, 18)) is False   # 토요일


def test_is_trading_day_uses_pykrx_calendar_result_directly(monkeypatch):
    monkeypatch.setattr(v5_live, "_krx_business_day", lambda d: False)   # 캘린더상 휴장일
    assert v5_live.is_trading_day(date(2026, 7, 17)) is False   # 평일이지만 캘린더가 휴장 판정


def test_is_trading_day_accepts_delayed_publish_of_today(monkeypatch):
    monkeypatch.setattr(v5_live, "_krx_business_day", lambda d: None)   # pykrx 실패 → 폴백
    today = date(2026, 7, 17)   # 금요일
    monkeypatch.setattr(v5_live, "_fdr_hist", lambda code, days=10: _bar_on("2026-07-17"))
    assert v5_live.is_trading_day(today) is True


def test_is_trading_day_accepts_previous_business_day_when_today_bar_missing(monkeypatch):
    # 재현: 15:05 스캔인데 FDR이 오늘 일봉을 아직 발행 안 함(직전 영업일 봉만 존재) — 07-17 실오판.
    monkeypatch.setattr(v5_live, "_krx_business_day", lambda d: None)
    today = date(2026, 7, 17)   # 금요일 — 직전 영업일 = 목요일(07-16)
    monkeypatch.setattr(v5_live, "_fdr_hist", lambda code, days=10: _bar_on("2026-07-16"))
    assert v5_live.is_trading_day(today) is True


def test_is_trading_day_previous_business_day_skips_weekend(monkeypatch):
    monkeypatch.setattr(v5_live, "_krx_business_day", lambda d: None)
    today = date(2026, 7, 20)   # 월요일 — 직전 영업일 = 07-17(금), 주말 제외
    monkeypatch.setattr(v5_live, "_fdr_hist", lambda code, days=10: _bar_on("2026-07-17"))
    assert v5_live.is_trading_day(today) is True


def test_is_trading_day_rejects_stale_data_beyond_previous_business_day(monkeypatch):
    monkeypatch.setattr(v5_live, "_krx_business_day", lambda d: None)
    today = date(2026, 7, 17)
    monkeypatch.setattr(v5_live, "_fdr_hist", lambda code, days=10: _bar_on("2026-07-10"))   # 훨씬 오래된 봉
    assert v5_live.is_trading_day(today) is False


def test_is_trading_day_failsafe_when_all_external_calls_fail(monkeypatch):
    monkeypatch.setattr(v5_live, "_krx_business_day", lambda d: None)
    monkeypatch.setattr(v5_live, "_fdr_hist", lambda code, days=10: None)
    assert v5_live.is_trading_day(date(2026, 7, 17)) is True   # 평일 페일세이프(비거래일 오판 방지)


# --- _idle_status (P3-2a) — 무신호 대기 사유 매핑 ---

def test_idle_status_non_trading_day():
    assert _idle_status(False, None, [], True, False) == ("non_trading_day", "비거래일 — 스캔 생략")


def test_idle_status_none_when_plan_already_exists():
    assert _idle_status(True, [{"ticker": "000010"}], [], False, False) == (None, None)


def test_idle_status_none_when_entry_plan_formed():
    items = [V5Item(ticker="000010", name="대장주", qty=1, ref_price=100.0, chg_pct=20.0)]
    assert _idle_status(True, None, items, True, True) == (None, None)


def test_idle_status_regime_gate():
    kind, reason = _idle_status(True, None, [], False, False)
    assert kind == "regime_gate"
    assert "코스닥" in reason and "50일선" in reason


def test_idle_status_skipped_scan_when_regime_data_missing():
    kind, reason = _idle_status(True, None, [], None, False)
    assert kind == "skipped_scan"
    assert "페일오픈" in reason


def test_idle_status_skipped_scan_when_scanned_but_no_setup():
    kind, reason = _idle_status(True, None, [], True, True)
    assert kind == "skipped_scan"
    assert "셋업" in reason
