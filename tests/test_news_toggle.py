"""`news.enabled`는 Gemini 뉴스 요약만 켜고 끈다 (ADR 0035): 꺼도 수집·저장은 그대로, 요약만 제목 요약."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from api_helpers import API, PASSWORD, make_settings, memory_sessions
from test_news import FIX, NOW, CountingSummarizer, _collector

from quantpilot.api.app import create_app
from quantpilot.core.news import SUMMARY_MAX_CHARS
from quantpilot.data.news import NewsCache
from quantpilot.realtime.hub import MemoryHub
from quantpilot.scheduler.context import JobContext
from quantpilot.scheduler.jobs.data import news_collect

RSS = {"https://rss/coin": (FIX / "rss.xml").read_text(encoding="utf-8")}


class Config:
    """settings 저장소 대신."""

    def __init__(self, values: dict[str, Any]) -> None:
        self.values = values

    async def get_setting(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)


def _run(config_values: dict[str, Any] | None):
    summarizer = CountingSummarizer()
    cache = NewsCache()
    ctx = JobContext(
        link=None,
        backup=None,
        utcnow=lambda: NOW,
        config=None if config_values is None else Config(config_values),
        news=_collector(RSS, repo=cache, summarizer=summarizer),
    )
    asyncio.run(news_collect(ctx))
    return summarizer, cache


def test_off_uses_title_summary_and_still_stores_news():
    """꺼짐: Gemini 자리 요약기는 한 번도 안 불리고, 매칭된 뉴스는 제목 요약·빈 위험 플래그로 저장된다."""
    summarizer, cache = _run({"news.enabled": False})
    assert summarizer.calls == []
    items = list(cache._items.values())
    assert items, "수집·저장은 그대로"
    for it in items:
        assert len(it.summary) <= SUMMARY_MAX_CHARS
        assert " ".join(it.title.split()).startswith(it.summary.rstrip("…"))
        assert it.risk_flags == [] and it.risk_score is None


@pytest.mark.parametrize(
    "values", [{"news.enabled": True}, {}, None], ids=["on", "unset", "no-config"]
)
def test_on_or_unset_keeps_llm_summary(values):
    """켜짐·값 없음·설정 저장소 없음: 지금처럼 주입된 요약기(Gemini)로 요약한다."""
    summarizer, cache = _run(values)
    items = list(cache._items.values())
    assert items and len(summarizer.calls) == len(items)
    assert all(it.summary.startswith("요약:") for it in items)


async def test_settings_report_news_summary(tmp_path):
    """GET /settings의 judge.news_summary: 저장값이 없으면 true, PATCH news.enabled=false 뒤 false."""
    engine, sessions = await memory_sessions()
    app = create_app(settings=make_settings(tmp_path), sessions=sessions, hub=MemoryHub())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        r = await c.post(f"{API}/auth/login", json={"password": PASSWORD})
        c.headers["Authorization"] = f"Bearer {r.json()['token']}"
        assert (await c.get(f"{API}/settings")).json()["judge"]["news_summary"] is True
        r = await c.patch(f"{API}/settings", json={"news.enabled": False})
        assert r.status_code == 200, r.text
        assert (await c.get(f"{API}/settings")).json()["judge"]["news_summary"] is False
        r = await c.patch(f"{API}/settings", json={"news.enabled": "off"})
        assert r.status_code == 400
    for t in list(app.state.tasks):
        t.cancel()
    app.state.backtest_pool.shutdown(wait=True)
    await engine.dispose()
