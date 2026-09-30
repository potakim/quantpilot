"""PaperBroker — 실시간(또는 재생) 시세로 가상 체결. 백테스터와 같은 CostModel을 쓴다.

- 시장가: 마지막 시세에 슬리피지·수수료 적용 후 즉시 체결
- 지정가: 매수는 시세 <= 지정가, 매도는 시세 >= 지정가일 때 체결. 아니면 대기 큐에 두고 다음 시세에서 재검사
- 원장(ledger): 모든 체결을 순서대로 보관 → 사후 리뷰·A/B 리포트의 원천
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime

from quantpilot.backtest.costs import CostModel
from quantpilot.core.models import Fill, Market, Order, OrderStatus, OrderType, Position, Side
from quantpilot.execution.broker import BrokerAdapter


class PaperBroker(BrokerAdapter):
    def __init__(
        self, market: Market, cost: CostModel, initial_cash: float, *, allow_short: bool = False
    ):
        self.market = market
        self.cost = cost
        self._cash = float(initial_cash)
        self._positions: dict[str, Position] = {}
        self._prices: dict[str, float] = {}
        self._pending: dict[str, Order] = {}
        self.ledger: list[Fill] = []
        self.allow_short = allow_short

    @property
    def is_paper(self) -> bool:
        return True

    # ---- 시세 ----
    def on_price(self, symbol: str, price: float, ts: datetime | None = None) -> list[Fill]:
        """새 시세가 오면 호출. 대기 중인 지정가 주문을 검사해 체결된 것을 돌려준다."""
        self._prices[symbol] = float(price)
        fills = []
        for oid, o in list(self._pending.items()):
            if o.symbol != symbol or o.limit_price is None:
                continue
            crossed = (o.side == Side.BUY and price <= o.limit_price) or (
                o.side == Side.SELL and price >= o.limit_price
            )
            if crossed:
                del self._pending[oid]
                fills.append(self._fill(o, o.limit_price, ts))
        return fills

    def last_price(self, symbol: str) -> float:
        if symbol not in self._prices:
            raise KeyError(f"{symbol}: 시세 없음 (on_price 먼저)")
        return self._prices[symbol]

    # ---- 주문 ----
    def submit(self, order: Order) -> Fill | Order:
        if order.qty <= 0:
            order.status, order.reject_reason = OrderStatus.REJECTED, "qty<=0"
            return order
        px_ref = self.last_price(order.symbol)
        pos = self._positions.get(order.symbol)
        held = pos.qty if pos else 0.0
        if order.side == Side.SELL and order.qty > held + 1e-12 and not self.allow_short:
            order.status, order.reject_reason = (
                OrderStatus.REJECTED,
                f"보유 {held:.6g} < 매도 {order.qty:.6g}",
            )
            return order
        if order.type == OrderType.LIMIT:
            assert order.limit_price is not None
            crossed = (order.side == Side.BUY and px_ref <= order.limit_price) or (
                order.side == Side.SELL and px_ref >= order.limit_price
            )
            if not crossed:
                self._pending[order.id] = order
                return order
            return self._fill(order, order.limit_price, order.ts)
        return self._fill(order, self.cost.fill_price(px_ref, order.side), order.ts)

    def _fill(self, order: Order, px: float, ts: datetime | None = None) -> Fill | Order:
        gross = order.qty * px
        fee, tax = self.cost.fee(gross), self.cost.tax(gross, order.side)
        if order.side == Side.BUY and gross + fee > self._cash + 1e-9:
            order.status, order.reject_reason = (
                OrderStatus.REJECTED,
                f"현금 부족 {self._cash:.0f} < {gross + fee:.0f}",
            )
            return order
        pos = self._positions.setdefault(
            order.symbol, Position(order.symbol, strategy=order.strategy)
        )
        if order.side == Side.BUY:
            self._cash -= gross + fee
            nq = pos.qty + order.qty
            pos.avg_price = (pos.avg_price * pos.qty + px * order.qty) / nq
            pos.qty = nq
            pos.opened_at = pos.opened_at or (ts or datetime.now(UTC))
        else:
            self._cash += gross - fee - tax
            pos.qty -= order.qty
            if not pos.is_open:
                pos.qty, pos.avg_price, pos.opened_at = 0.0, 0.0, None
        order.status = OrderStatus.FILLED
        f = Fill(
            order.id,
            order.symbol,
            order.side,
            order.qty,
            px,
            fee,
            tax,
            ts or datetime.now(UTC),
            order.strategy,
            order.reason,
        )
        self.ledger.append(f)
        return f

    def cancel(self, order_id: str) -> bool:
        o = self._pending.pop(order_id, None)
        if o:
            o.status = OrderStatus.CANCELLED
        return o is not None

    def pending(self) -> list[Order]:
        return list(self._pending.values())

    # ---- 상태 ----
    def positions(self) -> Mapping[str, Position]:
        return {s: p for s, p in self._positions.items() if p.is_open}

    def cash(self) -> float:
        return self._cash
