"""OrderExecutor — 주문 1건을 리스크 검사부터 원장 기록까지 끝낸다 (04 §5.2, ADR 0011).

1. risk.check → 거부면 signals.outcome = risk_rejected
2. limiter.acquire(group)
3. broker.submit (접수되면 signals.outcome = ordered) — retryable 오류는 지수 백오프(0.5·1·2초) 재시도, 다 실패하면 risk.api_error()
4. 대기(pending)면 order_status로 체결 확인. 시장가는 30초 뒤 취소하고 1회 재주문, 지정가는 ttl 뒤 취소
5. 체결 → ledger.save_order·record → 브로커 계좌 상태 저장(persist) → signals.outcome = filled

모든 주문은 이 순서로만 브로커에 닿는다 (불변식 #9). 청산은 재시도 10회, 실패 시 CRITICAL 로그.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

from quantpilot.core import clock
from quantpilot.core.errors import BrokerError, RateLimited
from quantpilot.core.models import Fill, Order, OrderStatus, OrderType, Position, Side
from quantpilot.core.ports import RiskGate
from quantpilot.core.repos import Ledger, SignalRepo
from quantpilot.execution.broker import BrokerAdapter
from quantpilot.execution.ratelimit import RateLimiter

log = logging.getLogger(__name__)

RETRIES = 3
EXIT_RETRIES = 10
BACKOFF_BASE = 0.5
BACKOFF_CAP = 4.0


def backoff(attempt: int) -> float:
    """attempt번째(0부터) 재시도 전 대기 초: 0.5·1·2·4·4…"""
    return min(BACKOFF_BASE * 2**attempt, BACKOFF_CAP)


class OrderExecutor:
    """core.ports.OrderExecutor 구현. TickRunner에 DirectExecutor 대신 주입한다."""

    def __init__(
        self,
        broker: BrokerAdapter,
        risk: RiskGate,
        ledger: Ledger,
        limiter: RateLimiter,
        *,
        signals: SignalRepo | None = None,
        group: str | None = None,
        confirm_timeout: float = 30.0,
        limit_ttl: float = 60.0,
        poll_interval: float = 0.5,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        self.broker = broker
        self.risk = risk
        self.ledger = ledger
        self.limiter = limiter
        self.signals = signals
        self.group = group  # 예: "upbit:order". None이면 한도 없음(페이퍼)
        self.confirm_timeout = confirm_timeout
        self.limit_ttl = limit_ttl
        self.poll_interval = poll_interval
        self._sleep = sleep
        self._monotonic = monotonic

    # ---- 계좌 조회 (브로커 위임) ----
    @property
    def is_paper(self) -> bool:
        """가상 체결 브로커인가."""
        return self.broker.is_paper

    def mark(self, symbol: str, price: float, ts: datetime) -> None:
        """페이퍼 브로커에 새 시세를 알린다. 실브로커는 거래소 시세를 쓰므로 무시."""
        on_price = getattr(self.broker, "on_price", None)
        if on_price is not None:
            on_price(symbol, price, ts)

    def last_price(self, symbol: str) -> float:
        """마지막 시세."""
        return self.broker.last_price(symbol)

    def positions(self) -> Mapping[str, Position]:
        """열린 포지션."""
        return self.broker.positions()

    def cash(self) -> float:
        """가용 현금."""
        return self.broker.cash()

    def equity(self) -> float:
        """현금 + 평가액."""
        return self.broker.equity()

    # ---- 실행 ----
    async def execute(
        self,
        order: Order,
        *,
        equity: float,
        price: float,
        positions: dict[str, Position],
        horizon: str,
        intraday_exposure: float,
    ) -> Fill | Order:
        """주문 1건 실행. 체결이면 Fill, 대기·거부·취소면 상태가 갱신된 Order."""
        order.market = order.market or self.broker.market
        order.paper = self.broker.is_paper
        order.ts = order.ts or clock.to_local(datetime.now(UTC), order.market)
        pos = positions.get(order.symbol)
        is_exit = order.side == Side.SELL and pos is not None and pos.qty > 0

        d = self.risk.check(
            order,
            equity=equity,
            price=price,
            positions=positions,
            horizon=horizon,
            intraday_exposure=intraday_exposure,
            now=order.ts,
        )
        if not d.allowed:
            order.status, order.reject_reason = OrderStatus.REJECTED, f"risk:{d.reason}"
            await self._outcome(order, "risk_rejected", d.reason)
            log.info("risk_rejected", extra=self._extra(order, reason=d.reason))
            return order
        order.qty = d.qty
        order.risk_adjustments = list(getattr(d, "adjustments", []))

        res = await self._submit(order, price, is_exit)
        if isinstance(res, Fill) or _pending(res):
            await self._outcome(order, "ordered")
        if _pending(res):
            res = await self._wait(order)
            if res is order and order.reject_reason == "timeout" and order.type == OrderType.MARKET:
                # 시장가 미체결 → 취소를 원장에 남기고 새 id로 1회 재주문
                await self._save_order(order)
                order = replace(order, id=uuid4().hex[:12], status=OrderStatus.PENDING)
                order.reject_reason = ""
                res = await self._submit(order, price, is_exit)
                if _pending(res):
                    res = await self._wait(order)
        return await self._finish(order, res)

    async def _submit(self, order: Order, price: float, is_exit: bool) -> Fill | Order:
        """한도 대기 후 제출. retryable 오류는 백오프 재시도, 다 실패하면 거부로 돌려준다."""
        retries = EXIT_RETRIES if is_exit else RETRIES
        for attempt in range(retries + 1):
            if self.group is not None:
                await self.limiter.acquire(self.group)
            try:
                res = self._submit_once(order, price)
            except BrokerError as e:
                if not e.retryable:
                    order.status, order.reject_reason = OrderStatus.REJECTED, f"broker:{e}"
                    log.warning("broker_rejected", extra=self._extra(order, error=str(e)))
                    return order
                if attempt == retries:
                    return self._give_up(order, e, is_exit)
                wait = backoff(attempt)
                if isinstance(e, RateLimited) and e.retry_after:
                    wait = max(wait, e.retry_after)
                log.warning(
                    "broker_retry",
                    extra=self._extra(order, attempt=attempt + 1, wait=wait, error=str(e)),
                )
                await self._sleep(wait)
                continue
            api_ok = getattr(self.risk, "api_ok", None)
            if api_ok is not None:
                api_ok()
            return res
        raise AssertionError("unreachable")

    def _submit_once(self, order: Order, price: float) -> Fill | Order:
        """브로커 호출 1회. 페이퍼는 체결 기준가로 잠시 호가를 맞추고 되돌린다 (DirectExecutor와 같음)."""
        on_price = getattr(self.broker, "on_price", None)
        if on_price is None:
            return self.broker.submit(order)
        prev = self.broker.last_price(order.symbol)
        on_price(order.symbol, price, order.ts)
        try:
            return self.broker.submit(order)
        finally:
            on_price(order.symbol, prev, order.ts)

    def _give_up(self, order: Order, err: BrokerError, is_exit: bool) -> Order:
        """재시도를 다 쓴 주문. API 오류로 세고, 청산이면 CRITICAL."""
        api_error = getattr(self.risk, "api_error", None)
        if api_error is not None:
            api_error()
        order.status, order.reject_reason = OrderStatus.REJECTED, f"broker_retry_exhausted:{err}"
        extra = self._extra(order, error=str(err))
        if is_exit:
            log.critical("exit_order_failed", extra=extra)
        else:
            log.error("broker_retry_exhausted", extra=extra)
        return order

    async def _wait(self, order: Order) -> Fill | Order:
        """대기 주문의 체결을 확인한다. 시간이 지나면 취소(reject_reason="timeout").

        브로커가 상태를 모르면(order_status → None) 대기 주문 그대로 돌려준다 (ADR 0011 §4).
        """
        await self._save_order(order)
        if self.broker.order_status(order.id) is None:
            return order
        note = getattr(self.risk, "note_pending", None)
        if note is not None:
            note(order, True)
        try:
            limit = self.confirm_timeout if order.type == OrderType.MARKET else self.limit_ttl
            deadline = self._monotonic() + limit
            while self._monotonic() < deadline:
                await self._sleep(self.poll_interval)
                st = self.broker.order_status(order.id)
                if st is not None and not _pending(st):
                    return st
            if not self.broker.cancel(order.id):
                st = self.broker.order_status(order.id)  # 취소 직전에 체결됐을 수 있다
                if st is not None and not _pending(st):
                    return st
            order.status, order.reject_reason = OrderStatus.CANCELLED, "timeout"
            log.warning("order_timeout_cancelled", extra=self._extra(order))
            return order
        finally:
            if note is not None:
                note(order, False)

    async def _finish(self, order: Order, res: Fill | Order) -> Fill | Order:
        """결과를 원장에 쓰고 신호 결과를 남긴다."""
        if isinstance(res, Fill):
            res.market = res.market or order.market
            res.paper = order.paper
            order.status = OrderStatus.FILLED
            await self._save_order(order)
            try:
                await self.ledger.record(res)
                persist = getattr(self.broker, "persist", None)
                if persist is not None:
                    await persist()
            except Exception:
                log.critical("ledger_write_failed", extra=self._extra(order), exc_info=True)
            await self._outcome(order, "filled")
            log.info("filled", extra=self._extra(order, qty=res.qty, price=res.price))
            return res
        await self._save_order(res)
        if res.status in (OrderStatus.REJECTED, OrderStatus.CANCELLED):
            await self._outcome(order, "expired", res.reject_reason or res.status.value)
        return res

    async def _save_order(self, order: Order) -> None:
        try:
            await self.ledger.save_order(order)
        except Exception:
            log.critical("ledger_write_failed", extra=self._extra(order), exc_info=True)

    async def _outcome(self, order: Order, outcome: str, reason: str | None = None) -> None:
        if self.signals is None or order.signal_id is None:
            return
        try:
            await self.signals.set_outcome(order.signal_id, outcome, reason)
        except Exception:
            log.exception("signal_outcome_failed", extra=self._extra(order))

    @staticmethod
    def _extra(order: Order, **kw: object) -> dict[str, object]:
        return {
            "symbol": order.symbol,
            "strategy": order.strategy,
            "order_id": order.id,
            "side": order.side.value,
            **kw,
        }


def _pending(res: Fill | Order) -> bool:
    return isinstance(res, Order) and res.status == OrderStatus.PENDING
