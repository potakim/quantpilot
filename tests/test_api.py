"""P1-12 API v1 통합 테스트 (httpx AsyncClient + SQLite 인메모리 + MemoryHub).

03 문서의 REST 엔드포인트 전부와 불변식 #6·#9·#10을 확인한다. WS·허브·엔진 큐 소비는 test_api_ws.py.
"""

# 엔진 시각은 시장 현지 tz-naive가 규칙이다 (CLAUDE.md, ADR 0009)

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("aiosqlite")

import httpx
from api_helpers import API, PASSWORD, SECRET, make_settings, memory_sessions

from quantpilot.api import auth as jwt
from quantpilot.api.app import create_app
from quantpilot.core.clock import MarketClock
from quantpilot.core.events import BarClosed, JudgmentEvent, RiskEvent, SignalEvent
from quantpilot.core.models import (
    Fill,
    Gate,
    JudgeResult,
    Market,
    Order,
    OrderStatus,
    Position,
    Side,
    Target,
)
from quantpilot.db.repo import (
    SqlCandleRepo,
    SqlConfigRepo,
    SqlLedger,
    SqlOpsRepo,
    SqlPositionRepo,
)
from quantpilot.engine.link import SettingsEngineLink
from quantpilot.judgment.base import LLMVerdict, State
from quantpilot.realtime import keys as hk
from quantpilot.realtime.bus import EventRecorder, HubBus
from quantpilot.realtime.hub import MemoryHub

UP = Market.UPBIT
SYM = "KRW-BTC"


class FakeCalibration:
    async def calibration(self, weeks: int) -> dict[str, Any]:
        return {"brier": 0.2, "ece": 0.05, "n": 40, "buckets": [], "by_provider": {}}

    async def ab(self, weeks: int) -> dict[str, Any]:
        return {
            "on": {"ret": 0.02, "mdd": -0.03},
            "off": {"ret": 0.01, "mdd": -0.05},
            "g2_pass": True,
        }


class FakeAnswerer:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def answer(self, question: str, context: dict[str, Any]) -> tuple[str, float]:
        self.calls.append((question, context))
        return f"gate={context['judgment']['gate']}", 0.0012


@dataclass
class Ctx:
    client: httpx.AsyncClient
    app: Any
    hub: MemoryHub
    sessions: Any
    settings: Any
    answerer: FakeAnswerer

    @property
    def h(self) -> dict[str, str]:
        return self.app.state.test_headers


@pytest.fixture
async def ctx(tmp_path):
    engine, sessions = await memory_sessions()
    hub = MemoryHub()
    settings = make_settings(tmp_path)
    answerer = FakeAnswerer()
    app = create_app(settings=settings, sessions=sessions, hub=hub, answerer=answerer)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(f"{API}/auth/login", json={"password": PASSWORD})
        assert r.status_code == 200, r.text
        app.state.test_headers = {"Authorization": f"Bearer {r.json()['token']}"}
        yield Ctx(client, app, hub, sessions, settings, answerer)
    for t in list(app.state.tasks):
        t.cancel()
    app.state.backtest_pool.shutdown(wait=True)
    await engine.dispose()


def now_local() -> datetime:
    return MarketClock(UP).now()


# ── 인증 (JWT) ───────────────────────────────────────────


