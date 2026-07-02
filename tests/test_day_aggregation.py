"""하루 다회 실행(us→kr) 집계 회귀 테스트.

2026-07-02 버그: US 런이 SELL 2건 체결 → KR 런이 last_run.json 을 빈 orders 로
덮어씀 → review/Daily 가 '매도 0 · 청산 거래 없음' 으로 집계.
last_run.json 은 같은 KST 날짜면 병합되는 '하루 누적 원장'이어야 한다.
"""
import json
from types import SimpleNamespace

from swing_trader.main import _day_counts, _save_run
from swing_trader.models import Order, Signal, SignalKind


def _order(oid: str, side: str, status: str = "filled") -> Order:
    return Order(order_id=oid, ticker="005930", side=side, quantity=3,
                 price=100.0, status=status, note="가상 체결 · 실현손익 -100원")


def _signal(ticker: str, kind: SignalKind = SignalKind.BUY) -> Signal:
    from swing_trader.models import RiskLevel
    return Signal(ticker=ticker, name=ticker, kind=kind, score=70.0,
                  price=100.0, event_risk=RiskLevel.LOW)


def test_save_run_merges_same_day_orders(tmp_path):
    cfg = SimpleNamespace(state_dir=tmp_path)
    _save_run(cfg, [], [_order("A1", "SELL"), _order("A2", "SELL")])   # US 런: 매도 2
    payload = _save_run(cfg, [], [])                                    # KR 런: 체결 없음
    assert [o["order_id"] for o in payload["orders"]] == ["A1", "A2"]
    on_disk = json.loads((tmp_path / "last_run.json").read_text(encoding="utf-8"))
    assert len(on_disk["orders"]) == 2


def test_save_run_resets_on_new_date(tmp_path):
    cfg = SimpleNamespace(state_dir=tmp_path)
    (tmp_path / "last_run.json").write_text(json.dumps({
        "date": "2000-01-01",
        "orders": [{"order_id": "OLD", "side": "SELL", "status": "filled"}],
        "signals": [],
    }), encoding="utf-8")
    payload = _save_run(cfg, [], [_order("N1", "BUY")])
    assert [o["order_id"] for o in payload["orders"]] == ["N1"]


def test_save_run_dedupes_by_order_id(tmp_path):
    cfg = SimpleNamespace(state_dir=tmp_path)
    _save_run(cfg, [], [_order("A1", "SELL")])
    payload = _save_run(cfg, [], [_order("A1", "SELL")])   # 같은 주문 재저장(재실행)
    assert [o["order_id"] for o in payload["orders"]] == ["A1"]


def test_save_run_merges_signals_across_markets(tmp_path):
    cfg = SimpleNamespace(state_dir=tmp_path)
    _save_run(cfg, [_signal("NVDA")], [])                    # US 런 신호
    payload = _save_run(cfg, [_signal("005930")], [])        # KR 런 신호
    tickers = {s["ticker"] for s in payload["signals"]}
    assert tickers == {"NVDA", "005930"}


def test_day_counts_from_merged_payload():
    payload = {"orders": [
        {"order_id": "A1", "side": "SELL", "status": "filled"},
        {"order_id": "A2", "side": "SELL", "status": "filled"},
        {"order_id": "B1", "side": "BUY", "status": "filled"},
        {"order_id": "B2", "side": "BUY", "status": "rejected"},   # 미체결은 제외
    ]}
    bought, sold = _day_counts(payload)
    assert (bought, sold) == (1, 2)
