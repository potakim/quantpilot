"""P1-07 TypeSafe Jev 어댑터 · 질문 v1 YAML · pricing (06 §2·§7, ADR 0012).

계약 fixture(tests/fixtures/typesafe_v1_response.json)는 TypeSafe 공식 문서의 응답 스키마
(docs.typesafe.ai quickstart·primitives/score·choice, 2026-09-30 조회)를 그대로 따른다.
실제 API 호출은 @pytest.mark.network 테스트 하나뿐이며 QP_TYPESAFE_API_KEY가 있을 때만 돈다.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path

import httpx
import pytest

from quantpilot.core.errors import JudgeError, JudgeTimeout
from quantpilot.core.events import SignalEvent
from quantpilot.core.models import Gate, JudgeResult, Market, Target
from quantpilot.judgment import DEFAULT_QUESTIONS, State
from quantpilot.judgment.pricing import PRICES, cost_usd, price_for
from quantpilot.judgment.questions import load_questions
from quantpilot.judgment.stub import StubPipeline
from quantpilot.judgment.typesafe import TypeSafeJudge

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "typesafe_v1_response.json").read_text(encoding="utf-8")
)
KEY = "ts-test-SECRET-7f3a9c"
STATE = State(
    "upbit",
    "KRW-ETH",
    "vol_breakout",
    "breakout k=0.5, +0.6% above target",
    {"vol_pctl_20d": 78, "ma_score": 0.75, "volume_ratio": "2.1x"},
    "현물 ETF 순유입 2일 연속, 규제 이슈 없음",
)


def _judge(handler, **kw) -> TypeSafeJudge:
    return TypeSafeJudge(KEY, transport=httpx.MockTransport(handler), **kw)


def _ok(body=None):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body if body is not None else FIXTURE)

    return handler


def _signal() -> SignalEvent:
    return SignalEvent(
        Market.UPBIT,
        "vol_breakout",
        Target("KRW-ETH", 0.2),
        "entry",
        datetime(2026, 9, 30, 9, 5),  # noqa: DTZ001 — 시장 현지 tz-naive
        signal_id=7,
    )


# ---------------------------------------------------------------- 질문 v1 YAML


def test_questions_v1_match_default_questions():
    qs = load_questions("v1")
    assert list(qs.spec) == [q.key for q in DEFAULT_QUESTIONS]
    for q in DEFAULT_QUESTIONS:
        s = qs.spec[q.key]
        assert s["type"] == q.kind
        assert s["instructions"].strip()
        if q.kind == "choice":
            assert tuple(s["criteria"]) == q.options
        else:
            assert qs.levels(q.key) == 5
    assert re.fullmatch(r"[0-9a-f]{16}", qs.prompt_hash)
    assert load_questions("v1").prompt_hash == qs.prompt_hash  # 결정적


@pytest.mark.invariant
def test_questions_never_ask_quantity_price_or_stop():
    """불변식 #7: 판단 모델에게 수량·가격·손절을 묻지 않는다."""
    banned = (
        "quantity", "how many", "how much to buy", "position size", "price target",
        "target price", "stop loss", "stop-loss", "take profit", "수량", "손절", "목표가",
    )  # fmt: skip
    payload = _judge(_ok()).payload(STATE)
    text = json.dumps(payload["questions"], ensure_ascii=False).lower()
    assert not [w for w in banned if w in text]
    assert set(JudgeResult.__dataclass_fields__) == {
        "answers", "confidence", "latency_ms", "model", "cost_usd", "raw",
    }  # fmt: skip


# ---------------------------------------------------------------- 요청 계약


def test_request_matches_typesafe_contract():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=FIXTURE)

    _judge(handler).judge(STATE)
    (req,) = seen
    assert req.method == "POST"
    assert str(req.url) == "https://api.typesafe.ai/v1/systemone"
    assert req.headers["authorization"] == f"Bearer {KEY}"
    body = json.loads(req.content)
    assert body["state"] == STATE.render()
    assert body["model"] == "jev-latest"
    assert list(body["questions"]) == [q.key for q in DEFAULT_QUESTIONS]  # 한 요청에 6개
    assert body["questions"]["regime"]["type"] == "choice"
    assert set(body["questions"]["regime"]["criteria"]) == {"trend_up", "range", "trend_down"}
    assert isinstance(body["questions"]["news_risk"]["criteria"], list)


def test_base_url_swap_for_kev_compatible_server():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json=FIXTURE)

    _judge(handler, base_url="http://127.0.0.1:8080/").judge(STATE)
    assert seen == ["http://127.0.0.1:8080/v1/systemone"]


# ---------------------------------------------------------------- 응답 계약 (fixture)


