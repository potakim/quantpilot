"""P1-10 Reconciler + 알림 (04 §5.4, 07 §6·§7.3, ADR 0015)."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from quantpilot.core.clock import to_local
from quantpilot.core.models import Market, Order, Position, Side
from quantpilot.data.aggregator import CandleAggregator
from quantpilot.db.models import Base
from quantpilot.db.repo import SqlConfigRepo, SqlPositionRepo, SqlRiskEventRepo
from quantpilot.engine.link import SettingsEngineLink
from quantpilot.engine.main import MarketEngine
from quantpilot.engine.replay import ReplayClock
from quantpilot.execution.reconciler import KIND, Reconciler, alert_key, diff_positions
from quantpilot.execution.risk import RiskManager
from quantpilot.notify.telegram import (
    CriticalLogHandler,
    LogNotifier,
    RepeatingNotifier,
    TelegramNotifier,
    from_settings,
)
from quantpilot.scheduler import registry
from quantpilot.scheduler.context import JobContext
from quantpilot.scheduler.jobs import health
from quantpilot.strategies import create

UP = Market.UPBIT
T0 = datetime(2026, 9, 30, 0, 0, tzinfo=UTC)
TOKEN = "123456:SECRET-telegram-token"


# ── 가짜 ─────────────────────────────────────────────────
class Now:
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


class MemPositions:
    def __init__(self, *rows: Position):
        self.rows = {(p.symbol, p.strategy): p for p in rows}

    async def upsert(self, position):
        k = (position.symbol, position.strategy)
        if position.is_open:
            self.rows[k] = position
        else:
            self.rows.pop(k, None)

    async def all(self, market):
        return sorted(self.rows.values(), key=lambda p: (p.symbol, p.strategy))


class MemEvents:
    def __init__(self):
        self.rows: list[dict] = []

    async def add(self, kind, detail, *, ts=None):
        self.rows.append({"id": len(self.rows) + 1, "kind": kind, "detail": detail, "ts": ts,
                          "resolved_at": None})  # fmt: skip
        return len(self.rows)

    async def open(self, kind, market):
        for r in reversed(self.rows):
            if (
                r["kind"] == kind
                and r["resolved_at"] is None
                and r["detail"]["market"] == market.value
            ):
                return r
        return None

    async def resolve(self, event_id, *, ts=None):
        self.rows[event_id - 1]["resolved_at"] = ts or T0


class Notes:
    def __init__(self):
        self.sent: list[tuple[str, str, str | None]] = []
        self.resolved: list[str] = []

    async def send(self, level, text, *, key=None):
        self.sent.append((level, text, key))

    async def resolve(self, key):
        self.resolved.append(key)


class Account:
    """브로커 조회 대역."""

    def __init__(self, *pos: Position, cash: float = 1_000_000.0):
        self._pos = {p.symbol: p for p in pos}
        self._cash = cash

    def positions(self):
        return dict(self._pos)

    def cash(self):
        return self._cash


class SpyRunner:
    def __init__(self):
        self.market = UP
        self.strategies = [create("vol_breakout")]
        self.risk = RiskManager()

    async def on_time_exit(self, name):  # pragma: no cover — 이 파일에서는 쓰지 않음
        pass


def _pos(sym, qty, strategy="vol_breakout", avg=100.0):
    return Position(sym, qty=qty, avg_price=avg, strategy=strategy, market=UP)


def _rec(positions, *, notes=None, link=None, events=None, cash_ref=None, now=None):
    now = now or Now()
    return Reconciler(
        positions,
        events or MemEvents(),
        link or SettingsEngineLink(MemConfig(), utcnow=now),
        notes or Notes(),
        cash_ref=cash_ref,
        utcnow=now,
    )


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


# ── diff_positions: 최소 주문 단위 ─────────────────────────
def test_diff_sums_strategy_rows_and_ignores_sub_unit_noise():
    db = [_pos("KRW-BTC", 0.3, "a"), _pos("KRW-BTC", 0.2, "b"), _pos("KRW-ETH", 1.0)]
    broker = {"KRW-BTC": _pos("KRW-BTC", 0.5 + 4e-9), "KRW-ETH": _pos("KRW-ETH", 1.0)}
    assert diff_positions(broker, db, UP) == []


def test_diff_one_unit_is_a_mismatch_for_coin_and_stock():
    db = [_pos("KRW-BTC", 0.5)]
    [m] = diff_positions({"KRW-BTC": _pos("KRW-BTC", 0.50000001)}, db, UP)
    assert m.symbol == "KRW-BTC" and m.db_qty == 0.5
    krx = [Position("005930", qty=10, strategy="gtaa", market=Market.KRX)]
    assert diff_positions({"005930": Position("005930", qty=10.4)}, krx, Market.KRX) == []
    assert len(diff_positions({"005930": Position("005930", qty=11)}, krx, Market.KRX)) == 1


def test_diff_detects_missing_on_either_side():
    got = diff_positions({"KRW-XRP": _pos("KRW-XRP", 5.0)}, [_pos("KRW-BTC", 0.1)], UP)
    assert [(m.symbol, m.broker_qty, m.db_qty) for m in got] == [
        ("KRW-BTC", 0.0, 0.1),
        ("KRW-XRP", 5.0, 0.0),
    ]


# ── 불일치 → 할트 ────────────────────────────────────────
@pytest.mark.invariant
async def test_mismatch_halts_engine_blocks_entries_but_allows_exits():
    now = Now()
    link = SettingsEngineLink(MemConfig(), utcnow=now)
    notes, events = Notes(), MemEvents()
    rec = _rec(MemPositions(_pos("KRW-BTC", 0.4)), notes=notes, link=link, events=events, now=now)

    res = await rec.run(UP, Account(_pos("KRW-BTC", 0.5)))
    assert res.halted and res.event_id == 1
    assert events.rows[0]["kind"] == KIND
    assert events.rows[0]["detail"]["mismatches"] == [
        {"symbol": "KRW-BTC", "broker_qty": 0.5, "db_qty": 0.4}
    ]
    assert await link.halt_reason(UP) == KIND
    [(lv, text, key)] = notes.sent
    assert lv == "critical" and key == alert_key(UP) and "KRW-BTC" in text

    # 엔진이 하트비트 때 할트를 읽어 RiskManager에 반영
    runner = SpyRunner()
    eng = MarketEngine(runner, CandleAggregator("1m", UP), ReplayClock(), link=link)
    await eng.on_link(to_local(now(), UP))
    assert runner.risk.halted_reason == KIND
    positions = {"KRW-BTC": _pos("KRW-BTC", 0.5)}
    buy = runner.risk.check(Order("KRW-ETH", Side.BUY, 1.0), equity=1e7, price=1e3,
                            positions=positions, now=T0)  # fmt: skip
    sell = runner.risk.check(Order("KRW-BTC", Side.SELL, 0.5), equity=1e7, price=1e3,
                             positions=positions, now=T0)  # fmt: skip
    assert not buy.allowed and buy.reason == f"halted:{KIND}"
    assert sell.allowed  # 청산은 할트와 무관 (불변식 #6)


async def test_repeat_runs_keep_one_open_event():
    events = MemEvents()
    rec = _rec(MemPositions(_pos("KRW-BTC", 0.4)), events=events)
    acct = Account(_pos("KRW-BTC", 0.5))
    first = await rec.run(UP, acct)
    second = await rec.run(UP, acct)
    assert len(events.rows) == 1 and first.event_id == second.event_id == 1


@pytest.mark.invariant
async def test_halt_is_not_released_when_mismatch_disappears():
    link = SettingsEngineLink(MemConfig(), utcnow=Now())
    positions = MemPositions(_pos("KRW-BTC", 0.4))
    rec = _rec(positions, link=link)
    await rec.run(UP, Account(_pos("KRW-BTC", 0.5)))
    await positions.upsert(_pos("KRW-BTC", 0.5))  # 누가 DB를 고쳐도
    res = await rec.run(UP, Account(_pos("KRW-BTC", 0.5)))
    assert not res.mismatches
    assert await link.halt_reason(UP) == KIND  # 해제는 사람 조작(accept_broker)만


async def test_match_does_nothing():
    notes = Notes()
    link = SettingsEngineLink(MemConfig(), utcnow=Now())
    res = await _rec(MemPositions(_pos("KRW-BTC", 0.5)), notes=notes, link=link).run(
        UP, Account(_pos("KRW-BTC", 0.5))
    )
    assert not res.halted and notes.sent == [] and await link.halt_reason(UP) is None


async def test_cash_difference_warns_once_without_halt():
    notes = Notes()
    link = SettingsEngineLink(MemConfig(), utcnow=Now())
    ref = {"v": 1_000_000.0}

    async def cash_ref(market):
        return ref["v"]

    rec = _rec(MemPositions(), notes=notes, link=link, cash_ref=cash_ref)
    acct = Account(cash=990_000.0)
    r1 = await rec.run(UP, acct)
    await rec.run(UP, acct)
    assert r1.cash_diff == -10_000.0 and not r1.halted
    assert [lv for lv, *_ in notes.sent] == ["warning"]  # 상태가 바뀔 때 한 번
    assert await link.halt_reason(UP) is None
    ref["v"] = 990_000.5  # 허용치(1원) 안
    assert (await rec.run(UP, acct)).cash_diff is None


# ── 사람 조작 해제 ───────────────────────────────────────
async def test_accept_broker_rewrites_db_resolves_event_and_releases_halt():
    now = Now()
    link = SettingsEngineLink(MemConfig(), utcnow=now)
    notes, events = Notes(), MemEvents()
    positions = MemPositions(
        _pos("KRW-BTC", 0.4), _pos("KRW-ETH", 1.0, "a"), _pos("KRW-ETH", 1.0, "b")
    )
    rec = _rec(positions, notes=notes, link=link, events=events, now=now)
    acct = Account(_pos("KRW-BTC", 0.5, avg=200.0), _pos("KRW-ETH", 3.0), _pos("KRW-XRP", 7.0))
    await rec.run(UP, acct)

    runner = SpyRunner()
    eng = MarketEngine(runner, CandleAggregator("1m", UP), ReplayClock(), link=link)
    await eng.on_link(to_local(now(), UP))
    assert runner.risk.halted_reason == KIND

    fixed = await rec.accept_broker(UP, acct)
    assert fixed == 3
    rows = {(p.symbol, p.strategy): p.qty for p in await positions.all(UP)}
    assert rows == {
        ("KRW-BTC", "vol_breakout"): 0.5,  # 전략 행 하나 → 그 행을 고침
        ("KRW-ETH", "reconciled"): 3.0,  # 여러 행 → 한 행으로
        ("KRW-XRP", "reconciled"): 7.0,  # DB에 없던 심볼
    }
    assert events.rows[0]["resolved_at"] is not None
    assert await link.halt_reason(UP) is None
    assert notes.resolved == [alert_key(UP)]
    assert diff_positions(acct.positions(), await positions.all(UP), UP) == []

    await eng.on_link(to_local(now(), UP) + timedelta(seconds=10))
    assert runner.risk.halted_reason == ""


async def test_engine_does_not_clear_other_halts():
    link = SettingsEngineLink(MemConfig(), utcnow=Now())
    runner = SpyRunner()
    runner.risk.halted_reason = "monthly_loss:-5.20%"
    eng = MarketEngine(runner, CandleAggregator("1m", UP), ReplayClock(), link=link)
    await eng.on_link(to_local(T0, UP))
    assert runner.risk.halted_reason == "monthly_loss:-5.20%"


# ── SQL 구현 ─────────────────────────────────────────────
async def test_sql_reconcile_roundtrip(sessions):
    positions = SqlPositionRepo(sessions)
    events = SqlRiskEventRepo(sessions)
    link = SettingsEngineLink(SqlConfigRepo(sessions), utcnow=Now())
    await positions.upsert(_pos("KRW-BTC", 0.4))
    rec = Reconciler(positions, events, link, Notes(), utcnow=Now())
    acct = Account(_pos("KRW-BTC", 0.5))

    res = await rec.run(UP, acct)
    opened = await events.open(KIND, UP)
    assert opened["id"] == res.event_id and opened["detail"]["market"] == "upbit"
    assert await events.open(KIND, Market.KRX) is None
    assert await link.halt_reason(UP) == KIND

    await rec.accept_broker(UP, acct)
    assert await events.open(KIND, UP) is None
    assert [(p.symbol, p.qty) for p in await positions.all(UP)] == [("KRW-BTC", 0.5)]
    assert await link.halt_reason(UP) is None


# ── scheduler 잡 ─────────────────────────────────────────
def _ctx(now, **kw):
    async def no_backup(market, strategy):
        raise AssertionError

    kw.setdefault("notifier", Notes())
    return JobContext(link=SettingsEngineLink(MemConfig(), utcnow=now), backup=no_backup, utcnow=now,
                      **kw)  # fmt: skip


async def test_reconcile_job_runs_reconciler_per_market():
    now = Now()
    ctx = _ctx(now)
    ctx.reconciler = _rec(MemPositions(_pos("KRW-BTC", 0.4)), link=ctx.link, notes=ctx.notifier)

    async def brokers(market):
        return Account(_pos("KRW-BTC", 0.5))

    ctx.brokers = brokers
    registry.install(ctx)
    await registry.run_job("reconcile")
    assert await ctx.link.halt_reason(UP) == KIND


async def test_heartbeat_alert_is_keyed_and_resolved_on_recovery():
    now = Now()
    ctx = _ctx(now)
    await ctx.link.beat(UP)
    now.t = T0 + timedelta(seconds=120)
    await health.engine_heartbeat(ctx)
    assert ctx.notifier.sent[-1][2] == health.heartbeat_key(UP)
    await ctx.link.beat(UP)
    await health.engine_heartbeat(ctx)
    assert ctx.notifier.resolved == [health.heartbeat_key(UP)]


# ── critical 반복 ────────────────────────────────────────
async def test_critical_repeats_every_five_minutes_until_resolved():
    now = Now()
    inner = Notes()
    rn = RepeatingNotifier(inner, utcnow=now)
    await rn.send("critical", "reconcile mismatch (upbit)", key="reconcile.upbit")
    await rn.send("info", "filled")
    await rn.send(
        "critical", "reconcile mismatch (upbit)", key="reconcile.upbit"
    )  # 5분 잡이 또 보냄
    assert [lv for lv, *_ in inner.sent] == ["critical", "info"]

    now.t = T0 + timedelta(minutes=4, seconds=59)
    assert await rn.tick() == 0
    now.t = T0 + timedelta(minutes=5)
    assert await rn.tick() == 1
    assert inner.sent[-1] == ("critical", "[repeat] reconcile mismatch (upbit)", "reconcile.upbit")
    now.t = T0 + timedelta(minutes=9)
    assert await rn.tick() == 0  # 마지막 전송부터 다시 5분
    now.t = T0 + timedelta(minutes=10)
    assert await rn.tick() == 1

    await rn.resolve("reconcile.upbit")
    now.t = T0 + timedelta(minutes=30)
    assert await rn.tick() == 0
    assert inner.resolved == ["reconcile.upbit"]


async def test_alert_repeat_job_ticks_repeating_notifier():
    now = Now()
    inner = Notes()
    ctx = _ctx(now, notifier=RepeatingNotifier(Notes(), utcnow=now))
    ctx.notifier.inner = inner
    await ctx.notifier.send("critical", "engine down", key="heartbeat.upbit")
    now.t = T0 + timedelta(minutes=5)
    await health.alert_repeat(ctx)
    assert [t for _, t, _ in inner.sent] == ["engine down", "[repeat] engine down"]
    await health.alert_repeat(_ctx(now))  # 반복 없는 notifier면 아무 일 없음


async def test_critical_log_is_forwarded_with_fields():
    notes = Notes()
    handler = CriticalLogHandler(notes)
    lg = logging.getLogger("quantpilot.test.critical")
    lg.addHandler(handler)
    try:
        lg.critical("exit_order_failed", extra={"symbol": "KRW-BTC", "strategy": "vol_breakout"})
        lg.error("not forwarded")
        await asyncio.sleep(0)
    finally:
        lg.removeHandler(handler)
    [(lv, text, _key)] = notes.sent
    assert lv == "critical" and "exit_order_failed" in text and "symbol=KRW-BTC" in text


# ── 텔레그램: 토큰 비노출 (불변식 #10) ─────────────────────
class FakeClient:
    def __init__(self, *, status=200, exc: Exception | None = None):
        self.calls: list[tuple[str, dict]] = []
        self.status, self.exc = status, exc

    async def post(self, url, json, timeout):
        self.calls.append((url, json))
        if self.exc is not None:
            raise self.exc
        return SimpleNamespace(status_code=self.status)


async def test_telegram_sends_to_chat_with_level_prefix():
    client = FakeClient()
    tg = TelegramNotifier(TOKEN, "42", client=client)
    await tg.send("critical", "reconcile mismatch (upbit)")
    [(url, body)] = client.calls
    assert url.endswith("/sendMessage") and body["chat_id"] == "42"
    assert "[critical] reconcile mismatch (upbit)" in body["text"]


@pytest.mark.invariant
async def test_telegram_token_never_in_text_logs_or_repr(caplog):
    client = FakeClient(exc=RuntimeError(f"connect failed https://api.telegram.org/bot{TOKEN}/x"))
    tg = TelegramNotifier(TOKEN, "42", client=client)
    with caplog.at_level(logging.DEBUG):
        await tg.send("warning", f"oops {TOKEN}")  # 본문에 섞여 들어와도
        client.exc, client.status = None, 401
        await tg.send("info", "x")
    assert TOKEN not in client.calls[0][1]["text"]
    assert TOKEN not in caplog.text
    assert any(getattr(r, "error", None) == "RuntimeError" for r in caplog.records)
    assert all(TOKEN not in str(r.__dict__) for r in caplog.records)
    assert TOKEN not in repr(tg)
    assert any(getattr(r, "status", None) == 401 for r in caplog.records)


def test_from_settings_picks_channel():
    assert isinstance(from_settings(SimpleNamespace(telegram_bot_token="", telegram_chat_id="")),
                      LogNotifier)  # fmt: skip
    tg = from_settings(SimpleNamespace(telegram_bot_token=TOKEN, telegram_chat_id="42"))
    assert isinstance(tg, TelegramNotifier)
    with pytest.raises(ValueError):
        TelegramNotifier("", "42")
