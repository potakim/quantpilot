"""엔진 진입점 — 시장 하나의 실시간 배선 (01 §3, 04 §7).

체결(WS) → on_stop_check(손절) → CandleAggregator → 봉 확정 → CandleStore 저장 → TickRunner.on_bars_closed.

같은 구간의 봉은 심볼마다 확정 시점이 다르다(다음 체결이 먼저 오면 즉시, 아니면 마감 + grace 타이머).
여러 심볼을 보는 전략이 한 시각에 한 번만 평가되도록, 확정된 봉은 그 구간의 마감 + grace까지 모았다가
시각별 묶음으로 넘긴다. 시장별 MarketEngine 하나, asyncio.TaskGroup으로 스트림과 타이머를 함께 돌린다.

실행: `python -m quantpilot.engine.main` — 업비트 페이퍼 전용 (settings.paper=True가 아니면 거부).
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Sequence

from quantpilot.core.events import BarClosed, TradeEvent
from quantpilot.core.ports import Clock
from quantpilot.data.aggregator import CandleAggregator
from quantpilot.data.store import CandleStore
from quantpilot.engine.tick import TickRunner

log = logging.getLogger(__name__)


class MarketEngine:
    """시장 하나: 체결 → 손절 검사·봉 집계, 타이머 → 확정 봉 묶음을 TickRunner로."""

    def __init__(
        self,
        runner: TickRunner,
        aggregator: CandleAggregator,
        clock: Clock,
        *,
        store: CandleStore | None = None,
    ):
        self.runner = runner
        self.aggregator = aggregator
        self.clock = clock
        self.store = store
        self._closed: list[BarClosed] = []

    async def on_trade(self, ev: TradeEvent) -> None:
        """체결 1건: 손절 검사(판단 모델 없음) 후 봉 집계."""
        await self.runner.on_stop_check(ev)
        bar = self.aggregator.on_trade(ev)
        if bar is not None:
            self._closed.append(bar)

    async def on_timer(self) -> int:
        """마감 + grace가 지난 구간의 봉을 시각별 묶음으로 넘긴다. 넘긴 봉 수를 돌려준다."""
        now = self.clock.now()
        self._closed += self.aggregator.on_timer(now)
        span, grace = self.aggregator.span, self.aggregator.grace
        due = [b for b in self._closed if now >= b.ts + span + grace]
        if not due:
            return 0
        self._closed = [b for b in self._closed if now < b.ts + span + grace]
        groups: dict[object, list[BarClosed]] = defaultdict(list)
        for b in due:
            groups[b.ts].append(b)
        for ts in sorted(groups):
            if self.store is not None:
                await self.store.upsert(groups[ts])
            await self.runner.on_bars_closed(groups[ts])
        return len(due)

    async def run(self, stream, *, interval: float = 1.0) -> None:
        """스트림(run()이 체결을 on_trade로 넘김)과 타이머를 함께 돌린다."""

        async def timer() -> None:
            while True:
                await asyncio.sleep(interval)
                await self.on_timer()

        async with asyncio.TaskGroup() as tg:
            tg.create_task(stream.run())
            tg.create_task(timer())


def build_upbit_paper(strategy_names: Sequence[str] = ("vol_breakout",)) -> MarketEngine:
    """업비트 페이퍼 엔진 배선. 판단 파이프라인은 P1-07/08 전까지 StubPipeline(게이팅 OFF)."""
    from quantpilot.backtest.costs import preset
    from quantpilot.config import settings
    from quantpilot.core.clock import MarketClock
    from quantpilot.core.models import Market
    from quantpilot.engine.replay import DirectExecutor, StubFeatureBuilder
    from quantpilot.execution.paper import PaperBroker
    from quantpilot.execution.risk import RiskManager
    from quantpilot.judgment.stub import StubPipeline
    from quantpilot.strategies import create

    if not settings.paper:
        raise RuntimeError(
            "실계좌 배선은 P1-05 OrderExecutor 이후. 지금은 settings.paper=True만 허용"
        )
    market = Market.UPBIT
    strategies = [create(n) for n in strategy_names]
    risk = RiskManager()
    broker = PaperBroker(market, preset(market), settings.initial_cash_krw)
    clock = MarketClock(market)
    runner = TickRunner(
        market,
        strategies,
        StubFeatureBuilder(market.value),
        StubPipeline(),
        DirectExecutor(broker, risk),
        risk,
        clock,
        _LogBus(),
        cost=preset(market),
    )
    return MarketEngine(runner, CandleAggregator("1m", market), clock)


class _LogBus:
    """Redis 허브(P1-12) 전까지 이벤트를 로그로만 남긴다."""

    async def publish(self, topic: str, event: object) -> None:
        """이벤트 1건을 debug 로그로."""
        log.debug("event", extra={"topic": topic, "event": type(event).__name__})


def main() -> None:
    """업비트 페이퍼 엔진 실행 (네트워크 필요)."""
    from quantpilot.data.upbit_ws import UpbitStream

    logging.basicConfig(level=logging.INFO)
    engine = build_upbit_paper()
    symbols = sorted({s for st in engine.runner.strategies for s in st.symbols})
    stream = UpbitStream(symbols, on_trade=engine.on_trade)
    asyncio.run(engine.run(stream))


if __name__ == "__main__":
    main()
