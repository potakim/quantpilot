"""실시간 분봉으로 일봉 전략 평가 (ADR 0025) — 1분봉을 '전일'로 착각해 매분 매매하던 문제의 회귀 방지."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pandas as pd
import pytest

from quantpilot.core.events import BarClosed
from quantpilot.core.models import Market, Side
from quantpilot.engine.daily import DailyRollup
from quantpilot.engine.replay import CollectingBus

UP = Market.UPBIT
NINE = timedelta(hours=9)


def local(*args: int) -> datetime:
    """업비트 현지(KST) tz-naive 봉 시각 — 엔진 규칙 (core/clock)."""
    return pd.Timestamp(*args).to_pydatetime()


def _minute(sym: str, ts: datetime, o: float, h: float, lo: float, c: float) -> BarClosed:
    return BarClosed(UP, sym, "1m", ts, o, h, lo, c, 1.0)


def _rising_days(n: int, last_day: str, start: float = 100.0) -> pd.DataFrame:
    """종가가 매일 1%씩 오르는 일봉 (이평 스코어 1, 전일 변동폭 2%) — 인덱스는 거래일 시작 09:00."""
    idx = pd.date_range(end=pd.Timestamp(last_day) + NINE, periods=n, freq="D")
    close = [start * 1.01**i for i in range(n)]
    opens = [start, *close[:-1]]
    return pd.DataFrame(
        {
            "open": opens,
            "high": [c * 1.01 for c in close],
            "low": [c * 0.99 for c in close],
            "close": close,
            "volume": 1.0,
        },
        index=idx,
    )


# ---------- DailyRollup ----------
def test_rollup_keeps_seed_open_and_updates_today():
    seed = _rising_days(3, "2026-09-29")  # 마지막 행 = 9/29 거래일(진행 중)
    r = DailyRollup(NINE, {"A": seed})
    today_open = seed["open"].iloc[-1]
    r.add(_minute("A", local(2026, 9, 29, 15, 0), 1, 999.0, 0.5, 123.0))
    row = r.view()["A"].iloc[-1]
    assert len(r.view()["A"]) == 3
    assert row["open"] == today_open and row["high"] == 999.0 and row["low"] == 0.5
    assert row["close"] == 123.0


def test_rollup_day_boundary_is_0900():
    r = DailyRollup(NINE)
    r.add(_minute("A", local(2026, 9, 30, 8, 59), 10, 11, 9, 10))  # 9/29 거래일
    r.add(_minute("A", local(2026, 9, 30, 9, 0), 20, 21, 19, 20))  # 9/30 거래일 시작
    r.add(_minute("A", local(2026, 9, 30, 8, 30), 1, 1, 1, 1))  # 지난 거래일의 늦은 봉은 버림
    df = r.view()["A"]
    assert list(df.index) == [pd.Timestamp("2026-09-29 09:00"), pd.Timestamp("2026-09-30 09:00")]
    assert df["open"].tolist() == [10, 20]


# ---------- 실시간 엔진 회귀 ----------
@pytest.fixture
def engine(monkeypatch, tmp_path):
    from quantpilot.config import settings
    from quantpilot.engine.main import build_upbit_paper

    monkeypatch.setattr(settings, "events_file", tmp_path / "none.yaml")
    seed = {"KRW-BTC": _rising_days(40, "2026-09-28")}  # 9/28 거래일까지 확정
    eng = build_upbit_paper(daily_seed=seed)
    bus = CollectingBus()
    eng.runner.bus = bus
    return eng, bus, seed["KRW-BTC"]


def _feed(eng, bars):
    async def go():
        for b in bars:
            await eng.runner.on_bars_closed([b])

    asyncio.run(go())


def test_one_entry_one_judgment_per_day_and_exit_next_day(engine):
    eng, bus, seed = engine
    p = float(seed["close"].iloc[-1])  # 9/29 09:00 시가
    target = p + float(seed["high"].iloc[-1] - seed["low"].iloc[-1]) * 0.5
    day = local(2026, 9, 29, 9, 0)
    bars = []
    for i in range(180):  # 3시간: 0.05%씩 올라 목표가를 넘었다가 다시 내린다
        o = p * (1 + 0.0005 * min(i, 90) - 0.0005 * max(0, i - 90))
        c = o * (1.0005 if i < 90 else 0.9995)
        bars.append(_minute("KRW-BTC", day + timedelta(minutes=i), o, max(o, c), min(o, c), c))
    assert max(b.high for b in bars) > target  # 장중 돌파가 실제로 일어난다
    _feed(eng, bars)

    entries = [e for t, e in bus.events if t == "signal" and e.kind == "entry"]
    judgments = [e for t, e in bus.events if t == "judgment"]
    fills = [e.fill for t, e in bus.events if t == "fill"]
    assert len(entries) == 1 and len(judgments) == 1  # 분봉 수백 개에도 진입·판단은 한 번
    assert [f.side for f in fills] == [Side.BUY]  # 장중 청산·재매수 없음
    assert fills[0].price >= target  # 목표가 이상에서 체결

    # 다음 거래일 첫 분: 전일 매수분 시간 청산 (05 §1)
    nxt = local(2026, 9, 30, 9, 0)
    _feed(eng, [_minute("KRW-BTC", nxt + timedelta(minutes=i), p, p, p, p) for i in range(5)])
    fills = [e.fill for t, e in bus.events if t == "fill"]
    assert [f.side for f in fills] == [Side.BUY, Side.SELL]
    assert not eng.runner.executor.positions()
    # 청산 신호는 실제로 보유분이 있던 다음 날 한 번뿐 — 9/29 09:00의 보유 없는 '비중 0'은 남지 않는다 (ADR 0027)
    kinds = [e.kind for t, e in bus.events if t == "signal"]
    assert kinds == ["entry", "exit"]
    rules = eng.runner.rule_days
    assert rules["2026-09-29"]["vol_breakout"] == {
        "evaluated": {"KRW-BTC"},
        "signaled": {"KRW-BTC"},
    }
    assert rules["2026-09-30"]["vol_breakout"] == {"evaluated": {"KRW-BTC"}, "signaled": set()}


def test_flat_day_without_breakout_records_rule_unmet_only(engine):
    """돌파가 없고 보유도 없는 날: 신호 0건, 규칙 미충족 1건(BTC)으로만 남는다 (ADR 0027)."""
    eng, bus, seed = engine
    p = float(seed["close"].iloc[-1])
    day = local(2026, 9, 29, 9, 0)
    _feed(eng, [_minute("KRW-BTC", day + timedelta(minutes=i), p, p, p, p) for i in range(30)])
    assert not [e for t, e in bus.events if t in ("signal", "judgment", "fill")]
    rec = eng.runner.rule_days["2026-09-29"]["vol_breakout"]
    assert rec == {"evaluated": {"KRW-BTC"}, "signaled": set()}
    assert eng.runner.rule_dirty


def test_engine_link_merges_rule_days(engine):
    """MarketEngine은 바뀐 기록만 우편함에 합쳐 쓰고 dirty를 내린다."""
    eng, _, seed = engine

    class Link:
        def __init__(self):
            self.calls = []

        async def merge_rules(self, market, days):
            self.calls.append({d: {k: dict(v) for k, v in s.items()} for d, s in days.items()})

    p = float(seed["close"].iloc[-1])
    _feed(eng, [_minute("KRW-BTC", local(2026, 9, 29, 9, 0), p, p, p, p)])
    link = Link()
    eng.link = link
    asyncio.run(eng._sync_rules(UP))
    asyncio.run(eng._sync_rules(UP))  # 바뀐 게 없으면 다시 쓰지 않는다
    assert len(link.calls) == 1 and "2026-09-29" in link.calls[0]
    assert not eng.runner.rule_dirty


def test_no_trading_without_daily_history(monkeypatch, tmp_path):
    """시드가 없으면 준비 기간이 차지 않아 진입하지 않는다 (REST 실패 시 안전한 쪽)."""
    from quantpilot.config import settings
    from quantpilot.engine.main import build_upbit_paper

    monkeypatch.setattr(settings, "events_file", tmp_path / "none.yaml")
    eng = build_upbit_paper()
    bus = CollectingBus()
    eng.runner.bus = bus
    t0 = local(2026, 10, 3, 10, 0)
    price = 100.0
    bars = []
    for i in range(60):
        bars.append(
            _minute(
                "KRW-BTC",
                t0 + timedelta(minutes=i),
                price,
                price * 1.002,
                price * 0.999,
                price * 1.0015,
            )
        )
        price *= 1.0015
    _feed(eng, bars)
    assert not [e for t, e in bus.events if t in ("signal", "fill")]
