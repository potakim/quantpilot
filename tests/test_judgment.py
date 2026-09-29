from quantpilot.core.models import Gate, JudgeResult
from quantpilot.judgment import AlwaysApprove, LLMVerdict, State, StubJudge, StubLLM, decide, gate


def test_gate_thresholds():
    assert gate(0.49) == Gate.HOLD
    assert gate(0.5) == Gate.HALF
    assert gate(0.89) == Gate.HALF
    assert gate(0.9) == Gate.FULL


def test_hard_block_overrides_confidence():
    jr = JudgeResult({"news_risk": 0.7, "event_ahead": 0.1}, confidence=0.95)
    d = decide(jr, [LLMVerdict("a", True, ""), LLMVerdict("b", True, "")])
    assert not d.proceed and d.blocks


def test_llm_consensus_requires_all():
    jr = JudgeResult({"news_risk": 0.1, "event_ahead": 0.1}, confidence=0.95)
    d = decide(jr, [LLMVerdict("claude", True, ""), LLMVerdict("gemini", False, "")])
    assert not d.proceed
    d = decide(jr, [LLMVerdict("claude", True, ""), LLMVerdict("gemini", True, "")])
    assert d.proceed and d.size_multiplier == 1.0
    d = decide(JudgeResult({}, 0.7), [LLMVerdict("claude", True, "")])
    assert d.size_multiplier == 0.5


def test_stub_pipeline_end_to_end_is_deterministic():
    st = State(
        "upbit",
        "KRW-ETH",
        "vol_breakout",
        "breakout",
        {"ma_score": 0.75, "vol_pctl_20d": 78},
        news_summary="현물 ETF 순유입 2일 연속",
    )
    jr1, jr2 = StubJudge().judge(st), StubJudge().judge(st)
    assert jr1.answers == jr2.answers and jr1.confidence == jr2.confidence
    assert abs(sum(jr1.answers["regime"].values()) - 1) < 1e-9
    verdicts = [StubLLM("c").review(st, jr1), StubLLM("g").review(st, jr1)]
    assert decide(jr1, verdicts).proceed
    risky = State(
        "upbit",
        "KRW-SOL",
        "vol_breakout",
        "breakout",
        {"ma_score": 0.5},
        news_summary="거래소 상장폐지 검토",
    )
    jr = StubJudge().judge(risky)
    assert not decide(jr, [StubLLM().review(risky, jr)]).proceed
    assert decide(JudgeResult({}, 0.95), [AlwaysApprove().review(st, jr1)]).proceed
    assert len(st.render()) < 400