async def test_login_wrong_password_is_401(ctx):
    r = await ctx.client.post(f"{API}/auth/login", json={"password": "nope"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "UNAUTHORIZED"


async def test_login_returns_token_and_expiry(ctx):
    r = await ctx.client.post(f"{API}/auth/login", json={"password": PASSWORD})
    body = r.json()
    assert set(body) == {"token", "expires_at"}
    exp = datetime.fromisoformat(body["expires_at"])
    assert timedelta(hours=11) < exp - datetime.now(UTC) <= timedelta(hours=12)


async def test_protected_route_needs_token(ctx):
    r = await ctx.client.get(f"{API}/strategies")
    assert r.status_code == 401
    assert r.json() == {"error": {"code": "UNAUTHORIZED", "message": "토큰 없음", "detail": {}}}


def _token(payload: dict[str, Any], secret: str = SECRET, alg: str = "HS256") -> str:
    import base64
    import json

    def b64(d: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()

    head, body = b64({"alg": alg, "typ": "JWT"}), b64(payload)
    if alg == "none":
        return f"{head}.{body}."
    return f"{head}.{body}.{jwt._sign(f'{head}.{body}'.encode(), secret)}"


@pytest.mark.parametrize(
    "token",
    [
        pytest.param(_token({"sub": "admin", "exp": 1}), id="expired"),
        pytest.param(_token({"sub": "admin", "exp": 4102444800}, secret="x" * 40), id="bad-sig"),
        pytest.param(_token({"sub": "admin", "exp": 4102444800}, alg="none"), id="alg-none"),
        pytest.param(_token({"sub": "admin", "exp": 4102444800}, alg="HS512"), id="alg-other"),
        pytest.param(_token({"sub": "admin"}), id="no-exp"),
        pytest.param("not.a.jwt.at.all", id="malformed"),
    ],
)
async def test_invalid_tokens_are_rejected(ctx, token):
    r = await ctx.client.get(f"{API}/settings", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "UNAUTHORIZED"


def test_jwt_roundtrip_and_expiry_boundary():
    tok = jwt.encode({"sub": "a", "exp": 1000}, SECRET)
    assert jwt.decode(tok, SECRET, now=datetime.fromtimestamp(999, UTC))["sub"] == "a"
    with pytest.raises(jwt.TokenError):
        jwt.decode(tok, SECRET, now=datetime.fromtimestamp(1000, UTC))


async def test_login_disabled_when_secret_too_short(tmp_path):
    engine, sessions = await memory_sessions()
    app = create_app(
        settings=make_settings(tmp_path, jwt_secret="short"), sessions=sessions, hub=MemoryHub()
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(f"{API}/auth/login", json={"password": PASSWORD})
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "NOT_READY"
    await engine.dispose()


# ── 2.1 시스템 ───────────────────────────────────────────


async def test_health_reports_engine_halt_and_feed(ctx):
    link = SettingsEngineLink(SqlConfigRepo(ctx.sessions))
    await link.beat(UP)
    await link.halt(UP, "reconcile_mismatch")
    await ctx.hub.set(hk.feed(UP), True, ttl=60)
    for path in ("/health", f"{API}/health"):
        r = await ctx.client.get(path)
        body = r.json()
        assert r.status_code == 200
        assert body["ok"] and body["paper"] is True and body["engine_alive"] is True
        assert body["ws_connected"] == {"upbit": True, "kis": False}
        assert body["halted"] == {"upbit": "reconcile_mismatch", "krx": None, "us": None}


async def test_get_settings_includes_locked_risk_rules(ctx):
    await SqlConfigRepo(ctx.sessions).set_setting("news.enabled", True)
    r = await ctx.client.get(f"{API}/settings", headers=ctx.h)
    body = r.json()
    assert body["settings"]["news.enabled"] is True
    assert body["risk_rules"]["locked"] is True
    assert body["risk_rules"]["max_symbol_weight"] == 0.25


async def test_patch_settings_allowed_keys(ctx):
    patch = {
        "gate.hold_below": 0.55,
        "gate.full_above": 0.92,
        "judge.provider": "typesafe",
        "llm.models": ["claude", "gemini"],
        "news.enabled": False,
        "notify.level": "warning",
    }
    r = await ctx.client.patch(f"{API}/settings", json=patch, headers=ctx.h)
    assert r.status_code == 200, r.text
    config = SqlConfigRepo(ctx.sessions)
    for k, v in patch.items():
        assert await config.get_setting(k) == v


@pytest.mark.parametrize(
    "patch",
    [
        {"gate.hold_below": 0.1},
        {"gate.full_above": 0.99},
        {"gate.hold_below": 0.7, "gate.full_above": 0.7},
        {"judge.provider": "gpt"},
        {"llm.models": ["claude"]},
        {"news.enabled": "yes"},
        {"notify.telegram_bot_token": "x"},
        {"gate_report.g1.vol_breakout": {"within_20pct": True}},
    ],
)
async def test_patch_settings_rejects_bad_values(ctx, patch):
    r = await ctx.client.patch(f"{API}/settings", json=patch, headers=ctx.h)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "INVALID_PARAM"


# ── 불변식 #6: 리스크 규칙은 어디서도 완화되지 않는다 ──────────


@pytest.mark.invariant
@pytest.mark.parametrize(
    "key",
    [
        "risk.max_symbol_weight",
        "risk.monthly_loss_limit",
        "max_loss_per_trade",
        "RiskRules.max_intraday_weight",
        "engine.halt.upbit",
    ],
)
async def test_invariant6_patch_settings_cannot_touch_risk_rules(ctx, key):
    r = await ctx.client.patch(f"{API}/settings", json={key: 1.0}, headers=ctx.h)
    assert r.status_code == 400
    assert await SqlConfigRepo(ctx.sessions).get_setting(key) is None
    rules = (await ctx.client.get(f"{API}/settings", headers=ctx.h)).json()["risk_rules"]
    assert rules["max_symbol_weight"] == 0.25 and rules["monthly_loss_limit"] == -0.05


@pytest.mark.invariant
def test_invariant6_no_route_writes_risk_rules_or_clears_halt(ctx):
    paths = ctx.app.openapi()["paths"]
    for path in paths:
        assert "risk/rules" not in path and "rules" not in path.split("/")
        if "halt" in path:
            pytest.fail(f"할트 해제 전용 경로가 있으면 안 된다: {path}")
    # 할트를 푸는 유일한 경로
    assert "post" in paths[f"{API}/reconcile/{{market}}/accept-broker"]
    assert set(paths[f"{API}/risk/events"]) == {"get"}


@pytest.mark.invariant
async def test_invariant6_close_position_allowed_while_halted(ctx):
    await SqlPositionRepo(ctx.sessions).upsert(
        Position(SYM, 0.01, 50_000_000, now_local(), "vol_breakout", UP)
    )
    await SettingsEngineLink(SqlConfigRepo(ctx.sessions)).halt(UP, "reconcile_mismatch")
    await ctx.hub.set(hk.px(UP, SYM), 60_000_000)
    buy = {"market": "upbit", "symbol": SYM, "side": "buy", "qty": 0.001}
    r = await ctx.client.post(f"{API}/orders", json=buy, headers=ctx.h)
    assert r.status_code == 409 and r.json()["error"]["code"] == "HALTED"
    r = await ctx.client.post(f"{API}/positions/upbit/{SYM}/close", headers=ctx.h)
    assert r.status_code == 201, r.text
    assert r.json()["order"]["side"] == "sell" and r.json()["risk"]["reason"] == "exit"


async def test_accept_broker_resolves_event_and_clears_halt(ctx):
    link = SettingsEngineLink(SqlConfigRepo(ctx.sessions))
    await link.halt(UP, "reconcile_mismatch")
    from quantpilot.db.repo import SqlRiskEventRepo

    ev = await SqlRiskEventRepo(ctx.sessions).add("reconcile_mismatch", {"market": "upbit"})
    url = f"{API}/reconcile/upbit/accept-broker"
    r = await ctx.client.post(url, json={}, headers=ctx.h)
    assert r.status_code == 403 and r.json()["error"]["code"] == "CONFIRMATION_REQUIRED"
    r = await ctx.client.post(url, json={"confirm_password": PASSWORD}, headers=ctx.h)
    assert r.status_code == 200, r.text
    assert r.json()["halted"] is None
    assert await link.halt_reason(UP) is None
    assert await SqlRiskEventRepo(ctx.sessions).open("reconcile_mismatch", UP) is None
    events = (await ctx.client.get(f"{API}/risk/events", headers=ctx.h)).json()["items"]
    assert events[0]["id"] == ev and events[0]["resolved_at"] is not None


# ── 불변식 #10: 키·비밀번호·시크릿이 응답·로그에 없다 ──────────


@pytest.mark.invariant
async def test_invariant10_keys_are_stored_but_never_returned(ctx, caplog):
    caplog.set_level(logging.DEBUG)
    key_value = "UPBIT-SECRET-VALUE-9f8e7d"
    body = {"keys": {"upbit_secret_key": key_value}}
    r = await ctx.client.post(f"{API}/settings/keys", json=body, headers=ctx.h)
    assert r.status_code == 403 and r.json()["error"]["code"] == "CONFIRMATION_REQUIRED"
    body["confirm_password"] = PASSWORD
    r = await ctx.client.post(f"{API}/settings/keys", json=body, headers=ctx.h)
    assert r.status_code == 200, r.text
    assert r.json()["stored"] == ["upbit_secret_key"] and r.json()["restart_required"]
    text = ctx.settings.keys_file.read_text(encoding="utf-8")
    assert f"QP_UPBIT_SECRET_KEY={key_value}" in text
    if os.name != "nt":  # Windows에는 POSIX 권한 비트가 없다
        assert oct(ctx.settings.keys_file.stat().st_mode)[-3:] == "600"
    bad = await ctx.client.post(
        f"{API}/settings/keys",
        json={"keys": {"jwt_secret": "x"}, "confirm_password": PASSWORD},
        headers=ctx.h,
    )
    assert bad.status_code == 400

    responses = [r.text, bad.text]
    for path in ("/settings", "/health", "/strategies", "/portfolio", "/reports/gates"):
        responses.append((await ctx.client.get(API + path, headers=ctx.h)).text)
    blob = "\n".join(responses) + caplog.text
    for secret in (key_value, SECRET, PASSWORD):
        assert secret not in blob
    assert "Bearer" not in caplog.text


@pytest.mark.invariant
def test_invariant10_auth_repr_hides_secret():
    a = jwt.Auth(SECRET, PASSWORD)
    assert SECRET not in repr(a) and PASSWORD not in repr(a)


# ── 2.2 전략 ─────────────────────────────────────────────


async def test_strategies_list_merges_registry_and_config(ctx):
    await SqlConfigRepo(ctx.sessions).upsert_strategy(
        name="vol_breakout",
        market=UP,
        allocation=0.1,
        symbols=[SYM],
        params={"k": 0.6},
        enabled=True,
    )
    r = await ctx.client.get(f"{API}/strategies", headers=ctx.h)
    by = {s["name"]: s for s in r.json()}
    assert set(by) == {"vol_breakout", "gem", "gtaa", "orb"}
    vb = by["vol_breakout"]
    assert vb["enabled"] and vb["allocation"] == 0.1 and vb["params"]["k"] == 0.6
    assert vb["symbols"] == [SYM] and vb["paper"] is True
    assert {"position", "month_pnl", "mdd_30d"} <= set(vb["status"]) and "g1" in vb["gate"]
    assert by["gem"]["enabled"] is False
    one = await ctx.client.get(f"{API}/strategies/vol_breakout", headers=ctx.h)
    assert one.json()["params"]["k"] == 0.6
    assert (await ctx.client.get(f"{API}/strategies/nope", headers=ctx.h)).status_code == 404


async def test_patch_strategy_validates_params_and_allocation(ctx):
    url = f"{API}/strategies/vol_breakout"
    r = await ctx.client.patch(url, json={"params": {"k": 99}}, headers=ctx.h)
    assert r.status_code == 400 and r.json()["error"]["code"] == "INVALID_PARAM"
    r = await ctx.client.patch(url, json={"params": {"nope": 1}}, headers=ctx.h)
    assert r.status_code == 400
    r = await ctx.client.patch(
        url, json={"params": {"k": 0.4}, "allocation": 0.15, "enabled": True}, headers=ctx.h
    )
    assert r.status_code == 200, r.text
    assert r.json()["params"]["k"] == 0.4 and r.json()["allocation"] == 0.15
    r = await ctx.client.patch(f"{API}/strategies/gem", json={"allocation": 0.6}, headers=ctx.h)
    assert r.status_code == 200
    r = await ctx.client.patch(f"{API}/strategies/gtaa", json={"allocation": 0.3}, headers=ctx.h)
    assert r.status_code == 400  # 합 1.05
    r = await ctx.client.patch(f"{API}/strategies/orb", json={"allocation": 0.1}, headers=ctx.h)
    assert r.status_code == 400  # intraday(vol_breakout+orb) 합 0.25 > 0.2
    r = await ctx.client.post(f"{API}/strategies/vol_breakout/reset-params", headers=ctx.h)
    assert r.json()["params"]["k"] == 0.5 and r.json()["allocation"] == 0.15


# ── 2.3 백테스트 ─────────────────────────────────────────


async def _wait_done(ctx: Ctx, bid: int) -> dict[str, Any]:
    for _ in range(600):
        r = await ctx.client.get(f"{API}/backtests/{bid}", headers=ctx.h)
        if r.json()["status"] in ("done", "failed"):
            return r.json()
        await asyncio.sleep(0.1)
    pytest.fail("backtest did not finish")


async def test_backtest_async_flow_and_report(ctx):
    seen: list[tuple[str, dict]] = []

    async def collect() -> None:
        async for ch, msg in ctx.hub.listen():
            seen.append((ch, msg["data"]))

    collector = asyncio.create_task(collect())
    await asyncio.sleep(0)  # 구독 등록
    req = {"strategy": "gem", "source": "synthetic", "start": "2020-01-01"}
    r = await ctx.client.post(f"{API}/backtests", json=req, headers=ctx.h)
    assert r.status_code == 202
    bid = r.json()["id"]
    assert r.json()["status"] == "queued"
    body = await _wait_done(ctx, bid)
    assert body["status"] == "done", body
    assert {"cagr", "max_drawdown"} <= set(body["metrics"])
    assert body["attempts"]["distinct_attempts"] == 1 and body["attempts"]["warn_after"] == 7
    assert body["cost_model"]["fee_rate"] > 0
    assert 0 < len(body["equity"]) <= 501 and len(body["drawdown"]) == len(body["equity"])
    assert all(p["v"] <= 0 for p in body["drawdown"])
    # 진행 채널
    collector.cancel()
    assert {ch for ch, _ in seen} == {f"backtest:{bid}"}
    assert [d["stage"] for _, d in seen] == ["running", "done"] and seen[-1][1]["done"] is True

    lst = (await ctx.client.get(f"{API}/backtests?strategy=gem", headers=ctx.h)).json()
    assert lst[0]["id"] == bid and lst[0]["attempt_no"] == 1
    csv = await ctx.client.get(f"{API}/backtests/{bid}/report.csv", headers=ctx.h)
    assert csv.status_code == 200 and csv.text.startswith("ts,symbol,side")
    assert (await ctx.client.get(f"{API}/backtests/999", headers=ctx.h)).status_code == 404


async def test_get_backtest_never_reports_done_with_empty_metrics(ctx, monkeypatch):
    """t20: 행을 읽은 직후 잡이 끝나도 status=done + 빈 metrics로 응답하지 않는다."""
    from quantpilot.api.routes import backtests as rt
    from quantpilot.db.models import BacktestRow

    row = BacktestRow(
        strategy="gem",
        params={},
        symbols=["SPY"],
        source="synthetic",
        unlocked_holdout=False,
        cost_model={},
        metrics={},
        attempt_no=0,
    )
    async with ctx.sessions.begin() as s:
        s.add(row)
        await s.flush()
        bid = row.id
    await ctx.hub.set(hk.backtest(bid), {"status": "running", "progress": 0.5})

    orig = rt.queries.backtest

    async def row_then_job_finishes(sessions, b):
        r = await orig(sessions, b)
        # 행을 읽은 직후 잡이 끝나는 경쟁 상황 재현 (run_job 순서: 행 → 허브 done)
        async with ctx.sessions.begin() as s:
            (await s.get_one(BacktestRow, b)).metrics = {"cagr": 0.1}
        await ctx.hub.set(hk.backtest(b), {"status": "done", "progress": 1.0, "done": True})
        return r

    monkeypatch.setattr(rt.queries, "backtest", row_then_job_finishes)
    body = (await ctx.client.get(f"{API}/backtests/{bid}", headers=ctx.h)).json()
    assert body["status"] == "running" and body["metrics"] == {}, body
    # 다음 조회에서는 done + 채워진 metrics
    monkeypatch.setattr(rt.queries, "backtest", orig)
    body = (await ctx.client.get(f"{API}/backtests/{bid}", headers=ctx.h)).json()
    assert body["status"] == "done" and body["metrics"] == {"cagr": 0.1}


async def test_backtest_unlock_holdout_only_once_per_strategy(ctx):
    req = {"strategy": "gem", "source": "synthetic", "unlock_holdout": True, "start": "2021-01-01"}
    r = await ctx.client.post(f"{API}/backtests", json=req, headers=ctx.h)
    assert r.status_code == 202
    await _wait_done(ctx, r.json()["id"])
    r2 = await ctx.client.post(f"{API}/backtests", json=req, headers=ctx.h)
    assert r2.status_code == 409 and r2.json()["error"]["code"] == "GATE_LOCKED"


async def test_backtest_rejects_bad_params_and_source(ctx):
    for req in (
        {"strategy": "vol_breakout", "params": {"k": 99}},
        {"strategy": "vol_breakout", "source": "bloomberg"},
    ):
        r = await ctx.client.post(f"{API}/backtests", json=req, headers=ctx.h)
        assert r.status_code == 400
    r = await ctx.client.post(f"{API}/backtests", json={"strategy": "nope"}, headers=ctx.h)
    assert r.status_code == 404


# ── 2.4 거래·포지션 / 불변식 #9 ─────────────────────────────


async def _seed_position(ctx: Ctx) -> None:
    await SqlPositionRepo(ctx.sessions).upsert(
        Position(SYM, 0.02, 50_000_000, now_local(), "vol_breakout", UP, stop=48_000_000)
    )
    await ctx.hub.set(hk.px(UP, SYM), 60_000_000)


async def test_portfolio_and_positions(ctx):
    await _seed_position(ctx)
    r = await ctx.client.get(f"{API}/portfolio", headers=ctx.h)
    body = r.json()
    assert r.status_code == 200
    up = body["by_market"]["upbit"]
    assert up["positions"][0]["symbol"] == SYM
    assert body["month_limit"] == -0.05 and body["halted"]["upbit"] is None
    assert body["total_includes_us"] is False
    r = await ctx.client.get(f"{API}/positions?market=upbit", headers=ctx.h)
    p = r.json()[0]
    assert p["strategy"] == "vol_breakout" and p["stop"] == 48_000_000
    assert p["price"] == 60_000_000 and p["unrealized"] == pytest.approx(200_000)


@pytest.mark.invariant
async def test_invariant9_manual_order_prechecked_then_queued(ctx):
    await ctx.hub.set(hk.px(UP, SYM), 50_000_000)
    body = {"market": "upbit", "symbol": SYM, "side": "buy", "amount": 1_000_000}
    r = await ctx.client.post(f"{API}/orders", json=body, headers=ctx.h)
    assert r.status_code == 201, r.text
    order = r.json()["order"]
    assert order["status"] == "queued" and order["qty"] == pytest.approx(0.02)
    assert r.json()["risk"]["allowed"] is True
    item = await ctx.hub.pop(hk.orders_queue(UP))
    assert item["op"] == "submit" and item["order"]["id"] == order["id"]
    assert await ctx.hub.pop(hk.orders_queue(UP)) is None


@pytest.mark.invariant
async def test_invariant9_risk_rejected_order_is_422_and_not_queued(ctx):
    await ctx.hub.set(hk.px(UP, SYM), 50_000_000)
    now = datetime.now(UTC)
    await SqlConfigRepo(ctx.sessions).set_setting(
        f"month_start_equity.{UP.value}",
        {"month": f"{now.year:04d}-{now.month:02d}", "equity": 20_000_000},
    )  # 월 손익 -50% → 서킷브레이커
    body = {"market": "upbit", "symbol": SYM, "side": "buy", "qty": 0.001}
    r = await ctx.client.post(f"{API}/orders", json=body, headers=ctx.h)
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "RISK_REJECTED" and err["detail"]["risk"]["allowed"] is False
    assert await ctx.hub.pop(hk.orders_queue(UP)) is None


async def test_order_validation_and_no_price(ctx):
    url = f"{API}/orders"
    both = {"market": "upbit", "symbol": SYM, "side": "buy", "qty": 1, "amount": 1}
    assert (await ctx.client.post(url, json=both, headers=ctx.h)).status_code == 400
    lim = {"market": "upbit", "symbol": SYM, "side": "buy", "qty": 1, "type": "limit"}
    assert (await ctx.client.post(url, json=lim, headers=ctx.h)).status_code == 400
    nop = {"market": "upbit", "symbol": "KRW-NOPE", "side": "buy", "qty": 1}
    r = await ctx.client.post(url, json=nop, headers=ctx.h)
    assert r.status_code == 409 and r.json()["error"]["code"] == "NO_PRICE"
    bad = {"market": "mars", "symbol": SYM, "side": "buy", "qty": 1}
    assert (await ctx.client.post(url, json=bad, headers=ctx.h)).status_code == 400


async def test_orders_fills_listing_and_cancel(ctx):
    ledger = SqlLedger(ctx.sessions)
    ts = now_local()
    pending = Order(SYM, Side.BUY, 0.01, market=UP, ts=ts, strategy="manual")
    done = Order(SYM, Side.BUY, 0.02, market=UP, ts=ts - timedelta(minutes=1), strategy="vb")
    done.status = OrderStatus.FILLED
    await ledger.save_order(pending)
    await ledger.save_order(done)
    await ledger.record(Fill(done.id, SYM, Side.BUY, 0.02, 5e7, 100, 0, ts, "vb", market=UP))

    r = await ctx.client.get(f"{API}/orders?market=upbit&limit=1", headers=ctx.h)
    body = r.json()
    assert len(body["items"]) == 1 and body["next_cursor"] is not None
    nxt = await ctx.client.get(
        f"{API}/orders", params={"market": "upbit", "before": body["next_cursor"]}, headers=ctx.h
    )
    assert [o["id"] for o in nxt.json()["items"]] == [done.id]
    st = await ctx.client.get(f"{API}/orders?status=pending", headers=ctx.h)
    assert [o["id"] for o in st.json()["items"]] == [pending.id]

    f = await ctx.client.get(f"{API}/fills?market=upbit&strategy=vb", headers=ctx.h)
    assert f.json()["items"][0]["order_id"] == done.id
    since = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    f2 = await ctx.client.get(f"{API}/fills", params={"from": since}, headers=ctx.h)
    assert f2.json()["items"] == []

    r = await ctx.client.delete(f"{API}/orders/{pending.id}", headers=ctx.h)
    assert r.status_code == 202
    assert await ctx.hub.pop(hk.orders_queue(UP)) == {"op": "cancel", "order_id": pending.id}
    assert (await ctx.client.delete(f"{API}/orders/{done.id}", headers=ctx.h)).status_code == 409
    assert (await ctx.client.delete(f"{API}/orders/zzz", headers=ctx.h)).status_code == 404


async def test_close_position_without_position_is_404(ctx):
    r = await ctx.client.post(f"{API}/positions/upbit/{SYM}/close", headers=ctx.h)
    assert r.status_code == 404


async def test_quotes_and_candles(ctx):
    r = await ctx.client.get(f"{API}/quotes/upbit/{SYM}", headers=ctx.h)
    assert r.status_code == 409
    await ctx.hub.set(hk.px(UP, SYM), 61_000_000)
    await ctx.hub.set(hk.ob(UP, SYM), {"asks": [[61_000_100, 0.1]], "bids": [[60_999_900, 0.2]]})
    r = await ctx.client.get(f"{API}/quotes/upbit/{SYM}", headers=ctx.h)
    assert r.json()["price"] == 61_000_000 and r.json()["orderbook"]["asks"][0][0] == 61_000_100

    t0 = now_local().replace(second=0, microsecond=0) - timedelta(minutes=30)
    bars = [
        BarClosed(UP, SYM, "1m", t0 + timedelta(minutes=i), 100 + i, 101 + i, 99 + i, 100.5 + i, 1)
        for i in range(10)
    ]
    await SqlCandleRepo(ctx.sessions).upsert(bars)
    r = await ctx.client.get(f"{API}/candles/upbit/{SYM}?tf=1m&limit=5", headers=ctx.h)
    c = r.json()
    assert len(c) == 5 and c[-1]["c"] == 109.5
    assert c[-1]["ts"].endswith("+00:00")
    r5 = await ctx.client.get(f"{API}/candles/upbit/{SYM}?tf=5m&limit=10", headers=ctx.h)
    assert sum(b["v"] for b in r5.json()) == 10  # 1분봉 10개 → 5분봉으로 합쳐짐
    bad = await ctx.client.get(f"{API}/candles/upbit/{SYM}?tf=7m", headers=ctx.h)
    assert bad.status_code == 400


# ── 2.5 AI 판단 (+ t10 숙제: judgments·llm_verdicts 기록 배선) ───


async def _record_judgment(ctx: Ctx, *, hold: bool = False) -> int:
    bus = HubBus(ctx.hub, UP, recorder=EventRecorder.from_sessions(ctx.sessions))
    ts = now_local()
    await bus.publish(
        "signal", SignalEvent(UP, "vol_breakout", Target(SYM, 0.2, reason="k"), "entry", ts)
    )
    state = State("upbit", SYM, "vol_breakout", "breakout", {"vol_pctl_20d": 78})
    jr = JudgeResult({"news_risk": 0.1}, 0.83, 120.0, "typesafe", 0.004)
    verdicts = (
        LLMVerdict("claude-sonnet-5", True, "ok", 800, 0.002, "abc"),
        LLMVerdict("gemini", not hold, "hmm", 700, 0.001, "abc"),
    )
    je = JudgmentEvent(
        0,
        jr,
        Gate.HOLD if hold else Gate.FULL,
        0.0 if hold else 1.0,
        ts,
        ("news_risk",) if hold else (),
        verdicts,
        state,
    )
    await bus.publish("judgment", je)
    body = (await ctx.client.get(f"{API}/judgments", headers=ctx.h)).json()
    return body["items"][0]["id"]


async def test_judgments_log_detail_and_filters(ctx):
    jid = await _record_judgment(ctx)
    hold_id = await _record_judgment(ctx, hold=True)
    body = (await ctx.client.get(f"{API}/judgments", headers=ctx.h)).json()
    assert [j["id"] for j in body["items"]] == [hold_id, jid]
    j = body["items"][1]
    assert j["symbol"] == SYM and j["strategy"] == "vol_breakout" and j["gate"] == "full"
    assert [v["model"] for v in j["verdicts"]] == ["claude-sonnet-5", "gemini"]
    held = await ctx.client.get(f"{API}/judgments?outcome=judged_hold", headers=ctx.h)
    assert [x["id"] for x in held.json()["items"]] == [hold_id]
    assert held.json()["items"][0]["outcome_reason"] == "news_risk"
    none = await ctx.client.get(f"{API}/judgments?strategy=gem", headers=ctx.h)
    assert none.json()["items"] == []

    d = (await ctx.client.get(f"{API}/judgments/{jid}", headers=ctx.h)).json()
    assert d["state"]["features"] == {"vol_pctl_20d": 78} and "text" in d["state"]
    assert d["answers"] == {"news_risk": 0.1} and d["orders"] == [] and d["fills"] == []
    assert d["verdicts"][0]["prompt_hash"] == "abc"
    assert (await ctx.client.get(f"{API}/judgments/9999", headers=ctx.h)).status_code == 404


async def test_judgment_ask_uses_injected_answerer(ctx):
    jid = await _record_judgment(ctx)
    r = await ctx.client.post(f"{API}/judgments/{jid}/ask", json={"question": "왜?"}, headers=ctx.h)
    assert r.status_code == 200
    assert r.json() == {"answer": "gate=full", "cost_usd": 0.0012}
    q, context = ctx.answerer.calls[0]
    assert q == "왜?" and context["verdicts"][0]["model"] == "claude-sonnet-5"
    r = await ctx.client.post(f"{API}/judgments/{jid}/ask", json={"question": ""}, headers=ctx.h)
    assert r.status_code == 400


async def test_calibration_and_ab_need_injected_source(ctx):
    for path in ("/judgments/calibration", "/judgments/ab"):
        r = await ctx.client.get(API + path, headers=ctx.h)
        assert r.status_code == 503 and r.json()["error"]["code"] == "NOT_READY"
    ctx.app.state.deps.calibration = FakeCalibration()
    cal = (await ctx.client.get(f"{API}/judgments/calibration?weeks=4", headers=ctx.h)).json()
    assert cal["brier"] == 0.2
    ab = (await ctx.client.get(f"{API}/judgments/ab", headers=ctx.h)).json()
    assert ab["g2_pass"] is True


async def test_judge_preview(ctx):
    st = {"market": "upbit", "symbol": SYM, "strategy": "vol_breakout", "signal": "breakout"}
    r = await ctx.client.post(f"{API}/judge/preview", json={"state": st}, headers=ctx.h)
    assert r.status_code == 200 and r.json()["decision"]["gate"] in ("hold", "half", "full")
    bad = await ctx.client.post(f"{API}/judge/preview", json={"state": {"x": 1}}, headers=ctx.h)
    assert bad.status_code == 400


# ── 2.6 리뷰·리포트 ──────────────────────────────────────


async def test_reviews_risk_events_and_ai_costs(ctx):
    await SqlOpsRepo(ctx.sessions).save_daily_review(
        datetime.now(UTC).date(), summary="조용한 하루", stats={"trades": 0}, cost_usd=0.01
    )
    r = await ctx.client.get(f"{API}/reviews?limit=5", headers=ctx.h)
    assert r.json()[0]["summary"] == "조용한 하루"

    await _record_judgment(ctx)
    month = datetime.now(UTC).strftime("%Y-%m")
    c = (await ctx.client.get(f"{API}/costs/ai?month={month}", headers=ctx.h)).json()
    assert c["by_provider"]["judge:typesafe"] == {"calls": 1, "usd": pytest.approx(0.004)}
    assert c["by_provider"]["llm:gemini"]["calls"] == 1
    assert c["total_usd"] == pytest.approx(0.007)
    assert (await ctx.client.get(f"{API}/costs/ai?month=2026-13", headers=ctx.h)).status_code == 400

    bus = HubBus(ctx.hub, UP, recorder=EventRecorder.from_sessions(ctx.sessions))
    for i in range(3):
        await bus.publish("warning", RiskEvent("judge_down", now_local(), UP, {"n": i}))
    page1 = (await ctx.client.get(f"{API}/risk/events?limit=2", headers=ctx.h)).json()
    assert [e["detail"]["n"] for e in page1["items"]] == [2, 1]
    assert (
        page1["items"][0]["kind"] == "judge_down"
        and page1["items"][0]["detail"]["market"] == "upbit"
    )
    page2 = await ctx.client.get(
        f"{API}/risk/events", params={"before": page1["next_cursor"]}, headers=ctx.h
    )
    assert [e["detail"]["n"] for e in page2.json()["items"]] == [0]


async def test_reports_gates_shape(ctx):
    body = (await ctx.client.get(f"{API}/reports/gates", headers=ctx.h)).json()
    assert set(body) == {"g1", "g2", "g3", "g4"}
    assert body["g1"]["pass"] is False and "vol_breakout" in body["g1"]["evidence"]["by_strategy"]
    assert body["g2"]["days"] == "0/28" and body["g2"]["pass"] is False
    assert body["g3"]["pass"] is False and body["g4"]["pass"] is False


async def test_claude_answerer_with_fake_client_and_default_stub(tmp_path):
    from types import SimpleNamespace

    from quantpilot.api.app import default_answerer
    from quantpilot.api.deps import StubAnswerer
    from quantpilot.judgment.anthropic import ClaudeAnswerer

    sent: dict[str, Any] = {}

    class Msgs:
        async def create(self, **kw):
            sent.update(kw)
            text = SimpleNamespace(type="text", text="뉴스 리스크가 낮아 통과했다")
            usage = SimpleNamespace(input_tokens=1000, output_tokens=100)
            return SimpleNamespace(content=[text], usage=usage, stop_reason="end_turn")

    ans = ClaudeAnswerer(client=SimpleNamespace(messages=Msgs()))
    text, cost = await ans.answer("왜 통과?", {"judgment": {"gate": "full"}})
    assert text.startswith("뉴스") and cost > 0
    assert "수량" in sent["system"] and "왜 통과?" in sent["messages"][0]["content"]
    assert isinstance(default_answerer(make_settings(tmp_path)), StubAnswerer)
