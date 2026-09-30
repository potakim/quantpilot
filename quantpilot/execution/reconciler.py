"""Reconciler — 브로커 계좌와 DB 원장을 대조한다 (04 §5.4, 07 §7.3, ADR 0015).

엔진(scheduler) 시작 시와 매 5분: 브로커 `positions()`·`cash()`와 DB `positions`·평가액 현금을 대조한다.
심볼별 수량 차이가 최소 주문 단위 이상이면 `risk_events(reconcile_mismatch)` + 할트 + critical 알림.
할트는 `EngineLink`의 할트 키로 엔진에 전해지고, 해제는 사람이 `accept_broker`("브로커 기준으로 맞추기")를
눌렀을 때만 한다. 불일치가 사라져도 스스로 풀지 않는다. RiskRules는 건드리지 않는다 (불변식 #6).
현금 차이는 할트 사유가 아니다 — 평가액 스냅샷이 1분 늦을 수 있어서 warning만 한 번 보낸다.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Protocol

from quantpilot.core.models import Market, Position
from quantpilot.core.ports import EngineLink, Notifier
from quantpilot.core.repos import PositionRepo, RiskEventRepo

log = logging.getLogger(__name__)

KIND = "reconcile_mismatch"
# 시장별 최소 주문 수량 단위: 코인 소수 8자리, 국내·미국 주식 1주 (CLAUDE.md 코딩 규칙)
MIN_QTY: dict[Market, float] = {Market.UPBIT: 1e-8, Market.KRX: 1.0, Market.US: 1.0}
# 현금 차이 허용치: 원화 1원, 달러 1센트
CASH_TOL: dict[Market, float] = {Market.UPBIT: 1.0, Market.KRX: 1.0, Market.US: 0.01}
# 부동소수 오차로 정확히 1단위 차이가 단위보다 살짝 작게 나오는 것을 막는다
_REL_EPS = 1e-6

CashRef = Callable[[Market], Awaitable["float | None"]]


class Account(Protocol):
    """대조 대상 계좌: BrokerAdapter의 조회 부분."""

    def positions(self) -> Mapping[str, Position]:
        """심볼별 보유 포지션."""
        ...

    def cash(self) -> float:
        """현금."""
        ...


@dataclass(frozen=True)
class Mismatch:
    """심볼 하나의 수량 불일치."""

    symbol: str
    broker_qty: float
    db_qty: float

    def as_dict(self) -> dict[str, float | str]:
        """risk_events.detail에 넣을 모양."""
        return {"symbol": self.symbol, "broker_qty": self.broker_qty, "db_qty": self.db_qty}


@dataclass
class ReconcileResult:
    """대조 1회 결과."""

    market: Market
    mismatches: list[Mismatch] = field(default_factory=list)
    cash_diff: float | None = None
    event_id: int | None = None

    @property
    def halted(self) -> bool:
        """이번 대조로 할트가 걸려 있어야 하는가."""
        return bool(self.mismatches)


def diff_positions(
    broker: Mapping[str, Position], db: Iterable[Position], market: Market
) -> list[Mismatch]:
    """심볼별 수량 차이가 최소 주문 단위 이상인 것만 돌려준다. DB는 전략별 행을 심볼로 합산한다."""
    unit = MIN_QTY[Market(market)]
    db_qty: dict[str, float] = defaultdict(float)
    for p in db:
        db_qty[p.symbol] += p.qty
    br_qty = {s: p.qty for s, p in broker.items() if p.is_open}
    out: list[Mismatch] = []
    for sym in sorted(set(db_qty) | set(br_qty)):
        b, d = br_qty.get(sym, 0.0), db_qty.get(sym, 0.0)
        if abs(b - d) >= unit * (1 - _REL_EPS):
            out.append(Mismatch(sym, b, d))
    return out


def alert_key(market: Market) -> str:
    """정합 불일치 critical 알림을 묶는 key."""
    return f"reconcile.{Market(market).value}"


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Reconciler:
    """브로커 ↔ DB 대조와 사람 조작 해제."""

    def __init__(
        self,
        positions: PositionRepo,
        events: RiskEventRepo,
        link: EngineLink,
        notifier: Notifier,
        *,
        cash_ref: CashRef | None = None,
        utcnow: Callable[[], datetime] = _utcnow,
    ) -> None:
        self.positions = positions
        self.events = events
        self.link = link
        self.notifier = notifier
        self.cash_ref = cash_ref
        self.utcnow = utcnow
        self._cash_warned: set[Market] = set()

    async def run(self, market: Market, account: Account) -> ReconcileResult:
        """대조 1회. 불일치면 risk_event(미해결 건이 없을 때만 새로) + 할트 + critical 알림."""
        market = Market(market)
        res = ReconcileResult(
            market, diff_positions(account.positions(), await self.positions.all(market), market)
        )
        await self._check_cash(market, account, res)
        if not res.mismatches:
            return res
        items = [m.as_dict() for m in res.mismatches]
        opened = await self.events.open(KIND, market)
        if opened is None:
            detail = {"market": market.value, "mismatches": items, "cash_diff": res.cash_diff}
            res.event_id = await self.events.add(KIND, detail, ts=self.utcnow())
            # CRITICAL 로그는 로그→알림 핸들러가 또 보내므로 여기선 error로 남긴다
            log.error(
                "reconcile mismatch",
                extra={"market": market.value, "mismatches": items, "event_id": res.event_id},
            )
        else:
            res.event_id = opened["id"]
        await self.link.halt(market, KIND, {"event_id": res.event_id})
        text = f"reconcile mismatch ({market.value}): " + ", ".join(
            f"{m.symbol} broker={m.broker_qty:g} db={m.db_qty:g}" for m in res.mismatches
        )
        await self.notifier.send("critical", f"{text} — new entries halted", key=alert_key(market))
        return res

    async def _check_cash(self, market: Market, account: Account, res: ReconcileResult) -> None:
        if self.cash_ref is None:
            return
        ref = await self.cash_ref(market)
        if ref is None:
            return
        diff = account.cash() - ref
        if abs(diff) <= CASH_TOL[market]:
            self._cash_warned.discard(market)
            return
        res.cash_diff = diff
        if market not in self._cash_warned:  # 상태가 바뀔 때 한 번만
            self._cash_warned.add(market)
            await self.notifier.send(
                "warning", f"reconcile cash differs ({market.value}): broker-db={diff:+,.2f}"
            )

    async def accept_broker(self, market: Market, account: Account) -> int:
        """사람 조작 전용: DB 포지션을 브로커 기준으로 덮고 이벤트를 해결, 할트를 푼다. 고친 심볼 수.

        전략 행이 하나면 그 행의 수량을 고치고, 여럿이거나 없으면 한 행("reconciled")으로 합친다.
        """
        market = Market(market)
        db = await self.positions.all(market)
        broker = account.positions()
        fixed = diff_positions(broker, db, market)
        rows: dict[str, list[Position]] = defaultdict(list)
        for p in db:
            rows[p.symbol].append(p)
        for m in fixed:
            mine = rows.get(m.symbol, [])
            keep = mine[0] if len(mine) == 1 else None
            strategy = keep.strategy if keep is not None else "reconciled"
            for p in mine:
                if p.strategy != strategy:
                    await self.positions.upsert(replace(p, qty=0.0))  # 수량 0 → 행 삭제
            bp = broker.get(m.symbol)
            await self.positions.upsert(
                Position(
                    m.symbol,
                    qty=bp.qty if bp else 0.0,
                    avg_price=bp.avg_price if bp else 0.0,
                    opened_at=bp.opened_at if bp else None,
                    strategy=strategy,
                    market=market,
                    stop=keep.stop if keep is not None else None,
                )
            )
        opened = await self.events.open(KIND, market)
        if opened is not None:
            await self.events.resolve(opened["id"], ts=self.utcnow())
        await self.link.clear_halt(market)
        await self.notifier.resolve(alert_key(market))
        await self.notifier.send(
            "info",
            f"reconcile accepted broker ({market.value}): {len(fixed)} symbol(s) — halt released",
        )
        log.info("reconcile accept_broker", extra={"market": market.value, "fixed": len(fixed)})
        return len(fixed)
