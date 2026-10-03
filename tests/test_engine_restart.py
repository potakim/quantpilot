"""엔진 재시작·영속화 (ADR 0028) — 장중 재시작 시 팔았다 다시 사던 문제, 손절선·월 기준선 소실, 분봉 미저장,
늦은 체결의 오래된 가격 회귀 방지."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from api_helpers import memory_sessions
from test_live_daily import _minute, _rising_days, local

from quantpilot.core.events import BarClosed
from quantpilot.core.models import Market, Side
from quantpilot.data.store import CandleStore
from quantpilot.engine.replay import CollectingBus

UP = Market.UPBIT
DAY = local(2026, 9, 29, 9, 0)


class FixedClock:
    """엔진 시계 고정 (우편함 처리 표시·월 기준선은 '지금'의 거래일·월로 정한다)."""

    def __init__(self, now):
        self.t = now

    def now(self):
        return self.t


@pytest.fixture
def no_events(monkeypatch, tmp_path):
    from quantpilot.config import settings

    monkeypatch.setattr(settings, "events_file", tmp_path / "none.yaml")


def _breakout_bars(p: float, n: int = 120) -> list[BarClosed]:
    """09:00부터 0.05%씩 오르는 분봉 — 목표가를 넘는다."""
    out = []
    for i in range(n):
        o = p * (1 + 0.0005 * i)
        c = o * 1.0005
        out.append(_minute("KRW-BTC", DAY + timedelta(minutes=i), o, c, o, c))
    return out


async def _db_engine(sessions, seed, now):
    from quantpilot.engine.main import build_upbit_paper

    eng = build_upbit_paper(sessions=sessions, daily_seed=seed)
    eng.clock = FixedClock(now)
    eng.runner.bus = CollectingBus()
    await eng.restore()
    return eng


async def _feed(eng, bars):
    for b in bars:
        await eng.runner.on_bars_closed([b])


def test_midday_restart_keeps_position_stop_and_does_not_rebuy(no_events):
    """10시대 진입 → 11시 재시작: 같은 날 다시 팔거나 사지 않고, 판단도 다시 묻지 않고, 손절선이 남는다."""

    async def go():
        _, sessions = await memory_sessions()
        seed = {"KRW-BTC": _rising_days(40, "2026-09-28")}
        p = float(seed["KRW-BTC"]["close"].iloc[-1])
        bars = _breakout_bars(p)
        eng = await _db_engine(sessions, {k: v.copy() for k, v in seed.items()}, DAY)
        await _feed(eng, bars)
        fills = [e.fill for t, e in eng.runner.bus.events if t == "fill"]
        assert [f.side for f in fills] == [Side.BUY]
        stop = eng.runner._on.stops["KRW-BTC"]
        eng.clock = FixedClock(DAY + timedelta(hours=2))
        await eng.on_link(eng.clock.now())  # 처리 표시를 우편함에 쓴다

        # 재시작: REST 시드에는 오늘 진행 중인 봉(이미 목표가를 넘은 고가)이 들어 있다
        new_seed = {"KRW-BTC": eng.runner.daily.view()["KRW-BTC"].copy()}
        eng2 = await _db_engine(sessions, new_seed, DAY + timedelta(hours=2))
        assert eng2.runner.executor.positions()["KRW-BTC"].is_open
        assert eng2.runner._on.stops["KRW-BTC"] == pytest.approx(stop)
        last = bars[-1].close
        more = [
            _minute("KRW-BTC", DAY + timedelta(minutes=120 + i), last, last, last, last)
            for i in range(3)
        ]
        await _feed(eng2, more)
        assert not [e for t, e in eng2.runner.bus.events if t in ("signal", "judgment", "fill")]

    asyncio.run(go())


def test_restart_without_mailbox_record_still_protects_todays_position(no_events):
    """우편함에 쓰기 전에 죽어도, 오늘 연 포지션이면 같은 날 청산·재진입하지 않는다."""

    async def go():
        _, sessions = await memory_sessions()
        seed = {"KRW-BTC": _rising_days(40, "2026-09-28")}
        p = float(seed["KRW-BTC"]["close"].iloc[-1])
        bars = _breakout_bars(p)
        eng = await _db_engine(sessions, {k: v.copy() for k, v in seed.items()}, DAY)
        await _feed(eng, bars)  # on_link를 부르지 않는다 → 우편함 기록 없음
        new_seed = {"KRW-BTC": eng.runner.daily.view()["KRW-BTC"].copy()}
        eng2 = await _db_engine(sessions, new_seed, DAY + timedelta(hours=2))
        last = bars[-1].close
        await _feed(
            eng2, [_minute("KRW-BTC", DAY + timedelta(minutes=121), last, last, last, last)]
        )
        assert not [e for t, e in eng2.runner.bus.events if t in ("signal", "fill")]

    asyncio.run(go())


def test_late_start_after_breakout_fills_near_current_price(no_events):
    """돌파가 이미 지난 뒤 처음 켜진 엔진은 목표가가 아니라 지금 분봉 가격 근처에서 산다."""

    async def go():
        _, sessions = await memory_sessions()
        seed = {"KRW-BTC": _rising_days(40, "2026-09-28")}
        p = float(seed["KRW-BTC"]["close"].iloc[-1])
        target = p + float(seed["KRW-BTC"]["high"].iloc[-1] - seed["KRW-BTC"]["low"].iloc[-1]) * 0.5
        bars = _breakout_bars(p)
        warm = await _db_engine(sessions, {k: v.copy() for k, v in seed.items()}, DAY)
        for b in bars:  # 오늘 진행 중인 봉만 만든다 (매매 없음) — REST 시드가 줄 값과 같다
            warm.runner.daily.add(b)
        today = {"KRW-BTC": warm.runner.daily.view()["KRW-BTC"].copy()}

        _, fresh_sessions = await memory_sessions()
        eng = await _db_engine(fresh_sessions, today, DAY + timedelta(hours=2))
        last = bars[-1].close
        assert last > target * 1.04
        await _feed(eng, [_minute("KRW-BTC", DAY + timedelta(minutes=121), last, last, last, last)])
        fills = [e.fill for t, e in eng.runner.bus.events if t == "fill"]
        assert [f.side for f in fills] == [Side.BUY]
        assert fills[0].price == pytest.approx(last, rel=0.01)  # 예전엔 목표가(약 4% 아래)에 체결

    asyncio.run(go())


def test_month_baseline_restored_from_mailbox(no_events):
    """월초 평가액이 그 달 것이면 재시작해도 그 값이 서킷브레이커 기준이 된다."""

    async def go():
        from quantpilot.db.repo import SqlConfigRepo
        from quantpilot.engine.link import month_start_key

        _, sessions = await memory_sessions()
        await SqlConfigRepo(sessions).set_setting(
            month_start_key(UP), {"month": "2026-09", "equity": 12_345_678.0}
        )
        seed = {"KRW-BTC": _rising_days(40, "2026-09-28")}
        eng = await _db_engine(sessions, seed, DAY)
        assert eng.runner.risk.month_start_equity == 12_345_678.0
        assert eng.runner.shadow.risk.month_start_equity == 12_345_678.0
        stale = await _db_engine(
            sessions, seed, local(2026, 10, 2, 10, 0)
        )  # 다른 달 기록은 쓰지 않는다
        assert stale.runner.risk.month_start_equity is None

    asyncio.run(go())


def test_db_engine_persists_minute_bars_and_survives_store_failure(no_events):
    """DB가 있으면 확정 분봉을 candles 표에 쓰고, 저장이 실패해도 매매 루프는 계속된다."""

    async def go():
        from quantpilot.db.repo import SqlCandleRepo

        _, sessions = await memory_sessions()
        seed = {"KRW-BTC": _rising_days(40, "2026-09-28")}
        eng = await _db_engine(sessions, seed, DAY + timedelta(minutes=5))
        assert isinstance(eng.store, CandleStore)
        p = float(seed["KRW-BTC"]["close"].iloc[-1])
        eng._closed = [_minute("KRW-BTC", DAY, p, p, p, p)]
        assert await eng.on_timer() == 1
        got = await CandleStore(SqlCandleRepo(sessions), UP).load(
            "KRW-BTC", "1m", DAY - timedelta(minutes=1), DAY + timedelta(minutes=1)
        )
        assert len(got) == 1

        class Broken:
            async def upsert(self, bars):
                raise RuntimeError("db down")

        eng.store = Broken()
        eng._closed = [_minute("KRW-BTC", DAY + timedelta(minutes=1), p, p, p, p)]
        assert await eng.on_timer() == 1  # 예외 없이 엔진으로 넘어간다
        assert eng.runner.history.last_ts("KRW-BTC") == DAY + timedelta(minutes=1)

    asyncio.run(go())
