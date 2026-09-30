"""P1-05 OrderExecutor·RateLimiter·PersistentPaperBroker (04 §5, ADR 0011)."""

# 엔진 시각은 시장 현지 tz-naive가 규칙이다 (CLAUDE.md, ADR 0009)
# ruff: noqa: DTZ001

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from quantpilot.backtest import ZERO, Backtester, preset
from quantpilot.core.errors import BrokerError, RateLimited
from quantpilot.core.events import BarClosed
from quantpilot.core.models import Fill, Market, Order, OrderStatus, OrderType, Position, Side
from quantpilot.data import synthetic as S
from quantpilot.engine.history import BarHistory
from quantpilot.engine.replay import (
    CollectingBus,
    DirectExecutor,
    ReplayClock,
    StubFeatureBuilder,
    UnrestrictedRisk,
)
from quantpilot.engine.tick import TickRunner
from quantpilot.execution import (
    NoLimiter,
    OrderExecutor,
    PaperBroker,
    PersistentPaperBroker,
    RiskManager,
    SlidingWindowLimiter,
)
from quantpilot.execution.executor import backoff
from quantpilot.execution.ratelimit import RedisSlidingWindowLimiter, budget
from quantpilot.judgment.stub import StubPipeline
from quantpilot.strategies import create

SYM = "KRW-BTC"
PX = 100_000.0
TS = datetime(2026, 9, 30, 10, 0)


# ── 테스트 부품 ─────────────────────────────────────────


class FakeTime:
    """sleep이 시계를 앞으로 돌린다. hooks는 시간이 흐를 때마다 불린다."""

    def __init__(self) -> None:
        self.t = 0.0
        self.sleeps: list[float] = []
        self.hooks: list = []

    def monotonic(self) -> float:
        return self.t

    async def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        self.t += s
        for h in self.hooks:
            h(self.t)


class MemLedger:
    def __init__(self, *, fail: bool = False) -> None:
        self.orders: dict[str, Order] = {}
        self.statuses: list[tuple[str, OrderStatus]] = []
        self.recorded: list[Fill] = []
        self.fail = fail

    async def save_order(self, order: Order) -> None:
        self.orders[order.id] = replace(order)
        self.statuses.append((order.id, order.status))

    async def record(self, fill: Fill) -> int:
        if self.fail:
            raise RuntimeError("db down")
        self.recorded.append(fill)
        return len(self.recorded)

    async def fills(self, market, *, symbol=None, since=None):
        return list(self.recorded)


class MemSignals:
    def __init__(self) -> None:
        self.outcomes: dict[int, tuple[str, str | None]] = {}

    async def add(self, **kw) -> int:
        return 1

    async def set_outcome(self, signal_id: int, outcome: str, reason: str | None = None) -> None:
        self.outcomes[signal_id] = (outcome, reason)


class ScriptedPaper(PaperBroker):
    """제출마다 errors에서 하나씩 꺼내 던지고, stick이 남아 있으면 시장가를 대기에 둔다(미체결 흉내)."""

    def __init__(self, *, cash: float = 10_000_000, errors=(), stick: int = 0, log=None):
        super().__init__(Market.UPBIT, preset("upbit"), cash)
        self.errors = list(errors)
        self.stick = stick
        self.submits: list[str] = []
        self.cancels: list[str] = []
        self.log = log if log is not None else []
        self.on_price(SYM, PX)

    def submit(self, order: Order):
        self.submits.append(order.id)
        self.log.append("submit")
        if self.errors:
            raise self.errors.pop(0)
        if order.type == OrderType.MARKET and self.stick > 0:
            self.stick -= 1
            self._pending[order.id] = order
            return order
        return super().submit(order)

    def cancel(self, order_id: str) -> bool:
        self.cancels.append(order_id)
        return super().cancel(order_id)


class LoggingRisk(RiskManager):
    def __init__(self, log: list[str], **kw) -> None:
        super().__init__(**kw)
        self.log = log

    def check(self, order, **kw):
        self.log.append("check")
        return super().check(order, **kw)


