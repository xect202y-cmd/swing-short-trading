"""run_scalp 정산 헬퍼 — 확정 일봉 매칭·미체결·그림자 분리."""
import pandas as pd

from swing_trader.main import _settle_scalp_plan
from swing_trader.scalp.strategy import PlanItem


def _df(d: str, o, h, l, c):
    idx = pd.DatetimeIndex([pd.Timestamp(d)])
    return pd.DataFrame({"open": [o], "high": [h], "low": [l], "close": [c]}, index=idx)


def _item(shadow=False, ticker="005930"):
    return PlanItem(model="v1", ticker=ticker, name="삼성전자", qty=10,
                    stop_pct=-2.0, prev_close=10000.0, prev_range=400.0, k=0.5,
                    shadow=shadow)


def test_settle_matches_bar_by_date():
    plan = {"date": "2026-07-02", "items": [_item(), _item(shadow=True)]}
    dfs = {"005930": _df("2026-07-02", 10000, 10400, 9950, 10350)}
    results, rows, missing = _settle_scalp_plan(plan, dfs, fee_bps=1.5, slip_bps=5.0)
    assert results["v1"]["pnl"] != 0.0
    assert results["v1"]["shadow_pnl"] != 0.0        # 그림자는 별도 합산
    assert len(results["v1"]["trades"]) == 1          # 실계좌 체결만 원장 기록
    assert rows["v1"][0]["name"] == "삼성전자"
    assert missing == []


def test_settle_no_bar_returns_none():
    plan = {"date": "2026-07-02", "items": [_item()]}
    results, rows, missing = _settle_scalp_plan(plan, {}, fee_bps=1.5, slip_bps=5.0)
    assert results is None and rows is None           # 데이터 미도착 → 보류 신호
    assert missing == ["005930"]


def test_settle_partial_missing_holds_everything():
    # 2종목 중 1종목만 확정봉 확보 → 부분 정산 금지, 전량 보류(all-or-nothing)
    plan = {"date": "2026-07-02", "items": [_item(ticker="005930"), _item(ticker="000660")]}
    dfs = {"005930": _df("2026-07-02", 10000, 10400, 9950, 10350)}
    results, rows, missing = _settle_scalp_plan(plan, dfs, fee_bps=1.5, slip_bps=5.0)
    assert results is None and rows is None
    assert missing == ["000660"]
