"""core dataclass ↔ ORM 행 변환. 코어는 ORM을 모르고, 변환은 여기서만 한다 (02 §4)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from quantpilot.core.models import Fill, Market, Order, OrderStatus, OrderType, Position, Side
from quantpilot.db.models import FillRow, OrderRow, PositionRow


# @MX:TODO P1-02 core/clock.py가 생기면 시장별 현지시간 ↔ UTC 변환으로 교체 (ADR 0008 §5)
def to_db_ts(ts: datetime) -> datetime:
    """DB에 쓸 UTC tz-aware 시각. tz-naive는 UTC로 간주한다."""
    return ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts.astimezone(UTC)


def from_db_ts(ts: datetime) -> datetime:
    """DB에서 읽은 시각을 UTC tz-naive로 돌려준다 (SQLite는 tz를 잃으므로 naive면 그대로)."""
    return ts if ts.tzinfo is None else ts.astimezone(UTC).replace(tzinfo=None)


def _opt_ts(ts: datetime | None, conv: Callable[[datetime], datetime]) -> datetime | None:
    return None if ts is None else conv(ts)


def _require_market(market: Market | None, what: str) -> str:
    if market is None:
        raise ValueError(f"{what}.market이 없으면 DB에 쓸 수 없다")
    return Market(market).value


def order_to_row(order: Order) -> OrderRow:
    """Order → orders 행. market·ts가 비어 있으면 ValueError."""
    if order.ts is None:
        raise ValueError("Order.ts가 없으면 DB에 쓸 수 없다")
    return OrderRow(
        id=order.id,
        broker_order_id=order.broker_order_id,
        signal_id=order.signal_id,
        ts=to_db_ts(order.ts),
        market=_require_market(order.market, "Order"),
        symbol=order.symbol,
        side=Side(order.side).value,
        type=OrderType(order.type).value,
        qty=order.qty,
        limit_price=order.limit_price,
        stop=order.stop,
        status=OrderStatus(order.status).value,
        reject_reason=order.reject_reason or None,
        risk_adjustments=list(order.risk_adjustments),
        size_multiplier=order.size_multiplier,
        strategy=order.strategy,
        paper=order.paper,
    )


def row_to_order(row: OrderRow) -> Order:
    """orders 행 → Order. reason은 주문 테이블에 없어 빈 문자열."""
    return Order(
        symbol=row.symbol,
        side=Side(row.side),
        qty=float(row.qty),
        type=OrderType(row.type),
        limit_price=row.limit_price,
        strategy=row.strategy,
        stop=row.stop,
        id=row.id,
        status=OrderStatus(row.status),
        reject_reason=row.reject_reason or "",
        market=Market(row.market),
        signal_id=row.signal_id,
        broker_order_id=row.broker_order_id,
        ts=from_db_ts(row.ts),
        risk_adjustments=list(row.risk_adjustments or []),
        size_multiplier=row.size_multiplier,
        paper=row.paper,
    )


def fill_to_row(fill: Fill) -> FillRow:
    """Fill → fills 행. market이 비어 있으면 ValueError."""
    return FillRow(
        order_id=fill.order_id,
        ts=to_db_ts(fill.ts),
        market=_require_market(fill.market, "Fill"),
        symbol=fill.symbol,
        side=Side(fill.side).value,
        qty=fill.qty,
        price=fill.price,
        fee=fill.fee,
        tax=fill.tax,
        strategy=fill.strategy,
        reason=fill.reason or None,
        paper=fill.paper,
    )


def row_to_fill(row: FillRow) -> Fill:
    """fills 행 → Fill."""
    return Fill(
        order_id=row.order_id,
        symbol=row.symbol,
        side=Side(row.side),
        qty=float(row.qty),
        price=float(row.price),
        fee=float(row.fee),
        tax=float(row.tax),
        ts=from_db_ts(row.ts),
        strategy=row.strategy,
        reason=row.reason or "",
        market=Market(row.market),
        paper=row.paper,
    )


def position_to_row(position: Position) -> PositionRow:
    """Position → positions 행. market이 비어 있으면 ValueError."""
    return PositionRow(
        market=_require_market(position.market, "Position"),
        symbol=position.symbol,
        strategy=position.strategy,
        qty=position.qty,
        avg_price=position.avg_price,
        opened_at=_opt_ts(position.opened_at, to_db_ts),
        stop=position.stop,
    )


def row_to_position(row: PositionRow) -> Position:
    """positions 행 → Position."""
    return Position(
        symbol=row.symbol,
        qty=float(row.qty),
        avg_price=float(row.avg_price),
        opened_at=_opt_ts(row.opened_at, from_db_ts),
        strategy=row.strategy,
        market=Market(row.market),
        stop=row.stop,
    )
