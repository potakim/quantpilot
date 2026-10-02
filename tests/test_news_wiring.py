"""뉴스·이벤트를 엔진 판단 입력과 scheduler에 배선 (ADR 0021)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pandas as pd
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from quantpilot.core.clock import to_local
from quantpilot.core.models import Market, Target
from quantpilot.core.news import NewsItem
from quantpilot.data.events import EventCalendar
from quantpilot.data.news import (
    DEFAULT_SOURCES_FILE,
    NewsCache,
    NewsRefresher,
    TitleSummarizer,
    load_news_config,
    match_symbols,
    parse_news_config,
    parse_rss,
)
from quantpilot.db.models import Base
from quantpilot.db.news_repo import SqlNewsRepo
from quantpilot.features.builder import FeatureBuilder
from quantpilot.scheduler.wiring import (
    make_daily_reviewer,
    make_news_collector,
    make_summarizer,
)
from quantpilot.strategies import create
from quantpilot.strategies.base import Context

NOW = datetime(2026, 10, 2, 3, 0, tzinfo=UTC)


@pytest.fixture
async def sessions():
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def _settings(**kw):
    base = {
        "news_file": None,
        "events_file": "missing-events.yaml",
        "google_api_key": "",
        "anthropic_api_key": "",
        "dart_api_key": "",
    }
    return SimpleNamespace(**(base | kw))


# ---------- 수집 설정 ----------
def test_naive_feed_dates_use_feed_offset():
    """시간대 표시 없는 한국 매체 날짜(KST)를 UTC로 바꾼다 — 그대로 UTC로 보면 9시간 미래가 된다."""
    xml = (
        "<rss><channel><item><title>t</title>"
        "<pubDate>2026-10-02 12:02:02</pubDate></item></channel></rss>"
    )
    (kst,) = parse_rss(xml, "einfomax", fetched_at=NOW, naive_utc_offset_hours=9)
    assert kst.ts == datetime(2026, 10, 2, 3, 2, 2, tzinfo=UTC)
    (utc,) = parse_rss(xml, "x", fetched_at=NOW)
    assert utc.ts == datetime(2026, 10, 2, 12, 2, 2, tzinfo=UTC)


def test_default_sources_cover_vol_breakout_universe():
    cfg = load_news_config()
    assert DEFAULT_SOURCES_FILE.exists()
    assert {f.kind for f in cfg.feeds} == {"rss", "dart"}
    assert set(create("vol_breakout").symbols) <= set(cfg.keywords)
    assert "*" in cfg.keywords
    einfomax = next(f for f in cfg.feeds if f.name == "einfomax")
    assert einfomax.naive_utc_offset_hours == 9


@pytest.mark.parametrize(
    "title",
    ["A new solution for Canada", "Second quarter results", "The method", "트리플 위칭 데이"],
)
def test_default_keywords_do_not_match_common_words(title):
    """짧은 약어(SOL·ADA·SEC·ETH·리플)가 일반 단어에 섞여 오탐하지 않는다."""
    cfg = load_news_config()
    item = NewsItem(ts=NOW, source="t", title=title)
    assert match_symbols(item, cfg.keywords) == []


def test_default_keywords_match_coin_names():
    cfg = load_news_config()
    item = NewsItem(ts=NOW, source="t", title="솔라나 네트워크 장애, 리플(XRP) 급락")
    assert set(match_symbols(item, cfg.keywords)) == {"KRW-SOL", "KRW-XRP"}


def test_empty_news_file_env_uses_default(monkeypatch):
    """`.env`의 `QP_NEWS_FILE=`(빈 값)은 Path('.')가 된다 — 폴더를 열지 말고 기본값을 쓴다."""
    from quantpilot.config import Settings

    monkeypatch.setenv("QP_NEWS_FILE", "")
    s = Settings(_env_file=None)
    assert load_news_config(s.news_file) == load_news_config()


def test_news_config_rejects_bad_rows():
    with pytest.raises(ValueError):
        parse_news_config({"feeds": [{"name": "x"}]})
    with pytest.raises(ValueError):
        parse_news_config({"feeds": [{"name": "x", "url": "u", "kind": "atom"}]})


def test_title_summarizer_truncates_without_flags():
    s = TitleSummarizer().summarize("가" * 150, "본문")
    assert len(s.summary) == 100 and s.summary.endswith("…")
    assert s.risk_flags == () and s.risk_score is None


# ---------- scheduler 배선 ----------
def test_collector_falls_back_to_title_summary_without_key(tmp_path):
    p = tmp_path / "news.yaml"
    p.write_text(
        "feeds:\n  - {name: a, url: 'https://a/rss'}\nkeywords:\n  KRW-BTC: [비트코인]\n",
        encoding="utf-8",
    )
    c = make_news_collector(_settings(news_file=p), NewsCache())
    assert isinstance(c.summarizer, TitleSummarizer)
    assert [f.name for f in c.feeds] == ["a"] and c.keywords == {"KRW-BTC": ("비트코인",)}


def test_summarizer_with_key_but_no_sdk_falls_back(monkeypatch):
    import quantpilot.judgment.google as g

    monkeypatch.setattr(g, "genai", None)
    assert isinstance(make_summarizer(_settings(google_api_key="k")), TitleSummarizer)


def test_daily_reviewer_none_without_key():
    assert make_daily_reviewer(_settings()) is None


async def test_daily_reviewer_asks_answerer_and_survives_failure():
    class Answerer:
        def __init__(self, fail=False):
            self.fail, self.seen = fail, []

        async def answer(self, question, context):
            if self.fail:
                raise TimeoutError
            self.seen.append((question, context))
            return "리뷰", 0.01

    a = Answerer()
    review = make_daily_reviewer(_settings(), answerer=a)
    assert await review({"upbit": {"fills": 3}}) == ("리뷰", 0.01)
    assert a.seen[0][1] == {"stats": {"upbit": {"fills": 3}}}
    assert "적용은 사람" in a.seen[0][0]
    text, cost = await make_daily_reviewer(_settings(), answerer=Answerer(fail=True))({})
    assert "실패" in text and cost is None


# ---------- 엔진: DB 뉴스 → 캐시 → 판단 입력 ----------
async def test_refresher_moves_db_news_to_cache_every_period(sessions):
    repo = SqlNewsRepo(sessions)
    now = [NOW]
    cache, calendar = NewsCache(), EventCalendar()
    r = NewsRefresher(repo, cache, calendar=calendar, utcnow=lambda: now[0])
    await repo.add(
        [
            NewsItem(
                NOW - timedelta(hours=1),
                "dart",
                "OO 상장폐지 사유 발생",
                symbols=["KRW-BTC"],
                summary="상장폐지 사유",
            ),
        ]
    )
    assert await r.maybe_refresh() == 1
    assert len(cache) == 1 and len(calendar) == 1  # DART 위험 공시 → 이벤트
    await repo.add([NewsItem(NOW, "x", "비트코인 해킹", symbols=["KRW-BTC"], summary="해킹")])
    assert await r.maybe_refresh() == 0  # 5분 안에는 다시 읽지 않는다
    now[0] += timedelta(minutes=5)
    assert await r.maybe_refresh() == 1 and len(cache) == 2


async def test_refresher_survives_db_error(caplog):
    class Broken:
        async def recent(self, since):
            raise OSError("db down")

    r = NewsRefresher(Broken(), NewsCache(), utcnow=lambda: NOW)
    assert await r.maybe_refresh() == 0
    assert "news refresh failed" in caplog.text


async def test_engine_state_carries_news_from_db(sessions, monkeypatch, tmp_path):
    """scheduler가 DB에 넣은 뉴스가 엔진 판단 입력(state.news_summary)까지 간다."""
    from quantpilot.config import settings
    from quantpilot.engine.main import build_upbit_paper

    monkeypatch.setattr(settings, "events_file", tmp_path / "none.yaml")
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    eng = build_upbit_paper(sessions=sessions)
    assert isinstance(eng.runner.feature_builder, FeatureBuilder)
    assert eng.news is not None

    await SqlNewsRepo(sessions).add(
        [
            NewsItem(
                datetime.now(UTC) - timedelta(hours=1),
                "blockmedia",
                "비트코인 거래소 해킹",
                symbols=["KRW-BTC"],
                summary="거래소 해킹으로 비트코인 출금 중단",
            )
        ]
    )
    await eng.news.refresh()

    # 봉 시각은 시장 현지시간(KST) tz-naive — 테스트 머신 시간대(CI는 UTC)와 무관하게
    local_now = to_local(datetime.now(UTC), Market.UPBIT)
    idx = pd.date_range(end=pd.Timestamp(local_now).floor("min"), periods=30, freq="min")
    df = pd.DataFrame(
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1.0}, index=idx
    )
    ctx = Context(ts=idx[-1], bars={"KRW-BTC": df}, positions={}, equity=1e7)
    state = eng.runner.feature_builder.build(
        symbol="KRW-BTC",
        strategy="vol_breakout",
        target=Target("KRW-BTC", 0.1, price=100.5),
        ctx=ctx,
    )
    assert "거래소 해킹" in state.news_summary


def test_engine_without_db_uses_feature_builder_without_refresher(monkeypatch, tmp_path):
    from quantpilot.config import settings
    from quantpilot.engine.main import build_upbit_paper

    monkeypatch.setattr(settings, "events_file", tmp_path / "none.yaml")
    eng = build_upbit_paper()
    assert isinstance(eng.runner.feature_builder, FeatureBuilder) and eng.news is None
