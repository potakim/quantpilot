"""core/repos.py Protocol의 SQLAlchemy(async) 구현. 세션 팩토리를 주입받는다."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from quantpilot.core.events import BarClosed
from quantpilot.core.models import Fill, JudgeResult, Market, Order, Position, Target
from quantpilot.db.mappers import (
    bar_to_row,
    fill_to_row,
    from_db_ts,
    order_to_row,
    position_to_row,
    row_to_bar,
    row_to_fill,
    row_to_order,
    row_to_position,
    to_db_ts,
)
from quantpilot.db.models import (
    CandleRow,
    DailyReviewRow,
    EquitySnapshotRow,
    FillRow,
    JudgmentRow,
    LlmVerdictRow,
    OrderRow,
    PositionRow,
    RiskEventRow,
    SettingRow,
    SignalRow,
    StrategyConfigRow,
)

Sessions = async_sessionmaker[AsyncSession]


class SqlCandleRepo:
    """확정된 봉 (candles 하이퍼테이블)."""

    def __init__(self, sessions: Sessions) -> None:
        self._sessions = sessions

    async def upsert(self, bars: list[BarClosed], *, source: str = "ws") -> int:
        """PK(ts, market, symbol, tf) 기준 merge. 같은 봉이 다시 오면 값을 덮어쓴다."""
        async with self._sessions.begin() as s:
            for bar in bars:
                await s.merge(bar_to_row(bar, source))
        return len(bars)

    async def load(
        self, market: Market, symbol: str, tf: str, start: datetime, end: datetime
    ) -> list[BarClosed]:
        """start 이상 end 미만 봉을 시간순으로 조회한다."""
        q = select(CandleRow).where(
            CandleRow.market == Market(market).value,
            CandleRow.symbol == symbol,
            CandleRow.tf == tf,
            CandleRow.ts >= to_db_ts(start, market),
            CandleRow.ts < to_db_ts(end, market),
        )
        async with self._sessions() as s:
            rows = (await s.scalars(q.order_by(CandleRow.ts))).all()
        return [row_to_bar(r) for r in rows]


class SqlLedger:
    """주문·체결 원장. shadow=True면 게이팅 OFF 섀도 원장 행만 쓰고 읽는다 (ADR 0016)."""

    def __init__(self, sessions: Sessions, *, shadow: bool = False) -> None:
        self._sessions = sessions
        self.shadow = shadow

    async def save_order(self, order: Order) -> None:
        """주문을 새로 쓰거나 상태를 갱신한다 (id 기준 merge)."""
        row = order_to_row(order)
        row.shadow = self.shadow
        async with self._sessions.begin() as s:
            await s.merge(row)

    async def record(self, fill: Fill) -> int:
        """체결 1건을 원장에 추가하고 행 id를 돌려준다."""
        row = fill_to_row(fill)
        row.shadow = self.shadow
        async with self._sessions.begin() as s:
            s.add(row)
            await s.flush()
            return row.id

    async def fills(
        self, market: Market, *, symbol: str | None = None, since: datetime | None = None
    ) -> list[Fill]:
        """이 원장(ON 또는 섀도)의 체결을 시간순으로 조회한다."""
        q = select(FillRow).where(
            FillRow.market == Market(market).value, FillRow.shadow.is_(self.shadow)
        )
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
        """판단 1회를 기록하고 id를 돌려준다. 질문 문구 prompt_hash는 state jsonb에 함께 둔다 (ADR 0014)."""
        prompt_hash = (result.raw or {}).get("prompt_hash")
        if prompt_hash:
            state = {**state, "prompt_hash": prompt_hash}
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

    async def add_verdicts(self, judgment_id: int, verdicts: list[Any]) -> int:
        """LLM 합의 결과(LLMVerdict 목록)를 llm_verdicts에 쓴다. 쓴 행 수."""
        rows = [
            LlmVerdictRow(
                judgment_id=judgment_id,
                model=str(v.model),
                approve=bool(v.approve),
                reason=str(v.reason),
                latency_ms=getattr(v, "latency_ms", None),
                cost_usd=getattr(v, "cost_usd", None),
                prompt_hash=getattr(v, "prompt_hash", "") or None,
            )
            for v in verdicts
        ]
        if not rows:
            return 0
        async with self._sessions.begin() as s:
            s.add_all(rows)
        return len(rows)

    async def set_realized(self, judgment_id: int, ret_24h: float, direction_hit: bool) -> None:
        """24시간 뒤 실현 수익률과 방향 적중 여부를 채운다."""
        async with self._sessions.begin() as s:
            row = await s.get_one(JudgmentRow, judgment_id)
            row.realized_ret_24h, row.direction_hit = ret_24h, direction_hit

    async def pending_realized(self, before: datetime, *, limit: int = 500) -> list[dict[str, Any]]:
        """before(UTC) 이전에 내려졌는데 24h 실현 수익률이 비어 있는 판단. 시각은 시장 현지 tz-naive."""
        q = (
            select(JudgmentRow, SignalRow)
            .join(SignalRow, JudgmentRow.signal_id == SignalRow.id)
            .where(JudgmentRow.realized_ret_24h.is_(None), JudgmentRow.ts <= before)
            .order_by(JudgmentRow.ts)
            .limit(limit)
        )
        async with self._sessions() as s:
            rows = (await s.execute(q)).all()
        out = []
        for j, sig in rows:
            market = Market(sig.market)
            out.append(
                {
                    "id": j.id,
                    "ts": from_db_ts(j.ts, market),
                    "market": market,
                    "symbol": sig.symbol,
                    "weight": sig.weight,
                    "price_hint": sig.price_hint,
                }
            )
        return out

    async def between(self, market: Market, start: datetime, end: datetime) -> list[dict[str, Any]]:
        """시장의 [start, end) 판단(현지 tz-naive) + 신호 심볼. 보정·A/B 리포트 입력 (06 §6)."""
        q = (
            select(JudgmentRow, SignalRow)
            .join(SignalRow, JudgmentRow.signal_id == SignalRow.id)
            .where(
                SignalRow.market == Market(market).value,
                JudgmentRow.ts >= to_db_ts(start, market),
                JudgmentRow.ts < to_db_ts(end, market),
            )
            .order_by(JudgmentRow.ts, JudgmentRow.id)
        )
        async with self._sessions() as s:
            rows = (await s.execute(q)).all()
        out = []
        for j, sig in rows:
            d = _row_dict(j)
            d.update(
                ts=from_db_ts(j.ts, Market(market)), symbol=sig.symbol, strategy_id=sig.strategy_id
            )
            out.append(d)
        return out

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


class SqlRiskEventRepo:
    """risk_events. 시장은 detail["market"]에 둔다 (표에 market 열이 없다)."""

    def __init__(self, sessions: Sessions) -> None:
        self._sessions = sessions

    async def add(self, kind: str, detail: dict[str, Any], *, ts: datetime | None = None) -> int:
        """이벤트 1건을 쓰고 id를 돌려준다."""
        row = RiskEventRow(kind=kind, detail=detail)
        if ts is not None:
            row.ts = ts
        async with self._sessions.begin() as s:
            s.add(row)
            await s.flush()
            return row.id

    async def open(self, kind: str, market: Market) -> dict[str, Any] | None:
        """그 시장의 미해결 이벤트(가장 최근). 없으면 None."""
        q = (
            select(RiskEventRow)
            .where(RiskEventRow.kind == kind, RiskEventRow.resolved_at.is_(None))
            .order_by(RiskEventRow.id.desc())
        )
        m = Market(market).value
        async with self._sessions() as s:
            for row in (await s.scalars(q)).all():
                if (row.detail or {}).get("market") == m:
                    return _row_dict(row)
        return None

    async def resolve(self, event_id: int, *, ts: datetime | None = None) -> None:
        """이벤트를 해결됨으로 표시한다."""
        async with self._sessions.begin() as s:
            row = await s.get(RiskEventRow, event_id)
            if row is not None:
                row.resolved_at = ts or datetime.now(UTC)


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

    async def strategy_id(self, name: str, market: Market) -> int | None:
        """(name, market) 전략 설정의 id. 없으면 None."""
        q = select(StrategyConfigRow.id).where(
            StrategyConfigRow.name == name, StrategyConfigRow.market == Market(market).value
        )
        async with self._sessions() as s:
            return await s.scalar(q)

    async def strategies(self, market: Market | None = None) -> list[dict[str, Any]]:
        """전략 설정 목록."""
        q = select(StrategyConfigRow).order_by(StrategyConfigRow.id)
        if market is not None:
            q = q.where(StrategyConfigRow.market == Market(market).value)
        async with self._sessions() as s:
            rows = (await s.scalars(q)).all()
        return [_row_dict(r) for r in rows]


class SqlOpsRepo:
    """운영 기록: 평가액 스냅샷(equity_snapshots)·일일 리뷰(daily_reviews)."""

    def __init__(self, sessions: Sessions) -> None:
        self._sessions = sessions

    async def add_equity_snapshot(
        self, *, market: Market, ts: datetime, cash: float, equity: float, paper: bool = True
    ) -> None:
        """평가액 1건을 쓴다. 같은 (ts, market, paper)면 덮어쓴다."""
        row = EquitySnapshotRow(
            ts=to_db_ts(ts, market),
            market=Market(market).value,
            paper=paper,
            cash=cash,
            equity=equity,
        )
        async with self._sessions.begin() as s:
            await s.merge(row)

    async def save_daily_review(
        self, day: date, *, summary: str, stats: dict[str, Any], cost_usd: float | None = None
    ) -> None:
        """그날 리뷰를 쓴다. 같은 날짜면 덮어쓴다."""
        row = DailyReviewRow(day=day, summary=summary, stats=stats, cost_usd=cost_usd)
        async with self._sessions.begin() as s:
            await s.merge(row)

    async def daily_review(self, day: date) -> dict[str, Any] | None:
        """그날 리뷰를 dict로 조회한다."""
        async with self._sessions() as s:
            row = await s.get(DailyReviewRow, day)
        return None if row is None else _row_dict(row)

    async def equity_snapshots(self, market: Market) -> list[dict[str, Any]]:
        """시장의 평가액 스냅샷 전부(시간순)."""
        q = select(EquitySnapshotRow).where(EquitySnapshotRow.market == Market(market).value)
        async with self._sessions() as s:
            rows = (await s.scalars(q.order_by(EquitySnapshotRow.ts))).all()
        return [_row_dict(r) for r in rows]


def _row_dict(row: Any) -> dict[str, Any]:
    return {c.key: getattr(row, c.key) for c in row.__mapper__.column_attrs}
