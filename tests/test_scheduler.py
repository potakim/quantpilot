"""스케줄러 (P1-09, 04 §8, 05 §5). APScheduler 없이 돈다 — 등록은 가짜 스케줄러로 확인한다."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from quantpilot.backtest import ZERO
from quantpilot.core.clock import to_local
from quantpilot.core.events import BarClosed
from quantpilot.core.models import JudgeResult, Market, Position, Side, Target
from quantpilot.data.aggregator import CandleAggregator
from quantpilot.db.models import Base
from quantpilot.db.repo import (
    SqlCandleRepo,
    SqlConfigRepo,
    SqlJudgmentRepo,
    SqlLedger,
    SqlOpsRepo,
    SqlPositionRepo,
    SqlSignalRepo,
)
from quantpilot.engine.link import SettingsEngineLink
from quantpilot.engine.main import MarketEngine
from quantpilot.engine.replay import CollectingBus, DirectExecutor, ReplayClock, StubFeatureBuilder
from quantpilot.engine.tick import TickRunner
from quantpilot.execution.paper import PaperBroker
from quantpilot.execution.risk import RiskManager
from quantpilot.judgment.stub import StubPipeline
from quantpilot.scheduler import registry
from quantpilot.scheduler.backup import backup_factory
from quantpilot.scheduler.context import JobContext
from quantpilot.scheduler.jobs import data, exits, health
from quantpilot.strategies import create

UP = Market.UPBIT
NY = ZoneInfo("America/New_York")
T0 = datetime(2026, 9, 30, 0, 0, tzinfo=UTC)  # = 09:00 KST


# ── 공용 가짜 ─────────────────────────────────────────────
class Now:
    """조작 가능한 UTC 시계."""

    def __init__(self, t: datetime = T0):
        self.t = t

    def __call__(self) -> datetime:
        return self.t


class MemConfig:
    def __init__(self):
        self.d: dict = {}

    async def get_setting(self, key, default=None):
        return self.d.get(key, default)

    async def set_setting(self, key, value):
        self.d[key] = value

    async def strategies(self, market=None):
        return []  # 전략 설정 행 없음 → 엔진은 기본 설정 (ADR 0032)


class Notes:
    def __init__(self):
        self.sent: list[tuple[str, str]] = []
        self.keys: list[str | None] = []
        self.resolved: list[str] = []

    async def send(self, level, text, *, key=None):
        self.sent.append((level, text))
        self.keys.append(key)

    async def resolve(self, key):
        self.resolved.append(key)


class SpyRunner:
    """on_time_exit 호출만 기록하는 TickRunner 대역."""

    def __init__(self, names=("vol_breakout",)):
        self.market = UP
        self.strategies = [create(n) for n in names]
        self.risk = RiskManager()
        self.calls: list[str] = []

    async def on_time_exit(self, name):
        self.calls.append(name)


def _ctx(now: Now, *, backup=None, **kw) -> JobContext:
    async def no_backup(market, strategy):
        raise AssertionError("엔진이 살아 있는데 백업 청산을 만들었다")

    link = SettingsEngineLink(MemConfig(), utcnow=now)
    return JobContext(link=link, backup=backup or no_backup, utcnow=now, notifier=Notes(), **kw)


@pytest.fixture
async def sessions():
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


def _bar(sym, ts, *, o=100.0, h=100.0, lo=100.0, c=100.0, market=UP) -> BarClosed:
    return BarClosed(market, sym, "1m", ts, o, h, lo, c, 1.0)


# ── 잡 등록 ──────────────────────────────────────────────
DOC_JOBS = {
    "upbit_daily_exit", "krx_close_orders", "us_orb_entry_window", "us_eod_exit", "gem_rebalance",
    "kis_token_refresh", "upbit_prescreen", "morning_brief", "news_collect", "fill_realized_24h",
    "daily_review", "equity_snapshot", "reconcile", "month_roll", "engine_heartbeat",
    "alert_repeat",  # 07 §6 critical 5분 반복 (P1-10)
}  # fmt: skip


class FakeScheduler:
    def __init__(self):
        self.jobs: dict[str, dict] = {}

    def add_job(self, func, trigger, **kw):
        assert kw["id"] not in self.jobs
        self.jobs[kw["id"]] = {"func": func, "trigger": trigger, **kw}


def test_all_jobs_of_doc_table_are_registered():
    sch = FakeScheduler()
    ids = registry.register(sch)
    assert {j["name"] for j in sch.jobs.values()} == DOC_JOBS
    assert set(ids) == set(sch.jobs)
    for j in sch.jobs.values():
        # 잡 저장소에는 모듈 함수 + 잡 이름만 들어간다
        assert j["func"] is registry.run_job and j["args"] == [j["name"]]
        assert j["replace_existing"] and j["coalesce"] and j["max_instances"] == 1


@pytest.mark.parametrize(
    ("name", "trigger", "fields"),
    [
        ("upbit_daily_exit", "cron", {"hour": 9, "minute": 0, "timezone": "Asia/Seoul"}),
        ("kis_token_refresh", "cron", {"hour": 8, "minute": 0, "timezone": "Asia/Seoul"}),
        ("upbit_prescreen", "cron", {"hour": 8, "minute": 10, "timezone": "Asia/Seoul"}),
        ("morning_brief", "cron", {"hour": 8, "minute": 30, "timezone": "Asia/Seoul"}),
        ("krx_close_orders", "cron", {"hour": 15, "minute": 20, "day_of_week": "mon-fri"}),
        ("news_collect", "cron", {"minute": 5}),
        ("fill_realized_24h", "cron", {"minute": 10}),
        ("daily_review", "cron", {"hour": 20, "minute": 30, "timezone": "Asia/Seoul"}),
        ("month_roll", "cron", {"day": 1, "hour": 0, "minute": 0, "timezone": "UTC"}),
        ("equity_snapshot", "interval", {"seconds": 60}),
        ("reconcile", "interval", {"seconds": 300}),
        ("engine_heartbeat", "interval", {"seconds": 30}),
    ],
)
def test_job_times_match_doc(name, trigger, fields):
    spec = registry.SPECS[name]
    assert spec.trigger == trigger
    assert fields.items() <= spec.fields.items()


@pytest.mark.parametrize(
    ("name", "day", "kst"),
    [
        ("us_orb_entry_window", date(2026, 7, 15), "22:35"),  # 서머타임
        ("us_orb_entry_window", date(2026, 1, 15), "23:35"),  # 표준시
        ("us_eod_exit", date(2026, 7, 15), "04:55"),
        ("us_eod_exit", date(2026, 1, 15), "05:55"),
    ],
)
def test_us_jobs_follow_daylight_saving(name, day, kst):
    f = registry.SPECS[name].fields
    et = datetime(
        day.year, day.month, day.day, f["hour"], f["minute"], tzinfo=ZoneInfo(f["timezone"])
    )
    assert et.astimezone(ZoneInfo("Asia/Seoul")).strftime("%H:%M") == kst


# ── 우편함 ────────────────────────────────────────────────
async def test_settings_link_roundtrip(sessions):
    now = Now()
    link = SettingsEngineLink(SqlConfigRepo(sessions), utcnow=now)
    assert await link.last_beat(UP) is None
    await link.beat(UP)
    assert await link.last_beat(UP) == T0
    assert await link.pending_time_exit(UP, "vol_breakout") is None
    cmd = await link.request_time_exit(UP, "vol_breakout")
    assert await link.pending_time_exit(UP, "vol_breakout") == cmd
    assert await link.pending_time_exit(UP, "orb") is None  # 전략마다 따로
    await link.ack_time_exit(UP, "vol_breakout", cmd)
    assert await link.pending_time_exit(UP, "vol_breakout") is None
    cmd2 = await link.request_time_exit(UP, "vol_breakout")
    assert cmd2 != cmd and await link.pending_time_exit(UP, "vol_breakout") == cmd2


# ── upbit_daily_exit → TickRunner.on_time_exit ────────────
async def test_daily_exit_with_live_engine_runs_engine_on_time_exit():
    now = Now()
    ctx = _ctx(now)
    await ctx.link.beat(UP)  # 엔진 살아 있음
    await registry.SPECS["upbit_daily_exit"].func(ctx)

    runner = SpyRunner()
    local = to_local(now(), UP)
    eng = MarketEngine(runner, CandleAggregator("1m", UP), ReplayClock(), link=ctx.link)
    await eng.on_link(local)
    assert runner.calls == ["vol_breakout"]
    assert await ctx.link.pending_time_exit(UP, "vol_breakout") is None  # ack됨
    await eng.on_link(local + timedelta(seconds=10))
    assert runner.calls == ["vol_breakout"]  # 같은 명령은 한 번만
    assert await ctx.link.last_beat(UP) == T0  # 엔진이 하트비트를 썼다


async def test_engine_timer_writes_heartbeat_every_link_period():
    now = Now()
    link = SettingsEngineLink(MemConfig(), utcnow=now)
    clock = ReplayClock()
    eng = MarketEngine(SpyRunner(), CandleAggregator("1m", UP), clock, link=link, link_every=5)
    t = to_local(now(), UP)
    clock.set(t)
    await eng.on_timer()
    assert await link.last_beat(UP) == T0
    now.t = T0 + timedelta(seconds=3)
    clock.set(t + timedelta(seconds=3))
    await eng.on_timer()
    assert await link.last_beat(UP) == T0  # 5초 전에는 다시 쓰지 않음
    now.t = T0 + timedelta(seconds=6)
    clock.set(t + timedelta(seconds=6))
    await eng.on_timer()
    assert await link.last_beat(UP) == now.t


def _backup_runner(calls: list[str]):
    """열린 vol_breakout 포지션 하나를 든 페이퍼 TickRunner. risk.check → submit 순서를 기록한다."""

    class SpyRisk(RiskManager):
        def check(self, order, **kw):
            calls.append(f"check:{order.symbol}")
            return super().check(order, **kw)

    class SpyBroker(PaperBroker):
        def submit(self, order):
            calls.append(f"submit:{order.symbol}")
            return super().submit(order)

    risk, broker, clock = SpyRisk(), SpyBroker(UP, ZERO, 1_000_000), ReplayClock()
    clock.set(to_local(T0, UP))
    broker._positions["KRW-BTC"] = Position("KRW-BTC", 0.01, 50_000_000, strategy="vol_breakout")
    broker.on_price("KRW-BTC", 51_000_000, clock.now())
    runner = TickRunner(
        UP, [create("vol_breakout")], StubFeatureBuilder("upbit"), StubPipeline(),
        DirectExecutor(broker, risk), risk, clock, CollectingBus(), cost=ZERO,
    )  # fmt: skip
    return runner, broker


@pytest.mark.invariant
async def test_daily_exit_without_heartbeat_backup_exits_through_risk_check():
    calls: list[str] = []
    runner, broker = _backup_runner(calls)
    built: list[tuple] = []

    async def backup(market, strategy):
        built.append((market, strategy))
        return runner

    now = Now()
    ctx = _ctx(now, backup=backup)
    await ctx.link.beat(UP)
    now.t = T0 + timedelta(seconds=91)  # 하트비트 91초 전 → 죽었다고 본다
    await exits.upbit_daily_exit(ctx)

    assert built == [(UP, "vol_breakout")]
    assert calls == ["check:KRW-BTC", "submit:KRW-BTC"]  # 불변식 #9
    assert not broker.positions()
    assert broker.ledger[-1].side == Side.SELL
    assert await ctx.link.pending_time_exit(UP, "vol_breakout") is None
    assert any("backup time exit" in t for _, t in ctx.notifier.sent)


@pytest.mark.invariant
async def test_backup_exit_allowed_while_halted():
    calls: list[str] = []
    runner, broker = _backup_runner(calls)
    runner.risk.halted_reason = "monthly circuit breaker"

    async def backup(market, strategy):
        return runner

    now = Now()
    ctx = _ctx(now, backup=backup)
    await exits.upbit_daily_exit(ctx)  # 하트비트 없음
    assert not broker.positions()  # 청산은 할트와 무관 (불변식 #6)


# ── engine_heartbeat 백업 모드 ────────────────────────────
async def test_heartbeat_loss_arms_backup_once_and_recovers():
    now = Now()
    ctx = _ctx(now)
    await ctx.link.beat(UP)
    await health.engine_heartbeat(ctx)
    assert ctx.notifier.sent == [] and not ctx.backup_armed

    now.t = T0 + timedelta(seconds=120)
    await health.engine_heartbeat(ctx)
    await health.engine_heartbeat(ctx)
    crit = [t for lv, t in ctx.notifier.sent if lv == "critical"]
    assert crit == ["engine heartbeat lost (upbit) — backup exit armed"]  # 한 번만
    assert UP in ctx.backup_armed

    await ctx.link.beat(UP)
    await health.engine_heartbeat(ctx)
    assert ctx.notifier.sent[-1] == ("info", "engine heartbeat recovered (upbit)")
    assert not ctx.backup_armed


async def test_heartbeat_never_started_engine_is_not_an_alarm():
    """첫 배포: scheduler가 엔진보다 먼저 뜬다 — 한 번도 하트비트가 없으면 critical·백업 모드 없음 (ADR 0029)."""
    now = Now()
    ctx = _ctx(now)
    now.t = T0 + timedelta(seconds=300)
    await health.engine_heartbeat(ctx)
    assert ctx.notifier.sent == [] and not ctx.backup_armed
    await ctx.link.beat(UP)  # 엔진이 뜬 뒤 끊기면 지금처럼 경보
    now.t = T0 + timedelta(seconds=600)
    await health.engine_heartbeat(ctx)
    assert [lv for lv, _ in ctx.notifier.sent] == ["critical"] and UP in ctx.backup_armed


async def test_heartbeat_backup_runs_command_engine_never_acked():
    runner = SpyRunner()

    async def backup(market, strategy):
        return runner

    now = Now()
    ctx = _ctx(now, backup=backup)
    await ctx.link.beat(UP)
    await exits.upbit_daily_exit(ctx)  # 엔진 살아 있음 → 명령만
    assert runner.calls == []
    now.t = T0 + timedelta(seconds=100)  # 엔진이 처리 못 하고 죽음
    await health.engine_heartbeat(ctx)
    assert runner.calls == ["vol_breakout"]
    assert await ctx.link.pending_time_exit(UP, "vol_breakout") is None
    await health.engine_heartbeat(ctx)
    assert runner.calls == ["vol_breakout"]  # 두 번 청산하지 않음


async def test_backup_factory_flattens_persisted_position(sessions):
    """DB 계좌 복원 → 백업 TickRunner.on_time_exit → OrderExecutor(원장·계좌 저장)."""
    positions = SqlPositionRepo(sessions)
    config = SqlConfigRepo(sessions)
    local_now = datetime.now(ZoneInfo("Asia/Seoul")).replace(tzinfo=None)
    await positions.upsert(
        Position("KRW-BTC", 0.01, 50_000_000, local_now, "vol_breakout", market=UP)
    )
    await config.set_setting("paper.cash.upbit", 9_500_000)
    bar_ts = local_now.replace(second=0, microsecond=0) - timedelta(minutes=2)
    await SqlCandleRepo(sessions).upsert([_bar("KRW-BTC", bar_ts, c=52_000_000)])

    runner = await backup_factory(sessions)(UP, "vol_breakout")
    assert runner.executor.last_price("KRW-BTC") == 52_000_000
    await runner.on_time_exit("vol_breakout")

    assert await positions.all(UP) == []
    fills = await SqlLedger(sessions).fills(UP)
    assert [(f.side, f.qty) for f in fills] == [(Side.SELL, pytest.approx(0.01))]
    assert await config.get_setting("paper.cash.upbit") > 9_500_000


# ── 목표가 재계산 ─────────────────────────────────────────
async def test_daily_exit_publishes_breakout_targets(sessions):
    candles = SqlCandleRepo(sessions)
    today9 = to_local(T0, UP)
    await candles.upsert(
        [
            _bar("KRW-BTC", today9 - timedelta(hours=10), h=110, lo=90, c=100),
            _bar("KRW-BTC", today9 - timedelta(minutes=1), h=105, lo=95, c=104),
            _bar("KRW-BTC", today9 - timedelta(days=2), h=999, lo=1, c=500),  # 전일 아님
        ]
    )
    bus = CollectingBus()
    now = Now()
    ctx = _ctx(now, candles=candles, bus=bus)
    await ctx.link.beat(UP)
    await exits.upbit_daily_exit(ctx)
    topic, status = bus.events[-1]
    assert topic == "strategy.status" and status["strategy"] == "vol_breakout"
    [t] = status["targets"]  # 봉이 있는 심볼만
    assert t == {"symbol": "KRW-BTC", "open": 104, "range": 20, "target": 104 + 20 * status["k"]}


# ── 미국장 폐장 5분 전 가드 ───────────────────────────────
@pytest.mark.parametrize(
    ("et", "runs"),
    [
        (datetime(2026, 7, 15, 15, 55, tzinfo=NY), True),
        (datetime(2026, 7, 15, 12, 55, tzinfo=NY), False),  # 정규일의 조기 폐장용 잡은 건너뜀
        (datetime(2026, 11, 27, 12, 55, tzinfo=NY), True),  # 추수감사절 다음 날 13:00 폐장
        (datetime(2026, 11, 27, 15, 55, tzinfo=NY), False),
        (datetime(2026, 7, 3, 15, 55, tzinfo=NY), False),  # 휴장
    ],
)
async def test_us_eod_exit_runs_only_five_minutes_before_close(et, runs):
    runner = SpyRunner(("orb",))

    async def backup(market, strategy):
        return runner

    utc = et.astimezone(UTC)
    ctx = _ctx(Now(utc), backup=backup, markets=(Market.US,))
    await exits.us_eod_exit(ctx)
    assert runner.calls == (["orb"] if runs else [])


# ── 데이터 잡 ─────────────────────────────────────────────
async def _judgment(sessions, ts_local: datetime, *, price_hint: float | None, weight=0.3) -> int:
    sid = await SqlConfigRepo(sessions).upsert_strategy(
        name="vol_breakout", market=UP, allocation=1.0, symbols=["KRW-BTC"]
    )
    sig = await SqlSignalRepo(sessions).add(
        strategy_id=sid, market=UP, target=Target("KRW-BTC", weight, price=price_hint),
        kind="entry", ts=ts_local,
    )  # fmt: skip
    return await SqlJudgmentRepo(sessions).add(
        signal_id=sig, result=JudgeResult({}, 0.7, model="stub"), state={}, gate="hold",
        blocks=[], ts=ts_local,
    )  # fmt: skip


async def test_fill_realized_24h_fills_old_judgments_only(sessions):
    now = Now(T0)
    t_old = to_local(T0 - timedelta(hours=25), UP)
    t_new = to_local(T0 - timedelta(hours=2), UP)
    old_id = await _judgment(sessions, t_old, price_hint=100.0)
    hold_id = await _judgment(sessions, t_old, price_hint=None)  # hold된 신호도 채운다
    new_id = await _judgment(sessions, t_new, price_hint=100.0)
    candles = SqlCandleRepo(sessions)
    await candles.upsert(
        [
            _bar("KRW-BTC", t_old - timedelta(minutes=1), c=80.0),
            _bar("KRW-BTC", t_old + timedelta(hours=24) - timedelta(minutes=1), c=90.0),
        ]
    )
    repo = SqlJudgmentRepo(sessions)
    ctx = _ctx(now, judgments=repo, candles=candles)
    await data.fill_realized_24h(ctx)

    old, hold, new = [await repo.get(i) for i in (old_id, hold_id, new_id)]
    assert old["realized_ret_24h"] == pytest.approx(-0.1) and old["direction_hit"] is False
    assert hold["realized_ret_24h"] == pytest.approx(0.125) and hold["direction_hit"] is True
    assert new["realized_ret_24h"] is None


async def test_news_collect_calls_collector():
    got = []

    class Collector:
        async def collect(self, now):
            got.append(now)
            return [1, 2]

    now = Now()
    await data.news_collect(_ctx(now, news=Collector()))
    assert got == [T0]


async def test_daily_review_saves_stats_and_notifies(sessions):
    ledger = SqlLedger(sessions)
    ops = SqlOpsRepo(sessions)
    now = Now(datetime(2026, 9, 30, 11, 30, tzinfo=UTC))  # 20:30 KST
    ctx = _ctx(now, ledger=ledger, ops=ops)
    await data.daily_review(ctx)
    row = await ops.daily_review(date(2026, 9, 30))
    assert row["stats"]["upbit"]["fills"] == 0
    assert "리뷰 모델 미연결" in row["summary"] and ctx.notifier.sent[-1][1] == row["summary"]

    async def reviewer(stats):
        return "오늘은 조용했다", 0.01

    ctx.reviewer = reviewer
    await data.daily_review(ctx)
    row = await ops.daily_review(date(2026, 9, 30))
    assert row["summary"] == "오늘은 조용했다" and row["cost_usd"] == pytest.approx(0.01)


# ── 평가액·월 롤 ──────────────────────────────────────────
async def test_equity_snapshot_and_month_roll(sessions):
    ops = SqlOpsRepo(sessions)
    config = MemConfig()

    async def account(market):
        return 1_000.0, 1_234.5

    now = Now(datetime(2026, 10, 1, 0, 0, 30, tzinfo=UTC))
    ctx = _ctx(now, ops=ops, config=config, account=account)
    await health.equity_snapshot(ctx)
    [snap] = await ops.equity_snapshots(UP)
    assert (snap["cash"], snap["equity"]) == (1_000.0, 1_234.5)

    await health.month_roll(ctx)
    assert config.d["month_start_equity.upbit"] == {"month": "2026-10", "equity": 1_234.5}


# ── run_job ──────────────────────────────────────────────
async def test_unwired_job_is_skipped(caplog):
    registry.install(_ctx(Now()))
    with caplog.at_level(logging.INFO, logger="quantpilot.scheduler.context"):
        await registry.run_job("krx_close_orders")
    assert any(getattr(r, "job", "") == "krx_close_orders" for r in caplog.records)


@pytest.mark.invariant
async def test_failed_job_is_contained_and_secret_not_logged(monkeypatch, caplog):
    secret = "QP_UPBIT_SECRET_abc123"

    async def boom(ctx):
        raise RuntimeError(f"auth failed key={secret}")

    spec = registry.SPECS["news_collect"]
    monkeypatch.setitem(
        registry.SPECS, "news_collect", registry.JobSpec(spec.name, "cron", {}, boom)
    )
    ctx = _ctx(Now())
    registry.install(ctx)
    with caplog.at_level(logging.DEBUG):
        await registry.run_job("news_collect")  # 예외가 새지 않는다
    assert secret not in caplog.text
    assert all(secret not in t for _, t in ctx.notifier.sent)
    assert ctx.notifier.sent[-1] == ("warning", "job failed: news_collect (RuntimeError)")
