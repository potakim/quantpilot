"""수동 주문 큐 소비 (03 §2.4, ADR 0017 §1).

API는 RiskManager로 사전 검사만 하고 `q:orders:<market>`에 넣는다. 계좌는 엔진 하나가 쥐고 있으므로
실제 실행은 여기서 OrderExecutor.execute(= RiskManager.check → BrokerAdapter.submit, 불변식 #9)로 한다.
API의 사전 검사 결과는 참고일 뿐이고, 실행 직전에 엔진 RiskManager가 다시 검사한다.

큐 항목
- `{"op": "submit", "order": {...}, "horizon": "swing"}`
- `{"op": "cancel", "order_id": "..."}`

시세 신선도: 체결가는 엔진이 마지막으로 본 가격이다. `price_age`(심볼 → 마지막 체결 후 초)를 받으면
매수(비중을 늘리는 주문)는 그 심볼 체결이 `stale_after`초보다 오래됐을 때 `stale_price`로 거부한다.
매도·청산은 시세와 무관하게 그대로 처리한다(불변식 #6). 자동 전략은 체결이 있어야 분봉이 생기므로
시세가 끊기면 애초에 진입하지 않는다 — 이 검사는 수동 주문만의 빈틈을 막는다 (07 §7.6).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from quantpilot.core.events import FillEvent, OrderEvent
from quantpilot.core.models import Fill, Market, Order, OrderStatus, OrderType, Side
from quantpilot.core.ports import Hub
from quantpilot.realtime import keys

log = logging.getLogger(__name__)

# 수동 매수를 받는 최대 시세 나이(초) — 업비트 WS 무응답 판정(data/upbit_ws.STALE_AFTER)과 같은 값
STALE_AFTER = 30.0


def order_from_item(data: dict[str, Any], market: Market) -> Order:
    """큐 항목의 order dict → Order. id는 API가 정한 것을 그대로 쓴다."""
    kw: dict[str, Any] = {
        "type": OrderType(data.get("type", "market")),
        "limit_price": data.get("limit_price"),
        "strategy": data.get("strategy") or "manual",
        "reason": data.get("reason") or "manual",
        "stop": data.get("stop"),
        "market": market,
    }
    if data.get("id"):
        kw["id"] = str(data["id"])
    return Order(str(data["symbol"]), Side(data["side"]), float(data["qty"]), **kw)


class ManualOrderConsumer:
    """시장 하나의 수동 주문 큐를 TickRunner의 executor로 실행한다."""

    def __init__(
        self,
        hub: Hub,
        runner: Any,
        *,
        max_per_drain: int = 20,
        price_age: Callable[[str], float | None] | None = None,
        stale_after: float = STALE_AFTER,
    ) -> None:
        self.hub = hub
        self.runner = runner
        self.market = Market(runner.market)
        self.max_per_drain = max_per_drain
        self.price_age = price_age  # 심볼 → 마지막 체결 후 초 (없으면 신선도 검사 안 함)
        self.stale_after = stale_after

    async def drain(self) -> int:
        """큐에 쌓인 항목을 최대 max_per_drain건 처리하고 처리 건수를 돌려준다."""
        n = 0
        while n < self.max_per_drain:
            item = await self.hub.pop(keys.orders_queue(self.market))
            if item is None:
                break
            n += 1
            try:
                await self.handle(item)
            except Exception:
                log.exception("manual order failed", extra={"market": self.market.value})
        return n

    async def handle(self, item: dict[str, Any]) -> Fill | Order | bool | None:
        """항목 1건 처리."""
        op = item.get("op")
        if op == "submit":
            return await self._submit(order_from_item(item["order"], self.market), item)
        if op == "cancel":
            return await self._cancel(str(item["order_id"]))
        log.warning("unknown queue op", extra={"op": op, "market": self.market.value})
        return None

    async def _submit(self, order: Order, item: dict[str, Any]) -> Fill | Order:
        runner, ex = self.runner, self.runner.executor
        order.ts = runner.clock.now()
        # 매수는 시세 신선도를 먼저 본다 — 끊긴 시세면 마지막 가격이 있든 없든 같은 이유로 거부
        if order.side == Side.BUY and self.price_age is not None:
            age = self.price_age(order.symbol)
            if age is None or age > self.stale_after:
                order.status, order.reject_reason = OrderStatus.REJECTED, "stale_price"
                log.warning(
                    "manual order rejected: stale price",
                    extra={"symbol": order.symbol, "age": age},
                )
                await runner.bus.publish("order", OrderEvent(order, order.ts))
                return order
        try:
            price = ex.last_price(order.symbol)
        except KeyError:
            order.status, order.reject_reason = OrderStatus.REJECTED, "no_price"
            await runner.bus.publish("order", OrderEvent(order, order.ts))
            return order
        res = await ex.execute(
            order,
            equity=ex.equity(),
            price=price,
            positions=dict(ex.positions()),
            horizon=str(item.get("horizon") or "swing"),
            intraday_exposure=runner._intraday_exposure(ex),
        )
        if isinstance(res, Fill):
            await runner.bus.publish("fill", FillEvent(res))
        else:
            await runner.bus.publish("order", OrderEvent(res, order.ts))
        log.info(
            "manual order",
            extra={
                "symbol": order.symbol,
                "strategy": order.strategy,
                "result": type(res).__name__,
            },
        )
        return res

    async def _cancel(self, order_id: str) -> bool:
        ex = self.runner.executor
        ok = bool(ex.broker.cancel(order_id))
        ledger = getattr(ex, "ledger", None)
        if ok and ledger is not None and hasattr(ledger, "order"):
            order = await ledger.order(order_id)
            if order is not None:
                order.status, order.reject_reason = OrderStatus.CANCELLED, "manual_cancel"
                await ledger.save_order(order)
                self.runner.risk.note_pending(order, False)
                await self.runner.bus.publish("order", OrderEvent(order, self.runner.clock.now()))
        log.info("manual cancel", extra={"order_id": order_id, "ok": ok})
        return ok
