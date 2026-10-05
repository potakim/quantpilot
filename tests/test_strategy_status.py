"""WS `strategy.status`는 실시간 엔진이 보낸다 (ADR 0034): 시작 시 전부, 그 뒤 하트비트마다 바뀐 전략만."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from api_helpers import memory_sessions
from test_engine_restart import DAY, FixedClock, _breakout_bars
from test_live_daily import _rising_days

from quantpilot.core.models import Market
from quantpilot.engine.replay import CollectingBus
from quantpilot.strategies import create


class SpyHub:
    """허브 대신: 발행한 (채널, 메시지)를 모은다."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, dict]] = []

    async def publish(self, channel: str, msg: dict) -> None:
        self.sent.append((channel, msg))

    async def set(self, *_a, **_k) -> None:
        return None

    def status(self) -> list[dict]:
        return [m["data"] for ch, m in self.sent if ch == "strategy.status"]


class BrokenHub(SpyHub):
    async def publish(self, channel: str, msg: dict) -> None:
        raise ConnectionError("redis down")


@pytest.fixture
def no_events(monkeypatch, tmp_path):
    from quantpilot.config import settings

    monkeypatch.setattr(settings, "events_file", tmp_path / "none.yaml")


async def _engine(sessions, hub):
    from quantpilot.engine.main import build_upbit_paper

    seed = {"KRW-BTC": _rising_days(40, "2026-09-28")}
    eng = build_upbit_paper(sessions=sessions, daily_seed=seed)
    eng.clock = FixedClock(DAY)
    eng.runner.bus = CollectingBus()
    eng.hub = hub
    await eng.restore()
    return eng, float(seed["KRW-BTC"]["close"].iloc[-1])


def test_engine_sends_status_on_start_then_only_changes(no_events):
    """시작: 전략 상태 1건. 바뀐 게 없으면 안 보내고, 매수 체결·끄기 뒤에는 그 전략만 다시 보낸다."""

    async def go():
        from quantpilot.db.repo import SqlConfigRepo

        _, sessions = await memory_sessions()
        hub = SpyHub()
        eng, p = await _engine(sessions, hub)
        assert hub.status() == [
            {
                "name": "vol_breakout",
                "enabled": True,
                "allocation": 0.15,
                "position": {},
                "market": "upbit",
            }
        ]
        assert hub.sent[0][1]["ts"].endswith("+00:00")  # 화면 공통 메시지 형식 {ts, data}

        await eng.on_link(DAY + timedelta(minutes=1))
        assert len(hub.status()) == 1  # 그대로면 안 보낸다

        for b in _breakout_bars(p):
            await eng.runner.on_bars_closed([b])
        await eng.on_link(DAY + timedelta(minutes=2))
        assert len(hub.status()) == 2
        bought = hub.status()[-1]["position"]
        assert list(bought) == ["KRW-BTC"] and bought["KRW-BTC"] > 0

        await SqlConfigRepo(sessions).upsert_strategy(
            name="vol_breakout",
            market=Market.UPBIT,
            allocation=0.15,
            symbols=list(create("vol_breakout").symbols),
            enabled=False,
        )
        await eng.on_link(DAY + timedelta(minutes=3))
        last = hub.status()[-1]
        assert (len(hub.status()), last["enabled"], last["position"]) == (3, False, bought)

    asyncio.run(go())


def test_status_publish_failure_does_not_stop_engine(no_events):
    """허브 발행이 실패해도 하트비트·매매는 계속하고, 다음 하트비트에 다시 시도한다."""

    async def go():
        _, sessions = await memory_sessions()
        eng, _ = await _engine(sessions, BrokenHub())
        await eng.on_link(DAY + timedelta(minutes=1))  # 예외 없이 지나간다
        eng.hub = SpyHub()
        await eng.on_link(DAY + timedelta(minutes=2))
        assert [s["name"] for s in eng.hub.status()] == ["vol_breakout"]  # 못 보낸 것을 다시 보낸다

    asyncio.run(go())


def test_backtest_runner_without_configs_has_no_status():
    """apply_configs를 부르지 않은 TickRunner(백테스트)는 보낼 상태가 없다."""
    from test_tick import Scripted, _runner

    runner, *_ = _runner([Scripted("a", ("AAA",), {})])
    assert runner.strategy_status() == []
