"""LLM 리뷰 프롬프트 (06 §5). 버전별 Markdown 문구를 읽어 두 리뷰어가 같은 프롬프트를 쓰게 한다.

- 문구는 `prompts/review_<version>.md` 한 곳. 모델별 프롬프트 차이를 두지 않는다(합의의 의미)
- `prompt_hash`(문구 sha256 앞 16자리)를 `LLMVerdict.prompt_hash` → `llm_verdicts.prompt_hash`에 남긴다
- 응답 계약: JSON {"approve": bool, "reason": str ≤ 80자}. 파싱 실패·계약 위반은 hold (approve=False)
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from string import Template

from quantpilot.core.models import JudgeResult

_DIR = Path(__file__).parent
REASON_MAX_CHARS = 80
MAX_OUTPUT_TOKENS = 200  # 06 §5 응답 길이 제한
REVIEW_TIMEOUT_S = 30.0  # 04 judgment 표 — 모델별 30초, 넘으면 hold
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)

# 구조화 출력(Claude output_config.format · Gemini response_schema)에 함께 넘기는 응답 스키마
VERDICT_SCHEMA: dict = {
    "type": "object",
    "properties": {"approve": {"type": "boolean"}, "reason": {"type": "string"}},
    "required": ["approve", "reason"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class ReviewPrompt:
    """버전 하나의 리뷰 프롬프트."""

    version: str
    template: str

    @property
    def prompt_hash(self) -> str:
        """문구 전체의 sha256 앞 16자리."""
        return hashlib.sha256(self.template.encode()).hexdigest()[:16]

    def render(self, state_text: str, judge: JudgeResult, rule: str = "") -> str:
        """state·판단 모델 답변·확신도·전략 규칙 한 줄을 채운 프롬프트."""
        answers = json.dumps(_rounded(judge.answers), ensure_ascii=False, sort_keys=True)
        return Template(self.template).safe_substitute(
            rule=rule or "(없음)",
            state=state_text,
            answers=answers,
            confidence=f"{judge.confidence:.2f}",
        )


def _rounded(v: object) -> object:
    if isinstance(v, dict):
        return {k: _rounded(x) for k, x in v.items()}
    return round(v, 2) if isinstance(v, float) else v


@cache
def load_review_prompt(version: str = "v1") -> ReviewPrompt:
    """judgment/prompts/review_<version>.md를 읽는다. 결과는 캐시된다."""
    return ReviewPrompt(version, (_DIR / f"review_{version}.md").read_text(encoding="utf-8"))


def parse_verdict(raw: str) -> tuple[bool, str]:
    """모델 응답 텍스트 → (approve, reason). 파싱 실패·계약 위반은 (False, 사유) — hold."""
    try:
        data = json.loads(_FENCE_RE.sub("", raw.strip()))
    except (ValueError, TypeError):
        return False, "응답 파싱 실패 → hold"
    if not isinstance(data, dict) or not isinstance(data.get("approve"), bool):
        return False, "응답 계약 위반 → hold"
    reason = re.sub(r"\s+", " ", str(data.get("reason") or "")).strip()
    if len(reason) > REASON_MAX_CHARS:
        reason = reason[: REASON_MAX_CHARS - 1] + "…"
    return data["approve"], reason
