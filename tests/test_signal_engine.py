"""이벤트 D-0/1/2 감지 + PaperBroker 매수/매도 손익."""
from datetime import date, timedelta
from pathlib import Path

from swing_trader.broker.paper import PaperBroker
from swing_trader.macro.event_filter import parse_events


def _cal(lines: list[str]) -> str:
    return "# 캘린더\n" + "\n".join(lines)


def test_event_detection_d0_d1_d2():
    today = date(2026, 6, 22)
    cal = _cal([
        f"- {today.isoformat()} 미국 PCE 물가지수 발표",          # D-0 (block)
        f"- {(today+timedelta(days=1)).isoformat()} FOMC 회의",   # D-1 (block)
        f"- {(today+timedelta(days=2)).isoformat()} 옵션만기일",  # D-2 (block)
        f"- {(today+timedelta(days=5)).isoformat()} 먼 이벤트",   # 범위 밖
    ])
    st = parse_events(cal, today, block_keywords=["PCE", "FOMC", "옵션만기"], window_days=2)
    dminus = sorted(e.d_minus for e in st.events)
    assert dminus == [0, 1, 2]
    assert st.has_block
    assert st.market_risk().value == "높음"  # D-0/1 에 block 이벤트


def test_no_events_low_risk():
    st = parse_events("# 캘린더\n특별한 일정 없음", date(2026, 6, 22), block_keywords=["FOMC"])
    assert st.events == []
    assert st.market_risk().value == "낮음"


def test_paper_broker_buy_then_sell_pnl(tmp_path: Path):
    prices = {"000001": 10000.0}
    pb = PaperBroker(seed_cash=1_000_000, state_path=tmp_path / "s.json",
                     price_fn=lambda s: prices.get(s), fee_bps=0.0, slippage_bps=0.0)
    buy = pb.place_buy_order("000001", 30, price=10000)
    assert buy.status == "filled"
    assert len(pb.get_positions()) == 1
    assert pb.get_cash_balance() == 1_000_000 - 300_000

    prices["000001"] = 10400.0  # +4%
    sell = pb.place_sell_order("000001", 30, price=10400)
    assert sell.status == "filled"
    assert pb.get_positions() == []
    assert round(pb.realized_pnl) == 12000   # (10400-10000)*30
    assert pb.get_cash_balance() == 1_000_000 + 12000


def test_paper_broker_rejects_oversell(tmp_path: Path):
    pb = PaperBroker(seed_cash=1_000_000, state_path=tmp_path / "s.json",
                     price_fn=lambda s: 10000.0)
    o = pb.place_sell_order("000001", 10, price=10000)  # 보유 없음
    assert o.status == "rejected"


def test_paper_broker_rejects_insufficient_cash(tmp_path: Path):
    pb = PaperBroker(seed_cash=50_000, state_path=tmp_path / "s.json", price_fn=lambda s: 10000.0)
    o = pb.place_buy_order("000001", 100, price=10000)  # 100만 필요, 5만뿐
    assert o.status == "rejected"


def test_advance_bar_increments_once_per_day(tmp_path: Path):
    pb = PaperBroker(seed_cash=1_000_000, state_path=tmp_path / "s.json", price_fn=lambda s: 10000.0)
    pb.place_buy_order("000001", 10, price=10000)
    assert pb.get_positions()[0].bars_held == 0
    assert pb.advance_bar("2026-06-21") is True
    assert pb.get_positions()[0].bars_held == 1
    assert pb.advance_bar("2026-06-21") is False           # 같은날 재실행 → 증가 안 함
    assert pb.get_positions()[0].bars_held == 1
    assert pb.advance_bar("2026-06-22") is True
    assert pb.get_positions()[0].bars_held == 2
