"""t19 화면 지표 API 통합 테스트 (ADR 0020): 오늘 손익·자산 곡선·오늘 일정·전략 손익·주문 수수료.

httpx AsyncClient + SQLite 인메모리 + MemoryHub. 시계는 2026-10-14 12:00 KST(수, KRX·NYSE 거래일)로 고정한다.
"""

# 엔진 시각은 시장 현지 tz-naive가 규칙이다 (CLAUDE.md, ADR 0009)

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("aiosqlite")

import httpx
from api_helpers import API, PASSWORD, make_settings, memory_sessions

from quantpilot.api import metrics
from quantpilot.api.app import create_app
from quantpilot.core.clock import to_local
from quantpilot.core.events import BarClosed
from quantpilot.core.models import Fill, Market, Order, Side
from quantpilot.db.repo import SqlCandleRepo, SqlConfigRepo, SqlLedger, SqlOpsRepo
from quantpilot.realtime import keys as hk
from quantpilot.realtime.hub import MemoryHub
from quantpilot.scheduler.jobs.exits import breakout_target

UP = Market.UPBIT
SYM = "KRW-BTC"
NOW = datetime(2026, 10, 14, 3, 0, tzinfo=UTC)  # 12:00 KST
LOCAL = to_local(NOW, UP)  # 2026-10-14 12:00 (KST, tz-naive)


@dataclass
class Ctx:
    client: httpx.AsyncClient
    sessions: Any
    hub: MemoryHub
    h: dict[str, str]


