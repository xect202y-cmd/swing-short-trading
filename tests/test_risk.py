"""리스크: 손절/익절 계산, 손익비/예상손실 검증, 1일 손실 한도 차단, 사이징."""
from swing_trader.strategy import risk as R


def test_build_plan_default_stop_and_targets():
    plan = R.build_plan(10000, default_stop_pct=-3.0, take1_pct=4.0, take2_pct=8.0)
    assert plan.stop == 9700.0
    assert plan.target1 == 10400.0
    assert plan.target2 == 10800.0
    assert round(plan.risk_pct, 1) == -3.0
    assert round(plan.reward1_pct, 1) == 4.0
    assert plan.reward_risk == round(4.0 / 3.0, 2)


def test_build_plan_clamps_deep_stop():
    # 노트 손절이 -8%로 너무 깊으면 -5% 하한으로 끌어올림
    plan = R.build_plan(10000, stop=9200, max_stop_pct=-5.0)
    assert plan.stop == 9500.0


def test_build_plan_uses_note_values():
    plan = R.build_plan(10000, stop=9750, target1=11000, target2=11500)
    assert (plan.stop, plan.target1, plan.target2) == (9750, 11000, 11500)


def test_position_size_respects_budget():
    # 1차 30만, 종목한도 50만, 현금 100만, 가격 1만 → 30주
    assert R.position_size(1_000_000, 10_000, 300_000, 500_000) == 30
    # 현금이 모자라면 현금 기준
    assert R.position_size(50_000, 10_000, 300_000, 500_000) == 5


def test_validate_plan_blocks_low_reward_risk():
    # 손익비 1.0 < 1.5 → 차단
    plan = R.build_plan(10000, stop=9700, target1=10300)
    chk = R.validate_plan(plan, quantity=10, min_reward_risk=1.5, allowed_loss=-30000)
    assert not chk.ok
    assert any("손익비" in r for r in chk.reasons)


def test_validate_plan_blocks_no_stop():
    chk = R.validate_plan(None, quantity=10, min_reward_risk=1.5, allowed_loss=-30000)
    assert not chk.ok
    assert any("손절가 없는" in r for r in chk.reasons)


def test_validate_plan_blocks_expected_loss_over_limit():
    # 진입 10000, 손절 9700(-300/주), 100주 → 예상손실 -30000 ... allowed -20000 → 차단
    plan = R.build_plan(10000, stop=9700, target1=10600)
    chk = R.validate_plan(plan, quantity=100, min_reward_risk=1.5, allowed_loss=-20000)
    assert not chk.ok
    assert any("예상 손실" in r for r in chk.reasons)


def test_daily_loss_limit_blocks_new_trades():
    assert R.daily_loss_breached(-30000, -30000) is True
    assert R.daily_loss_breached(-30001, -30000) is True
    assert R.daily_loss_breached(-10000, -30000) is False
