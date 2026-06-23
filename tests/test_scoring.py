"""스윙 점수 계산 — 0~100 범위, 항목 합산, 좋은 셋업이 더 높은 점수."""
from swing_trader.models import RiskLevel, StockNote, TechnicalSnapshot
from swing_trader.strategy.scoring import score_candidate

WEIGHTS = {
    "sector_momentum": 20, "technical_setup": 25, "upside_room": 15,
    "volume_surge": 20, "macro_event_risk": 10, "short_stop_structure": 10,
}


def _tech(price=10000, ma5=10000, ma20=9900, ma60=9500, rsi=55, v5=2_000_000, v20=1_000_000,
          prev_high=9900, high20=11000, low20=9000, tv_eok=200.0):
    return TechnicalSnapshot(
        ticker="000001", price=price, ma={5: ma5, 20: ma20, 60: ma60}, rsi=rsi,
        vol_avg={5: v5, 20: v20}, vol_today=v5, prev_high=prev_high,
        high_20=high20, low_20=low20, trading_value_eok=tv_eok,
    )


def test_score_total_in_range_and_sums():
    note = StockNote(name="테스트", ticker="000001", target1=11000, stop=9700)
    bd = score_candidate(note, _tech(), RiskLevel.LOW, RiskLevel.LOW, WEIGHTS)
    assert 0 <= bd.total <= 100
    assert abs(bd.total - sum(i.score for i in bd.items)) < 0.01
    assert len(bd.items) == 6


def test_good_setup_scores_higher_than_bad():
    note = StockNote(name="A", ticker="000001", target1=11500, stop=9700)
    good = _tech(rsi=58, v5=2_500_000, v20=1_000_000, prev_high=9800, ma20=9950)  # 정배열/돌파/거래량
    bad = _tech(rsi=80, v5=900_000, v20=1_000_000, prev_high=11000, ma5=9000, ma20=9500, ma60=10000)
    good_score = score_candidate(note, good, RiskLevel.LOW, RiskLevel.LOW, WEIGHTS).total
    bad_score = score_candidate(note, bad, RiskLevel.HIGH, RiskLevel.HIGH, WEIGHTS).total
    assert good_score > bad_score


def test_high_event_risk_lowers_safety_item():
    note = StockNote(name="A", ticker="000001", target1=11000, stop=9700)
    low = score_candidate(note, _tech(), RiskLevel.LOW, RiskLevel.LOW, WEIGHTS)
    high = score_candidate(note, _tech(), RiskLevel.HIGH, RiskLevel.HIGH, WEIGHTS)
    low_item = next(i for i in low.items if "안전도" in i.name)
    high_item = next(i for i in high.items if "안전도" in i.name)
    assert low_item.score > high_item.score
