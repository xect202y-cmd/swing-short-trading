"""브로커 추상 인터페이스."""
from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import Order, Position


class Broker(ABC):
    name: str = "base"

    @abstractmethod
    def get_cash_balance(self) -> float: ...

    @abstractmethod
    def get_positions(self) -> list[Position]: ...

    @abstractmethod
    def get_price(self, symbol: str) -> float | None: ...

    @abstractmethod
    def place_buy_order(self, symbol: str, quantity: int, price: float | None = None,
                        order_type: str = "limit") -> Order: ...

    @abstractmethod
    def place_sell_order(self, symbol: str, quantity: int, price: float | None = None,
                         order_type: str = "limit") -> Order: ...

    @abstractmethod
    def get_order_status(self, order_id: str) -> str: ...
