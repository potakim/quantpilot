"""판단 계층 인터페이스 — "코드가 계산하고, 모델은 판단하고, 코드가 실행한다".

JudgeProvider: Jev / Laya / Kev 같은 '시스템 1' 판단 모델. 원자 질문(choice/score) 묶음을 한 번에 보내고
확률·확신도만 받는다. 수량·가격·손절은 절대 묻지 않는다.
LLMProvider:   Claude / Gemini. 후보 신호에 대해 approve/hold와 한 줄 이유를 받는다. 두 모델 합의 시만 통과.

0단계는 인터페이스와 스텁만 있다. 실제 어댑터(TypeSafe SDK, Anthropic SDK, Google GenAI SDK)는 1단계.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Literal

from quantpilot.core.models import Gate, JudgeResult


@dataclass(frozen=True)
class Question:
    """원자 질문. kind='choice'는 options 중 하나의 확률 분포, 'score'는 0~1 실수."""

    key: str
    prompt: str
    kind: Literal["choice", "score"]
    options: tuple[str, ...] = ()


# 기획서의 기본 질문 세트 (regime, news_risk, liquidity_stress, event_ahead, already_priced, signal_quality)
DEFAULT_QUESTIONS: tuple[Question, ...] = (
    Question("regime", "현재 시장 국면은?", "choice", ("trend_up", "range", "trend_down")),
    Question("news_risk", "24시간 내 뉴스가 포지션에 불리할 위험 (0~1)", "score"),
    Question("liquidity_stress", "유동성 스트레스 (0~1)", "score"),
    Question("event_ahead", "24시간 내 큰 이벤트(발표·상장폐지 심사 등) 가능성 (0~1)", "score"),
    Question("already_priced", "뉴스가 이미 가격에 반영됐을 가능성 (0~1)", "score"),
    Question("signal_quality", "이 후보 신호의 품질 (0~1)", "score"),
)


@dataclass
class State:
    """피처 빌더가 만든 판단 입력. 숫자는 등급·백분위로 변환해 짧게 넣는다 (400토큰 이내 목표)."""

    market: str
    symbol: str
    strategy: str
    signal: str
    features: dict = field(default_factory=dict)  # 예: {"vol_pctl_20d": 78, "ma_score": 0.75, ...}
    news_summary: str = ""
    events_24h: str = "none"

    def render(self) -> str:
        lines = [
            f"market: {self.market} {self.symbol}",
            f"strategy: {self.strategy}",
            f"signal: {self.signal}",
        ]
        lines += [f"{k}: {v}" for k, v in self.features.items()]
        lines += [f"news_24h: {self.news_summary or 'none'}", f"events_24h: {self.events_24h}"]
        return "\n".join(lines)


class JudgeProvider(ABC):
    name: str = "judge"

    @abstractmethod
    def judge(
        self, state: State, questions: tuple[Question, ...] = DEFAULT_QUESTIONS
    ) -> JudgeResult: ...

    async def ajudge(
        self, state: State, questions: tuple[Question, ...] = DEFAULT_QUESTIONS
    ) -> JudgeResult:
        """틱 루프용 비동기 판단. 네트워크 어댑터는 이벤트 루프를 막지 않도록 재정의한다 (ADR 0012)."""
        return self.judge(state, questions)


@dataclass
class LLMVerdict:
    model: str
    approve: bool
    reason: str
    latency_ms: float = 0.0
    cost_usd: float = 0.0
    prompt_hash: str = ""  # llm_verdicts.prompt_hash (judgment/prompts)


class LLMProvider(ABC):
    name: str = "llm"

    @abstractmethod
    def review(self, state: State, judge: JudgeResult) -> LLMVerdict: ...

    async def areview(self, state: State, judge: JudgeResult, rule: str = "") -> LLMVerdict:
        """파이프라인용 비동기 리뷰. 네트워크 어댑터는 이벤트 루프를 막지 않도록 재정의한다."""
        return self.review(state, judge)


def gate(confidence: float, *, hold_below: float = 0.5, full_above: float = 0.9) -> Gate:
    """확신도 게이팅 (TypeSafe confidence 가이드 기본값). 임계값은 설정에서 조정."""
    if confidence < hold_below:
        return Gate.HOLD
    if confidence >= full_above:
        return Gate.FULL
    return Gate.HALF


def hard_blocks(
    result: JudgeResult, *, news_risk_max: float = 0.5, event_ahead_max: float = 0.5
) -> list[str]:
    """확신도와 무관하게 진입을 막는 조건. 코드가 판단한다."""
    reasons = []
    a = result.answers
    if float(a.get("news_risk", 0)) > news_risk_max:
        reasons.append(f"news_risk {a['news_risk']:.2f} > {news_risk_max}")
    if float(a.get("event_ahead", 0)) > event_ahead_max:
        reasons.append(f"event_ahead {a['event_ahead']:.2f} > {event_ahead_max}")
    return reasons


@dataclass
class Decision:
    gate: Gate
    blocks: list[str]
    verdicts: list[LLMVerdict]
    size_multiplier: float  # 0 / 0.5 / 1.0

    @property
    def proceed(self) -> bool:
        return self.size_multiplier > 0


def decide(
    judge_result: JudgeResult,
    verdicts: list[LLMVerdict],
    *,
    require_all_llm: bool = True,
    hold_below: float = 0.5,
    full_above: float = 0.9,
) -> Decision:
    blocks = hard_blocks(judge_result)
    g = gate(judge_result.confidence, hold_below=hold_below, full_above=full_above)
    llm_ok = (
        all(v.approve for v in verdicts) if require_all_llm else any(v.approve for v in verdicts)
    )
    if blocks or g == Gate.HOLD or (verdicts and not llm_ok):
        return Decision(Gate.HOLD, blocks, verdicts, 0.0)
    return Decision(g, blocks, verdicts, 0.5 if g == Gate.HALF else 1.0)
