"""P1-08 LLM 리뷰어(Claude·Gemini) + JudgmentPipeline. 가짜 SDK 클라이언트로 결정적으로 돈다."""

# 시각은 시장 현지 tz-naive (core/clock.py 규칙)
# ruff: noqa: DTZ001

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime
from types import SimpleNamespace

import pytest

from quantpilot.core.errors import JudgeError, JudgeTimeout
from quantpilot.core.events import SignalEvent
from quantpilot.core.models import Gate, JudgeResult, Market, Target
from quantpilot.judgment.anthropic import ClaudeReviewer
from quantpilot.judgment.base import JudgeProvider, LLMProvider, LLMVerdict, State
from quantpilot.judgment.google import GeminiReviewer
from quantpilot.judgment.pipeline import JudgmentPipeline, build_pipeline, make_reviewer
from quantpilot.judgment.prompts import load_review_prompt, parse_verdict
from quantpilot.judgment.stub import StubLLM, StubPipeline

TS = datetime(2026, 9, 30, 9, 15)
STATE = State("upbit", "KRW-ETH", "orb", "orb breakout +0.4%", {"ma_score": 0.9})
SAFE = {"news_risk": 0.1, "event_ahead": 0.05, "signal_quality": 0.8}
SECRET = "fake-anthropic-key-xyz"
OK_JSON = '{"approve": true, "reason": "막을 이유 없음"}'


def _signal(strategy: str = "orb") -> SignalEvent:
    return SignalEvent(
        Market.UPBIT, strategy, Target("KRW-ETH", 0.5, reason="5분 고가 돌파"), "entry", TS, 7
    )


class FixedJudge(JudgeProvider):
    name = "fixed"

    def __init__(self, answers=SAFE, confidence: float = 0.95, cost: float = 0.0, exc=None):
        self.answers, self.confidence, self.cost, self.exc = answers, confidence, cost, exc
        self.consecutive_timeouts = 0

    def judge(self, state, questions=()):
        if self.exc:
            raise self.exc
        return JudgeResult(dict(self.answers), self.confidence, model=self.name, cost_usd=self.cost)


class FakeReviewer(LLMProvider):
    def __init__(self, name: str, approve: bool = True, delay: float = 0.0, cost: float = 0.0):
        self.name, self.approve, self.delay, self.cost = name, approve, delay, cost
        self.calls: list[str] = []

    def review(self, state, judge):  # pragma: no cover - 파이프라인은 areview만 쓴다
        raise AssertionError

    async def areview(self, state, judge, rule=""):
        self.calls.append(rule)
        await asyncio.sleep(self.delay)
        return LLMVerdict(self.name, self.approve, "ok", cost_usd=self.cost)


# ---------- 가짜 SDK 클라이언트 ----------


class FakeAnthropic:
    """AsyncAnthropic의 `messages.create` 부분."""

    def __init__(self, text=OK_JSON, stop_reason="end_turn", exc=None, usage=(1000, 50)):
        self.text, self.stop_reason, self.exc, self.usage = text, stop_reason, exc, usage
        self.calls: list[dict] = []
        self.messages = self

    async def create(self, **kw):
        self.calls.append(kw)
        if self.exc:
            raise self.exc
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=self.text)],
            usage=SimpleNamespace(input_tokens=self.usage[0], output_tokens=self.usage[1]),
            stop_reason=self.stop_reason,
        )


class FakeGenAI:
    """genai.Client의 `aio.models.generate_content` 부분."""

    def __init__(self, text=OK_JSON, exc=None, usage=(1000, 50)):
        self.text, self.exc, self.usage = text, exc, usage
        self.calls: list[dict] = []
        self.aio = SimpleNamespace(models=self)

    async def generate_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        if self.exc:
            raise self.exc
        meta = SimpleNamespace(
            prompt_token_count=self.usage[0], candidates_token_count=self.usage[1]
        )
        return SimpleNamespace(text=self.text, usage_metadata=meta)


JR = JudgeResult(dict(SAFE), 0.95, model="fixed")


# ---------- 프롬프트·파싱 ----------


def test_review_prompt_contract():
    p = load_review_prompt("v1")
    assert len(p.prompt_hash) == 16
    text = p.render(STATE.render(), JR, "orb: 5분 고가 돌파")
    assert "orb: 5분 고가 돌파" in text and "KRW-ETH" in text and '"news_risk": 0.1' in text
    assert "$" not in text  # 자리표시자가 모두 채워졌다
    assert "막아야 할 이유" in text  # 신호를 만들지 말고 막을 이유만 묻는다 (06 §5)


