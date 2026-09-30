"""뉴스 수집·중복 제거·이벤트 캘린더·news_items 저장소 (P1-06)."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from quantpilot.core.news import NewsItem, NewsRepo, Summary, raw_hash
from quantpilot.data.events import EventCalendar, events_from_dart, parse_calendar
from quantpilot.data.news import (
    DART_LIST_URL,
    Feed,
    NewsCache,
    NewsCollector,
    parse_dart,
    parse_rss,
)
from quantpilot.db.models import Base
from quantpilot.db.news_repo import SqlNewsRepo

FIX = Path(__file__).parent / "fixtures" / "news"
NOW = datetime(2026, 9, 30, 4, 0, tzinfo=UTC)
KEYWORDS = {
    "KRW-BTC": ["비트코인", "bitcoin", "BTC"],
    "KRW-ETH": ["이더리움", "ethereum", "ETH"],
    "*": ["CPI", "FOMC"],
}
STOCKS = {"005930": "005930", "123456": "123456"}
SECRET = "dart-secret-key-123"


class CountingSummarizer:
    def __init__(self):
        self.calls: list[str] = []

    def summarize(self, title: str, body: str) -> Summary:
        self.calls.append(title)
        flags = ("hack",) if "해킹" in title else ()
        return Summary(f"요약:{title[:20]}", flags, 0.8 if flags else 0.1)


def fake_fetch(pages: dict[str, str], seen: list | None = None):
    async def fetch(url, params):
        if seen is not None:
            seen.append((url, dict(params)))
        if url not in pages:
            raise RuntimeError(f"GET {url}?crtfc_key={params.get('crtfc_key')} failed")
        return pages[url]

    return fetch


def _collector(pages, repo=None, summarizer=None, feeds=None, **kw):
    return NewsCollector(
        feeds or [Feed("coin", "https://rss/coin")],
        summarizer or CountingSummarizer(),
        repo if repo is not None else NewsCache(),
        keywords=KEYWORDS,
        stock_symbols=STOCKS,
        dart_api_key=SECRET,
        fetch=fake_fetch(pages),
        **kw,
    )


# ---------- 파싱 ----------
def test_parse_rss_and_atom():
    rss = parse_rss((FIX / "rss.xml").read_text(), "coin", fetched_at=NOW)
    assert len(rss) == 5
    assert rss[0].ts == datetime(2026, 9, 30, 1, 0, tzinfo=UTC)
    assert rss[1].ts == datetime(2026, 9, 29, 17, 30, tzinfo=UTC)  # +0900 → UTC
    assert rss[0].url == "https://news.example.com/a1"
    atom = parse_rss((FIX / "atom.xml").read_text(), "wire", fetched_at=NOW)
    assert [(a.title, a.url, a.ts) for a in atom] == [
        (
            "Ethereum hard fork scheduled",
            "https://wire.example.com/e1",
            datetime(2026, 9, 30, 3, 0, tzinfo=UTC),
        )
    ]


def test_parse_rss_rejects_entity_declarations():
    bomb = '<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "aaaa">]><rss><channel><item><title>&a;</title></item></channel></rss>'
    with pytest.raises(ValueError):
        parse_rss(bomb, "x", fetched_at=NOW)


def test_parse_dart_maps_stock_codes_and_kst_date():
    items = parse_dart((FIX / "dart_list.json").read_text(), "dart", stock_symbols=STOCKS)
    assert [i.symbols for i in items] == [["005930"], ["123456"], []]
    assert items[0].ts == datetime(2026, 9, 29, 15, 0, tzinfo=UTC)  # 2026-09-30 00:00 KST
    assert items[0].url.endswith("rcpNo=20260930800001")
    assert (
        parse_dart(
            '{"status": "013", "message": "조회된 데이타가 없습니다."}', "d", stock_symbols={}
        )
        == []
    )
    with pytest.raises(ValueError):
        parse_dart('{"status": "020", "message": "요청 제한"}', "d", stock_symbols={})


# ---------- 중복 제거 ----------
def test_raw_hash_normalizes_whitespace_and_case():
    assert raw_hash("  비트코인   ETF ", "u") == raw_hash("비트코인 etf", "u")
    assert raw_hash("a", "u1") != raw_hash("a", "u2")


def test_dedup_within_batch_and_across_runs_before_summarizing():
    """같은 배치의 중복·이미 저장된 뉴스는 요약기를 부르지 않는다."""
    pages = {"https://rss/coin": (FIX / "rss.xml").read_text()}
    summ = CountingSummarizer()
    repo = NewsCache()
    first = asyncio.run(_collector(pages, repo, summ).collect(NOW))
    # 5건 중 1건은 공백만 다른 중복, 1건(날씨)은 매칭 없음 → 3건
    assert sorted(i.title for i in first) == [
        "미국 CPI 발표 앞두고 관망",
        "비트코인 현물 ETF 2일 연속 순유입",
        "이더리움 거래소 해킹 의혹, 입출금 중단",
    ]
    assert len(summ.calls) == 3 and len(repo) == 3
    by_title = {i.title: i for i in first}
    assert by_title["미국 CPI 발표 앞두고 관망"].symbols == ["*"]
    assert by_title["이더리움 거래소 해킹 의혹, 입출금 중단"].risk_flags == ["hack"]

    second = asyncio.run(_collector(pages, repo, summ).collect(NOW + timedelta(hours=1)))
    assert second == [] and len(summ.calls) == 3 and len(repo) == 3


def test_old_news_is_ignored():
    pages = {"https://rss/coin": (FIX / "rss.xml").read_text()}
    out = asyncio.run(_collector(pages).collect(NOW + timedelta(days=3)))
    assert out == []


def test_feed_failure_is_isolated_and_key_not_logged(caplog):
    pages = {"https://rss/coin": (FIX / "rss.xml").read_text()}
    feeds = [Feed("coin", "https://rss/coin"), Feed("dart", DART_LIST_URL, "dart")]
    with caplog.at_level(logging.DEBUG):
        out = asyncio.run(_collector(pages, feeds=feeds).collect(NOW))
    assert len(out) == 3  # DART 실패해도 RSS는 수집
    for rec in caplog.records:
        assert SECRET not in rec.getMessage()
        assert SECRET not in str(rec.__dict__)
    assert SECRET not in repr(_collector(pages, feeds=feeds))


def test_dart_feed_passes_key_as_param_and_matches_symbols():
    seen: list = []
    c = NewsCollector(
        [Feed("dart", DART_LIST_URL, "dart")],
        CountingSummarizer(),
        NewsCache(),
        stock_symbols=STOCKS,
        dart_api_key=SECRET,
        fetch=fake_fetch({DART_LIST_URL: (FIX / "dart_list.json").read_text()}, seen),
    )
    out = asyncio.run(c.collect(NOW))
    assert sorted(i.symbols[0] for i in out) == ["005930", "123456"]
    assert seen[0][1]["crtfc_key"] == SECRET and seen[0][1]["bgn_de"] == "20260930"


def test_dart_feed_skipped_without_key():
    c = NewsCollector(
        [Feed("dart", DART_LIST_URL, "dart")],
        CountingSummarizer(),
        NewsCache(),
        dart_api_key="",
        fetch=fake_fetch({}),
    )
    assert asyncio.run(c.collect(NOW)) == []


# ---------- 이벤트 캘린더 ----------
def test_calendar_yaml_and_dart_events(tmp_path):
    p = tmp_path / "events.yaml"
    p.write_text(
        "events:\n"
        "  - ts: 2026-10-29T18:00:00Z\n    kind: fomc\n    title: FOMC 금리 결정\n"
        "  - ts: 2026-10-02 03:00\n    market: upbit\n    kind: exchange_maintenance\n"
        "    title: 업비트 정기 점검\n    symbols: [KRW-BTC]\n",
        encoding="utf-8",
    )
    cal = EventCalendar.from_yaml(p)
    assert len(cal) == 2
    maint = cal.within("KRW-BTC", NOW, NOW + timedelta(days=3))
    assert [(e.kind, e.ts) for e in maint] == [
        ("exchange_maintenance", datetime(2026, 10, 1, 18, 0, tzinfo=UTC))  # 03:00 KST
    ]
    assert cal.within("KRW-ETH", NOW, NOW + timedelta(days=3)) == []
    assert [e.kind for e in cal.within("KRW-ETH", NOW, NOW + timedelta(days=40))] == ["fomc"]

    dart = parse_dart((FIX / "dart_list.json").read_text(), "dart", stock_symbols=STOCKS)
    added = cal.add(events_from_dart(dart))
    assert added == 2 and cal.add(events_from_dart(dart)) == 0
    kinds = {e.symbols: e.kind for e in cal.within("123456", NOW - timedelta(days=1), NOW)}
    assert kinds == {("123456",): "delisting_review"}
    assert EventCalendar.from_yaml(tmp_path / "missing.yaml").within("x", NOW, NOW) == []


def test_calendar_rejects_incomplete_rows():
    with pytest.raises(ValueError):
        parse_calendar({"events": [{"ts": "2026-10-01T00:00:00Z", "kind": "fomc"}]})


# ---------- news_items 저장소 ----------
@pytest.fixture
async def sessions():
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def test_sql_news_repo_dedup_and_roundtrip(sessions):
    repo: NewsRepo = SqlNewsRepo(sessions)
    a = NewsItem(
        NOW,
        "coin",
        "비트코인 ETF",
        "https://a",
        symbols=["KRW-BTC"],
        summary="요약",
        risk_flags=["regulation"],
        risk_score=0.4,
    )
    dup = NewsItem(NOW, "coin", " 비트코인  etf", "https://a")
    b = NewsItem(NOW - timedelta(hours=1), "coin", "이더리움", "https://b", symbols=["KRW-ETH"])
    assert await repo.add([a, dup, b]) == 2
    assert await repo.add([a]) == 0
    assert await repo.known_hashes([a.raw_hash, "nope"]) == {a.raw_hash}
    got = await repo.recent(NOW - timedelta(hours=2))
    assert [g.title for g in got] == ["이더리움", "비트코인 ETF"]
    assert (
        got[1].ts == NOW and got[1].risk_flags == ["regulation"] and got[1].raw_hash == a.raw_hash
    )


async def test_collector_with_sql_repo(sessions):
    pages = {"https://rss/coin": (FIX / "rss.xml").read_text()}
    repo = SqlNewsRepo(sessions)
    assert len(await _collector(pages, repo).collect(NOW)) == 3
    assert await _collector(pages, repo).collect(NOW) == []
