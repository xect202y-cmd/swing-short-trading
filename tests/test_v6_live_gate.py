"""v6 라이브 regime 게이트가 의존하는 정책 계약 고정(순수 단언).

OrderManager 배선 자체는 통합 run-once 로 검증. 여기선 게이트 임계의 회귀만 잡는다.
"""
from swing_trader.strategy.market_regime import Regime
from swing_trader.strategy.regime_policy import policy_for


def test_crash_blocks_and_high_bar():
    pol = policy_for(Regime.CRASH)
    assert pol.block_new_entry is True
    assert 72 < pol.ai_min_score              # CRASH ai_min(80) 이 일반 점수보다 높음
    assert pol.min_reward_risk == 2.50


def test_bull_is_permissive():
    pol = policy_for(Regime.BULL)
    assert pol.block_new_entry is False
    assert pol.ai_min_score == 70
    assert pol.require_uptrend is False
