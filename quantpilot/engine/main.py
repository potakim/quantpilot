"""엔진 진입점 — 시장 하나의 실시간 배선 (01 §3, 04 §7).

체결(WS) → on_stop_check(손절) → CandleAggregator → 봉 확정 → CandleStore 저장 → TickRunner.on_bars_closed.
EngineLink가 있으면 타이머가 link_every초마다 하트비트를 쓰고, scheduler가 넣은 시간 청산 명령을
TickRunner.on_time_exit로 실행한 뒤 ack한다 (04 §8). 수동 주문 큐(ManualOrderConsumer, ADR 0017)도
같은 타이머에서 비운다. 이벤트는 HubBus로 허브(Redis pub/sub) → api WS 허브 → 화면에 간다 (P1-12).

같은 구간의 봉은 심볼마다 확정 시점이 다르다(다음 체결이 먼저 오면 즉시, 아니면 마감 + grace 타이머).
여러 심볼을 보는 전략이 한 시각에 한 번만 평가되도록, 확정된 봉은 그 구간의 마감 + grace까지 모았다가
시각별 묶음으로 넘긴다. 시장별 MarketEngine 하나, asyncio.TaskGroup으로 스트림과 타이머를 함께 돌린다.

실행: `python -m quantpilot.engine.main` — 업비트 페이퍼 전용 (settings.paper=True가 아니면 거부).
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import TYPE_CHECKING

import pandas as pd

from quantpilot.core.clock import UPBIT_DAY_START, to_utc, upbit_trading_day
from quantpilot.core.events import BarClosed, TradeEvent
from quantpilot.core.models import Market
from quantpilot.core.ports import Clock, EngineLink
from quantpilot.data.aggregator import CandleAggregator
from quantpilot.data.store import CandleStore
from quantpilot.engine.daily import DailyRollup
from quantpilot.engine.tick import TickRunner

if TYPE_CHECKING:
    from quantpilot.core.ports import Hub
    from quantpilot.data.news import NewsRefresher
    from quantpilot.db.repo import Sessions
    from quantpilot.engine.orders import ManualOrderConsumer

log = logging.getLogger(__name__)

# 시작 시 받는 과거 일봉 수 — 변동성 돌파 warmup 25봉 + 이평 20일에 여유 (ADR 0025)
DAILY_SEED_DAYS = 60


class MarketEngine:
    """시장 하나: 체결 → 손절 검사·봉 집계, 타이머 → 확정 봉 묶음을 TickRunner로."""

    def __init__(
        self,
        runner: TickRunner,
        aggregator: CandleAggregator,
        clock: Clock,
        *,
        store: CandleStore | None = None,
        link: EngineLink | None = None,
        link_every: float = 5.0,
        orders: ManualOrderConsumer | None = None,
        news: NewsRefresher | None = None,
    ):
        self.runner = runner
        self.aggregator = aggregator
        self.clock = clock
        self.store = store
        self.link = link
        self.link_every = link_every
        self.orders = orders
        self.news = news  # scheduler가 DB에 쌓은 뉴스 → 피처 빌더 캐시 (ADR 0021)
        self._closed: list[BarClosed] = []
        self._last_link: datetime | None = None

    async def on_trade(self, ev: TradeEvent) -> None:
        """체결 1건: 손절 검사(판단 모델 없음) 후 봉 집계."""
        await self.runner.on_stop_check(ev)
        bus = getattr(self.runner, "bus", None)
        if bus is not None:
            await bus.publish("trade", ev)  # 화면 시세 (HubBus가 초당 4건으로 줄인다)
        bar = self.aggregator.on_trade(ev)
        if bar is not None:
            self._closed.append(bar)

    async def on_timer(self) -> int:
        """마감 + grace가 지난 구간의 봉을 시각별 묶음으로 넘긴다. 넘긴 봉 수를 돌려준다."""
        now = self.clock.now()
        await self.on_link(now)
        if self.news is not None:
            await self.news.maybe_refresh()
        if self.orders is not None:
            await self.orders.drain()
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
                try:
                    await self.store.upsert(groups[ts])
                except Exception as e:  # noqa: BLE001 — 저장 실패로 매매·청산이 멈추면 안 된다 (ADR 0028)
                    log.error("candle store failed", extra={"error": type(e).__name__})
            await self.runner.on_bars_closed(groups[ts])
        return len(due)

    async def restore(self) -> None:
        """재시작 직후 상태 복원 (ADR 0028): 계좌 → 월 손실 기준선 → 손절선·오늘 처리 표시."""
        for ex in (self.runner.executor, getattr(self.runner, "shadow", None)):
            restore = getattr(getattr(ex, "broker", None), "restore", None)
            if restore is not None:
                await restore()
        market = self.runner.market
        now = self.clock.now()
        done = None
        if self.link is not None:
            ms = await self.link.month_start(market)
            month = f"{now.year:04d}-{now.month:02d}"  # RiskManager는 주문 시각(현지)으로 월을 센다
            if ms and ms.get("month") == month:
                for risk in self._risks():
                    risk.month_start_equity = float(ms["equity"])
                    risk._month = (now.year, now.month)
            done = await self.link.done(market)
        self.runner.restore_state(now, done)
        log.info("engine state restored", extra={"market": market.value})

    def _risks(self) -> list:
        """ON·섀도 원장의 RiskManager (섀도도 같은 월 기준으로 서킷브레이커를 건다)."""
        out = [self.runner.risk]
        shadow = getattr(self.runner, "shadow", None)
        if shadow is not None and getattr(shadow, "risk", None) is not None:
            out.append(shadow.risk)
        return out

    async def on_link(self, now: datetime) -> None:
        """하트비트를 쓰고 scheduler의 시간 청산 명령을 처리한다 (link_every초마다)."""
        if self.link is None:
            return
        if (
            self._last_link is not None
            and (now - self._last_link).total_seconds() < self.link_every
        ):
            return
        self._last_link = now
        market = self.runner.market
        await self.link.beat(market)
        await self._sync_rules(market)
        await self._sync_done(market, now)
        await self._sync_halt(market)
        await self._sync_prescreen(market)
        for s in self.runner.strategies:
            cmd_id = await self.link.pending_time_exit(market, s.name)
            if cmd_id is None:
                continue
            log.info("time_exit 명령 처리", extra={"strategy": s.name, "cmd": cmd_id})
            await self.runner.on_time_exit(s.name)
            await self.link.ack_time_exit(market, s.name, cmd_id)

    async def _sync_done(self, market, now: datetime) -> None:
        """바뀐 오늘 처리 표시를 우편함에 쓴다 — 재시작 뒤 같은 날 다시 사지 않게 (ADR 0028)."""
        if not getattr(self.runner, "done_dirty", False) or self.runner.daily is None:
            return
        await self.link.set_done(market, self.runner.done_today(now))
        self.runner.done_dirty = False

    async def _sync_rules(self, market) -> None:
        """바뀐 규칙 평가 기록(규칙 미충족 집계)을 우편함에 합쳐 쓴다 (ADR 0027)."""
        if not getattr(self.runner, "rule_dirty", False):
            return
        await self.link.merge_rules(market, self.runner.rule_days)
        self.runner.rule_dirty = False

    async def _sync_halt(self, market) -> None:
        """scheduler(Reconciler)가 건 할트를 RiskManager에 반영한다. 풀리는 건 할트 키가 지워졌을 때만."""
        reason = await self.link.halt_reason(market)
        risk = self.runner.risk
        if reason and not risk.halted_reason:
            risk.halted_reason = reason
            log.warning("halted by link", extra={"market": market.value, "reason": reason})
        elif not reason and risk.halted_reason.startswith("reconcile"):
            risk.halted_reason = ""
            log.info("halt released", extra={"market": market.value})

    async def _sync_prescreen(self, market) -> None:
        """08:10 사전 심사 제외 목록을 판단 파이프라인에 넣는다 (ADR 0022).

        오늘 업비트 거래일(09:00 KST 경계)의 목록만 쓴다. 지난 날 것이나 없으면 제외 없음.
        파이프라인이 제외를 모르면(StubPipeline = 게이팅 OFF) 아무것도 하지 않는다.
        """
        apply = getattr(getattr(self.runner, "pipeline", None), "set_prescreen", None)
        if apply is None or market != Market.UPBIT:
            return
        got = await self.link.prescreen(market)
        today = upbit_trading_day(to_utc(self.clock.now(), market)).isoformat()
        blocked = got[1] if got is not None and got[0] == today else {}
        apply(blocked)

    async def run(self, stream, *, interval: float = 1.0) -> None:
        """스트림(run()이 체결을 on_trade로 넘김)과 타이머를 함께 돌린다."""

        async def timer() -> None:
            while True:
                await asyncio.sleep(interval)
                await self.on_timer()

        async with asyncio.TaskGroup() as tg:
            tg.create_task(stream.run())
            tg.create_task(timer())


def build_upbit_paper(
    strategy_names: Sequence[str] = ("vol_breakout",),
    *,
    sessions: Sessions | None = None,
    hub: Hub | None = None,
    daily_seed: Mapping[str, pd.DataFrame] | None = None,
) -> MarketEngine:
    """업비트 페이퍼 엔진 배선. 판단 파이프라인은 settings.judge_provider로 고른다 (stub이면 게이팅 OFF).

    게이팅 OFF 섀도 원장(06 §6.2)을 항상 함께 둔다: 같은 신호·같은 시세·같은 규칙, 배수 1.0.

    sessions가 있으면 계좌를 DB에 저장하는 PersistentPaperBroker + OrderExecutor(원장 기록)를 쓰고,
    scheduler와 settings 우편함(SettingsEngineLink)으로 연결한다. 시작 전에 `restore()`를 불러야 한다.
    hub가 있으면 이벤트를 HubBus로 발행(sessions가 있으면 DB 기록 포함)하고 수동 주문 큐를 소비한다.
    """
    from quantpilot.backtest.costs import preset
    from quantpilot.config import settings
    from quantpilot.core.clock import MarketClock
    from quantpilot.data.events import EventCalendar
    from quantpilot.data.news import CacheNewsSource, NewsCache, NewsRefresher
    from quantpilot.engine.replay import DirectExecutor
    from quantpilot.execution.paper import PaperBroker
    from quantpilot.execution.risk import RiskManager
    from quantpilot.features.builder import FeatureBuilder
    from quantpilot.judgment.pipeline import build_pipeline
    from quantpilot.strategies import create

    if not settings.paper:
        raise RuntimeError(
            "실계좌 배선은 P1-05 OrderExecutor 이후. 지금은 settings.paper=True만 허용"
        )
    market = Market.UPBIT
    strategies = [create(n) for n in strategy_names]
    risk = RiskManager()
    clock = MarketClock(market)
    cost = preset(market)
    link = None
    # 판단 입력: 지표 + 뉴스(DB → 메모리 캐시) + 이벤트 캘린더 (04 §3, ADR 0021)
    news_cache = NewsCache()
    calendar = EventCalendar.from_yaml(settings.events_file)
    features = FeatureBuilder(market, news=CacheNewsSource(news_cache), events=calendar)
    refresher = None
    store: CandleStore | None = None
    if sessions is None:
        executor = DirectExecutor(PaperBroker(market, cost, settings.initial_cash_krw), risk)
        # 게이팅 OFF 섀도: 같은 CostModel·같은 규칙의 별도 RiskManager (06 §6.2, ADR 0016)
        shadow = DirectExecutor(PaperBroker(market, cost, settings.initial_cash_krw), RiskManager())
    else:
        from quantpilot.db.repo import SqlConfigRepo, SqlLedger, SqlPositionRepo, SqlSignalRepo
        from quantpilot.engine.link import SettingsEngineLink
        from quantpilot.execution.executor import OrderExecutor
        from quantpilot.execution.persistent_paper import (
            PersistentPaperBroker,
            SettingsPositionRepo,
        )
        from quantpilot.execution.ratelimit import NoLimiter

        config = SqlConfigRepo(sessions)
        broker = PersistentPaperBroker(
            market,
            cost,
            settings.initial_cash_krw,
            positions=SqlPositionRepo(sessions),
            config=config,
        )
        executor = OrderExecutor(
            broker, risk, SqlLedger(sessions), NoLimiter(), signals=SqlSignalRepo(sessions)
        )
        shadow_broker = PersistentPaperBroker(
            market,
            cost,
            settings.initial_cash_krw,
            positions=SettingsPositionRepo(config, market),
            config=config,
            book="shadow",
        )
        # 섀도는 신호 outcome을 덮어쓰지 않는다(signals=None) — outcome은 ON 원장의 결과
        shadow = OrderExecutor(
            shadow_broker, RiskManager(), SqlLedger(sessions, shadow=True), NoLimiter()
        )
        link = SettingsEngineLink(config)
        from quantpilot.db.news_repo import SqlNewsRepo

        refresher = NewsRefresher(SqlNewsRepo(sessions), news_cache, calendar=calendar)
        from quantpilot.db.repo import SqlCandleRepo

        # 확정 1분봉 저장: 24h 수익률·계좌 평가·화면 시세·09:00 목표가가 candles 표를 읽는다 (ADR 0028)
        store = CandleStore(SqlCandleRepo(sessions), market)
    bus: object = _LogBus()
    recorder = None
    if hub is not None:
        from quantpilot.realtime.bus import EventRecorder, HubBus

        recorder = EventRecorder.from_sessions(sessions) if sessions is not None else None
        bus = HubBus(hub, market, recorder=recorder)
    runner = TickRunner(
        market,
        strategies,
        features,
        build_pipeline(settings, bus=bus),
        executor,
        risk,
        clock,
        bus,
        cost=cost,
        shadow=shadow,
        record_signal=recorder.signal if recorder is not None else None,
        # 일봉 전략은 분봉을 거래일(09:00 KST) 일봉으로 묶어 평가한다 (ADR 0025)
        daily=DailyRollup(UPBIT_DAY_START, daily_seed),
    )
    orders = None
    if hub is not None:
        from quantpilot.engine.orders import ManualOrderConsumer

        orders = ManualOrderConsumer(hub, runner)
    return MarketEngine(
        runner,
        CandleAggregator("1m", market),
        clock,
        store=store,
        link=link,
        orders=orders,
        news=refresher,
    )


class _LogBus:
    """허브 없이 돌릴 때(테스트·단독 실행) 이벤트를 로그로만 남긴다."""

    async def publish(self, topic: str, event: object) -> None:
        """이벤트 1건을 debug 로그로."""
        log.debug("event", extra={"topic": topic, "event": type(event).__name__})


def fetch_daily_seed(
    symbols: Sequence[str], count: int = DAILY_SEED_DAYS
) -> dict[str, pd.DataFrame]:
    """시작 시 업비트 REST 일봉(오늘 진행 중인 봉 포함)을 받는다. 실패한 심볼은 빼고 로그만 남긴다.

    시드가 없는 심볼은 준비 기간(warmup)이 차지 않아 그 전략이 진입하지 않는다 — 안전한 쪽으로 멈춘다.
    """
    from quantpilot.data.loader import upbit_candles

    seed: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        try:
            seed[sym] = upbit_candles(sym, "1d", count=count)
        except Exception as e:  # noqa: BLE001 — 네트워크·응답 오류는 그 심볼만 빼고 계속
            log.error("daily seed failed", extra={"symbol": sym, "error": type(e).__name__})
    return seed


def main() -> None:
    """업비트 페이퍼 엔진 실행 (네트워크 필요)."""
    from quantpilot.config import settings
    from quantpilot.data.upbit_ws import UpbitStream
    from quantpilot.db.session import make_sessions
    from quantpilot.notify.telegram import CriticalLogHandler, from_settings
    from quantpilot.realtime.hub import make_hub

    logging.basicConfig(level=logging.INFO)
    logging.getLogger().addHandler(CriticalLogHandler(from_settings(settings)))  # 청산 실패 등
    from quantpilot.strategies import create

    symbols = sorted({s for name in ("vol_breakout",) for s in create(name).symbols})
    engine = build_upbit_paper(
        sessions=make_sessions(settings.db_url),
        hub=make_hub(settings),
        daily_seed=fetch_daily_seed(symbols),
    )
    stream = UpbitStream(symbols, on_trade=engine.on_trade)

    async def _run() -> None:
        await engine.restore()
        await engine.run(stream)

    asyncio.run(_run())


if __name__ == "__main__":
    main()
