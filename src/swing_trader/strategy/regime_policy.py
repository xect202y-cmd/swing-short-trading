"""Regime → 파라미터 정책 테이블. 백테스트·라이브·문서·config 단일 소스."""
from __future__ import annotations

from dataclasses import dataclass

from .market_regime import Regime


@dataclass(frozen=True)
class RegimePolicy:
    require_uptrend: bool
    block_new_entry: bool
    trail_pct: float
    risk_per_trade_pct: float
    max_stop_pct: float      # 라이브 캡(백테스트 미반영)
    ai_min_score: float      # 라이브
    min_reward_risk: float   # 라이브


V6_POLICY: dict = {
    Regime.BULL:    RegimePolicy(False, False, 3.0, 1.00, -7.0, 70, 1.75),
    Regime.NEUTRAL: RegimePolicy(True,  False, 2.5, 0.75, -6.0, 72, 1.90),
    Regime.BEAR:    RegimePolicy(True,  False, 2.0, 0.50, -5.0, 75, 2.20),
    Regime.CRASH:   RegimePolicy(True,  True,  1.5, 0.25, -4.0, 80, 2.50),
}


def policy_for(regime: Regime, table: dict | None = None) -> RegimePolicy:
    return (table or V6_POLICY)[regime]


def allow_wide_stop(regime: Regime, *, ai_score: float, reward_risk: float,
                    invalidation_pct: float, liquidity_ok: bool, portfolio_ok: bool) -> bool:
    """-7% 손절은 BULL + 모든 조건 충족 시에만(라이브)."""
    return (regime == Regime.BULL and ai_score >= 75 and reward_risk >= 2.0
            and liquidity_ok and invalidation_pct >= -7.0 and portfolio_ok)


def crash_entry_allowed(regime: Regime, *, ai_score: float, reward_risk: float,
                        market_stabilizing: bool, sector_up: bool, stock_up: bool) -> bool:
    """CRASH 예외 진입. CRASH 아니면 이 함수는 진입 허용(True)."""
    if regime != Regime.CRASH:
        return True
    return (ai_score >= 80 and reward_risk >= 2.5 and market_stabilizing
            and sector_up and stock_up)
