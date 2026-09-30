"""core/repos.py Protocol의 SQLAlchemy(async) 구현. 세션 팩토리를 주입받는다."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from quantpilot.core.models import Fill, JudgeResult, Market, Order, Position, Target
from quantpilot.db.mappers import (
    fill_to_row,
    order_to_row,
    position_to_row,
    row_to_fill,
    row_to_order,
    row_to_position,
    to_db_ts,
)
from quantpilot.db.models import (
    FillRow,
    JudgmentRow,
    OrderRow,
    PositionRow,
    SettingRow,
    SignalRow,
    StrategyConfigRow,
)

Sessions = async_sessionmaker[AsyncSession]


class SqlLedger:
    """주문·체결 원장."""

    def __init__(self, sessions: Sessions) -> None:
        self._sessions = sessions

    async def save_order(self, order: Order) -> None:
        """주문을 새로 쓰거나 상태를 갱신한다 (id 기준 merge)."""
        async with self._sessions.begin() as s:
            await s.merge(order_to_row(order))

    async def record(self, fill: Fill) -> int:
        """체결 1건을 원장에 추가하고 행 id를 돌려준다."""
        row = fill_to_row(fill)
        async with self._sessions.begin() as s:
            s.add(row)
            await s.flush()
            return row.id

    async def fills(
        self, market: Market, *, symbol: str | None = None, since: datetime | None = None
    ) -> list[Fill]:
        """체결을 시간순으로 조회한다."""
        q = select(FillRow).where(FillRow.market == Market(market).value)
        if symbol is not None:
            q = q.where(FillRow.symbol == symbol)
        if since is not None:
            q = q.where(FillRow.ts >= to_db_ts(since, market))
        async with self._sessions() as s:
            rows = (await s.scalars(q.order_by(FillRow.ts, FillRow.id))).all()
        return [row_to_fill(r) for r in rows]

    async def order(self, order_id: str) -> Order | None:
        """주문 1건 조회."""
        async with self._sessions() as s:
            row = await s.get(OrderRow, order_id)
        return None if row is None else row_to_order(row)


class SqlSignalRepo:
    """전략 신호 기록."""

    def __init__(self, sessions: Sessions) -> None:
        self._sessions = sessions

    async def add(
        self, *, strategy_id: int, market: Market, target: Target, kind: str, ts: datetime
    ) -> int:
        """신호를 pending으로 기록하고 id를 돌려준다."""
        row = SignalRow(
            ts=to_db_ts(ts, market),
            strategy_id=strategy_id,
            market=Market(market).value,
            symbol=target.symbol,
            kind=kind,
            weight=target.weight,
            price_hint=target.price,
            stop=target.stop,
            reason=target.reason or None,
            outcome="pending",
        )
        async with self._sessions.begin() as s:
            s.add(row)
            await s.flush()
            return row.id

    async def set_outcome(self, signal_id: int, outcome: str, reason: str | None = None) -> None:
        """신호의 최종 결과를 기록한다."""
        async with self._sessions.begin() as s:
            row = await s.get_one(SignalRow, signal_id)
            row.outcome, row.outcome_reason = outcome, reason

    async def get(self, signal_id: int) -> dict[str, Any] | None:
        """신호 1건을 dict로 조회한다."""
        async with self._sessions() as s:
            row = await s.get(SignalRow, signal_id)
        return None if row is None else _row_dict(row)


class SqlJudgmentRepo:
    """판단 모델 호출 기록."""

    def __init__(self, sessions: Sessions) -> None:
        self._sessions = sessions

    async def add(
        self,
        *,
        signal_id: int,
        result: JudgeResult,
        state: dict[str, Any],
        gate: str,
        blocks: list[str],
        ts: datetime,
    ) -> int:
        """판단 1회를 기록하고 id를 돌려준다."""
        row = JudgmentRow(
            signal_id=signal_id,
            provider=result.model,
            state=state,
            answers=result.answers,
            confidence=result.confidence,
            gate=gate,
            blocks=list(blocks),
            latency_ms=result.latency_ms,
            cost_usd=result.cost_usd,
        )
        async with self._sessions.begin() as s:
            # judgments에는 market 컬럼이 없어 연결된 신호의 시장 현지시간으로 본다 (ADR 0009 §6)
            signal = await s.get_one(SignalRow, signal_id)
            row.ts = to_db_ts(ts, Market(signal.market))
            s.add(row)
            await s.flush()
            return row.id

    async def set_realized(self, judgment_id: int, ret_24h: float, direction_hit: bool) -> None:
        """24시간 뒤 실현 수익률과 방향 적중 여부를 채운다."""
        async with self._sessions.begin() as s:
            row = await s.get_one(JudgmentRow, judgment_id)
            row.realized_ret_24h, row.direction_hit = ret_24h, direction_hit

    async def get(self, judgment_id: int) -> dict[str, Any] | None:
        """판단 1건을 dict로 조회한다."""
        async with self._sessions() as s:
            row = await s.get(JudgmentRow, judgment_id)
        return None if row is None else _row_dict(row)


class SqlPositionRepo:
    """현재 포지션 스냅샷."""

    def __init__(self, sessions: Sessions) -> None:
        self._sessions = sessions

    async def upsert(self, position: Position) -> None:
        """포지션을 저장한다. 수량이 0이면 행을 지운다."""
        row = position_to_row(position)
        async with self._sessions.begin() as s:
            if position.is_open:
                await s.merge(row)
            else:
                existing = await s.get(PositionRow, (row.market, row.symbol, row.strategy))
                if existing is not None:
                    await s.delete(existing)

    async def all(self, market: Market) -> list[Position]:
        """시장의 열린 포지션 전부."""
        q = select(PositionRow).where(PositionRow.market == Market(market).value)
        async with self._sessions() as s:
            rows = (await s.scalars(q.order_by(PositionRow.symbol, PositionRow.strategy))).all()
        return [row_to_position(r) for r in rows]


class SqlConfigRepo:
    """전략 설정과 settings. 리스크 규칙은 저장하지 않는다 (ADR 0003)."""

    def __init__(self, sessions: Sessions) -> None:
        self._sessions = sessions

    async def get_setting(self, key: str, default: Any = None) -> Any:
        """settings 값을 읽는다."""
        async with self._sessions() as s:
            row = await s.get(SettingRow, key)
        return default if row is None else row.value

    async def set_setting(self, key: str, value: Any) -> None:
        """settings 값을 쓴다."""
        async with self._sessions.begin() as s:
            await s.merge(SettingRow(key=key, value=value))

    async def upsert_strategy(
        self,
        *,
        name: str,
        market: Market,
        allocation: float,
        symbols: list[str],
        params: dict[str, Any] | None = None,
        enabled: bool = False,
        paper: bool = True,
    ) -> int:
        """(name, market) 기준으로 전략 설정을 저장하고 id를 돌려준다."""
        mk = Market(market).value
        async with self._sessions.begin() as s:
            row = await s.scalar(
                select(StrategyConfigRow).where(
                    StrategyConfigRow.name == name, StrategyConfigRow.market == mk
                )
            )
            if row is None:
                row = StrategyConfigRow(name=name, market=mk)
                s.add(row)
            row.allocation, row.symbols, row.params = allocation, list(symbols), dict(params or {})
            row.enabled, row.paper = enabled, paper
            await s.flush()
            return row.id

    async def strategies(self, market: Market | None = None) -> list[dict[str, Any]]:
        """전략 설정 목록."""
        q = select(StrategyConfigRow).order_by(StrategyConfigRow.id)
        if market is not None:
            q = q.where(StrategyConfigRow.market == Market(market).value)
        async with self._sessions() as s:
            rows = (await s.scalars(q)).all()
        return [_row_dict(r) for r in rows]


def _row_dict(row: Any) -> dict[str, Any]:
    return {c.key: getattr(row, c.key) for c in row.__mapper__.column_attrs}
