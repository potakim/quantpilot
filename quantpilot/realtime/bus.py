"""엔진 이벤트 → 허브·DB (01 §3 단계 8, 02 §2, ADR 0017 §2).

- `HubBus`: core.ports.EventBus 구현. topic을 화면 WS 채널로 바꿔 발행하고 상태 키를 갱신한다.
  시세(ticks)는 심볼당 초당 4건, portfolio는 시장당 5초에 1건으로 줄인다 (03 §3).
- `EventRecorder`: signal·judgment·warning 이벤트를 signals·judgments·llm_verdicts·risk_events에 쓴다.
  JudgmentEvent의 signal_id가 비어 있으면 같은 버스에 바로 앞서 온 신호를 쓴다 — TickRunner는
  신호 발행 → 판단 → 판단 발행을 한 코루틴에서 차례로 하므로 둘은 항상 이웃이다.
기록·발행 실패는 로그만 남기고 삼킨다 — 화면·기록 때문에 매매가 멈추면 안 된다 (01 §2 redis 행).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from quantpilot.core import clock
from quantpilot.core.events import (
    FillEvent,
    JudgmentEvent,
    OrderEvent,
    RiskEvent,
    SignalEvent,
    TradeEvent,
)
from quantpilot.core.models import Market
from quantpilot.core.ports import Hub
from quantpilot.realtime import keys
from quantpilot.realtime.hub import to_jsonable

log = logging.getLogger(__name__)

TICK_MIN_INTERVAL = 0.25  # 초당 최대 4건
PORTFOLIO_MIN_INTERVAL = 5.0
ORDERBOOK_DEPTH = 5  # 화면 호가 단계 (03 §3)
ORDERBOOK_TTL = 10.0  # 허브 ob 키 유효 시간 (02 §5) — 시세가 끊기면 10초 뒤 화면이 "호가 없음"으로


def orderbook_payload(snapshot: Any, depth: int = ORDERBOOK_DEPTH) -> dict[str, list[list[float]]]:
    """호가 스냅샷(levels[0] = 최우선) → 화면 모양 {asks: [[가격, 수량]…], bids: [[가격, 수량]…]}."""
    levels = list(snapshot.levels)[:depth]
    return {
        "asks": [[lv.ask_price, lv.ask_size] for lv in levels],
        "bids": [[lv.bid_price, lv.bid_size] for lv in levels],
    }


def _utc(ts: datetime, market: Market | None) -> datetime:
    if ts.tzinfo is not None or market is None:
        return ts if ts.tzinfo is not None else ts.replace(tzinfo=UTC)
    return clock.to_utc(ts, market)


def state_dict(state: Any) -> dict[str, Any]:
    """판단 입력(State)을 judgments.state jsonb로."""
    if state is None:
        return {}
    out = to_jsonable(state)
    if not isinstance(out, dict):
        out = {"value": out}
    render = getattr(state, "render", None)
    if callable(render):
        out["text"] = render()
    return out


def judgment_summary(
    je: JudgmentEvent,
    judgment_id: int | None,
    signal: SignalEvent | None,
    signal_id: int | None = None,
) -> dict:
    """WS `judgments` 채널 메시지 (03 §3)."""
    return {
        "id": judgment_id,
        "signal_id": signal_id or je.signal_id or None,
        "symbol": signal.target.symbol if signal else None,
        "strategy": signal.strategy if signal else None,
        "confidence": je.result.confidence,
        "gate": je.gate.value,
        "size_multiplier": je.size_multiplier,
        "blocks": list(je.blocks),
        "verdicts": [{"model": v.model, "approve": v.approve} for v in je.verdicts],
    }


class EventRecorder:
    """signals·judgments·llm_verdicts·risk_events 기록 (t10 숙제). 저장소는 주입한다."""

    def __init__(self, config: Any, signals: Any, judgments: Any, risk_events: Any) -> None:
        self.config = config
        self.signals = signals
        self.judgments = judgments
        self.risk_events = risk_events

    @classmethod
    def from_sessions(cls, sessions: Any) -> EventRecorder:
        """SQL 저장소로 만든다."""
        from quantpilot.db.repo import (
            SqlConfigRepo,
            SqlJudgmentRepo,
            SqlRiskEventRepo,
            SqlSignalRepo,
        )

        return cls(
            SqlConfigRepo(sessions),
            SqlSignalRepo(sessions),
            SqlJudgmentRepo(sessions),
            SqlRiskEventRepo(sessions),
        )

    async def signal(self, ev: SignalEvent) -> int:
        """신호 1건을 쓰고 id를 돌려준다. 전략 설정 행이 없으면 기본 설정으로 만든다 (ADR 0032)."""
        sid = await self.config.strategy_id(ev.strategy, ev.market)
        if sid is None:
            from quantpilot.strategies import REGISTRY, strategy_config

            cfg = (
                strategy_config(ev.strategy)
                if ev.strategy in REGISTRY
                else {"allocation": 0.0, "symbols": [ev.target.symbol], "enabled": True}
            )
            sid = await self.config.upsert_strategy(name=ev.strategy, market=ev.market, **cfg)
            log.warning(
                "전략 설정 행이 없어 새로 만들었다",
                extra={"strategy": ev.strategy, "market": Market(ev.market).value},
            )
        return await self.signals.add(
            strategy_id=sid, market=ev.market, target=ev.target, kind=ev.kind, ts=ev.ts
        )

    async def judgment(self, je: JudgmentEvent, signal_id: int) -> int:
        """판단 1건 + LLM 합의 결과를 쓴다. hold면 신호 결과를 judged_hold로."""
        jid = await self.judgments.add(
            signal_id=signal_id,
            result=je.result,
            state=state_dict(je.state),
            gate=je.gate.value,
            blocks=list(je.blocks),
            ts=je.ts,
        )
        if je.verdicts:
            await self.judgments.add_verdicts(jid, list(je.verdicts))
        if je.size_multiplier <= 0:
            await self.signals.set_outcome(signal_id, "judged_hold", ",".join(je.blocks) or None)
        return jid

    async def risk(self, ev: RiskEvent) -> int:
        """리스크 이벤트 1건 (시장은 detail.market에)."""
        detail = {**to_jsonable(ev.detail)}
        if ev.market is not None:
            detail["market"] = Market(ev.market).value
        return await self.risk_events.add(ev.kind, detail, ts=_utc(ev.ts, ev.market))


class HubBus:
    """EventBus → 허브 채널. 시장 하나의 TickRunner·MarketEngine이 쓴다."""

    def __init__(
        self,
        hub: Hub,
        market: Market,
        *,
        recorder: EventRecorder | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.hub = hub
        self.market = Market(market)
        self.recorder = recorder
        self._monotonic = monotonic
        self._last_emit: dict[str, float] = {}
        self._last_signal: tuple[int | None, SignalEvent] | None = None

    def _due(self, key: str, interval: float) -> bool:
        now = self._monotonic()
        last = self._last_emit.get(key)
        if last is not None and now - last < interval:
            return False
        self._last_emit[key] = now
        return True

    async def publish(self, topic: str, event: object) -> None:
        """이벤트 1건. 실패해도 예외를 올리지 않는다."""
        try:
            await self._publish(topic, event)
        except Exception:
            log.exception("hub publish failed", extra={"topic": topic})

    async def _publish(self, topic: str, event: object) -> None:
        m = self.market.value
        if isinstance(event, TradeEvent):
            await self.hub.set(keys.px(m, event.symbol), event.price, ttl=60)
            await self.hub.set(keys.feed(m), True, ttl=60)
            if self._due(f"tick:{event.symbol}", TICK_MIN_INTERVAL):
                data = {
                    "price": event.price,
                    "volume": event.qty,
                    "side": event.side.value if event.side else None,
                }
                await self.hub.publish(
                    keys.ticks_channel(m, event.symbol), message(event.ts, data, self.market)
                )
        elif topic == "tick":
            equity = getattr(event, "equity", None)
            await self.hub.set(keys.eq(m), equity)
            if self._due("portfolio", PORTFOLIO_MIN_INTERVAL):
                ts = getattr(event, "ts", None)
                await self.hub.publish(
                    "portfolio", message(ts, {"market": m, "equity": equity}, self.market)
                )
        elif isinstance(event, SignalEvent):
            sid = event.signal_id
            if sid is None and self.recorder is not None:
                sid = await self.recorder.signal(event)
            self._last_signal = (sid, event)
        elif isinstance(event, JudgmentEvent):
            signal = self._last_signal[1] if self._last_signal else None
            sid = event.signal_id or (self._last_signal[0] if self._last_signal else None)
            jid = None
            if self.recorder is not None and sid:
                jid = await self.recorder.judgment(event, sid)
            await self.hub.publish(
                "judgments",
                message(event.ts, judgment_summary(event, jid, signal, sid), self.market),
            )
        elif isinstance(event, FillEvent):
            await self.hub.publish(
                "fills", message(event.fill.ts, to_jsonable(event.fill), self.market)
            )
        elif isinstance(event, OrderEvent):
            await self.hub.publish(
                "orders", message(event.ts, to_jsonable(event.order), self.market)
            )
        elif isinstance(event, RiskEvent):
            eid = await self.recorder.risk(event) if self.recorder is not None else None
            data = {"id": eid, "kind": event.kind, "market": m, "detail": event.detail}
            await self.hub.publish("risk", message(event.ts, data, self.market))
        else:
            log.debug("unrouted event", extra={"topic": topic, "event": type(event).__name__})


def message(
    ts: datetime | None, data: dict[str, Any], market: Market | None = None
) -> dict[str, Any]:
    """허브 메시지 형식 {ts, data}. ts는 UTC ISO-8601 (tz-naive면 시장 현지시간으로 보고 변환)."""
    ts = datetime.now(UTC) if ts is None else _utc(ts, market)
    return {"ts": ts.isoformat(), "data": to_jsonable(data)}
