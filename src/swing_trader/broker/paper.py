"""PaperBroker — 실주문 없이 가상 체결. 수수료/슬리피지 반영, 상태는 JSON 저장."""
from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from pathlib import Path

from ..models import Order, Position
from .base import Broker

PriceFn = Callable[[str], float | None]


class PaperBroker(Broker):
    name = "paper"

    def __init__(
        self,
        seed_cash: float,
        state_path: Path,
        price_fn: PriceFn | None = None,
        fee_bps: float = 1.5,
        slippage_bps: float = 5.0,
    ):
        self.state_path = state_path
        self.price_fn = price_fn or (lambda s: None)
        self.fee_bps = fee_bps
        self.slippage_bps = slippage_bps
        self._cash = seed_cash
        self._positions: dict[str, Position] = {}
        self._orders: dict[str, Order] = {}
        self._realized = 0.0
        self._last_bar_date = ""   # 마지막으로 보유일을 올린 거래일(같은날 중복 실행 방지)
        self._load(seed_cash)

    # ── 상태 영속화 ──
    def _load(self, seed_cash: float) -> None:
        if not self.state_path.exists():
            return
        try:
            d = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        self._cash = d.get("cash", seed_cash)
        self._realized = d.get("realized", 0.0)
        self._last_bar_date = d.get("last_bar_date", "")
        for t, p in d.get("positions", {}).items():
            self._positions[t] = Position(
                ticker=t, name=p.get("name", t), quantity=p["quantity"], avg_price=p["avg_price"],
                stop=p.get("stop", 0.0), target1=p.get("target1", 0.0), target2=p.get("target2"),
                bars_held=p.get("bars_held", 0), entry_score=p.get("entry_score"),
                entry_date=p.get("entry_date"), entry_reasons=p.get("entry_reasons", []),
                partial_done=p.get("partial_done", False), high_water=p.get("high_water", 0.0),
            )

    def save(self) -> None:
        d = {
            "cash": round(self._cash, 2),
            "realized": round(self._realized, 2),
            "last_bar_date": self._last_bar_date,
            "positions": {
                t: {"name": p.name, "quantity": p.quantity, "avg_price": round(p.avg_price, 2),
                    "stop": p.stop, "target1": p.target1, "target2": p.target2, "bars_held": p.bars_held,
                    "entry_score": p.entry_score, "entry_date": p.entry_date, "entry_reasons": p.entry_reasons,
                    "partial_done": p.partial_done, "high_water": p.high_water}
                for t, p in self._positions.items()
            },
        }
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── 조회 ──
    def get_cash_balance(self) -> float:
        return round(self._cash, 2)

    def get_positions(self) -> list[Position]:
        return list(self._positions.values())

    def get_price(self, symbol: str) -> float | None:
        # price_fn 이 pandas Series/단일원소 Series 를 줄 수 있어 스칼라(float|None)로 강제.
        # (계약: float|None. 보유 포지션 평가 시 'Series or x'로 ValueError 나던 버그 방지)
        v = self.price_fn(symbol)
        if v is None:
            return None
        try:
            if hasattr(v, "iloc"):
                v = v.iloc[-1]
            return float(v)
        except (TypeError, ValueError, IndexError):
            return None

    @property
    def realized_pnl(self) -> float:
        return round(self._realized, 2)

    def advance_bar(self, today: str) -> bool:
        """거래일이 바뀌었으면 보유 포지션의 보유일수를 1 증가(같은날 중복 실행은 무시)."""
        if self._last_bar_date == today:
            return False
        for p in self._positions.values():
            p.bars_held += 1
        self._last_bar_date = today
        return True

    # ── 체결 ──
    def _fill_price(self, ref: float, side: str) -> float:
        slip = ref * self.slippage_bps / 10000
        return round(ref + slip, 2) if side == "BUY" else round(ref - slip, 2)

    def _fee(self, notional: float) -> float:
        return round(abs(notional) * self.fee_bps / 10000, 2)

    def place_buy_order(self, symbol, quantity, price=None, order_type="limit") -> Order:
        ref = price or self.get_price(symbol)
        oid = "P-" + uuid.uuid4().hex[:8]
        if not ref or quantity <= 0:
            o = Order(oid, symbol, "BUY", quantity, ref or 0.0, order_type, status="rejected",
                      note="가격/수량 없음")
            self._orders[oid] = o
            return o
        fill = self._fill_price(ref, "BUY")
        notional = fill * quantity
        fee = self._fee(notional)
        if notional + fee > self._cash:
            o = Order(oid, symbol, "BUY", quantity, ref, order_type, status="rejected", note="현금 부족")
            self._orders[oid] = o
            return o
        self._cash -= notional + fee
        pos = self._positions.get(symbol)
        if pos:
            tot = pos.quantity + quantity
            pos.avg_price = (pos.avg_price * pos.quantity + fill * quantity) / tot
            pos.quantity = tot
        else:
            self._positions[symbol] = Position(ticker=symbol, name=symbol, quantity=quantity,
                                                avg_price=fill, stop=0.0, target1=0.0)
        o = Order(oid, symbol, "BUY", quantity, ref, order_type, status="filled",
                  filled_price=fill, fee=fee, slippage=round(abs(fill - ref) * quantity, 2),
                  note="가상 체결")
        self._orders[oid] = o
        return o

    def place_sell_order(self, symbol, quantity, price=None, order_type="limit") -> Order:
        ref = price or self.get_price(symbol)
        oid = "P-" + uuid.uuid4().hex[:8]
        pos = self._positions.get(symbol)
        if not pos or not ref or quantity <= 0 or quantity > pos.quantity:
            o = Order(oid, symbol, "SELL", quantity, ref or 0.0, order_type, status="rejected",
                      note="보유수량/가격 부족")
            self._orders[oid] = o
            return o
        fill = self._fill_price(ref, "SELL")
        notional = fill * quantity
        fee = self._fee(notional)
        realized = (fill - pos.avg_price) * quantity - fee
        self._cash += notional - fee
        self._realized += realized
        pos.quantity -= quantity
        if pos.quantity <= 0:
            del self._positions[symbol]
        o = Order(oid, symbol, "SELL", quantity, ref, order_type, status="filled",
                  filled_price=fill, fee=fee, slippage=round(abs(fill - ref) * quantity, 2),
                  note=f"가상 체결 · 실현손익 {round(realized):,}원")
        self._orders[oid] = o
        return o

    def get_order_status(self, order_id: str) -> str:
        o = self._orders.get(order_id)
        return o.status if o else "unknown"
