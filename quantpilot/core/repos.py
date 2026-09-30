"""저장소 인터페이스. 코어는 이 Protocol만 알고, 구현(db/repo.py)은 주입받는다 (ADR 0008)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from quantpilot.core.events import BarClosed
from quantpilot.core.models import Fill, JudgeResult, Market, Order, Position, Target


class CandleRepo(Protocol):
    """확정된 봉 저장소 (candles). 시각은 시장 현지 tz-naive로 주고받는다."""

    async def upsert(self, bars: list[BarClosed], *, source: str = "ws") -> int:
        """(ts, market, symbol, tf) 기준으로 쓰거나 덮어쓰고 처리한 행 수를 돌려준다."""
        ...

    async def load(
        self, market: Market, symbol: str, tf: str, start: datetime, end: datetime
    ) -> list[BarClosed]:
        """start 이상 end 미만(봉 시작 시각 기준) 봉을 시간순으로 조회한다."""
        ...


class Ledger(Protocol):
    """주문·체결 원장."""

    async def save_order(self, order: Order) -> None:
        """주문을 새로 쓰거나 상태를 갱신한다."""
        ...

    async def record(self, fill: Fill) -> int:
        """체결 1건을 원장에 추가하고 행 id를 돌려준다."""
        ...

    async def fills(
        self, market: Market, *, symbol: str | None = None, since: datetime | None = None
    ) -> list[Fill]:
        """체결을 시간순으로 조회한다."""
        ...


class SignalRepo(Protocol):
    """전략 신호(후보 Target) 기록."""

    async def add(
        self, *, strategy_id: int, market: Market, target: Target, kind: str, ts: datetime
    ) -> int:
        """신호를 pending으로 기록하고 id를 돌려준다."""
        ...

    async def set_outcome(self, signal_id: int, outcome: str, reason: str | None = None) -> None:
        """신호의 최종 결과(judged_hold·risk_rejected·ordered·filled·expired)를 기록한다."""
        ...


class JudgmentRepo(Protocol):
    """판단 모델 호출 기록."""

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
        ...

    async def set_realized(self, judgment_id: int, ret_24h: float, direction_hit: bool) -> None:
        """24시간 뒤 실현 수익률과 방향 적중 여부를 채운다."""
        ...


class PositionRepo(Protocol):
    """현재 포지션 스냅샷 (재시작 복원용)."""

    async def upsert(self, position: Position) -> None:
        """포지션을 저장한다. 수량이 0이면 행을 지운다."""
        ...

    async def all(self, market: Market) -> list[Position]:
        """시장의 열린 포지션 전부."""
        ...


class RiskEventRepo(Protocol):
    """risk_events: 서킷브레이커·할트·정합 불일치 기록 (02 §1.5). resolved_at이 비면 미해결."""

    async def add(self, kind: str, detail: dict[str, Any], *, ts: datetime | None = None) -> int:
        """이벤트 1건을 쓰고 id를 돌려준다."""
        ...

    async def open(self, kind: str, market: Market) -> dict[str, Any] | None:
        """그 시장의 미해결 이벤트(가장 최근). 없으면 None."""
        ...

    async def resolve(self, event_id: int, *, ts: datetime | None = None) -> None:
        """이벤트를 해결됨으로 표시한다."""
        ...


class ConfigRepo(Protocol):
    """전략 설정과 settings 키-값. 리스크 규칙은 여기 없다 (ADR 0003)."""

    async def get_setting(self, key: str, default: Any = None) -> Any:
        """settings 값을 읽는다."""
        ...

    async def set_setting(self, key: str, value: Any) -> None:
        """settings 값을 쓴다."""
        ...

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
        ...

    async def strategies(self, market: Market | None = None) -> list[dict[str, Any]]:
        """전략 설정 목록."""
        ...
