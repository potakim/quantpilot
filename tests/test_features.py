"""FeatureBuilder (P1-06, 04 §3, 06 §3)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from quantpilot.backtest import preset
from quantpilot.core import ports
from quantpilot.core.models import Market, Target
from quantpilot.core.news import EventItem, NewsItem
from quantpilot.data import synthetic as S
from quantpilot.data.events import EventCalendar
from quantpilot.data.news import CacheNewsSource, NewsCache
from quantpilot.engine.replay import CollectingBus, DirectExecutor, ReplayClock, UnrestrictedRisk
from quantpilot.engine.tick import TickRunner
from quantpilot.execution.paper import PaperBroker
from quantpilot.features import FeatureBuilder, compute_features, count_tokens
from quantpilot.judgment.stub import StubPipeline
from quantpilot.strategies import create
from quantpilot.strategies.base import Context

SYM = "KRW-ETH"
# 업비트 현지(KST) 2026-09-30 12:00 == UTC 03:00
TS_LOCAL = pd.Timestamp("2026-09-30 12:00")
NOW_UTC = datetime(2026, 9, 30, 3, 0, tzinfo=UTC)


def _ctx(df: pd.DataFrame) -> Context:
    return Context(ts=TS_LOCAL, bars={SYM: df}, positions={}, equity=1_000_000)


def _df(n: int = 300) -> pd.DataFrame:
    df = S.daily(S.seed_of(SYM), periods=n, start="2025-12-01")
    df.index = pd.date_range(end=TS_LOCAL, periods=n, freq="D")
    return df


def _news(hours_ago: float, summary: str, flags=(), symbols=(SYM,), n=0) -> NewsItem:
    return NewsItem(
        ts=NOW_UTC - timedelta(hours=hours_ago),
        source="t",
        title=f"title-{hours_ago}-{n}",
        url=f"https://x/{hours_ago}/{n}",
        symbols=list(symbols),
        summary=summary,
        risk_flags=list(flags),
    )


def _builder(news=(), events=(), **kw) -> FeatureBuilder:
    cache = NewsCache(retention=timedelta(days=30))
    asyncio.run(cache.add(list(news)))
    return FeatureBuilder(
        Market.UPBIT, news=CacheNewsSource(cache), events=EventCalendar(list(events)), **kw
    )


def test_satisfies_core_protocol():
    fb: ports.FeatureBuilder = FeatureBuilder(Market.UPBIT)
    assert callable(fb.build)


def test_features_are_grades_not_raw_prices():
    df = _df()
    st = FeatureBuilder(Market.UPBIT, spread=lambda s: 2.4).build(
        symbol=SYM,
        strategy="vol_breakout",
        target=Target(SYM, 1.0, reason="breakout k=0.5"),
        ctx=_ctx(df),
    )
    f = st.features
    assert set(f) == {
        "vol_pctl_20d",
        "ma_score",
        "volume_ratio",
        "spread_bps",
        "dist_from_high_20d_pct",
        "rsi2",
    }
    assert 0 <= f["vol_pctl_20d"] <= 100 and isinstance(f["vol_pctl_20d"], int)
    assert 0 <= f["ma_score"] <= 1
    assert f["volume_ratio"].endswith("x") and len(f["volume_ratio"].split(".")[1]) == 2
    assert f["spread_bps"] == 2
    assert f["dist_from_high_20d_pct"] <= 0
    assert 0 <= f["rsi2"] <= 100 and isinstance(f["rsi2"], int)
    # 원시 가격이 state에 들어가지 않는다
    text = st.render()
    for px in (df["close"].iloc[-1], df["high"].tail(20).max()):
        assert f"{px:.2f}" not in text and str(int(px)) not in text
    assert st.news_summary == "" and st.events_24h == "none"


def test_short_history_omits_features_instead_of_guessing():
    assert compute_features(_df(2)) == {}
    assert set(compute_features(_df(10))) == {"ma_score", "rsi2"}
    assert compute_features(_df(5).iloc[0:0]) == {}


@pytest.mark.invariant
def test_price_target_does_not_look_at_current_bar():
    """불변식 #3: Target.price 전략의 state는 현재 봉(종가·고가·거래량)에 영향받지 않는다."""
    df = _df()
    bumped = df.copy()
    bumped.iloc[-1, bumped.columns.get_loc("close")] *= 1.5
    bumped.iloc[-1, bumped.columns.get_loc("high")] *= 1.6
    bumped.iloc[-1, bumped.columns.get_loc("volume")] *= 10
    fb = FeatureBuilder(Market.UPBIT)
    t = Target(SYM, 1.0, price=float(df["open"].iloc[-1]), reason="breakout")
    a = fb.build(symbol=SYM, strategy="vol_breakout", target=t, ctx=_ctx(df))
    b = fb.build(symbol=SYM, strategy="vol_breakout", target=t, ctx=_ctx(bumped))
    assert a.features == b.features
    # 종가 체결 전략은 현재 봉까지 본다
    close_t = Target(SYM, 1.0, reason="rebalance")
    c = fb.build(symbol=SYM, strategy="gem", target=close_t, ctx=_ctx(df))
    d = fb.build(symbol=SYM, strategy="gem", target=close_t, ctx=_ctx(bumped))
    assert c.features != d.features


