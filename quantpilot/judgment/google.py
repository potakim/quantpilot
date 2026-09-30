"""Google Gemini 어댑터 (04 judgment 표). 1단계 P1-06: 뉴스 요약기. 리뷰어(GeminiReviewer)는 P1-08.

- `google-genai` SDK는 이 파일 안에서만 import한다(try/except ImportError). 테스트는 `client`를 주입한다
- 키는 `settings.google_api_key`(QP_GOOGLE_API_KEY)로만 받는다. 로그·예외·repr에 키를 싣지 않는다
- 요약 계약(`core.news.Summary`): summary ≤ 100자, risk_flags ⊂ RISK_FLAGS, risk_score ∈ [0,1] | None.
  모델에게 수량·가격·매매 여부를 묻지 않는다
- 응답이 깨지거나 호출이 실패하면 예외 대신 제목을 100자로 자른 요약 + risk_score=None을 돌려준다
  (위험도를 모른다는 뜻. 피처 빌더는 요약문만 싣고, 판단 모델이 news_risk를 따로 답한다)
"""

from __future__ import annotations

import json
import logging
import re
from string import Template
from typing import Any, Protocol

from quantpilot.core.news import RISK_FLAGS, SUMMARY_MAX_CHARS, Summary

try:
    from google import genai
except ImportError:  # pragma: no cover - ai extra 없는 설치
    genai = None

log = logging.getLogger(__name__)

SUMMARY_PROMPT_V1 = Template(
    "다음 뉴스를 한국어 100자 이내로 요약하고, 해당하는 위험 플래그와 "
    "보유 포지션에 불리할 위험도(0~1)를 답하라. 매매 여부·가격·수량은 답하지 마라.\n"
    f"허용 플래그: {', '.join(sorted(RISK_FLAGS))}\n"
    '출력은 JSON {"summary": str, "risk_flags": [str], "risk_score": number} 뿐이다.\n\n'
    "제목: $title\n본문: $body"
)
_BODY_CHARS = 2000
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


class _Models(Protocol):
    def generate_content(self, *, model: str, contents: str, config: Any) -> Any: ...


class GenAIClient(Protocol):
    """google-genai `Client`의 이 모듈이 쓰는 부분 (`client.models.generate_content`)."""

    models: _Models


def _truncate(text: str, limit: int = SUMMARY_MAX_CHARS) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def parse_summary(raw: str, *, fallback_title: str) -> Summary:
    """모델 응답 텍스트 → Summary. 계약을 벗어난 값은 고치거나 버린다."""
    try:
        data = json.loads(_FENCE_RE.sub("", raw.strip()))
        if not isinstance(data, dict):
            raise ValueError("not an object")  # noqa: TRY004 — 파싱 실패는 ValueError로 통일
        summary = _truncate(str(data.get("summary") or "")) or _truncate(fallback_title)
        flags = data.get("risk_flags") or []
        flags = tuple(dict.fromkeys(f for f in flags if isinstance(f, str) and f in RISK_FLAGS))
        score = data.get("risk_score")
        if isinstance(score, bool) or not isinstance(score, int | float):
            score = None
        else:
            score = min(1.0, max(0.0, float(score)))
        return Summary(summary, flags, score)
    except (ValueError, TypeError) as e:
        log.warning("summary parse failed", extra={"error": type(e).__name__})
        return Summary(_truncate(fallback_title), (), None)


class GeminiSummarizer:
    """뉴스 100자 요약 + 위험 플래그 (core.news.Summarizer 구현)."""

    def __init__(
        self,
        model: str = "gemini-3.5-flash-lite",
        *,
        api_key: str | None = None,
        client: GenAIClient | None = None,
    ) -> None:
        self.model = model
        if client is None:
            if genai is None:
                raise ImportError("google-genai가 필요합니다: uv pip install -e '.[ai]'")
            if api_key is None:
                from quantpilot.config import settings

                api_key = settings.google_api_key
            if not api_key:
                raise ValueError("QP_GOOGLE_API_KEY가 비어 있습니다")
            client = genai.Client(api_key=api_key)
        self._client = client

    def __repr__(self) -> str:
        return f"GeminiSummarizer(model={self.model!r})"

    def summarize(self, title: str, body: str) -> Summary:
        """제목·본문 → Summary. 호출 실패·응답 오류는 제목 절단 요약으로 대신한다."""
        prompt = SUMMARY_PROMPT_V1.safe_substitute(title=title, body=body[:_BODY_CHARS])
        try:
            resp = self._client.models.generate_content(
                model=self.model,
                contents=prompt,
                config={"temperature": 0, "response_mime_type": "application/json"},
            )
            raw = getattr(resp, "text", None) or ""
        except Exception as e:  # noqa: BLE001 — SDK·네트워크 오류가 다양, 수집을 멈추지 않음
            log.warning(
                "summarize call failed", extra={"model": self.model, "error": type(e).__name__}
            )
            return Summary(_truncate(title), (), None)
        return parse_summary(raw, fallback_title=title)
