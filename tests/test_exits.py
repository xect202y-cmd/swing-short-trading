"""부분익절+트레일링 청산 로직 테스트."""
from __future__ import annotations

from swing_trader.models import Position, TechnicalSnapshot
from swing_trader.strategy.rules import decide_exit


def _tech(price: float) -> TechnicalSnapshot:
    return TechnicalSnapshot(
        ticker="X", price=price, ma={5: price - 1, 20: price - 2, 60: price - 5}, rsi=55,
        macd=0.0, macd_signal=0.0, vol_avg={5: 1000, 20: 1000}, vol_today=1100,
        prev_high=price, high_20=price, low_20=price - 10, trading_value_eok=100,
    )


def _pos() -> Position:
    return Position(ticker="X", name="X", quantity=10, avg_price=100, stop=97,
                    target1=105, target2=110, bars_held=2)


def test_partial_exit_at_target1_with_breakeven_stop():
    d = decide_exit(_pos(), 105, _tech(105), partial_pct=0.5, trail_pct=3.0)
    assert d["action"] == "partial"
    assert d["qty"] == 5                 # 절반 익절
    assert d["new_stop"] == 100          # 잔량 본전(브레이크이븐) 스탑


def test_runner_takes_target2_full_exit():
    pos = _pos()
    pos.partial_done, pos.quantity, pos.stop, pos.high_water = True, 5, 100, 108
    d = decide_exit(pos, 110, _tech(110), partial_pct=0.5, trail_pct=3.0)
    assert d["action"] == "all"          # 2차 익절 도달 → 잔량 청산


def test_runner_trailing_stop_exit():
    pos = _pos()
    pos.partial_done, pos.quantity, pos.stop, pos.high_water = True, 5, 100, 110
    # 최고가 110, 트레일 3% → 106.7. 현재가 106 → 트레일링 청산
    d = decide_exit(pos, 106, _tech(106), partial_pct=0.5, trail_pct=3.0)
    assert d["action"] == "all"


def test_max_hold_forces_exit():
    pos = _pos()
    pos.bars_held = 20
    d = decide_exit(pos, 102, _tech(102), max_hold_days=20)
    assert d["action"] == "all" and "강제청산" in d["reasons"][0]