def make(broker=None, risk=None, ledger=None, **kw):
    ft = FakeTime()
    broker = broker or ScriptedPaper()
    ex = OrderExecutor(
        broker,
        risk or RiskManager(),
        ledger or MemLedger(),
        NoLimiter(),
        signals=MemSignals(),
        sleep=ft.sleep,
        monotonic=ft.monotonic,
        **kw,
    )
    return ex, ft


def buy(qty: float = 1.0, **kw) -> Order:
    return Order(SYM, Side.BUY, qty, strategy="vol_breakout", ts=TS, signal_id=7, **kw)


async def run(ex: OrderExecutor, order: Order, price: float = PX):
    return await ex.execute(
        order,
        equity=ex.equity(),
        price=price,
        positions=dict(ex.positions()),
        horizon="swing",
        intraday_exposure=0.0,
    )


# ── 불변식 #9: risk.check → submit ───────────────────────


@pytest.mark.invariant
async def test_risk_check_runs_before_submit_and_rejection_never_reaches_broker():
    calls: list[str] = []
    broker = ScriptedPaper(log=calls)
    ex, _ = make(broker, LoggingRisk(calls))
    assert isinstance(await run(ex, buy(1.0)), Fill)
    assert calls == ["check", "submit"]

    # 할트 중 신규 진입 → 브로커에 닿지 않는다
    calls.clear()
    ex.risk.halted_reason = "api_errors:3"
    o = buy(1.0, id="big")
    o.ts = TS + timedelta(seconds=5)
    res = await run(ex, o)
    assert res.status == OrderStatus.REJECTED and res.reject_reason.startswith("risk:")
    assert calls == ["check"] and "big" not in broker.submits
    assert "big" not in ex.ledger.orders  # 브로커에 안 닿은 주문은 원장에 없다
    assert ex.signals.outcomes[7][0] == "risk_rejected"


async def test_fill_is_recorded_with_market_and_order_saved_as_filled():
    ex, _ = make()
    f = await run(ex, buy(1.0))
    assert isinstance(f, Fill) and f.market == Market.UPBIT and f.paper
    assert ex.ledger.recorded == [f]
    assert ex.ledger.orders[f.order_id].status == OrderStatus.FILLED
    assert ex.signals.outcomes[7] == ("filled", None)


# ── 재시도·백오프 ────────────────────────────────────────


def test_backoff_schedule():
    assert [backoff(i) for i in range(5)] == [0.5, 1.0, 2.0, 4.0, 4.0]


async def test_retryable_errors_back_off_then_fill_and_reset_api_errors():
    broker = ScriptedPaper(errors=[BrokerError("503", retryable=True)] * 2)
    risk = RiskManager()
    risk.api_error()
    ex, ft = make(broker, risk)
    assert isinstance(await run(ex, buy()), Fill)
    assert ft.sleeps == [0.5, 1.0]
    assert len(broker.submits) == 3
    assert risk._consecutive_api_errors == 0  # api_ok


async def test_retries_exhausted_counts_api_error_and_three_orders_halt():
    broker = ScriptedPaper(errors=[BrokerError("timeout", retryable=True)] * 12)
    risk = RiskManager()
    ex, ft = make(broker, risk)
    res = await run(ex, buy())
    assert res.status == OrderStatus.REJECTED
    assert res.reject_reason.startswith("broker_retry_exhausted")
    assert len(broker.submits) == 4 and ft.sleeps == [0.5, 1.0, 2.0]
    assert ex.ledger.orders[res.id].status == OrderStatus.REJECTED
    assert ex.signals.outcomes[7][0] == "expired"
    for i in (1, 2):
        o = buy()
        o.ts = TS + timedelta(seconds=10 * i)
        await run(ex, o)
    assert risk.halted_reason == "api_errors:3"


async def test_non_retryable_error_is_not_retried_nor_counted():
    broker = ScriptedPaper(errors=[BrokerError("insufficient funds", retryable=False)])
    risk = RiskManager()
    ex, ft = make(broker, risk)
    res = await run(ex, buy())
    assert res.status == OrderStatus.REJECTED and res.reject_reason.startswith("broker:")
    assert len(broker.submits) == 1 and ft.sleeps == []
    assert risk._consecutive_api_errors == 0


