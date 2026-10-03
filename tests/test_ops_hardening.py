"""배포 전 운영 안정화 (ADR 0029) — 로그 비밀, 빈 환경변수, 스케줄러 훅 보호, 시세 연결 표시."""

from __future__ import annotations

import asyncio
import logging

import httpx

from quantpilot.core.models import Market
from quantpilot.realtime import keys as hk
from quantpilot.realtime.hub import MemoryHub


def test_setup_logging_keeps_tokens_out_of_logs(caplog):
    """httpx는 INFO에서 URL 전체(텔레그램 봇 토큰·DART 키)를 남긴다 — WARNING으로 올려 막는다."""
    from quantpilot.logsetup import QUIET, setup_logging

    before = {n: logging.getLogger(n).level for n in QUIET}
    try:
        setup_logging()
        caplog.set_level(logging.INFO)
        t = httpx.MockTransport(lambda r: httpx.Response(200, json={}))
        with httpx.Client(transport=t) as c:
            c.post("https://api.telegram.org/botSECRET123/sendMessage")
            c.get("https://opendart.fss.or.kr/api/list.json", params={"crtfc_key": "DARTKEY9"})
        assert "SECRET123" not in caplog.text and "DARTKEY9" not in caplog.text
    finally:
        for n, lv in before.items():
            logging.getLogger(n).setLevel(lv)


def test_empty_env_does_not_override_registered_keys(monkeypatch, tmp_path):
    """.env의 `QP_X=` 빈 줄(compose가 빈 환경변수로 넣는다)이 화면 등록 키(data/keys.env)를 덮지 않는다."""
    from quantpilot.config import Settings

    keys = tmp_path / "keys.env"
    keys.write_text("QP_TELEGRAM_BOT_TOKEN=from-ui\n", encoding="utf-8")
    monkeypatch.setenv("QP_TELEGRAM_BOT_TOKEN", "")
    assert Settings(_env_file=(keys,)).telegram_bot_token == "from-ui"
    monkeypatch.setenv("QP_TELEGRAM_BOT_TOKEN", "from-env")  # 값이 있는 환경변수는 여전히 우선
    assert Settings(_env_file=(keys,)).telegram_bot_token == "from-env"


def test_scheduler_starts_without_prescreen_when_ai_config_is_broken(monkeypatch, caplog):
    """사전 심사 훅을 만들다 실패해도(키 없음 등) 그 훅만 빠지고 나머지 잡은 뜬다."""
    import quantpilot.scheduler.main as sched

    def broken(*a, **k):
        raise ValueError("QP_ANTHROPIC_API_KEY=sk-secret missing")

    monkeypatch.setattr(sched, "make_prescreen", broken)
    caplog.set_level(logging.ERROR)
    assert sched._hooks(None) == {}
    assert "prescreen hook disabled" in caplog.text
    assert "sk-secret" not in caplog.text  # 예외 메시지(키가 섞일 수 있음)는 남기지 않는다


def test_engine_marks_feed_on_trades_throttled(monkeypatch, tmp_path):
    """체결을 받으면 허브 feed 키(TTL 60초)를 쓴다 — /health 시세 연결 표시. 10초에 한 번만 쓴다."""
    from quantpilot.config import settings
    from quantpilot.engine.main import FEED_EVERY, build_upbit_paper

    monkeypatch.setattr(settings, "events_file", tmp_path / "none.yaml")
    hub = MemoryHub()
    eng = build_upbit_paper(hub=hub)
    t = [100.0]
    eng._monotonic = lambda: t[0]
    writes = []
    real_set = hub.set

    async def spy(key, value, *, ttl=None):
        writes.append((key, ttl))
        await real_set(key, value, ttl=ttl)

    hub.set = spy

    async def go():
        await eng._mark_feed()
        t[0] += FEED_EVERY / 2
        await eng._mark_feed()
        t[0] += FEED_EVERY
        await eng._mark_feed()
        return await hub.get(hk.feed(Market.UPBIT))

    assert asyncio.run(go()) == 1
    assert writes == [(hk.feed(Market.UPBIT), 60.0)] * 2


def test_engine_without_hub_skips_feed(monkeypatch, tmp_path):
    from quantpilot.config import settings
    from quantpilot.engine.main import build_upbit_paper

    monkeypatch.setattr(settings, "events_file", tmp_path / "none.yaml")
    eng = build_upbit_paper()
    asyncio.run(eng._mark_feed())  # 허브가 없으면 예외 없이 아무것도 하지 않는다