@pytest.mark.parametrize(
    "raw,expected",
    [
        (OK_JSON, (True, "막을 이유 없음")),
        ('```json\n{"approve": false, "reason": "규제 뉴스"}\n```', (False, "규제 뉴스")),
        ("not json", (False, "응답 파싱 실패 → hold")),
        ("[1, 2]", (False, "응답 계약 위반 → hold")),
        ('{"approve": "yes", "reason": "x"}', (False, "응답 계약 위반 → hold")),
        ("", (False, "응답 파싱 실패 → hold")),
    ],
)
def test_parse_verdict_failures_are_hold(raw, expected):
    assert parse_verdict(raw) == expected


def test_parse_verdict_truncates_reason():
    approve, reason = parse_verdict('{"approve": true, "reason": "' + "가" * 200 + '"}')
    assert approve and len(reason) == 80


# ---------- 어댑터 ----------


async def test_claude_request_shape_and_cost():
    client = FakeAnthropic()
    v = await ClaudeReviewer(client=client).areview(STATE, JR, "orb")
    assert (v.approve, v.model) == (True, "claude-sonnet-5")
    assert v.prompt_hash == load_review_prompt("v1").prompt_hash
    # Claude Sonnet 5 $2/$10 per M → 1000·2 + 50·10 = 2500 / 1e6
    assert v.cost_usd == pytest.approx(0.0025)
    kw = client.calls[0]
    assert kw["max_tokens"] == 200 and kw["thinking"] == {"type": "disabled"}
    assert kw["output_config"]["format"]["type"] == "json_schema"
    assert "temperature" not in kw  # Sonnet 5는 샘플링 인자를 받지 않는다 (ADR 0014)


async def test_gemini_request_shape_and_cost():
    client = FakeGenAI()
    v = await GeminiReviewer(client=client).areview(STATE, JR, "orb")
    assert (v.approve, v.model) == (True, "gemini-3.5-flash")
    # Gemini 3.5 Flash $1.50/$9 per M → 1500 + 450 = 1950 / 1e6
    assert v.cost_usd == pytest.approx(0.00195)
    cfg = client.calls[0]["config"]
    assert cfg["temperature"] == 0 and cfg["max_output_tokens"] == 200


async def test_both_reviewers_get_identical_prompt():
    a, g = FakeAnthropic(), FakeGenAI()
    await ClaudeReviewer(client=a).areview(STATE, JR, "orb")
    await GeminiReviewer(client=g).areview(STATE, JR, "orb")
    assert a.calls[0]["messages"][0]["content"] == g.calls[0]["contents"]


@pytest.mark.parametrize(
    "make",
    [
        lambda: ClaudeReviewer(client=FakeAnthropic(text="garbage")),
        lambda: ClaudeReviewer(client=FakeAnthropic(stop_reason="refusal")),
        lambda: ClaudeReviewer(client=FakeAnthropic(exc=RuntimeError("boom"))),
        lambda: GeminiReviewer(client=FakeGenAI(text="{broken")),
        lambda: GeminiReviewer(client=FakeGenAI(exc=ConnectionError("down"))),
    ],
    ids=["claude-parse", "claude-refusal", "claude-error", "gemini-parse", "gemini-error"],
)
async def test_adapter_failures_are_hold(make):
    v = await make().areview(STATE, JR)
    assert v.approve is False and "hold" in v.reason


def test_unknown_model_fails_at_start():
    with pytest.raises(ValueError):
        ClaudeReviewer("claude-unknown-9", client=FakeAnthropic())


def test_empty_key_rejected_and_key_never_in_repr(monkeypatch):
    """키는 설정으로만 받고 repr·클라이언트 밖으로 새지 않는다 (불변식 #10). SDK 없이도 돈다."""
    import quantpilot.judgment.anthropic as mod

    made: list[dict] = []
    monkeypatch.setattr(
        mod,
        "anthropic",
        SimpleNamespace(AsyncAnthropic=lambda **kw: made.append(kw) or FakeAnthropic()),
    )
    with pytest.raises(ValueError):
        ClaudeReviewer(api_key="")
    r = ClaudeReviewer(api_key=SECRET)
    assert SECRET not in repr(r) and SECRET not in str(vars(r).get("prompt"))
    assert made[0]["api_key"] == SECRET and made[0]["max_retries"] == 0  # 재시도 = 30초 초과