def test_contract_fixture_parses_to_probabilities_and_scores():
    jr = _judge(_ok()).judge(STATE)
    assert jr.answers["regime"] == {"trend_up": 0.72, "range": 0.21, "trend_down": 0.07}
    # score 0..4 단계 기댓값 → /4
    assert jr.answers["news_risk"] == 0.12
    assert jr.answers["liquidity_stress"] == 0.1
    assert jr.answers["event_ahead"] == 0.05
    assert jr.answers["already_priced"] == 0.3125
    assert jr.answers["signal_quality"] == 0.7
    assert all(0.0 <= jr.answers[k] <= 1.0 for k in jr.answers if k != "regime")
    assert jr.model == "typesafe:jev-1.13.0"
    assert jr.cost_usd == pytest.approx(392 * 0.042 / 1_000_000)
    assert jr.raw["prompt_hash"] == load_questions("v1").prompt_hash
    assert jr.raw["questions_version"] == "v1"
    assert jr.raw["usage"] == {"input_tokens": 392, "output_tokens": 65}
    assert jr.latency_ms >= 0


def test_fixture_scores_are_level_expectations():
    """fixture 자체가 문서 정의(score = Σ 단계 × 확률)와 일치하는지."""
    for key, a in FIXTURE["answers"].items():
        assert abs(sum(a["probabilities"].values()) - 1) < 1e-9, key
        if a["type"] == "score":
            exp = sum(int(k) * v for k, v in a["probabilities"].items())
            assert a["score"] == pytest.approx(exp), key


def test_confidence_is_min_of_question_confidences():
    assert _judge(_ok()).judge(STATE).confidence == 0.55  # already_priced
    for q in DEFAULT_QUESTIONS:
        body = copy.deepcopy(FIXTURE)
        body["answers"][q.key]["confidence"] = 0.31
        assert _judge(_ok(body)).judge(STATE).confidence == 0.31, q.key


async def test_ajudge_matches_judge():
    j = _judge(_ok())
    a, b = await j.ajudge(STATE), j.judge(STATE)
    assert (a.answers, a.confidence, a.model, a.cost_usd) == (
        b.answers,
        b.confidence,
        b.model,
        b.cost_usd,
    )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda b: b["answers"].pop("event_ahead"),
        lambda b: b.pop("answers"),
        lambda b: b["answers"]["regime"]["probabilities"].pop("range"),
        lambda b: b["answers"]["regime"]["probabilities"].update(trend_up=0.9),
        lambda b: b["answers"]["news_risk"].update(score=4.5),
        lambda b: b["answers"]["news_risk"].update(type="noul"),
        lambda b: b["answers"]["signal_quality"].update(confidence=1.2),
        lambda b: b["answers"]["signal_quality"].update(confidence="high"),
    ],
    ids=[
        "missing-key",
        "no-answers",
        "missing-option",
        "probs-not-1",
        "score-out-of-range",
        "wrong-type",
        "confidence-out-of-range",
        "confidence-not-number",
    ],
)
def test_contract_violation_raises_judge_error(mutate):
    body = copy.deepcopy(FIXTURE)
    mutate(body)
    with pytest.raises(JudgeError):
        _judge(_ok(body)).judge(STATE)


@pytest.mark.parametrize("status", [400, 401, 429, 500, 503])
def test_http_error_raises_judge_error(status):
    with pytest.raises(JudgeError) as e:
        _judge(lambda r: httpx.Response(status, json={"error": "x"})).judge(STATE)
    assert not isinstance(e.value, JudgeTimeout)


def test_non_json_and_connection_error_raise_judge_error():
    with pytest.raises(JudgeError):
        _judge(lambda r: httpx.Response(200, text="<html>")).judge(STATE)

    def refuse(request):
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(JudgeError):
        _judge(refuse).judge(STATE)


def test_unknown_response_model_falls_back_to_request_model_price():
    body = copy.deepcopy(FIXTURE)
    body["model"] = "sys1-experimental"
    jr = _judge(_ok(body)).judge(STATE)
    assert jr.model == "typesafe:sys1-experimental"
    assert jr.cost_usd == pytest.approx(cost_usd("jev-latest", 392, 65))


# ---------------------------------------------------------------- 3초 타임아웃 → hold


def test_default_timeout_is_three_seconds():
    assert TypeSafeJudge(KEY).timeout == 3.0


async def test_async_timeout_raises_and_counts_consecutive():
    async def slow(request):
        await asyncio.sleep(1.0)
        return httpx.Response(200, json=FIXTURE)

    j = _judge(slow, timeout=0.05)
    for n in (1, 2):
        with pytest.raises(JudgeTimeout):
            await j.ajudge(STATE)
        assert j.consecutive_timeouts == n
    j._transport = httpx.MockTransport(_ok())
    await j.ajudge(STATE)
    assert j.consecutive_timeouts == 0  # 성공하면 초기화


def test_sync_timeout_and_late_answer_raise_judge_timeout():
    def timeout(request):
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(JudgeTimeout):
        _judge(timeout).judge(STATE)

    def late(request):  # MockTransport는 타임아웃을 강제하지 않는다 → 늦게 온 답도 버린다
        import time

        time.sleep(0.1)
        return httpx.Response(200, json=FIXTURE)

    j = _judge(late, timeout=0.05)
    with pytest.raises(JudgeTimeout):
        j.judge(STATE)
    assert j.consecutive_timeouts == 1


