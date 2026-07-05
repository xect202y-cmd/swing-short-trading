"""v8 — v7 진입(눌림 반등) × 무한매수법(라오어) 운용 백테스트 검증.

규칙: 진입 후 매일 종가 1분할 DCA(divisions 상한), '전일까지 평단' 대비 +take(고가) 도달 시
전량 익절, 손절 없음, max_cycle_days 미도달 시 종가 청산.
"""
import pandas as pd
from swing_trader.strategy import backtest as BT


def _df(closes, vol=5_000_000, high_mul=1.01):
    n = len(closes)
    idx = pd.bdate_range(end="2026-06-26", periods=n)
    return pd.DataFrame(
        {"open": closes, "high": [c * high_mul for c in closes], "low": [c * 0.99 for c in closes],
         "close": closes, "volume": [vol] * n}, index=idx,
    )


def test_v8_take_profit_at_avg_plus_take():
    # 눌림 반등 진입 → 하락 DCA로 평단↓ → 반등 고가가 평단+10% 도달 → 익절(ret=take-cost)
    closes = [100.0] * 22 + [97.0, 99.0] + [96.0, 92.0, 88.0, 86.0, 84.0] + [97.0] + [97.0] * 3
    trades = BT._v8_stock_trades(_df(closes), take=0.10, divisions=40, max_cycle_days=60,
                                 cost=0.0, min_tv_eok=0, require_uptrend=False)
    assert len(trades) >= 1
    d, r = trades[0]
    assert isinstance(d, str) and len(d) == 10
    assert abs(r - 0.10) < 1e-9, f"익절 수익률은 정확히 take(10%)여야 함, got {r}"


def test_v8_no_stop_and_cycle_timeout_exit():
    # 계속 하락 — 손절 없이 버티다 max_cycle_days 에 종가 청산(평단 대비 음수)
    closes = [100.0] * 22 + [97.0, 99.0] + [95.0 - i for i in range(30)]
    trades = BT._v8_stock_trades(_df(closes), take=0.10, divisions=40, max_cycle_days=15,
                                 cost=0.0, min_tv_eok=0, require_uptrend=False)
    assert len(trades) == 1
    _, r = trades[0]
    assert r < 0, "미도달 사이클은 종가 청산으로 음수 수익률"
    assert r > -1.0


def test_v8_divisions_cap_freezes_average():
    # divisions=1 → 추가 매수 없음 = 평단 고정(첫 체결가). 반등이 첫가+10% 도달 시 익절.
    closes = [100.0] * 22 + [97.0, 99.0] + [90.0, 85.0] + [112.0] + [112.0] * 3
    trades = BT._v8_stock_trades(_df(closes), take=0.10, divisions=1, max_cycle_days=60,
                                 cost=0.0, min_tv_eok=0, require_uptrend=False)
    assert len(trades) >= 1
    _, r = trades[0]
    assert abs(r - 0.10) < 1e-9  # 평단=첫 체결가 고정이므로 정확히 +10%


def test_v8_uptrend_gate_blocks_flat_series():
    # require_uptrend=True + 횡보(20일선==60일선)면 정배열 조건 미충족 → 거래 없음
    closes = [100.0] * 22 + [97.0, 99.0] + [100.0] * 10
    trades = BT._v8_stock_trades(_df(closes), take=0.10, divisions=40, max_cycle_days=60,
                                 cost=0.0, min_tv_eok=0, require_uptrend=True)
    assert trades == []


def test_v8_cost_subtracted():
    closes = [100.0] * 22 + [97.0, 99.0] + [90.0, 85.0] + [112.0] + [112.0] * 3
    trades = BT._v8_stock_trades(_df(closes), take=0.10, divisions=1, max_cycle_days=60,
                                 cost=0.0013, min_tv_eok=0, require_uptrend=False)
    assert abs(trades[0][1] - (0.10 - 0.0013)) < 1e-9
