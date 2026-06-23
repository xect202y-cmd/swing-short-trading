"""PositionManager — 보유 포지션의 매도 조건 점검 + 청산 주문."""
from __future__ import annotations

import logging
from datetime import date

from ..broker.base import Broker
from ..config import Config
from ..market.data_provider import DataProvider
from ..market.technicals import build_snapshot
from ..models import Order
from ..strategy import rules

log = logging.getLogger(__name__)


def _today() -> str:
    return date.today().isoformat()


class PositionManager:
    def __init__(self, cfg: Config, broker: Broker, provider: DataProvider):
        self.cfg = cfg
        self.broker = broker
        self.provider = provider

    def check_and_exit(self) -> list[tuple[Order, list[str], dict]]:
        """청산된 거래는 (매도주문, 사유, 청산기록dict)로 반환. 기록은 원장/브리핑 분석용."""
        out: list[tuple[Order, list[str], dict]] = []
        ind_cfg = self.cfg.get("indicators", default={})
        rsi_ob = float(ind_cfg.get("rsi_overbought", 70))
        rk = self.cfg.get("risk", default={})
        params = dict(
            momentum_days=int(rk.get("momentum_exit_days", 5)),
            soft_hold_days=int(rk.get("soft_hold_days", 10)),
            max_hold_days=int(rk.get("max_hold_days", 20)),
            partial_pct=float(rk.get("partial_exit_pct", 0.5)),
            trail_pct=float(rk.get("trail_pct", 3.0)),
        )
        for pos in self.broker.get_positions():
            df, _ = self.provider.get_ohlcv(pos.ticker)
            tech = build_snapshot(pos.ticker, df, ind_cfg)
            entry, cur = pos.avg_price, tech.price
            d = rules.decide_exit(pos, cur, tech, rsi_overbought=rsi_ob, **params)
            pos.high_water = d["hw"]                 # 트레일링 기준 갱신
            if d["action"] == "hold":
                pos.stop = d["new_stop"]             # 트레일링 스탑 상향만(하향 안 함)
                continue
            qty, reasons = d["qty"], d["reasons"]
            order = self.broker.place_sell_order(pos.ticker, qty, price=cur)
            order.stop = pos.stop
            order.target = pos.target1
            partial = d["action"] == "partial"
            if partial:                              # 잔량 보유 → 본전 스탑으로 올림
                pos.partial_done = True
                pos.stop = d["new_stop"]
            tag = "부분익절" if partial else "청산"
            order.note = (order.note + f" · [{tag}] " + "; ".join(reasons)).strip(" ·")
            exit_px = order.filled_price or cur
            closed = {
                "ticker": pos.ticker, "name": pos.name, "qty": qty, "partial": partial,
                "entry": round(entry, 2), "exit": round(exit_px, 2),
                "pnl": round((exit_px - entry) * qty - order.fee, 0),
                "return_pct": round((exit_px - entry) / entry * 100, 2) if entry else None,
                "entry_score": pos.entry_score, "entry_date": pos.entry_date,
                "exit_date": _today(), "hold_days": pos.bars_held,
                "exit_reason": "; ".join(reasons),
                "stop_respected": True,
            }
            out.append((order, reasons, closed))
        return out