async def test_rate_limited_waits_retry_after():
    broker = ScriptedPaper(errors=[RateLimited("429", retry_after=3.0)])
    ex, ft = make(broker)
    assert isinstance(await run(ex, buy()), Fill)
    assert ft.sleeps == [3.0]


def _held(broker: PaperBroker, qty: float = 1.0) -> None:
    broker._positions[SYM] = Position(SYM, qty, PX, TS, "vol_breakout")


async def test_exit_retries_ten_times():
    broker = ScriptedPaper(errors=[BrokerError("503", retryable=True)] * 10)
    _held(broker)
    ex, ft = make(broker)
    res = await run(ex, Order(SYM, Side.SELL, 1.0, ts=TS))
    assert isinstance(res, Fill)
    assert len(ft.sleeps) == 10 and max(ft.sleeps) == 4.0


async def test_exit_failure_logs_critical(caplog):
    broker = ScriptedPaper(errors=[BrokerError("503", retryable=True)] * 11)
    _held(broker)
    ex, _ = make(broker)
    with caplog.at_level(logging.WARNING, logger="quantpilot.execution.executor"):
        res = await run(ex, Order(SYM, Side.SELL, 1.0, ts=TS))
    assert res.status == OrderStatus.REJECTED and len(broker.submits) == 11
    crit = [r for r in caplog.records if r.levelno == logging.CRITICAL]
    assert [r.getMessage() for r in crit] == ["exit_order_failed"]
    assert crit[0].symbol == SYM


# ── 대기 주문: 체결 확인·타임아웃 ─────────────────────────


async def test_market_order_unfilled_for_30s_is_cancelled_and_resubmitted_once():
    broker = ScriptedPaper(stick=1)
    ex, ft = make(broker)
    first = buy()
    f = await run(ex, first)
    assert isinstance(f, Fill) and f.order_id != first.id
    assert broker.cancels == [first.id] and len(broker.submits) == 2
    assert ft.t == pytest.approx(30.0)
    assert ex.ledger.orders[first.id].status == OrderStatus.CANCELLED
    assert ex.ledger.orders[f.order_id].status == OrderStatus.FILLED
    assert ex.risk._pending_sides[SYM] == set()


async def test_market_resubmit_also_unfilled_ends_cancelled():
    broker = ScriptedPaper(stick=2)
    ex, _ = make(broker)
    res = await run(ex, buy())
    assert isinstance(res, Order) and res.status == OrderStatus.CANCELLED
    assert len(broker.submits) == 2 and len(broker.cancels) == 2
    assert ex.ledger.recorded == []
    assert ex.signals.outcomes[7][0] == "expired"


async def test_limit_order_fills_while_waiting():
    broker = ScriptedPaper()
    ex, ft = make(broker)
    ft.hooks.append(lambda t: broker.on_price(SYM, 98_000.0, TS) if t >= 5 else None)
    o = buy(type=OrderType.LIMIT, limit_price=99_000.0)
    f = await run(ex, o)
    assert isinstance(f, Fill) and f.price == 99_000.0
    assert ex.ledger.recorded == [f]
    assert ft.t == pytest.approx(5.0)


async def test_limit_order_cancelled_after_ttl_without_resubmit():
    broker = ScriptedPaper()
    ex, ft = make(broker, limit_ttl=60.0)
    o = buy(type=OrderType.LIMIT, limit_price=90_000.0)
    res = await run(ex, o)
    assert res.status == OrderStatus.CANCELLED and res.reject_reason == "timeout"
    assert ft.t == pytest.approx(60.0) and len(broker.submits) == 1
    assert broker.pending() == []


async def test_broker_without_order_status_returns_pending_as_is():
    class Blind(ScriptedPaper):
        def order_status(self, order_id):
            return None

    broker = Blind(stick=1)
    ex, ft = make(broker)
    res = await run(ex, buy())
    assert res.status == OrderStatus.PENDING and ft.sleeps == []
    assert ex.ledger.orders[res.id].status == OrderStatus.PENDING


async def test_ledger_failure_does_not_change_fill(caplog):
    ex, _ = make(ledger=MemLedger(fail=True))
    with caplog.at_level(logging.CRITICAL, logger="quantpilot.execution.executor"):
        f = await run(ex, buy())
    assert isinstance(f, Fill) and ex.positions()[SYM].qty == 1.0
    assert "ledger_write_failed" in caplog.text


