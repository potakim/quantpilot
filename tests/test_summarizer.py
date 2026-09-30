"""GeminiSummarizer 요약 계약 (P1-06). 실응답 모양 fixture + 가짜 클라이언트."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from quantpilot.core.news import RISK_FLAGS, SUMMARY_MAX_CHARS, Summary
from quantpilot.judgment.google import GeminiSummarizer, parse_summary

RESPONSES = json.loads((Path(__file__).parent / "fixtures/news/gemini_responses.json").read_text())
TITLE = "이더리움 거래소 해킹 의혹, 입출금 중단"
SECRET = "google-secret-key-xyz"


class FakeClient:
    def __init__(self, text: str | None = None, exc: Exception | None = None):
        self.text, self.exc, self.calls = text, exc, []
        self.models = self

    def generate_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        if self.exc:
            raise self.exc
        return SimpleNamespace(text=self.text)


def _check_contract(s: Summary):
    assert isinstance(s, Summary)
    assert 0 < len(s.summary) <= SUMMARY_MAX_CHARS
    assert set(s.risk_flags) <= RISK_FLAGS and len(set(s.risk_flags)) == len(s.risk_flags)
    assert s.risk_score is None or 0.0 <= s.risk_score <= 1.0


@pytest.mark.parametrize("key", sorted(RESPONSES))
def test_every_fixture_satisfies_contract(key):
    _check_contract(GeminiSummarizer(client=FakeClient(RESPONSES[key])).summarize(TITLE, "본문"))


def test_ok_and_fenced_responses_parse():
    ok = parse_summary(RESPONSES["ok"], fallback_title=TITLE)
    assert ok == Summary("미국 비트코인 현물 ETF에 이틀 연속 자금 순유입, 규제 이슈 없음", (), 0.1)
    fenced = parse_summary(RESPONSES["fenced"], fallback_title=TITLE)
    assert fenced.risk_flags == ("hack", "exchange_outage") and fenced.risk_score == 0.8


def test_contract_repairs():
    assert len(parse_summary(RESPONSES["too_long"], fallback_title=TITLE).summary) == 100
    bad = parse_summary(RESPONSES["bad_flags"], fallback_title=TITLE)
    assert bad.risk_flags == ("regulation",) and bad.risk_score == 1.0
    assert parse_summary(RESPONSES["bool_score"], fallback_title=TITLE).risk_score is None


@pytest.mark.parametrize("key", ["broken", "not_object"])
def test_unparseable_falls_back_to_title_with_unknown_risk(key):
    assert parse_summary(RESPONSES[key], fallback_title=TITLE) == Summary(TITLE, (), None)


def test_call_failure_falls_back_and_never_logs_key(caplog):
    client = FakeClient(exc=RuntimeError(f"401 invalid key {SECRET}"))
    s = GeminiSummarizer(client=client)
    with caplog.at_level(logging.DEBUG):
        out = s.summarize(TITLE, "본문")
    assert out == Summary(TITLE, (), None)
    assert all(SECRET not in str(r.__dict__) for r in caplog.records)


def test_request_is_deterministic_and_asks_no_trading_numbers():
    client = FakeClient(RESPONSES["ok"])
    GeminiSummarizer(client=client).summarize(TITLE, "본문" * 5000)
    call = client.calls[0]
    assert call["model"] == "gemini-3.5-flash-lite"
    assert call["config"]["temperature"] == 0
    assert TITLE in call["contents"] and len(call["contents"]) < 3000  # 본문 2000자 절단
    assert "수량은 답하지 마라" in call["contents"]


def test_key_only_from_settings_and_not_in_repr(monkeypatch):
    s = GeminiSummarizer(client=FakeClient(RESPONSES["ok"]))
    assert SECRET not in repr(s)
    pytest.importorskip("google.genai")
    from quantpilot import config

    monkeypatch.setattr(config.settings, "google_api_key", "")
    with pytest.raises(ValueError, match="QP_GOOGLE_API_KEY"):
        GeminiSummarizer()


@pytest.mark.network
def test_real_gemini_summary_contract():
    if not os.environ.get("QP_GOOGLE_API_KEY"):
        pytest.skip("QP_GOOGLE_API_KEY 없음")
    pytest.importorskip("google.genai")
    s = GeminiSummarizer().summarize(TITLE, "한 해외 거래소가 이더리움 입출금을 중단했다.")
    _check_contract(s)
