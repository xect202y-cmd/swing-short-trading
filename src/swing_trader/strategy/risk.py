"""리스크 관리 — 손절/익절 계산, 포지션 사이징, 손실 한도, 플랜 검증.

손절가 없는 매수는 금지. 손익비 미달/예상손실 초과 주문은 차단.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..models import TradePlan


def build_plan(
    entry: float,
    *,
    stop: float | None = None,
    target1: float | None = None,
    target2: float | None = None,
    default_stop_pct: float = -3.0,
    max_stop_pct: float = -5.0,
    take1_pct: float = 4.0,
    take2_pct: float = 8.5,
) -> TradePlan:
    """노트의 손절/목표가 있으면 사용, 없으면 % 기반 계산. 손절은 max_stop_pct 보다 깊지 않게."""
    if stop is None:
        stop = round(entry * (1 + default_stop_pct / 100), 2)
    # 손절이 너무 깊으면(예: -7%) 하한(-5%)으로 끌어올림
    floor = entry * (1 + max_stop_pct / 100)
    if stop < floor:
        stop = round(floor, 2)
    if target1 is None:
        target1 = round(entry * (1 + take1_pct / 100), 2)
    if target2 is None:
        target2 = round(entry * (1 + take2_pct / 100), 2)
    return TradePlan(entry=entry, stop=stop, target1=target1, target2=target2)


def position_size(cash: float, price: float, first_entry: int, max_per_stock: int) -> int:
    """1차 진입 수량. 가용 현금/종목 비중 한도 내에서 정수 주."""
    budget = min(first_entry, max_per_stock, cash)
    if price <= 0:
        return 0
    return int(budget // price)


def estimated_loss(plan: TradePlan, quantity: int) -> float:
    """손절 도달 시 예상 손실액(음수)."""
    return (plan.stop - plan.entry) * quantity


@dataclass
class PlanCheck:
    ok: bool
    reasons: list[str]


def validate_plan(plan: TradePlan | None, quantity: int, *, min_reward_risk: float, allowed_loss: float) -> PlanCheck:
    reasons: list[str] = []
    if plan is None:
        return PlanCheck(False, ["손절가 없는 매수 신호 — 금지"])
    if plan.stop >= plan.entry:
        reasons.append("손절가가 진입가 이상 — 무효")
    rr = plan.reward_risk
    if rr is None or rr < min_reward_risk:
        reasons.append(f"손익비 {rr} < 최소 {min_reward_risk}")
    if quantity <= 0:
        reasons.append("진입 수량 0 — 자금 부족")
    loss = estimated_loss(plan, quantity)
    if loss < allowed_loss:  # allowed_loss 는 음수(예: -30000)
        reasons.append(f"예상 손실 {round(loss):,}원 > 허용 {round(allowed_loss):,}원")
    return PlanCheck(len(reasons) == 0, reasons)


def daily_loss_breached(realized_today: float, limit: float) -> bool:
    """오늘 실현손익이 한도(음수) 이하면 True → 신규매매 중지."""
    return realized_today <= limit


def account_loss_breached(realized_total: float, limit: float) -> bool:
    return realized_total <= limit
