"""웹 화면 데모 API (P1-13, ADR 0018 §3) — 데모·Lighthouse 측정 전용. 실거래·실계좌와 무관하다.

`create_app`에 테스트와 같은 부품(SQLite 인메모리 · MemoryHub · 스텁 답변기)을 넣고, 합성 데이터
(`data/synthetic.py` 캔들, 결정적인 판단·체결·포지션)를 심은 뒤 127.0.0.1:8000에 띄운다.
보정·A/B 카드는 `qp report ab`와 같은 계산(`cli.build_ab_report`)을 심은 데이터에 돌린 값이다.

- 비밀번호·JWT 시크릿은 `QP_ADMIN_PASSWORD`·`QP_JWT_SECRET` 환경변수로만 받는다 (불변식 #10).
  기본값은 두지 않는다 — 없으면 시작하지 않는다. CI는 실행마다 무작위 값을 만든다.
- 패키지·테스트는 이 스크립트를 import하지 않는다.
- 엔진 대신 짧은 루프가 하트비트·시세 틱·호가·portfolio를 흘려 WS 실시간 갱신을 보여 준다.

사용:
    python scripts/web_demo_api.py [--port 8000] [--halt] [--engine-down]
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import os
import random
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from quantpilot.core.clock import MarketClock
from quantpilot.core.events import BarClosed, JudgmentEvent, SignalEvent
from quantpilot.core.models import (
    Fill,
    Gate,
    JudgeResult,
    Market,
    Order,
    OrderStatus,
    OrderType,
    Position,
    Side,
    Target,
)
from quantpilot.data.synthetic import daily, seed_of

log = logging.getLogger("web_demo_api")

UP = Market.UPBIT
COINS = {  # 심볼 → 합성 시작가 (원)
    "KRW-BTC": 142_000_000.0,
    "KRW-ETH": 5_400_000.0,
    "KRW-SOL": 218_000.0,
    "KRW-XRP": 812.0,
    "KRW-ADA": 1_050.0,
}
MINUTES = 2 * 24 * 60  # 1분봉 이틀치


async def memory_sessions() -> tuple[Any, Any]:
    """SQLite 인메모리 세션 (tests/api_helpers.py와 같은 구성)."""
    from sqlalchemy import event
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import StaticPool

    from quantpilot.db.models import Base

    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _fk_on(dbapi_conn: Any, _: Any) -> None:
        dbapi_conn.execute("pragma foreign_keys=on")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


class DemoCalibration:
    """CalibrationSource: 심은 판단·원장에 `qp report ab` 계산을 그대로 돌린다."""

    def __init__(self, sessions: Any) -> None:
        self.sessions = sessions

    async def _report(self, weeks: int) -> dict[str, Any]:
        from quantpilot.cli import build_ab_report

        return await build_ab_report(self.sessions, UP, weeks=weeks, now=datetime.now(UTC))

    async def calibration(self, weeks: int) -> dict[str, Any]:
        """`{brier, ece, n, buckets, by_provider}`."""
        return (await self._report(weeks))["calibration"]

    async def ab(self, weeks: int) -> dict[str, Any]:
        """`{on, off, g2_pass, ...}`."""
        r = await self._report(weeks)
        return {k: v for k, v in r.items() if k not in ("start", "end", "calibration")}


def _bars(symbol: str, start_local: datetime) -> list[BarClosed]:
    df = daily(
        seed_of(symbol),
        start=start_local.strftime("%Y-%m-%d %H:%M"),
        periods=MINUTES,
        start_price=COINS[symbol],
        drift=0.0,
        vol=0.0009,
        freq="1min",
    )
    return [
        BarClosed(UP, symbol, "1m", ts.to_pydatetime(), *map(float, row))
        for ts, row in df.iterrows()
    ]


def _book(price: float) -> dict[str, list[list[float]]]:
    tick = max(price * 0.0002, 0.01)
    rng = random.Random(int(price))
    asks = [[round(price + tick * (i + 1), 2), round(rng.uniform(0.5, 6), 2)] for i in range(5)]
    bids = [[round(price - tick * (i + 1), 2), round(rng.uniform(0.5, 6), 2)] for i in range(5)]
    return {"asks": asks, "bids": bids}


async def seed(sessions: Any, hub: Any, *, halt: bool) -> dict[str, float]:
    """합성 데이터를 심고 심볼별 마지막 가격을 돌려준다."""
    from quantpilot.db.repo import (
        SqlCandleRepo,
        SqlConfigRepo,
        SqlJudgmentRepo,
        SqlLedger,
        SqlPositionRepo,
        SqlRiskEventRepo,
    )
    from quantpilot.engine.link import SettingsEngineLink
    from quantpilot.judgment.base import LLMVerdict, State
    from quantpilot.realtime import keys as hk
    from quantpilot.realtime.bus import EventRecorder
    from quantpilot.scheduler.backup import restore_account
    from quantpilot.scheduler.jobs.health import month_start_key
    from quantpilot.strategies import REGISTRY

    config = SqlConfigRepo(sessions)
    plan = {
        "vol_breakout": (0.15, True),
        "gem": (0.40, True),
        "gtaa": (0.35, True),
        "orb": (0.0, False),
    }
    for name, cls in REGISTRY.items():
        alloc, enabled = plan.get(name, (0.0, False))
        await config.upsert_strategy(
            name=name,
            market=cls.market,
            allocation=alloc,
            symbols=list(cls.symbols),
            enabled=enabled,
        )

    now_local = MarketClock(UP).now().replace(second=0, microsecond=0)
    start = now_local - timedelta(minutes=MINUTES - 1)
    candles = SqlCandleRepo(sessions)
    last: dict[str, float] = {}
    for sym in COINS:
        bars = _bars(sym, start)
        await candles.upsert(bars, source="demo")
        last[sym] = bars[-1].close
        await hub.set(hk.px(UP, sym), last[sym])
        await hub.set(hk.ob(UP, sym), _book(last[sym]))
        await hub.set(
            hk.strategy_state(UP, sym),
            {
                "target": round(last[sym] * (0.994 if sym == "KRW-ETH" else 1.012), 2),
                "ma_score": 0.75,
            },
        )
    await hub.set(hk.feed(UP), True)
    await hub.set("fx:usdkrw", 1380.0)

    # 판단: (몇 시간 전, 심볼, 게이트, 확신도, regime 1위, news_risk, 검토 [(모델, 승인, 이유)], 실현)
    rng = random.Random(20260930)
    specs: list[
        tuple[float, str, Gate, float, str, float, list[tuple[str, bool, str]], float | None]
    ]
    specs = [
        (
            0.5,
            "KRW-ETH",
            Gate.HALF,
            0.88,
            "trend_up",
            0.12,
            [
                ("claude-sonnet-5", True, "20일선 위 정배열, 돌파 거래량 증가. 규칙 위반 없음."),
                ("gemini-3.5-flash", True, "유입 뉴스가 이미 반영됐을 수 있으나 차단 사유 아님."),
            ],
            None,
        ),
        (1.1, "KRW-SOL", Gate.HOLD, 0.41, "trend_up", 0.63, [], None),
        (
            3.0,
            "KRW-XRP",
            Gate.HOLD,
            0.66,
            "range",
            0.20,
            [
                ("claude-sonnet-5", True, "레인지 상단 돌파, 거래량 평이."),
                ("gemini-3.5-flash", False, "돌파 강도가 약하고 되돌림 위험."),
            ],
            None,
        ),
        (
            5.0,
            "KRW-BTC",
            Gate.FULL,
            0.93,
            "trend_up",
            0.09,
            [
                ("claude-sonnet-5", True, "추세 강함, 뉴스 리스크 낮음."),
                ("gemini-3.5-flash", True, "규칙 위반 없음."),
            ],
            None,
        ),
    ]
    for i in range(28):  # 지난 4주 — 24시간 실현 수익률이 채워진 판단
        conf = round(rng.uniform(0.45, 0.97), 2)
        hit = rng.random() < conf
        ret = round(rng.uniform(0.002, 0.03) * (1 if hit else -1), 4)
        gate = Gate.FULL if conf >= 0.9 else Gate.HALF if conf >= 0.6 else Gate.HOLD
        regime = rng.choice(["trend_up", "range", "trend_down"])
        verdicts = (
            []
            if gate == Gate.HOLD
            else [("claude-sonnet-5", True, "규칙 위반 없음."), ("gemini-3.5-flash", True, "승인.")]
        )
        sym = list(COINS)[i % len(COINS)]
        specs.append(
            (
                26 + i * 23.0,
                sym,
                gate,
                conf,
                regime,
                round(rng.uniform(0.02, 0.4), 2),
                verdicts,
                ret,
            )
        )

    specs.sort(key=lambda s: -s[0])  # 오래된 것부터 넣어 id 순서 = 시간 순서 (엔진과 같게)
    recorder = EventRecorder.from_sessions(sessions)
    judgments = SqlJudgmentRepo(sessions)
    for hours, sym, gate, conf, regime, news, vs, ret in specs:
        ts = now_local - timedelta(hours=hours)
        sid = await recorder.signal(
            SignalEvent(UP, "vol_breakout", Target(sym, 0.2, reason="k=0.5 돌파"), "entry", ts)
        )
        others = {"trend_up": 0.0, "range": 0.0, "trend_down": 0.0}
        others[regime] = round(0.5 + conf / 3, 2)
        rest = [k for k in others if k != regime]
        others[rest[0]] = round((1 - others[regime]) * 0.7, 2)
        others[rest[1]] = round(1 - others[regime] - others[rest[0]], 2)
        answers = {
            "regime": others,
            "news_risk": news,
            "liquidity_stress": 0.08,
            "event_ahead": 0.05,
            "already_priced": 0.34,
            "signal_quality": round(2.5 + conf * 2, 1),
        }
        blocks = ("news_risk",) if news >= 0.5 else ()
        state = State(
            "upbit",
            sym,
            "vol_breakout",
            "breakout k=0.5",
            {"vol_pctl_20d": 78, "ma_score": 0.75, "volume_ratio": "2.1x"},
            "합성 데모 뉴스 요약 — 실제 기사 아님",
        )
        je = JudgmentEvent(
            sid,
            JudgeResult(answers, conf, 180.0 + hours % 60, "typesafe", 0.00001),
            gate,
            {Gate.FULL: 1.0, Gate.HALF: 0.5, Gate.HOLD: 0.0}[gate],
            ts,
            blocks,
            tuple(LLMVerdict(m, a, r, 900.0, 0.002, "demo") for m, a, r in vs),
            state,
        )
        jid = await recorder.judgment(je, sid)
        if ret is not None:
            await judgments.set_realized(jid, ret, ret > 0)

    # 원장: 게이팅 ON(실제)과 OFF(섀도) — A/B 카드용. OFF는 보류 신호까지 체결해 손실 거래가 더 있다.
    async def trade(ledger: SqlLedger, sym: str, t0: datetime, pnl: float, qty: float) -> None:
        px = COINS[sym]
        for side, price, dt in (
            (Side.BUY, px, t0),
            (Side.SELL, px * (1 + pnl), t0 + timedelta(hours=20)),
        ):
            o = Order(
                sym, side, qty, market=UP, ts=dt, strategy="vol_breakout", status=OrderStatus.FILLED
            )
            await ledger.save_order(o)
            fee = price * qty * 0.0005
            await ledger.record(
                Fill(o.id, sym, side, qty, price, fee, 0.0, dt, "vol_breakout", "demo", market=UP)
            )

    on, off = SqlLedger(sessions), SqlLedger(sessions, shadow=True)
    for k in range(10):
        t0 = now_local - timedelta(days=26 - k * 2.5)
        pnl = rng.uniform(-0.012, 0.02)
        await trade(on, "KRW-ETH", t0, pnl, 0.3)
        await trade(off, "KRW-ETH", t0, pnl, 0.3)
        if k % 3 == 0:  # ON이 보류한 신호 — OFF만 체결, 손실
            await trade(off, "KRW-SOL", t0 + timedelta(hours=2), -0.035, 8.0)

    # 오늘 체결 (거래 화면 진입 마커) + 미체결 지정가 1건
    entry_ts = now_local - timedelta(minutes=30)
    buy = Order("KRW-ETH", Side.BUY, 0.4841, market=UP, ts=entry_ts, strategy="vol_breakout")
    buy.status = OrderStatus.FILLED
    await on.save_order(buy)
    eth = last["KRW-ETH"] * 0.997
    await on.record(
        Fill(
            buy.id,
            "KRW-ETH",
            Side.BUY,
            0.4841,
            eth,
            eth * 0.4841 * 0.0005,
            0.0,
            entry_ts,
            "vol_breakout",
            "돌파 진입 · 절반 사이징 · 2/2 승인",
            market=UP,
        )
    )
    pending = Order(
        "KRW-XRP",
        Side.BUY,
        1200.0,
        OrderType.LIMIT,
        round(last["KRW-XRP"] * 0.99, 2),
        "manual",
        "demo limit",
        market=UP,
        ts=now_local - timedelta(minutes=10),
    )
    await on.save_order(pending)

    positions = SqlPositionRepo(sessions)
    await positions.upsert(
        Position("KRW-ETH", 0.4841, eth, entry_ts, "vol_breakout", UP, stop=round(eth * 0.963))
    )
    await positions.upsert(
        Position("360750", 30, 18_450, now_local - timedelta(days=3), "gtaa", Market.KRX)
    )
    await positions.upsert(
        Position("SPY", 8, 612.4, now_local - timedelta(days=20), "gem", Market.US)
    )

    # 월초 평가액 → 월 손익 (업비트 −1.2%, 국내 +0.4%, 미국 −0.3%)
    month = datetime.now(UTC).strftime("%Y-%m")
    for m, pnl in ((UP, -0.012), (Market.KRX, 0.004), (Market.US, -0.003)):
        acct = await restore_account(sessions, m)
        try:
            eq = float(acct.equity())
        except KeyError:
            eq = float(acct.cash())
        await config.set_setting(month_start_key(m), {"month": month, "equity": eq / (1 + pnl)})

    events = SqlRiskEventRepo(sessions)
    ev = await events.add("judge_down", {"market": "upbit", "note": "demo"})
    await events.resolve(ev)
    link = SettingsEngineLink(config)
    if halt:
        await link.halt(UP, "reconcile_mismatch: 브로커 수량 불일치 (데모)")
        await events.add("reconcile_mismatch", {"market": "upbit", "note": "demo"})
    log.info("demo data seeded", extra={"symbols": sorted(COINS), "judgments": len(specs)})
    return last


async def pulse(sessions: Any, hub: Any, last: dict[str, float], *, engine_alive: bool) -> None:
    """엔진 대신: 하트비트 · 시세 틱 · 호가 · portfolio를 흘린다."""
    from quantpilot.db.repo import SqlConfigRepo
    from quantpilot.engine.link import SettingsEngineLink
    from quantpilot.realtime import keys as hk
    from quantpilot.realtime.bus import message
    from quantpilot.scheduler.backup import restore_account

    link = SettingsEngineLink(SqlConfigRepo(sessions))
    rng = random.Random(7)
    n = 0
    while True:
        if engine_alive and n % 30 == 0:
            for m in Market:
                await link.beat(m)
        for sym in ("KRW-BTC", "KRW-ETH"):
            last[sym] *= 1 + rng.gauss(0, 0.0004)
            price = round(last[sym], 0)
            await hub.set(hk.px(UP, sym), price)
            data = {"price": price, "volume": round(rng.uniform(0.001, 0.2), 4), "side": "buy"}
            await hub.publish(hk.ticks_channel(UP, sym), message(None, data))
            if n % 2 == 0:
                book = _book(price)
                await hub.set(hk.ob(UP, sym), book)
                await hub.publish(hk.orderbook_channel(UP, sym), message(None, book))
        if n % 5 == 0:
            try:
                acct = await restore_account(sessions, UP)
                equity = float(acct.equity())
            except KeyError:
                equity = None
            if equity is not None:
                data = {"market": "upbit", "equity": equity}
                await hub.publish("portfolio", message(None, data))
        n += 1
        await asyncio.sleep(1.0)


async def main(argv: list[str] | None = None) -> None:
    """앱을 만들고, 데이터를 심고, uvicorn과 펄스 루프를 함께 돌린다."""
    import uvicorn

    from quantpilot.api.app import create_app
    from quantpilot.api.deps import StubAnswerer
    from quantpilot.config import Settings
    from quantpilot.realtime.hub import MemoryHub

    p = argparse.ArgumentParser(description="QuantPilot 웹 데모 API (데모 전용)")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--halt", action="store_true", help="업비트 할트 상태로 시작 (배너 확인용)")
    p.add_argument("--engine-down", action="store_true", help="하트비트를 보내지 않는다")
    a = p.parse_args(argv)

    admin_password = os.environ.get("QP_ADMIN_PASSWORD", "")
    jwt_secret = os.environ.get("QP_JWT_SECRET", "")
    if not admin_password or not jwt_secret:
        raise SystemExit("QP_ADMIN_PASSWORD와 QP_JWT_SECRET 환경변수를 설정해야 한다")

    tmp = Path(tempfile.mkdtemp(prefix="qp-demo-"))
    settings = Settings(
        _env_file=None,
        data_dir=tmp,
        keys_file=tmp / "keys.env",
        redis_url="",
        admin_password=admin_password,
        jwt_secret=jwt_secret,
    )
    engine, sessions = await memory_sessions()
    hub = MemoryHub()
    last = await seed(sessions, hub, halt=a.halt)
    app = create_app(
        settings=settings,
        sessions=sessions,
        hub=hub,
        answerer=StubAnswerer(),
        calibration=DemoCalibration(sessions),
    )
    server = uvicorn.Server(uvicorn.Config(app, host=a.host, port=a.port, log_level="info"))
    ticker = asyncio.create_task(pulse(sessions, hub, last, engine_alive=not a.engine_down))
    try:
        await server.serve()
    finally:
        ticker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await ticker
        await engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
