"""Anthropic Claude 리뷰어 (04 judgment 표, 06 §5, ADR 0014). 1단계 P1-08.

- `anthropic` SDK는 이 파일 안에서만 import한다(try/except ImportError). 테스트는 `client`를 주입한다
- 키는 `settings.anthropic_api_key`(QP_ANTHROPIC_API_KEY)로만 받는다. 로그·예외·repr에 키를 싣지 않는다
- 후보를 막을 이유가 있는지만 묻는다(approve/hold + 한 줄 이유). 수량·가격·손절은 묻지 않는다 (불변식 #7)
- Claude Sonnet 5는 temperature 등 샘플링 인자를 받지 않는다(400). 대신 JSON 스키마 구조화 출력과
  thinking disabled로 응답 모양을 고정한다 (ADR 0014)
- 호출 실패·거부(refusal)·파싱 실패는 모두 hold. 30초 타임아웃은 파이프라인이 건다
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from quantpilot.core.models import JudgeResult
from quantpilot.judgment.base import LLMProvider, LLMVerdict, State
from quantpilot.judgment.pricing import cost_usd, price_for
from quantpilot.judgment.prompts import (
    MAX_OUTPUT_TOKENS,
    REVIEW_TIMEOUT_S,
    VERDICT_SCHEMA,
    load_review_prompt,
    parse_verdict,
)

try:
    import anthropic
except ImportError:  # pragma: no cover - ai extra 없는 설치
    anthropic = None

log = logging.getLogger(__name__)


class ClaudeReviewer(LLMProvider):
    """진입 후보 리스크 검토 (LLM 2모델 합의의 한쪽)."""

    def __init__(
        self,
        model: str = "claude-sonnet-5",
        *,
        api_key: str | None = None,
        client: Any = None,
        timeout: float = REVIEW_TIMEOUT_S,
        prompt_version: str = "v1",
    ) -> None:
        price_for(model)  # 단가표에 없는 모델이면 시작할 때 실패한다
        self.model = model
        self.name = model
        self.prompt = load_review_prompt(prompt_version)
        if client is None:
            if anthropic is None:
                raise ImportError("anthropic이 필요합니다: uv pip install -e '.[ai]'")
            if api_key is None:
                from quantpilot.config import settings

                api_key = settings.anthropic_api_key
            if not api_key:
                raise ValueError("QP_ANTHROPIC_API_KEY가 비어 있습니다")
            # 재시도하면 30초 예산을 넘긴다 — 실패는 그 신호 hold로 끝낸다
            client = anthropic.AsyncAnthropic(api_key=api_key, timeout=timeout, max_retries=0)
        self._client = client

    def __repr__(self) -> str:
        return f"ClaudeReviewer(model={self.model!r})"

    def review(self, state: State, judge: JudgeResult) -> LLMVerdict:
        """동기 리뷰 (CLI용). 이벤트 루프 안에서는 areview를 쓴다."""
        return asyncio.run(self.areview(state, judge))

    async def areview(self, state: State, judge: JudgeResult, rule: str = "") -> LLMVerdict:
        """후보 1건 검토. 실패는 예외 대신 hold 판정으로 돌려준다."""
        t = time.perf_counter()
        ph = self.prompt.prompt_hash
        try:
            resp = await self._client.messages.create(
                model=self.model,
                max_tokens=MAX_OUTPUT_TOKENS,
                thinking={"type": "disabled"},
                output_config={"format": {"type": "json_schema", "schema": VERDICT_SCHEMA}},
                messages=[
                    {"role": "user", "content": self.prompt.render(state.render(), judge, rule)}
                ],
            )
        except Exception as e:  # noqa: BLE001 — SDK·네트워크 오류가 다양, 판단은 hold로 끝낸다
            log.warning(
                "claude review failed", extra={"model": self.model, "error": type(e).__name__}
            )
            ms = (time.perf_counter() - t) * 1000
            return LLMVerdict(
                self.name, False, f"호출 실패({type(e).__name__}) → hold", ms, 0.0, ph
            )
        ms = (time.perf_counter() - t) * 1000
        usage = getattr(resp, "usage", None)
        cost = cost_usd(
            self.model,
            int(getattr(usage, "input_tokens", 0) or 0),
            int(getattr(usage, "output_tokens", 0) or 0),
        )
        if getattr(resp, "stop_reason", None) == "refusal":
            return LLMVerdict(self.name, False, "모델 거부(refusal) → hold", ms, cost, ph)
        text = next((b.text for b in resp.content if getattr(b, "type", "") == "text"), "")
        approve, reason = parse_verdict(text)
        return LLMVerdict(self.name, approve, reason, ms, cost, ph)


ASK_SYSTEM = (
    "너는 자동매매 판단 기록을 설명하는 도우미다. 주어진 state·answers·verdicts만 근거로 "
    "한국어로 짧게 답한다. 매수·매도 권유, 수량·가격·손절가 제시는 하지 않는다. "
    "근거에 없는 내용은 모른다고 답한다."
)


class ClaudeAnswerer:
    """판단 로그 '이 판단에 대해 물어보기' (03 §2.5). 설명만 하고 수량·가격은 답하지 않는다 (불변식 #7)."""

    def __init__(
        self,
        model: str = "claude-sonnet-5",
        *,
        api_key: str | None = None,
        client: Any = None,
        max_tokens: int = 600,
    ) -> None:
        price_for(model)
        self.model = model
        self.max_tokens = max_tokens
        if client is None:
            if anthropic is None:
                raise ImportError("anthropic이 필요합니다: uv pip install -e '.[ai]'")
            if api_key is None:
                from quantpilot.config import settings

                api_key = settings.anthropic_api_key
            if not api_key:
                raise ValueError("QP_ANTHROPIC_API_KEY가 비어 있습니다")
            client = anthropic.AsyncAnthropic(api_key=api_key, timeout=30.0, max_retries=0)
        self._client = client

    def __repr__(self) -> str:
        return f"ClaudeAnswerer(model={self.model!r})"

    async def answer(self, question: str, context: dict[str, Any]) -> tuple[str, float]:
        """질문 1건에 답하고 (답변, 비용USD)를 돌려준다."""
        resp = await self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            thinking={"type": "disabled"},
            system=ASK_SYSTEM,
            messages=[
                {
                    "role": "user",
                    "content": "근거:\n"
                    + json.dumps(context, ensure_ascii=False, default=str)
                    + f"\n\n질문: {question}",
                }
            ],
        )
        usage = getattr(resp, "usage", None)
        cost = cost_usd(
            self.model,
            int(getattr(usage, "input_tokens", 0) or 0),
            int(getattr(usage, "output_tokens", 0) or 0),
        )
        text = next((b.text for b in resp.content if getattr(b, "type", "") == "text"), "")
        return text, cost
