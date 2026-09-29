"""스텁 판단 모델 — 네트워크 없이 파이프라인을 끝까지 돌려보기 위한 결정적(deterministic) 구현.

피처를 규칙으로 읽어 그럴듯한 확률을 낸다. 실제 Jev/Laya/LLM 어댑터가 오기 전까지의 자리표시자이며,
게이팅 A/B의 'OFF' 기준선으로도 쓸 수 있다 (AlwaysApprove).
"""
from __future__ import annotations

import hashlib
import time

from quantpilot.core.models import JudgeResult
from quantpilot.judgment.base import (DEFAULT_QUESTIONS, JudgeProvider, LLMProvider, LLMVerdict,
                                      Question, State)


class StubJudge(JudgeProvider):
    name = "stub-judge"

    def judge(self, state: State, questions: tuple[Question, ...] = DEFAULT_QUESTIONS) -> JudgeResult:
        t = time.perf_counter()
        f = state.features
        ma = float(f.get("ma_score", 0.5))
        vol_pctl = float(f.get("vol_pctl_20d", 50)) / 100
        news = state.news_summary.lower()
        risky = any(w in news for w in ("hack", "해킹", "상장폐지", "delist", "sec", "소송", "규제"))
        answers: dict = {}
        for q in questions:
            if q.key == "regime":
                up = 0.2 + 0.6 * ma
                down = 0.2 + 0.6 * (1 - ma)
                rng = max(0.05, 1 - up - down + 0.3)
                s = up + down + rng
                answers["regime"] = {"trend_up": up / s, "range": rng / s, "trend_down": down / s}
            elif q.key == "news_risk":
                answers["news_risk"] = 0.65 if risky else 0.1 + 0.1 * vol_pctl
            elif q.key == "liquidity_stress":
                answers["liquidity_stress"] = min(0.9, 0.05 + 0.4 * max(0.0, vol_pctl - 0.7))
            elif q.key == "event_ahead":
                answers["event_ahead"] = 0.7 if state.events_24h not in ("none", "") else 0.05
            elif q.key == "already_priced":
                answers["already_priced"] = 0.3
            elif q.key == "signal_quality":
                answers["signal_quality"] = 0.4 + 0.5 * ma
            elif q.kind == "score":
                answers[q.key] = 0.5
            else:
                answers[q.key] = {o: 1 / len(q.options) for o in q.options}
        conf = max(0.05, min(0.98, 0.55 + 0.4 * ma - (0.3 if risky else 0.0)))
        return JudgeResult(answers, round(conf, 3), (time.perf_counter() - t) * 1000, self.name)


class StubLLM(LLMProvider):
    """이유 문장은 state 해시로 결정적으로 고른다. approve는 hard block과 같은 규칙."""
    def __init__(self, name: str = "stub-llm"):
        self.name = name

    def review(self, state: State, judge: JudgeResult) -> LLMVerdict:
        risky = float(judge.answers.get("news_risk", 0)) > 0.5
        h = int(hashlib.sha1(state.render().encode()).hexdigest(), 16) % 3
        reasons = ("규칙 위반 없음, 추세·거래량 일치", "뉴스 리스크 없음, 진입 허용", "이벤트 없음, 신호 품질 양호")
        return LLMVerdict(self.name, not risky, "뉴스 리스크로 보류" if risky else reasons[h])


class AlwaysApprove(LLMProvider):
    name = "always-approve"

    def review(self, state: State, judge: JudgeResult) -> LLMVerdict:
        return LLMVerdict(self.name, True, "gating off (A/B baseline)")
