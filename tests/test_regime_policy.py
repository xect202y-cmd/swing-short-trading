from swing_trader.strategy.market_regime import Regime
from swing_trader.strategy.regime_policy import (
    allow_wide_stop, crash_entry_allowed, policy_for)


def test_policy_values():
    assert policy_for(Regime.BULL).require_uptrend is False
    assert policy_for(Regime.NEUTRAL).require_uptrend is True
    assert policy_for(Regime.CRASH).block_new_entry is True
    assert policy_for(Regime.BULL).trail_pct == 3.0
    assert policy_for(Regime.CRASH).trail_pct == 1.5
    assert policy_for(Regime.BEAR).min_reward_risk == 2.20


def test_wide_stop_only_when_all_true():
    ok = dict(ai_score=80, reward_risk=2.5, invalidation_pct=-6.0,
              liquidity_ok=True, portfolio_ok=True)
    assert allow_wide_stop(Regime.BULL, **ok) is True
    assert allow_wide_stop(Regime.NEUTRAL, **ok) is False          # not BULL
    assert allow_wide_stop(Regime.BULL, **{**ok, "ai_score": 74}) is False
    assert allow_wide_stop(Regime.BULL, **{**ok, "invalidation_pct": -8.0}) is False


def test_crash_entry_needs_all():
    base = dict(ai_score=80, reward_risk=2.5, market_stabilizing=True,
                sector_up=True, stock_up=True)
    assert crash_entry_allowed(Regime.CRASH, **base) is True
    assert crash_entry_allowed(Regime.CRASH, **{**base, "stock_up": False}) is False
    assert crash_entry_allowed(Regime.BULL, **base) is True        # non-crash always allowed here
