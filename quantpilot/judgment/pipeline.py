"""판단 파이프라인 (04 judgment 표, 06 §4·§7, ADR 0014). 1단계 P1-08.

판단 모델 → hard_blocks·확신도 게이트 → LLM 2모델 합의(병렬, 모델별 30초) → decide().
- 판단 모델 실패(JudgeError)는 확신도 0 → hold (ADR 0012)
- hard_blocks나 게이트가 이미 hold면 LLM을 부르지 않는다 — 하드블록이 확신도·합의보다 우선 (불변식 #8)
- LLM 타임아웃·오류·파싱 실패는 그 모델 hold. 2/2 approve일 때만 통과
- LLM 합의를 거치는 전략은 `llm_strategies`(06 §4 표: orb·gem·gtaa). vol_breakout의 08:10 사전 심사는 스케줄러 몫
- 하루 AI 비용이 `budget_usd_daily`를 넘으면 LLM 합의 중단(= hold), 판단 모델은 계속
- 판단 모델 연속 타임아웃이 `judge_down_after`(10)에 닿으면 RiskEvent("judge_down")를 warning으로 한 번 발행
- 리뷰어에게는 확률·확신도만 보여 주고 approve/hold만 받는다. 수량·가격은 정하지 않는다 (불변식 #7)
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from datetime import date
from typing import Any

from quantpilot.core.errors import JudgeError
from quantpilot.core.events import JudgmentEvent, RiskEvent, SignalEvent
from quantpilot.core.models import JudgeResult
from quantpilot.core.ports import EventBus
from quantpilot.judgment.base import JudgeProvider, LLMProvider, LLMVerdict, State, decide
from quantpilot.judgment.prompts import REVIEW_TIMEOUT_S

log = logging.getLogger(__name__)

LLM_STRATEGIES = frozenset({"orb", "gem", "gtaa"})
JUDGE_DOWN_AFTER = 10


class JudgmentPipeline:
    """JudgmentPipeline Protocol 구현 (core/ports.py). 사이징 배수만 돌려준다."""

    def __init__(
        self,
        judge: JudgeProvider,
        reviewers: Sequence[LLMProvider] = (),
        *,
        bus: EventBus | None = None,
        gating: bool = True,
        hold_below: float = 0.5,
        full_above: float = 0.9,
        llm_strategies: frozenset[str] = LLM_STRATEGIES,
        review_timeout: float = REVIEW_TIMEOUT_S,
        budget_usd_daily: float = 2.0,
        judge_down_after: int = JUDGE_DOWN_AFTER,
    ) -> None:
        if not (0.3 <= hold_below <= 0.7 and 0.7 <= full_above <= 0.98):
            raise ValueError("게이트 임계값 범위: hold_below 0.3~0.7, full_above 0.7~0.98 (06 §2)")
        self.judge = judge
        self.reviewers = list(reviewers)
        self.bus = bus
        self.gating = gating
        self.hold_below, self.full_above = hold_below, full_above
        self.llm_strategies = llm_strategies
        self.review_timeout = review_timeout
        self.budget_usd_daily = budget_usd_daily
        self.judge_down_after = judge_down_after
        self._spent: dict[date, float] = {}
        self._judge_down_sent = False

    def spent_usd(self, day: date) -> float:
        """그날 쓴 AI 비용 (판단 모델 + LLM)."""
        return self._spent.get(day, 0.0)

    async def evaluate(self, signal: SignalEvent, state: State) -> JudgmentEvent:
        """판단 1회. gating=False(A/B OFF 기준선)면 기록만 하고 배수 1.0, LLM은 부르지 않는다."""
        day = signal.ts.date()
        try:
            jr = await self.judge.ajudge(state)
        except JudgeError as e:
            jr = JudgeResult({}, 0.0, model=self.judge.name, raw={"error": type(e).__name__})
        self._add_cost(day, jr.cost_usd)
        await self._check_judge_down(signal)

        pre = decide(jr, [], hold_below=self.hold_below, full_above=self.full_above)
        verdicts: list[LLMVerdict] = []
        if self.gating and pre.proceed and signal.strategy in self.llm_strategies:
            if self.spent_usd(day) >= self.budget_usd_daily:
                verdicts = [LLMVerdict("budget", False, "AI 일일 예산 초과 → hold")]
            else:
                verdicts = await self._consensus(state, jr, _rule(signal))
                self._add_cost(day, sum(v.cost_usd for v in verdicts))

        d = decide(jr, verdicts, hold_below=self.hold_below, full_above=self.full_above)
        mult = d.size_multiplier if self.gating else 1.0
        return JudgmentEvent(
            signal.signal_id or 0, jr, d.gate, mult, signal.ts, tuple(d.blocks), tuple(verdicts)
        )

    async def _consensus(self, state: State, jr: JudgeResult, rule: str) -> list[LLMVerdict]:
        """리뷰어를 동시에 부른다. 모델별 타임아웃·예외는 그 모델 hold."""
        return list(await asyncio.gather(*(self._one(r, state, jr, rule) for r in self.reviewers)))

    async def _one(self, r: LLMProvider, state: State, jr: JudgeResult, rule: str) -> LLMVerdict:
        try:
            return await asyncio.wait_for(r.areview(state, jr, rule), self.review_timeout)
        except TimeoutError:
            log.warning("llm review timeout", extra={"model": r.name})
            ms = self.review_timeout * 1000
            return LLMVerdict(r.name, False, f"{self.review_timeout:g}초 타임아웃 → hold", ms)
        except Exception as e:  # noqa: BLE001 — 어댑터가 놓친 오류도 hold로 끝낸다
            log.warning("llm review error", extra={"model": r.name, "error": type(e).__name__})
            return LLMVerdict(r.name, False, f"리뷰 오류({type(e).__name__}) → hold")

    def _add_cost(self, day: date, usd: float) -> None:
        self._spent[day] = self._spent.get(day, 0.0) + usd

    async def _check_judge_down(self, signal: SignalEvent) -> None:
        n = int(getattr(self.judge, "consecutive_timeouts", 0) or 0)
        if n < self.judge_down_after:
            self._judge_down_sent = False
            return
        if self._judge_down_sent:
            return
        self._judge_down_sent = True
        ev = RiskEvent(
            "judge_down",
            signal.ts,
            signal.market,
            {"provider": self.judge.name, "consecutive_timeouts": n},
        )
        log.error("judge down", extra={"provider": self.judge.name, "timeouts": n})
        if self.bus is not None:
            await self.bus.publish("warning", ev)


def _rule(signal: SignalEvent) -> str:
    reason = signal.target.reason
    return f"{signal.strategy}: {reason}" if reason else signal.strategy


def make_reviewer(name: str) -> LLMProvider:
    """설정 이름 → 리뷰어. claude | gemini | stub."""
    if name == "claude":
        from quantpilot.judgment.anthropic import ClaudeReviewer

        return ClaudeReviewer()
    if name == "gemini":
        from quantpilot.judgment.google import GeminiReviewer

        return GeminiReviewer()
    if name == "stub":
        from quantpilot.judgment.stub import StubLLM

        return StubLLM()
    raise ValueError(f"알 수 없는 LLM 리뷰어: {name!r} (claude | gemini | stub)")


def build_pipeline(settings: Any, *, bus: EventBus | None = None) -> Any:
    """설정(`judge_provider`, `llm_providers`)대로 파이프라인을 만든다. stub이면 StubPipeline."""
    if settings.judge_provider == "stub":
        from quantpilot.judgment.stub import StubPipeline

        return StubPipeline()
    if settings.judge_provider != "typesafe":
        raise ValueError(f"아직 배선되지 않은 판단 모델: {settings.judge_provider!r}")
    from quantpilot.judgment.typesafe import TypeSafeJudge

    return JudgmentPipeline(
        TypeSafeJudge.from_settings(settings),
        [make_reviewer(n) for n in settings.llm_providers],
        bus=bus,
        hold_below=settings.gate_hold_below,
        full_above=settings.gate_full_above,
        budget_usd_daily=settings.ai_budget_usd_daily,
    )
