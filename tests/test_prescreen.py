"""08:10 사전 심사: scheduler 심사 → settings 우편함 → 엔진 → 판단 파이프라인 (ADR 0004·0022)."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from quantpilot.core.clock import to_local, upbit_trading_day
from quantpilot.core.events import SignalEvent
from quantpilot.core.models import Gate, Market, Target
from quantpilot.core.news import NewsItem
from quantpilot.data.news import NewsCache
from quantpilot.engine.link import SettingsEngineLink
from quantpilot.engine.main import MarketEngine
from quantpilot.judgment.base import LLMProvider, LLMVerdict
from quantpilot.judgment.pipeline import JudgmentPipeline
from quantpilot.judgment.stub import StubJudge, StubLLM, StubPipeline
from quantpilot.scheduler.wiring import Prescreen, make_prescreen

KST = ZoneInfo("Asia/Seoul")


def kst(*args: int) -> datetime:
    """KST 벽시계 → 업비트 현지 tz-naive (엔진 시계·봉 시각 규칙)."""
    return to_local(datetime(*args, tzinfo=KST), Market.UPBIT)


# 2026-10-02 08:10 KST
NOW = datetime(2026, 10, 1, 23, 10, tzinfo=UTC)


class MemConfig:
    def __init__(self):
        self.d: dict = {}

    async def get_setting(self, key, default=None):
        return self.d.get(key, default)

    async def set_setting(self, key, value):
        self.d[key] = value


class Notes:
    def __init__(self):
        self.sent: list[tuple[str, str]] = []

    async def send(self, level, text, *, key=None):
        self.sent.append((level, text))


class Reviewer(LLMProvider):
    """뉴스에 '해킹'이 있으면 hold, slow면 타임아웃."""

    def __init__(self, name="r", slow=False):
        self.name, self.slow, self.rules = name, slow, []

    def review(self, state, judge):  # pragma: no cover - areview만 쓴다
        raise NotImplementedError

    async def areview(self, state, judge, rule=""):
        self.rules.append(rule)
        if self.slow:
            await asyncio.sleep(1)
        if "해킹" in state.news_summary:
            return LLMVerdict(self.name, False, "해킹 뉴스", cost_usd=0.001)
        return LLMVerdict(self.name, True, "문제 없음", cost_usd=0.001)


async def _cache(*items: NewsItem) -> NewsCache:
    c = NewsCache()
    await c.add(list(items))
    return c


# ---------- 거래일 ----------
@pytest.mark.parametrize(
    ("kst_time", "day"),
    [
        (datetime(2026, 10, 2, 8, 59, tzinfo=KST), date(2026, 10, 1)),
        (datetime(2026, 10, 2, 9, 0, tzinfo=KST), date(2026, 10, 2)),
        (datetime(2026, 10, 3, 8, 10, tzinfo=KST), date(2026, 10, 2)),
    ],
)
def test_upbit_trading_day_turns_at_0900_kst(kst_time, day):
    assert upbit_trading_day(kst_time) == day


async def test_link_prescreen_roundtrip():
    link = SettingsEngineLink(MemConfig(), utcnow=lambda: NOW)
    assert await link.prescreen(Market.UPBIT) is None
    await link.set_prescreen(Market.UPBIT, "2026-10-02", {"KRW-SOL": "해킹"})
    assert await link.prescreen(Market.UPBIT) == ("2026-10-02", {"KRW-SOL": "해킹"})


# ---------- scheduler 심사 ----------
async def test_prescreen_blocks_coin_with_risky_news_and_timeouts():
    cache = await _cache(
        NewsItem(
            NOW - timedelta(hours=2),
            "x",
            "솔라나 해킹",
            symbols=["KRW-SOL"],
            summary="솔라나 브리지 해킹으로 출금 중단",
        ),
        NewsItem(
            NOW - timedelta(hours=30),
            "x",
            "에이다 해킹",
            symbols=["KRW-ADA"],
            summary="에이다 해킹(24시간 밖)",
        ),
        NewsItem(NOW - timedelta(hours=1), "x", "FOMC", symbols=["*"], summary="FOMC 동결"),
    )
    a, b = Reviewer("a"), Reviewer("b")
    p = Prescreen(StubJudge(), [a, b], cache, ("KRW-BTC", "KRW-SOL", "KRW-ADA"))
    blocked, cost = await p.screen(NOW)
    assert set(blocked) == {"KRW-SOL"} and "해킹" in blocked["KRW-SOL"]
    assert cost == pytest.approx(0.006)  # 3코인 × 2리뷰어
    assert a.rules[0].startswith("vol_breakout 사전 심사")

    slow = Prescreen(
        StubJudge(), [Reviewer("a"), Reviewer("slow", slow=True)], cache, ("KRW-BTC",), timeout=0.05
    )
    blocked, _ = await slow.screen(NOW)
    assert "리뷰 실패" in blocked["KRW-BTC"]  # 타임아웃 = hold = 제외


async def test_prescreen_hook_writes_mailbox_and_notifies():
    cache = await _cache(
        NewsItem(
            NOW - timedelta(hours=1), "x", "솔라나 해킹", symbols=["KRW-SOL"], summary="솔라나 해킹"
        ),
    )
    link = SettingsEngineLink(MemConfig(), utcnow=lambda: NOW)
    notes = Notes()
    ctx = SimpleNamespace(utcnow=lambda: NOW, link=link, notifier=notes)
    await Prescreen(StubJudge(), [Reviewer()], cache, ("KRW-BTC", "KRW-SOL"))(ctx)
    day, blocked = await link.prescreen(Market.UPBIT)
    assert day == "2026-10-02" and list(blocked) == ["KRW-SOL"]
    assert notes.sent and "KRW-SOL" in notes.sent[0][1]


def test_make_prescreen_defaults_to_stub_models_and_vol_breakout_universe(tmp_path):
    s = SimpleNamespace(
        judge_provider="stub", llm_providers=["stub", "stub"], events_file=tmp_path / "none.yaml"
    )
    p = make_prescreen(s, NewsCache())
    assert isinstance(p.judge, StubJudge) and len(p.reviewers) == 2
    assert all(isinstance(r, StubLLM) for r in p.reviewers)
    assert set(p.symbols) == {"KRW-BTC", "KRW-ETH", "KRW-SOL", "KRW-XRP", "KRW-ADA"}


# ---------- 판단 파이프라인 ----------
def _signal(strategy="vol_breakout", symbol="KRW-SOL"):
    return SignalEvent(
        Market.UPBIT,
        strategy,
        Target(symbol, 0.1, price=1.0),
        "entry",
        kst(2026, 10, 2, 10, 0),
    )


async def test_pipeline_holds_excluded_vol_breakout_entry_but_still_judges():
    from quantpilot.judgment.base import State

    pipe = JudgmentPipeline(StubJudge(), [StubLLM()])
    st = State("upbit", "KRW-SOL", "vol_breakout", "breakout", {"ma_score": 1.0})
    ok = await pipe.evaluate(_signal(), st)
    assert ok.size_multiplier > 0

    pipe.set_prescreen({"KRW-SOL": "a: 해킹 뉴스"})
    je = await pipe.evaluate(_signal(), st)
    assert je.gate == Gate.HOLD and je.size_multiplier == 0.0
    assert "prescreen: a: 해킹 뉴스" in je.blocks
    assert je.result.answers  # 판단 모델은 불렀다 (보정 표본)
    # 다른 코인·다른 전략은 영향 없음
    assert (await pipe.evaluate(_signal(symbol="KRW-BTC"), st)).size_multiplier > 0
    assert "prescreen" not in " ".join((await pipe.evaluate(_signal("orb"), st)).blocks)


async def test_pipeline_gating_off_records_block_but_keeps_size():
    from quantpilot.judgment.base import State

    pipe = JudgmentPipeline(StubJudge(), [StubLLM()], gating=False)
    pipe.set_prescreen({"KRW-SOL": "x"})
    je = await pipe.evaluate(_signal(), State("upbit", "KRW-SOL", "vol_breakout", "b"))
    assert je.size_multiplier == 1.0 and "prescreen: x" in je.blocks


# ---------- 엔진: 우편함 → 파이프라인 ----------
async def _engine_with(pipeline, kst_now: datetime, stored):
    link = SettingsEngineLink(MemConfig(), utcnow=lambda: NOW)
    if stored is not None:
        await link.set_prescreen(Market.UPBIT, *stored)
    runner = SimpleNamespace(market=Market.UPBIT, pipeline=pipeline)
    clock = SimpleNamespace(now=lambda: kst_now)
    return MarketEngine(runner, None, clock, link=link)


async def test_engine_applies_only_todays_prescreen():
    pipe = JudgmentPipeline(StubJudge(), [])
    eng = await _engine_with(pipe, kst(2026, 10, 2, 10, 0), ("2026-10-02", {"KRW-SOL": "x"}))
    await eng._sync_prescreen(Market.UPBIT)
    assert pipe._prescreen == {"KRW-SOL": "x"}

    # 다음 날 09:00 이후에는 어제 목록을 버린다
    eng.clock = SimpleNamespace(now=lambda: kst(2026, 10, 3, 9, 0))
    await eng._sync_prescreen(Market.UPBIT)
    assert pipe._prescreen == {}


async def test_engine_before_0900_keeps_previous_trading_day_list():
    pipe = JudgmentPipeline(StubJudge(), [])
    eng = await _engine_with(pipe, kst(2026, 10, 3, 8, 30), ("2026-10-02", {"KRW-SOL": "x"}))
    await eng._sync_prescreen(Market.UPBIT)
    assert pipe._prescreen == {"KRW-SOL": "x"}


async def test_engine_with_stub_pipeline_ignores_prescreen():
    """judge_provider=stub(게이팅 OFF)은 사전 심사로 사이징을 바꾸지 않는다 (ADR 0021 §6)."""
    eng = await _engine_with(
        StubPipeline(), kst(2026, 10, 2, 10, 0), ("2026-10-02", {"KRW-SOL": "x"})
    )
    await eng._sync_prescreen(Market.UPBIT)  # set_prescreen 없음 → 아무 일 없음