async def test_pipeline_timeout_is_hold_when_gating_on():
    async def slow(request):
        await asyncio.sleep(1.0)
        return httpx.Response(200, json=FIXTURE)

    ev = await StubPipeline(_judge(slow, timeout=0.05), gating=True).evaluate(_signal(), STATE)
    assert ev.gate == Gate.HOLD and ev.size_multiplier == 0.0
    assert ev.result.confidence == 0.0
    assert ev.result.raw == {"error": "JudgeTimeout"}
    assert ev.signal_id == 7


async def test_pipeline_contract_error_is_hold_and_ok_answer_passes_gate():
    bad = copy.deepcopy(FIXTURE)
    bad["answers"].pop("news_risk")
    ev = await StubPipeline(_judge(_ok(bad)), gating=True).evaluate(_signal(), STATE)
    assert ev.gate == Gate.HOLD and ev.size_multiplier == 0.0
    assert ev.result.raw == {"error": "JudgeError"}

    ev = await StubPipeline(_judge(_ok()), gating=True).evaluate(_signal(), STATE)
    assert ev.gate == Gate.HALF and ev.size_multiplier == 0.5  # min confidence 0.55
    assert ev.result.model == "typesafe:jev-1.13.0"


async def test_pipeline_gating_off_ignores_judge_failure():
    """게이팅 OFF(A/B 섀도 기준선, ADR 0010)는 판단 결과와 무관하게 1.0 — 실패도 기록만."""
    ev = await StubPipeline(_judge(lambda r: httpx.Response(500)), gating=False).evaluate(
        _signal(), STATE
    )
    assert ev.size_multiplier == 1.0 and ev.gate == Gate.HOLD


async def test_pipeline_hard_block_from_typesafe_answer():
    body = copy.deepcopy(FIXTURE)
    body["answers"]["news_risk"].update(score=3.0, confidence=0.99)  # → 0.75 > 0.5
    ev = await StubPipeline(_judge(_ok(body)), gating=True).evaluate(_signal(), STATE)
    assert ev.size_multiplier == 0.0 and ev.blocks and "news_risk" in ev.blocks[0]


# ---------------------------------------------------------------- 키 (불변식 #10)


@pytest.mark.invariant
def test_api_key_never_leaks(caplog):
    caplog.set_level(logging.DEBUG)

    def timeout(request):
        raise httpx.ReadTimeout("slow", request=request)

    outputs: list[str] = []
    for handler in (timeout, lambda r: httpx.Response(401, json={"error": "bad key"})):
        with pytest.raises(JudgeError) as e:
            _judge(handler).judge(STATE)
        outputs += [str(e.value), repr(e.value)]
    j = _judge(_ok())
    jr = j.judge(STATE)
    outputs += [repr(j), str(vars(jr)), json.dumps(jr.raw)]
    outputs += [json.dumps(r.__dict__, default=str) for r in caplog.records]
    assert caplog.records  # 오류 경로가 실제로 로그를 남겼다
    assert not [o for o in outputs if KEY in o]
    assert all(r.symbol == "KRW-ETH" for r in caplog.records if r.name.endswith("typesafe"))


def test_key_comes_from_settings_and_empty_key_is_rejected():
    from quantpilot.config import Settings

    s = Settings(typesafe_api_key=KEY)
    assert TypeSafeJudge.from_settings(s)._api_key == KEY
    with pytest.raises(ValueError) as e:
        TypeSafeJudge.from_settings(Settings(typesafe_api_key=""))
    assert "QP_TYPESAFE_API_KEY" in str(e.value)


# ---------------------------------------------------------------- pricing


def test_pricing_table():
    assert cost_usd("jev-latest", 1_000_000, 5_000) == pytest.approx(0.042)  # 출력 무료
    assert cost_usd("jev-1.13.0", 500) == pytest.approx(500 * 0.042 / 1e6)
    assert price_for("gemini-3.5-flash-lite") is PRICES["gemini-3.5-flash-lite"]  # 가장 긴 접두사
    assert price_for("gemini-3.5-flash") is PRICES["gemini-3.5-flash"]
    assert cost_usd("claude-sonnet-5", 30_000, 3_000) == pytest.approx(0.09)  # 기획서 추정치와 일치
    with pytest.raises(ValueError):
        price_for("jevx")
    with pytest.raises(ValueError):
        cost_usd("jev-latest", -1)
    with pytest.raises(ValueError):
        TypeSafeJudge(KEY, model="unknown-model")  # 시작할 때 실패


# ---------------------------------------------------------------- 실제 API (수동)


@pytest.mark.network
def test_real_typesafe_call_matches_contract():
    key = os.environ.get("QP_TYPESAFE_API_KEY")
    if not key:
        pytest.skip("QP_TYPESAFE_API_KEY 없음")
    jr = TypeSafeJudge(key, timeout=10.0).judge(STATE)
    assert set(jr.answers) == {q.key for q in DEFAULT_QUESTIONS}
    assert 0.0 <= jr.confidence <= 1.0
    assert jr.model.startswith("typesafe:jev")
