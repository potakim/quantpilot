"""PersistentPaperBroker — 가상 계좌 상태(포지션·현금)를 DB에 저장·복원하는 PaperBroker (04 §5.1, ADR 0011 §3).

체결 계산은 PaperBroker와 같다. 주문·체결 원장은 OrderExecutor가 쓰고, 이 클래스는 계좌 상태만 쓴다.
submit은 동기라서 바뀐 심볼을 모아 두었다가 `persist()`에서 한 번에 쓴다.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from quantpilot.backtest.costs import CostModel
from quantpilot.core.models import Fill, Market, Order, Position
from quantpilot.core.repos import ConfigRepo, PositionRepo
from quantpilot.execution.paper import PaperBroker


class PersistentPaperBroker(PaperBroker):
    def __init__(
        self,
        market: Market,
        cost: CostModel,
        initial_cash: float,
        *,
        positions: PositionRepo,
        config: ConfigRepo,
        allow_short: bool = False,
    ):
        super().__init__(market, cost, initial_cash, allow_short=allow_short)
        self.position_repo = positions
        self.config = config
        self._dirty: dict[str, Position] = {}
        self._cash_dirty = False

    @property
    def cash_key(self) -> str:
        """settings에 가상 현금을 두는 키."""
        return f"paper.cash.{Market(self.market).value}"

    async def restore(self) -> None:
        """DB의 포지션·현금으로 계좌를 되살린다. 저장된 현금이 없으면 초기 현금 그대로."""
        cash = await self.config.get_setting(self.cash_key)
        if cash is not None:
            self._cash = float(cash)
        self._positions = {p.symbol: p for p in await self.position_repo.all(self.market)}
        self._dirty.clear()
        self._cash_dirty = False

    def _fill(self, order: Order, px: float, ts: datetime | None = None) -> Fill | Order:
        res = super()._fill(order, px, ts)
        if isinstance(res, Fill):
            res.market = self.market
            pos = self._positions[order.symbol]
            # 전량 청산 시 PaperBroker가 수량을 0으로 만든다 → upsert가 행을 지운다
            self._dirty[order.symbol] = replace(pos, market=self.market)
            self._cash_dirty = True
        return res

    async def persist(self) -> None:
        """체결로 바뀐 포지션과 현금을 DB에 쓴다."""
        dirty, self._dirty = self._dirty, {}
        for pos in dirty.values():
            await self.position_repo.upsert(pos)
        if self._cash_dirty:
            self._cash_dirty = False
            await self.config.set_setting(self.cash_key, self._cash)