@pytest.fixture
async def ctx(tmp_path):
    engine, sessions = await memory_sessions()
    hub = MemoryHub()
    app = create_app(
        settings=make_settings(tmp_path), sessions=sessions, hub=hub, utcnow=lambda: NOW
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(f"{API}/auth/login", json={"password": PASSWORD})
        assert r.status_code == 200, r.text
        yield Ctx(client, sessions, hub, {"Authorization": f"Bearer {r.json()['token']}"})
    app.state.backtest_pool.shutdown(wait=True)
    await engine.dispose()


async def _snap(ctx: Ctx, ts: datetime, equity: float) -> None:
    await SqlOpsRepo(ctx.sessions).add_equity_snapshot(market=UP, ts=ts, cash=0.0, equity=equity)


async def _hourly_bars(ctx: Ctx, start: datetime, closes: list[float]) -> None:
    bars = [
        BarClosed(UP, SYM, "1m", start + timedelta(hours=i), c, c, c, c, 1.0)
        for i, c in enumerate(closes)
    ]
    await SqlCandleRepo(ctx.sessions).upsert(bars)


# ── 오늘 손익 (/portfolio) ────────────────────────────────


async def test_today_pnl_uses_first_snapshot_after_local_midnight(ctx):
    midnight = LOCAL.replace(hour=0, minute=0)
    await _snap(ctx, midnight - timedelta(minutes=10), 9_000_000)  # 어제 — 기준 아님
    await _snap(ctx, midnight + timedelta(minutes=1), 9_800_000)  # 오늘 첫 스냅샷 = 기준
    await _snap(ctx, midnight + timedelta(hours=10), 11_000_000)
    body = (await ctx.client.get(f"{API}/portfolio", headers=ctx.h)).json()
    up = body["by_market"]["upbit"]
    expected = up["equity"] - 9_800_000
    assert up["today_pnl"]["amount"] == pytest.approx(expected)
    assert up["today_pnl"]["pct"] == pytest.approx(expected / 9_800_000)
    assert body["by_market"]["krx"]["today_pnl"] is None  # 업비트만 스냅샷이 있다
    assert body["today_pnl_krw"] == pytest.approx(expected)


async def test_today_pnl_is_null_without_snapshot_today(ctx):
    await _snap(ctx, LOCAL - timedelta(days=1), 9_000_000)  # 어제 것만
    body = (await ctx.client.get(f"{API}/portfolio", headers=ctx.h)).json()
    assert all(v["today_pnl"] is None for v in body["by_market"].values())
    assert body["today_pnl_krw"] is None


# ── 자산 곡선 (/portfolio/equity) ─────────────────────────


async def _seed_curve(ctx: Ctx) -> list[tuple[datetime, float]]:
    start = LOCAL - timedelta(days=3)
    seeded = [(start + timedelta(minutes=10 * i), 10_000_000 + 1_000 * i) for i in range(432)]
    for ts, v in seeded:
        await _snap(ctx, ts, v)
    return seeded


async def test_equity_curve_downsamples_hourly_and_daily(ctx):
    seeded = await _seed_curve(ctx)
    r = await ctx.client.get(f"{API}/portfolio/equity?market=upbit&days=30", headers=ctx.h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == "equity_snapshots" and body["days"] == 30
    hours = {ts.replace(minute=0) for ts, _ in seeded}
    assert len(body["points"]) == len(hours)  # 1시간당 1점
    assert body["points"][-1]["v"] == seeded[-1][1]  # 묶음마다 마지막 값
    assert body["points"][0]["ts"].endswith("+00:00")
    assert body["benchmark"] is None  # 봉이 없으면 null
    daily = (await ctx.client.get(f"{API}/portfolio/equity?days=90", headers=ctx.h)).json()
    assert len(daily["points"]) == len({ts.date() for ts, _ in seeded})  # 하루당 1점


async def test_equity_curve_benchmark_tracks_btc_with_same_capital(ctx):
    await _seed_curve(ctx)
    start = (LOCAL - timedelta(days=4)).replace(minute=0)
    closes = [50_000_000 + 10_000 * i for i in range(4 * 24 + 12)]
    await _hourly_bars(ctx, start, closes)
    body = (await ctx.client.get(f"{API}/portfolio/equity?days=30", headers=ctx.h)).json()
    pts, bench = body["points"], body["benchmark"]
    assert bench is not None and len(bench) == len(pts)
    assert bench[0]["v"] == pytest.approx(pts[0]["v"])  # 같은 시작 자본
    assert bench[-1]["v"] > bench[0]["v"]  # BTC가 올랐다
    assert [b["ts"] for b in bench] == [p["ts"] for p in pts]


async def test_equity_curve_validates_days_and_empty_is_not_error(ctx):
    for bad in ("7", "abc"):
        r = await ctx.client.get(f"{API}/portfolio/equity?days={bad}", headers=ctx.h)
        assert r.status_code == 400 and r.json()["error"]["code"] == "INVALID_PARAM"
    r = await ctx.client.get(f"{API}/portfolio/equity?market=krx&days=365", headers=ctx.h)
    assert r.status_code == 200 and r.json()["points"] == [] and r.json()["benchmark"] is None


# ── 오늘 일정 (/schedule) ─────────────────────────────────


async def test_schedule_omits_markets_without_live_engine(ctx):
    """1단계: KRX·미국 잡과 연결되지 않은 아침 브리핑은 일정에 없다. 항목마다 한국어 title (ADR 0031)."""
    items = (await ctx.client.get(f"{API}/schedule", headers=ctx.h)).json()
    names = {i["name"] for i in items}
    assert {"krx_close_orders", "kis_token_refresh", "us_orb_entry_window"}.isdisjoint(names)
    assert {"us_eod_exit", "gem_rebalance", "morning_brief"}.isdisjoint(names)
    assert {"upbit_daily_exit", "upbit_prescreen", "vol_breakout"} <= names
    assert all(i["title"] and not i["title"].isascii() for i in items)


async def test_schedule_lists_today_jobs_with_done_flags_and_breakout_target(ctx, monkeypatch):
    from quantpilot.api.routes import schedule

    # 2단계처럼 모든 시장에 엔진이 있다고 보고 시장별 시각·휴장 계산을 확인한다
    monkeypatch.setattr(schedule, "LIVE_MARKETS", frozenset(Market))
    nine = LOCAL.replace(hour=9, minute=0)
    # 전일 09:00 ~ 오늘 09:00 1분봉: 고가 52M·저가 48M, 마지막 종가 50M → 목표가 50M + 4M × 0.5
    closes = [48_000_000, 52_000_000] + [50_000_000] * 22
    await _hourly_bars(ctx, nine - timedelta(days=1), closes)
    r = await ctx.client.get(f"{API}/schedule", headers=ctx.h)
    assert r.status_code == 200, r.text
    items = r.json()
    by = {i["name"]: i for i in items}
    assert by["upbit_daily_exit"]["done"] is True  # 09:00 KST < 12:00
    assert by["krx_close_orders"]["done"] is False  # 15:20 KST
    assert by["krx_close_orders"]["next_action"]["at"] == "2026-10-14T06:20:00+00:00"
    assert by["us_orb_entry_window"]["next_action"]["at"] == "2026-10-14T13:35:00+00:00"
    assert "news_collect" not in by and "equity_snapshot" not in by  # 매시·interval 잡은 뺀다
    vb = by["vol_breakout"]
    assert vb["market"] == "upbit" and vb["done"] is False
    assert vb["next_action"]["what"] == "KRW-BTC 목표가 ₩52,000,000 돌파 시 진입"
    assert vb["targets"][0]["target"] == pytest.approx(52_000_000)
    ats = [i["next_action"]["at"] for i in items]
    assert ats == sorted(ats) and all(a.endswith("+00:00") for a in ats)


async def test_schedule_breakout_is_null_safe_without_candles(ctx):
    items = (await ctx.client.get(f"{API}/schedule", headers=ctx.h)).json()
    vb = next(i for i in items if i["name"] == "vol_breakout")
    assert vb["targets"] == [] and "목표가 계산 불가" in vb["next_action"]["what"]
    assert vb["next_action"]["at"] == "2026-10-15T00:00:00+00:00"  # 다음 09:00 KST


def test_breakout_target_pure_function():
    t0 = LOCAL.replace(day=13, hour=9, minute=0)
    bars = [
        BarClosed(UP, SYM, "1m", t0, 100, 110, 90, 100, 1),
        BarClosed(UP, SYM, "1m", t0 + timedelta(minutes=1), 100, 105, 95, 102, 1),
    ]
    assert breakout_target(SYM, bars, 0.5) == {
        "symbol": SYM,
        "open": 102,
        "range": 20,
        "target": 112.0,
    }
    assert breakout_target(SYM, [], 0.5) is None


def test_cron_fires_respects_weekday_and_timezone():
    start, end = datetime(2026, 10, 16, 15, tzinfo=UTC), datetime(2026, 10, 18, 15, tzinfo=UTC)
    weekday = {"day_of_week": "mon-fri", "hour": 15, "minute": 20, "timezone": "Asia/Seoul"}
    assert metrics.cron_fires(weekday, start, end) == []  # 토·일 (KST)
    daily = {"hour": 9, "minute": 0, "timezone": "Asia/Seoul"}
    assert metrics.cron_fires(daily, start, end) == [
        datetime(2026, 10, 17, 0, tzinfo=UTC),
        datetime(2026, 10, 18, 0, tzinfo=UTC),
    ]
    assert metrics.cron_fires({"minute": 5, "timezone": "Asia/Seoul"}, start, end) == []


# ── 전략별 이번 달 손익·MDD (/strategies) ───────────────────


async def test_strategy_month_pnl_and_mdd_from_fill_replay(ctx):
    cfg = SqlConfigRepo(ctx.sessions)
    await cfg.upsert_strategy(name="vol_breakout", market=UP, allocation=0.2, symbols=[SYM])
    await cfg.set_setting(
        "month_start_equity.upbit", {"month": "2026-10", "equity": 10_000_000}
    )  # 자본 = 0.2 × 10M = 2M
    t_buy = LOCAL.replace(day=10, hour=0, minute=30)
    for oid, qty, shadow in (("o1", 0.02, False), ("o2", 1.0, True)):  # 섀도 원장은 제외된다
        ledger = SqlLedger(ctx.sessions, shadow=shadow)
        await ledger.save_order(
            Order(SYM, Side.BUY, qty, id=oid, market=UP, ts=t_buy, strategy="vol_breakout")
        )
        await ledger.record(
            Fill(oid, SYM, Side.BUY, qty, 50_000_000, 0, 0, t_buy, "vol_breakout", market=UP)
        )
    start = LOCAL.replace(day=10, hour=0, minute=0)
    n = int((LOCAL - start).total_seconds() // 3600) + 1
    closes = [50_000_000] * 24 + [45_000_000] * 24 + [55_000_000] * (n - 48)
    await _hourly_bars(ctx, start, closes)
    body = (await ctx.client.get(f"{API}/strategies", headers=ctx.h)).json()
    vb = next(s for s in body if s["name"] == "vol_breakout")
    # 현금 1M + 0.02 BTC × 55M = 2.1M → +5%. 45M 구간에서 2.0M → 1.9M = 5% 낙폭
    assert vb["status"]["month_pnl"] == pytest.approx(0.05)
    assert vb["status"]["mdd_30d"] == pytest.approx(0.05)
    gem = next(s for s in body if s["name"] == "gem")
    assert gem["status"]["month_pnl"] is None and gem["status"]["mdd_30d"] is None
    one = (await ctx.client.get(f"{API}/strategies/vol_breakout", headers=ctx.h)).json()
    assert one["status"]["month_pnl"] == pytest.approx(0.05)


async def test_strategy_metrics_null_without_fills(ctx):
    await _hourly_bars(ctx, LOCAL - timedelta(days=2), [50_000_000] * 24)
    body = (await ctx.client.get(f"{API}/strategies", headers=ctx.h)).json()
    for s in body:
        assert s["status"]["month_pnl"] is None and s["status"]["mdd_30d"] is None


# ── 주문 수수료 (/quotes) ─────────────────────────────────


async def test_quote_includes_cost_model_fee_rate(ctx):
    await ctx.hub.set(hk.px(UP, SYM), 61_000_000)
    body = (await ctx.client.get(f"{API}/quotes/upbit/{SYM}", headers=ctx.h)).json()
    assert body["fee_rate"] == 0.0005 and body["tax_rate_sell"] == 0.0
    await ctx.hub.set(hk.px(Market.KRX, "005930"), 70_000)
    krx = (await ctx.client.get(f"{API}/quotes/krx/005930", headers=ctx.h)).json()
    assert krx["fee_rate"] == 0.00015 and krx["tax_rate_sell"] == 0.002
