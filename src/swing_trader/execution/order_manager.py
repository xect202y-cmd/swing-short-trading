"""OrderManager — 주문 전 모든 안전장치를 검사한다(섹션 12).

PAPER 기본 / LIVE 이중보호 / 1일 손실 / 중복매수 / 비중한도 / 이벤트리스크 /
손절가 필수 / 예상손실 한도. 모든 차단은 사유와 함께 반환.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

from ..broker.base import Broker
from ..config import Config
from ..models import Order, RiskLevel, Signal, SignalKind
from ..strategy import portfolio as pf
from ..strategy import risk as risk_mod

log = logging.getLogger(__name__)

_EV_RANK = {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 2}


def rank_candidates(signals: list[Signal]) -> list[Signal]:
    """후보 우선순위: 총점→손익비→변동성낮음→이벤트안전. sig.rank 부여하고 BUY를 순서대로 반환."""
    cands = [s for s in signals if s.ticker and s.ticker != "?" and s.score > 0
             and s.kind in (SignalKind.BUY, SignalKind.BUY_WATCH, SignalKind.BLOCKED)]
    cands.sort(key=lambda s: (
        -s.score,
        -((s.plan.reward_risk if s.plan else 0) or 0),
        (s.atr_pct or 99),
        _EV_RANK.get(s.event_risk, 1),
    ))
    for i, s in enumerate(cands, 1):
        s.rank = i
    return [s for s in cands if s.kind == SignalKind.BUY]


@dataclass
class ExecResult:
    placed: list[Order]
    blocked: list[tuple[Signal, list[str]]]  # (신호, 사유들) — 차단 종목 상세 분석용


class OrderManager:
    def __init__(self, cfg: Config, broker: Broker, realized_today: float = 0.0, realized_total: float = 0.0):
        self.cfg = cfg
        self.broker = broker
        self.realized_today = realized_today
        self.realized_total = realized_total

    def _capital(self, key: str, default):
        return self.cfg.get("capital", key, default=default)

    def _risk(self, key: str, default):
        return self.cfg.get("risk", key, default=default)

    def can_open_new(self) -> tuple[bool, list[str]]:
        reasons: list[str] = []
        if risk_mod.daily_loss_breached(self.realized_today, float(self._risk("daily_loss_limit", -30000))):
            reasons.append(f"1일 손실 한도 도달({round(self.realized_today):,}원) — 신규매매 중지")
        if risk_mod.account_loss_breached(self.realized_total, float(self._risk("account_loss_limit", -50000))):
            reasons.append(f"계좌 손실 한도 도달({round(self.realized_total):,}원) — 주간 매매 중지")
        positions = self.broker.get_positions()
        if len(positions) >= int(self._capital("max_positions", 2)):
            reasons.append(f"최대 보유 종목수({len(positions)}) 도달")
        return (len(reasons) == 0), reasons

    def evaluate_buy(self, sig: Signal) -> tuple[bool, int, list[str]]:
        """매수 가능 여부 + 수량 + 차단사유."""
        reasons: list[str] = []
        ok_new, gate = self.can_open_new()
        reasons += gate

        if sig.kind != SignalKind.BUY:
            reasons.append(f"신호 종류 {sig.kind.value} — 자동 매수 대상 아님")
        if sig.event_risk == RiskLevel.HIGH:
            reasons.append("이벤트 리스크 높음 — 주문 금지")
        if sig.plan is None:
            reasons.append("손절가 없는 매수 — 금지")

        # 중복 매수 방지
        held = {p.ticker for p in self.broker.get_positions()}
        if sig.ticker in held:
            reasons.append("이미 보유 중 — 중복 매수 방지")

        # 실전 주문 조건(점수/플래그)
        if self.cfg.safety.live_allowed:
            live_min = float(self.cfg.get("scoring", "live_min_score", default=80))
            if sig.score < live_min:
                reasons.append(f"실전 최소점수 {live_min} 미만(점수 {sig.score})")

        # 사이징 + 플랜 검증
        cash = self.broker.get_cash_balance()
        qty = risk_mod.position_size(
            cash, sig.plan.entry if sig.plan else sig.price,
            int(self._capital("first_entry", 300000)), int(self._capital("max_per_stock", 500000)),
        )
        chk = risk_mod.validate_plan(
            sig.plan, qty,
            min_reward_risk=float(self._risk("min_reward_risk", 1.5)),
            allowed_loss=float(self._risk("daily_loss_limit", -30000)),
        )
        reasons += chk.reasons
        return (len(reasons) == 0), qty, reasons

    def execute_signals(self, signals: list[Signal]) -> ExecResult:
        placed: list[Order] = []
        blocked: list[tuple[Signal, list[str]]] = []
        buy_ranked = rank_candidates(signals)          # 우선순위 부여 + BUY 순서
        limits = pf.limits_from_cfg(self.cfg)
        exposure = pf.Exposure(float(self._capital("seed", 1_000_000)),
                               self.broker.get_positions(), self.broker.get_price)
        new_today = 0
        for sig in buy_ranked:
            ok, qty, reasons = self.evaluate_buy(sig)
            # 포트폴리오 노출 한도 + 하루 신규수
            if ok:
                value = (sig.plan.entry if sig.plan else sig.price) * qty
                if new_today >= limits["max_new"]:
                    ok, reasons = False, [f"하루 신규매수 한도({limits['max_new']}) 도달"]
                else:
                    can, exp_reasons = exposure.can_add(sig.ticker, sig.sector, value, limits)
                    if not can:
                        ok, reasons = False, exp_reasons
            if not ok:
                blocked.append((sig, reasons))
                log.info("매수 차단 %s: %s", sig.name, "; ".join(reasons))
                continue
            order = self.broker.place_buy_order(sig.ticker, qty, price=sig.plan.entry, order_type="limit")
            order.note = (f"점수 {sig.score:.0f} · " + order.note).strip(" ·")
            order.stop = sig.plan.stop
            order.target = sig.plan.target1
            placed.append(order)
            exposure.add(sig.ticker, sig.sector, (sig.plan.entry) * qty)
            new_today += 1
            for p in self.broker.get_positions():
                if p.ticker == sig.ticker and p.stop == 0.0:
                    p.stop, p.target1, p.target2, p.name = sig.plan.stop, sig.plan.target1, sig.plan.target2, sig.name
                    p.entry_score = sig.score
                    p.entry_date = date.today().isoformat()
                    p.entry_reasons = [r for r in sig.reasons if not r.startswith("데이터 출처")]
                    p.sector = sig.sector
        return ExecResult(placed=placed, blocked=blocked)
