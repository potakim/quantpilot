"""BrokerAdapter 인터페이스. 업비트·KIS·Alpaca·Paper가 모두 이 형태를 구현한다.

실전 어댑터(pyupbit, python-kis, alpaca-py)는 1~2단계에서 붙인다. 0단계는 PaperBroker만 있다.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping

from quantpilot.core.models import Fill, Market, Order, Position


class BrokerAdapter(ABC):
    market: Market

    @abstractmethod
    def submit(self, order: Order) -> Fill | Order:
        """주문 제출. 즉시 체결이면 Fill, 대기·거부면 상태가 갱신된 Order를 돌려준다."""

    @abstractmethod
    def cancel(self, order_id: str) -> bool: ...

    def order_status(self, order_id: str) -> Fill | Order | None:
        """제출한 주문의 현재 상태. 체결이면 Fill, 대기·취소면 Order, 모르면 None (ADR 0011 §4)."""
        return None

    @abstractmethod
    def positions(self) -> Mapping[str, Position]: ...

    @abstractmethod
    def cash(self) -> float: ...

    @abstractmethod
    def last_price(self, symbol: str) -> float: ...

    def equity(self) -> float:
        return self.cash() + sum(
            p.qty * self.last_price(s) for s, p in self.positions().items() if p.is_open
        )

    @property
    def is_paper(self) -> bool:
        return False
