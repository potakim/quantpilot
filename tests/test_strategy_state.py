"""허브 `st` 키(목표가·이평 스코어)는 실시간 엔진이 쓴다 — 거래 화면 목표가 점선·돌파 문구·관심 종목 부제."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pandas as pd
import pytest
from api_helpers import memory_sessions
from test_engine_restart import DAY, FixedClock
from test_live_daily import _rising_days

from quantpilot.core.models import Market
from quantpilot.engine.main import STATE_TTL
from quantpilot.engine.replay import CollectingBus
from quantpilot.realtime import keys as hk
from quantpilot.strategies.base import Context
from quantpilot.strategies.vol_breakout import VolBreakout

KEY = hk.strategy_state(Market.UPBIT, "KRW-BTC")


class BrokenHub:
    """허브 대신: 무엇을 해도 연결 오류."""

    async def publish(self, *_a, **_k) -> None:
        raise ConnectionError("redis down")

    async def set(self, *_a, **_k) -> None:
        raise ConnectionError("redis down")


@pytest.fixture
def no_events(monkeypatch, tmp_path):
    from quantpilot.config import settings

    monkeypatch.setattr(settings, "events_file", tmp_path / "none.yaml")


def _today_target(days: pd.DataFrame, k: float = 0.5) -> float:
    """05 §1: 오늘 시가 + (전일 고가 − 전일 저가) × K."""
    p1 = days.iloc[-2]
    return float(days["open"].iloc[-1]) + float(p1["high"] - p1["low"]) * k


async def _engine(sessions, hub, last_day: str):
    from quantpilot.engine.main import build_upbit_paper

    seed = {"KRW-BTC": _rising_days(40, last_day)}
    eng = build_upbit_paper(sessions=sessions, daily_seed=seed)
    eng.clock = FixedClock(DAY)
    eng.runner.bus = CollectingBus()
    eng.hub = hub
    await eng.restore()
    return eng, seed["KRW-BTC"]


def test_state_target_is_the_price_on_bar_buys_at():
    """화면 목표가 = on_bar가 돌파 진입하는 가격 (계산을 한 곳에서 공유), 이평 스코어는 전일 종가 기준."""
    days = _rising_days(40, "2026-09-29")  # 매일 1% 상승 → 오늘 고가가 목표가를 넘는다
    s = VolBreakout()
    ctx = Context(
        ts=days.index[-1], bars={"KRW-BTC": days}, positions={}, equity=1e6, params=s.params
    )
    entry = [t for t in s.on_bar(ctx) if t.weight > 0]
    assert len(entry) == 1
    assert s.state(ctx) == {"KRW-BTC": {"target": entry[0].price, "ma_score": 1.0}}
    assert entry[0].price == pytest.approx(_today_target(days))

    # 웜업 전이면 화면 상태도 없다
    short = {"KRW-BTC": days.tail(10)}
    assert s.state(Context(days.index[-1], short, {}, 1e6, s.params)) == {}


def test_engine_writes_state_on_start_and_refreshes_every_heartbeat(no_events):
    """시작 직후 /quotes가 읽는 키에 목표가가 있고, TTL 60초로 사라졌다가 다음 하트비트에 다시 쓰인다."""
    from quantpilot.realtime.hub import MemoryHub

    async def go():
        _, sessions = await memory_sessions()
        now = [0.0]
        hub = MemoryHub(monotonic=lambda: now[0])
        eng, days = await _engine(sessions, hub, "2026-09-29")

        st = await hub.get(KEY)
        assert st == {"target": pytest.approx(_today_target(days)), "ma_score": 1.0}

        now[0] += STATE_TTL + 1  # 엔진이 멈춘 것처럼 시간만 흐름
        assert await hub.get(KEY) is None

        await eng.on_link(DAY + timedelta(minutes=1))
        assert await hub.get(KEY) == st

    asyncio.run(go())


def test_no_state_until_todays_bar_exists(no_events):
    """09:00이 지났는데 오늘 일봉이 아직 없으면 쓰지 않는다 — 어제 목표가가 오늘 것처럼 보이면 안 된다."""
    from quantpilot.realtime.hub import MemoryHub

    async def go():
        _, sessions = await memory_sessions()
        hub = MemoryHub()
        await _engine(sessions, hub, "2026-09-28")  # 씨앗의 마지막 봉 = 어제
        assert await hub.get(KEY) is None

    asyncio.run(go())


def test_hub_failure_does_not_stop_engine(no_events):
    """허브가 죽어도 시작·하트비트는 예외 없이 지나간다 (표시용 키)."""

    async def go():
        _, sessions = await memory_sessions()
        eng, _ = await _engine(sessions, BrokenHub(), "2026-09-29")
        await eng.on_link(DAY + timedelta(minutes=1))

    asyncio.run(go())