async def test_key_not_logged_on_failure(caplog):
    caplog.set_level(logging.DEBUG)
    r = ClaudeReviewer(client=FakeAnthropic(exc=RuntimeError(f"401 bad key {SECRET}")))
    v = await r.areview(STATE, JR)
    # 예외 메시지(키가 섞일 수 있다)는 로그·판정 이유에 싣지 않고 예외 이름만 남긴다
    assert SECRET not in caplog.text and SECRET not in v.reason and "RuntimeError" in v.reason


# ---------- 파이프라인 ----------


async def test_consensus_both_approve_full():
    a, b = FakeReviewer("a"), FakeReviewer("b")
    ev = await JudgmentPipeline(FixedJudge(), [a, b]).evaluate(_signal(), STATE)
    assert (ev.gate, ev.size_multiplier) == (Gate.FULL, 1.0)
    assert [v.model for v in ev.verdicts] == ["a", "b"]
    assert a.calls == ["orb: 5분 고가 돌파"]  # 전략 규칙 한 줄이 리뷰어에게 간다


async def test_one_hold_blocks_entry():
    p = JudgmentPipeline(FixedJudge(), [FakeReviewer("a"), FakeReviewer("b", approve=False)])
    ev = await p.evaluate(_signal(), STATE)
    assert (ev.gate, ev.size_multiplier) == (Gate.HOLD, 0.0)


async def test_review_timeout_is_hold():
    slow = FakeReviewer("slow", delay=1.0)
    p = JudgmentPipeline(FixedJudge(), [FakeReviewer("a"), slow], review_timeout=0.05)
    ev = await p.evaluate(_signal(), STATE)
    assert ev.size_multiplier == 0.0
    v = next(v for v in ev.verdicts if v.model == "slow")
    assert v.approve is False and "타임아웃" in v.reason


async def test_reviewer_exception_is_hold():
    class Boom(FakeReviewer):
        async def areview(self, state, judge, rule=""):
            raise RuntimeError("x")

    ev = await JudgmentPipeline(FixedJudge(), [FakeReviewer("a"), Boom("b")]).evaluate(
        _signal(), STATE
    )
    assert ev.size_multiplier == 0.0 and "RuntimeError" in ev.verdicts[1].reason


async def test_reviewers_run_in_parallel():
    rs = [FakeReviewer("a", delay=0.2), FakeReviewer("b", delay=0.2)]
    t = time.perf_counter()
    ev = await JudgmentPipeline(FixedJudge(), rs).evaluate(_signal(), STATE)
    elapsed = time.perf_counter() - t
    assert ev.size_multiplier == 1.0
    assert elapsed < 0.35, f"직렬이면 0.4초 이상: {elapsed:.3f}"


async def test_hard_block_skips_llm():
    """hard_blocks가 확신도·합의보다 우선 — LLM을 부르지도 않는다 (불변식 #8)."""
    a = FakeReviewer("a")
    judge = FixedJudge({**SAFE, "news_risk": 0.8}, confidence=0.99)
    ev = await JudgmentPipeline(judge, [a]).evaluate(_signal(), STATE)
    assert ev.size_multiplier == 0.0 and ev.blocks and a.calls == [] and ev.verdicts == ()


async def test_low_confidence_skips_llm():
    a = FakeReviewer("a")
    ev = await JudgmentPipeline(FixedJudge(confidence=0.3), [a]).evaluate(_signal(), STATE)
    assert ev.gate == Gate.HOLD and a.calls == []


async def test_judge_error_is_hold_without_llm():
    a = FakeReviewer("a")
    ev = await JudgmentPipeline(FixedJudge(exc=JudgeError("bad")), [a]).evaluate(_signal(), STATE)
    assert ev.size_multiplier == 0.0 and ev.result.confidence == 0.0 and a.calls == []
    assert ev.result.raw == {"error": "JudgeError"}


async def test_non_llm_strategy_uses_gate_only():
    """vol_breakout은 진입마다 LLM을 부르지 않는다(08:10 사전 심사는 스케줄러) — 06 §4 표."""
    a = FakeReviewer("a", approve=False)
    ev = await JudgmentPipeline(FixedJudge(confidence=0.7), [a]).evaluate(
        _signal("vol_breakout"), STATE
    )
    assert (ev.gate, ev.size_multiplier) == (Gate.HALF, 0.5) and a.calls == []


async def test_gating_off_records_only():
    a = FakeReviewer("a", approve=False)
    p = JudgmentPipeline(FixedJudge({**SAFE, "news_risk": 0.9}), [a], gating=False)
    ev = await p.evaluate(_signal(), STATE)
    assert ev.size_multiplier == 1.0 and ev.blocks and a.calls == []