# ── 불변식 #10: 로그에 키 없음 ───────────────────────────


@pytest.mark.invariant
async def test_executor_logs_never_contain_exchange_keys(monkeypatch, caplog):
    secret = "QP-SECRET-7f3a9c"
    monkeypatch.setenv("QP_UPBIT_ACCESS_KEY", secret)
    monkeypatch.setenv("QP_UPBIT_SECRET_KEY", secret)
    broker = ScriptedPaper(errors=[BrokerError("503", retryable=True)] * 4)
    ex, _ = make(broker)
    with caplog.at_level(logging.DEBUG):
        await run(ex, buy())
    assert caplog.records  # 재시도 로그가 실제로 남았다
    for r in caplog.records:
        assert secret not in r.getMessage()
        assert all(secret not in str(v) for v in vars(r).values())


# ── TickRunner 교체 가능성 (불변식 #2) ───────────────────


def test_order_executor_is_drop_in_for_direct_executor_in_tick_runner():
    strat = create("vol_breakout")
    data = S.universe(strat.symbols, periods=200, start="2020-01-01")
    _, _, month_last = Backtester(preset(strat.market), holdout_months=0)._prepare(data)
    market = Market(strat.market)

    def go(make_exec):
        broker = PaperBroker(market, preset(market), 10_000_000)
        risk = UnrestrictedRisk()
        clock = ReplayClock(month_last)
        ex = make_exec(broker, risk)
        runner = TickRunner(
            market,
            [create("vol_breakout")],
            StubFeatureBuilder(market.value),
            StubPipeline(),
            ex,
            risk,
            clock,
            CollectingBus(),
            cost=preset(market),
            history=BarHistory(),
        )
        by_ts: dict = {}
        for sym, df in data.items():
            for ts, o, h, lo, c, v in df.itertuples(name=None):
                bar = BarClosed(market, sym, "1d", ts.to_pydatetime(), o, h, lo, c, v)
                by_ts.setdefault(ts, []).append(bar)

        async def feed():
            for ts in sorted(by_ts):
                clock.set(ts.to_pydatetime())
                await runner.on_bars_closed(by_ts[ts])

        asyncio.run(feed())
        return broker, ex

    b1, _ = go(DirectExecutor)
    ledger = MemLedger()
    b2, _ = go(lambda b, r: OrderExecutor(b, r, ledger, NoLimiter()))
    sig = [(f.symbol, f.side, f.qty, f.price, f.ts) for f in b1.ledger]
    assert len(sig) > 10
    assert [(f.symbol, f.side, f.qty, f.price, f.ts) for f in b2.ledger] == sig
    assert [(f.symbol, f.side, f.qty, f.price, f.ts) for f in ledger.recorded] == sig


# ── RateLimiter ──────────────────────────────────────────


def test_budget_is_80_percent_of_exchange_limit():
    assert budget("upbit:order") == (9, 1.0)
    assert budget("upbit:query") == (24, 1.0)
    assert budget("kis:order") == (16, 1.0)
    assert budget("kis_paper:order") == (1, 1.0)
    assert budget("alpaca") == (160, 60.0)


async def test_sliding_window_waits_when_budget_used_and_groups_are_independent():
    ft = FakeTime()
    lim = SlidingWindowLimiter(clock=ft.monotonic, sleep=ft.sleep)
    for _ in range(9):
        await lim.acquire("upbit:order")
    await lim.acquire("upbit:query")
    assert ft.sleeps == []
    await lim.acquire("upbit:order")
    assert ft.sleeps == [pytest.approx(1.0)]


async def test_executor_acquires_limiter_per_submit_attempt():
    class Counting:
        def __init__(self):
            self.groups: list[str] = []

        async def acquire(self, group):
            self.groups.append(group)

    lim = Counting()
    broker = ScriptedPaper(errors=[BrokerError("503", retryable=True)])
    ft = FakeTime()
    ex = OrderExecutor(broker, RiskManager(), MemLedger(), lim, group="upbit:order", sleep=ft.sleep)
    await run(ex, buy())
    assert lim.groups == ["upbit:order", "upbit:order"]