def test_news_selection_flagged_first_max_three_within_24h_symbol_only():
    news = [
        _news(1, "최신 무위험"),
        _news(2, "두번째 무위험"),
        _news(3, "세번째 무위험"),
        _news(20, "오래된 규제 뉴스", flags=("regulation",)),
        _news(30, "24시간 밖", flags=("hack",)),
        _news(1, "다른 종목", flags=("hack",), symbols=("KRW-BTC",)),
        _news(4, "거시 뉴스", symbols=("*",)),
        NewsItem(ts=NOW_UTC, source="t", title="요약 없음", symbols=[SYM]),
    ]
    st = _builder(news).build(
        symbol=SYM, strategy="vol_breakout", target=Target(SYM, 1.0), ctx=_ctx(_df())
    )
    assert st.news_summary == '"오래된 규제 뉴스" | "최신 무위험" | "두번째 무위험"'
    assert "title-" not in st.render() and "https://" not in st.render()


def test_events_within_24h_both_directions_and_symbol_scoped():
    ev = [
        EventItem(NOW_UTC + timedelta(hours=5), "fomc", "FOMC 금리 결정"),
        EventItem(NOW_UTC - timedelta(hours=3), "delisting_review", "상장폐지 심사", (SYM,)),
        EventItem(NOW_UTC + timedelta(hours=30), "cpi", "CPI 발표"),
        EventItem(NOW_UTC + timedelta(hours=1), "earnings", "다른 종목 실적", ("KRW-BTC",)),
    ]
    st = _builder(events=ev).build(
        symbol=SYM, strategy="vol_breakout", target=Target(SYM, 1.0), ctx=_ctx(_df())
    )
    assert st.events_24h == "delisting_review: 상장폐지 심사 (-3h); fomc: FOMC 금리 결정 (+5h)"


def test_state_under_400_tokens_worst_case_trims_news_first():
    """06 §3: state는 400토큰 이내. 넘으면 뉴스부터 자른다."""
    long_kr = "가" * 150  # 요약기 계약을 어겨도 100자로 잘린다
    news = [_news(i + 1, long_kr + str(i), flags=("hack",), n=i) for i in range(5)]
    events = [
        EventItem(NOW_UTC + timedelta(hours=i + 1), "maint", "점검" * 50, ()) for i in range(10)
    ]
    fb = _builder(news, events, spread=lambda s: 3.0)
    st = fb.build(
        symbol=SYM,
        strategy="vol_breakout",
        target=Target(SYM, 1.0, reason="이유" * 100),
        ctx=_ctx(_df()),
    )
    assert count_tokens(st.render()) <= 400
    assert st.news_summary == ""  # 뉴스가 먼저 빠지고
    assert st.events_24h.endswith(" …")  # 그다음 이벤트가 줄어든다
    assert set(st.features) >= {"vol_pctl_20d", "rsi2"}  # 피처는 자르지 않는다


def test_typical_state_keeps_all_three_news():
    news = [
        _news(i + 1, "현물 ETF 순유입 2일 연속, 규제 이슈 없음" + "." * 50, n=i) for i in range(3)
    ]
    st = _builder(news, [EventItem(NOW_UTC + timedelta(hours=2), "fomc", "FOMC")]).build(
        symbol=SYM,
        strategy="vol_breakout",
        target=Target(SYM, 1.0, reason="breakout"),
        ctx=_ctx(_df()),
    )
    assert st.news_summary.count(" | ") == 2
    assert count_tokens(st.render()) <= 400


def test_count_tokens_is_conservative():
    assert count_tokens("") == 0
    assert count_tokens("가나다") == 3
    assert count_tokens("vol_pctl_20d: 78") == 10  # vol _ pctl _ 2 0 d : 7 8
    assert count_tokens("breakout") == 2


def test_same_input_same_state():
    fb = _builder([_news(1, "뉴스")])
    args = {
        "symbol": SYM,
        "strategy": "vol_breakout",
        "target": Target(SYM, 1.0),
        "ctx": _ctx(_df()),
    }
    assert fb.build(**args).render() == fb.build(**args).render()


# ---------- TickRunner 통합: 진입마다 400토큰 이내 state ----------
class RecordingPipeline(StubPipeline):
    def __init__(self):
        super().__init__()
        self.states = []

    async def evaluate(self, signal, state):
        self.states.append(state)
        return await super().evaluate(signal, state)


@pytest.mark.integration
def test_tickrunner_with_feature_builder_states_fit_budget():
    strat = create("vol_breakout")
    market = Market(strat.market)
    data = S.universe(strat.symbols, periods=200, start="2020-01-01")
    broker = PaperBroker(market, preset(market), 10_000_000)
    risk = UnrestrictedRisk()
    pipe = RecordingPipeline()
    runner = TickRunner(
        market,
        [strat],
        FeatureBuilder(market),
        pipe,
        DirectExecutor(broker, risk),
        risk,
        ReplayClock(()),
        CollectingBus(),
        cost=preset(market),
    )
    from quantpilot.core.events import BarClosed

    async def go():
        for ts in sorted(set().union(*(df.index for df in data.values()))):
            evs = [
                BarClosed(
                    market,
                    s,
                    "1d",
                    ts.to_pydatetime(),
                    *df.loc[ts, ["open", "high", "low", "close", "volume"]],
                )
                for s, df in data.items()
                if ts in df.index
            ]
            await runner.on_bars_closed(evs)

    asyncio.run(go())
    assert len(pipe.states) > 5
    for st in pipe.states:
        assert count_tokens(st.render()) <= 400
        assert "rsi2" in st.features