async def test_daily_budget_stops_llm_but_not_judge():
    a = FakeReviewer("a", cost=0.6)
    p = JudgmentPipeline(FixedJudge(cost=0.001), [a], budget_usd_daily=1.0)
    first = await p.evaluate(_signal(), STATE)
    second = await p.evaluate(_signal(), STATE)
    assert first.size_multiplier == 1.0 and second.size_multiplier == 1.0
    assert p.spent_usd(TS.date()) == pytest.approx(1.202)
    third = await p.evaluate(_signal(), STATE)  # 예산 초과 → LLM 합의 중단 = hold
    assert third.size_multiplier == 0.0 and third.verdicts[0].model == "budget"
    assert len(a.calls) == 2 and third.result.confidence == 0.95  # 판단 모델은 계속


class RecordingBus:
    def __init__(self):
        self.events: list[tuple[str, object]] = []

    async def publish(self, topic, event):
        self.events.append((topic, event))


async def test_judge_down_after_ten_timeouts_published_once():
    judge = FixedJudge(exc=JudgeTimeout("slow"))
    bus = RecordingBus()
    p = JudgmentPipeline(judge, [], bus=bus)
    judge.consecutive_timeouts = 9
    await p.evaluate(_signal(), STATE)
    assert bus.events == []
    judge.consecutive_timeouts = 10
    await p.evaluate(_signal(), STATE)
    judge.consecutive_timeouts = 11
    await p.evaluate(_signal(), STATE)
    assert len(bus.events) == 1
    topic, ev = bus.events[0]
    assert topic == "warning" and ev.kind == "judge_down"
    assert ev.detail == {"provider": "fixed", "consecutive_timeouts": 10}
    judge.consecutive_timeouts = 0  # 회복하면 다음 장애에 다시 알린다
    await p.evaluate(_signal(), STATE)
    judge.consecutive_timeouts = 10
    await p.evaluate(_signal(), STATE)
    assert len(bus.events) == 2


def test_gate_threshold_range_enforced():
    with pytest.raises(ValueError):
        JudgmentPipeline(FixedJudge(), hold_below=0.1)
    with pytest.raises(ValueError):
        JudgmentPipeline(FixedJudge(), full_above=0.99)


# ---------- 배선 ----------


def _settings(**kw):
    base = {
        "judge_provider": "stub",
        "llm_providers": ["stub", "stub"],
        "gate_hold_below": 0.5,
        "gate_full_above": 0.9,
        "ai_budget_usd_daily": 2.0,
        "typesafe_api_key": "ts-key",
    }
    return SimpleNamespace(**{**base, **kw})


def test_build_pipeline_stub_default():
    assert isinstance(build_pipeline(_settings()), StubPipeline)


def test_build_pipeline_typesafe_wires_judge_and_reviewers():
    p = build_pipeline(_settings(judge_provider="typesafe", ai_budget_usd_daily=1.5))
    assert isinstance(p, JudgmentPipeline) and p.judge.name == "typesafe"
    assert len(p.reviewers) == 2 and all(isinstance(r, StubLLM) for r in p.reviewers)
    assert p.gating and p.budget_usd_daily == 1.5


def test_build_pipeline_rejects_unknown():
    with pytest.raises(ValueError):
        build_pipeline(_settings(judge_provider="laya"))
    with pytest.raises(ValueError):
        make_reviewer("gpt")


# ---------- 실제 API (기본 제외) ----------


@pytest.mark.network
@pytest.mark.skipif(not os.getenv("QP_ANTHROPIC_API_KEY"), reason="QP_ANTHROPIC_API_KEY 없음")
async def test_claude_live_contract():
    v = await ClaudeReviewer(api_key=os.environ["QP_ANTHROPIC_API_KEY"]).areview(STATE, JR, "orb")
    assert isinstance(v.approve, bool) and v.cost_usd > 0 and "실패" not in v.reason


@pytest.mark.network
@pytest.mark.skipif(not os.getenv("QP_GOOGLE_API_KEY"), reason="QP_GOOGLE_API_KEY 없음")
async def test_gemini_live_contract():
    v = await GeminiReviewer(api_key=os.environ["QP_GOOGLE_API_KEY"]).areview(STATE, JR, "orb")
    assert isinstance(v.approve, bool) and v.cost_usd > 0 and "실패" not in v.reason