class FakeRedis:
    """정렬 집합 명령만 흉내 낸다."""

    def __init__(self) -> None:
        self.z: dict[str, dict[str, float]] = {}

    async def zremrangebyscore(self, key, lo, hi):
        z = self.z.setdefault(key, {})
        for m in [m for m, s in z.items() if lo <= s <= hi]:
            del z[m]

    async def zadd(self, key, mapping):
        self.z.setdefault(key, {}).update(mapping)

    async def zcard(self, key):
        return len(self.z.get(key, {}))

    async def zrem(self, key, member):
        self.z.get(key, {}).pop(member, None)

    async def zrange(self, key, start, stop, withscores=False):
        items = sorted(self.z.get(key, {}).items(), key=lambda kv: kv[1])
        return items[start : stop + 1]

    async def expire(self, key, seconds):
        pass


async def test_redis_limiter_shares_budget_across_processes():
    ft = FakeTime()
    ft.t = 1000.0
    redis = FakeRedis()
    a = RedisSlidingWindowLimiter(redis, clock=ft.monotonic, sleep=ft.sleep)
    b = RedisSlidingWindowLimiter(redis, clock=ft.monotonic, sleep=ft.sleep)
    for i in range(9):
        await (a if i % 2 else b).acquire("upbit:order")
    assert ft.sleeps == []
    await a.acquire("upbit:order")
    assert len(ft.sleeps) == 1 and ft.sleeps[0] == pytest.approx(1.0)
    assert await redis.zcard("qp:rl:upbit:order") <= 9


# ── PersistentPaperBroker + SQL 원장 (INSERT 확인) ────────


@pytest.fixture
async def sessions():
    pytest.importorskip("aiosqlite")
    from sqlalchemy import event
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import StaticPool

    from quantpilot.db.models import Base

    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _fk_on(dbapi_conn, _):
        dbapi_conn.execute("pragma foreign_keys=on")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def test_persistent_paper_writes_ledger_positions_cash_and_restores(sessions):
    from quantpilot.core.models import Target
    from quantpilot.db.repo import SqlConfigRepo, SqlLedger, SqlPositionRepo, SqlSignalRepo

    config, positions = SqlConfigRepo(sessions), SqlPositionRepo(sessions)
    ledger, signals = SqlLedger(sessions), SqlSignalRepo(sessions)
    sid = await config.upsert_strategy(
        name="vol_breakout", market=Market.UPBIT, allocation=0.5, symbols=[SYM]
    )
    signal_id = await signals.add(
        strategy_id=sid, market=Market.UPBIT, target=Target(SYM, 0.1), kind="entry", ts=TS
    )

    def broker():
        b = PersistentPaperBroker(
            Market.UPBIT, ZERO, 10_000_000, positions=positions, config=config
        )
        b.on_price(SYM, PX)
        return b

    b = broker()
    ex = OrderExecutor(b, RiskManager(), ledger, NoLimiter(), signals=signals)
    o = buy(2.0)
    o.signal_id = signal_id
    f = await run(ex, o)
    assert isinstance(f, Fill)

    rows = await ledger.fills(Market.UPBIT)
    assert [(r.order_id, r.qty, r.price) for r in rows] == [(o.id, 2.0, PX)]
    assert (await ledger.order(o.id)).status == OrderStatus.FILLED
    assert (await signals.get(signal_id))["outcome"] == "filled"

    restored = broker()
    await restored.restore()
    assert restored.cash() == pytest.approx(10_000_000 - 2 * PX)
    assert restored.positions()[SYM].qty == 2.0
    assert restored.positions()[SYM].strategy == "vol_breakout"

    # 전량 청산 → positions 행이 지워지고 현금이 돌아온다
    ex2 = OrderExecutor(restored, RiskManager(), ledger, NoLimiter())
    sell = Order(SYM, Side.SELL, 2.0, strategy="vol_breakout", ts=TS + timedelta(minutes=1))
    assert isinstance(await run(ex2, sell), Fill)
    assert await positions.all(Market.UPBIT) == []
    assert await config.get_setting("paper.cash.upbit") == pytest.approx(10_000_000)
    assert len(await ledger.fills(Market.UPBIT)) == 2
