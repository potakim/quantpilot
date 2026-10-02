"""scheduler 부품 배선 — 뉴스 수집기·일일 리뷰 모델 (04 §8, ADR 0021).

키가 없거나 SDK가 설치되지 않았으면 키 없이 도는 대체물로 내려간다:
- 요약기: Gemini(QP_GOOGLE_API_KEY) → 없으면 제목 절단 요약(TitleSummarizer)
- 일일 리뷰: Claude(QP_ANTHROPIC_API_KEY) → 없으면 None (daily_review가 통계만 저장)
키 값은 로그에 남기지 않는다 (불변식 #10).
"""

from __future__ import annotations

import logging
from typing import Any

from quantpilot.core.news import NewsRepo, Summarizer
from quantpilot.data.news import NewsCollector, TitleSummarizer, load_news_config
from quantpilot.scheduler.context import Reviewer

log = logging.getLogger(__name__)

# 06 §5: 사후 리뷰 프롬프트 — 맞은 것·틀린 것·규칙 위반, 500자. 제안은 하되 적용은 사람이
DAILY_REVIEW_QUESTION = (
    "오늘의 원장·판단 통계를 보고 무엇이 맞았고 무엇이 틀렸는지, 리스크 규칙 위반이 없었는지 "
    "500자 이내 한국어로 리뷰하라. 파라미터 변경 제안은 해도 되지만 적용은 사람이 한다고 밝혀라. "
    "수량·가격·손절 값은 제시하지 마라."
)


def make_summarizer(settings: Any) -> Summarizer:
    """Gemini 요약기. 키나 SDK가 없으면 제목 요약으로 대신한다."""
    if settings.google_api_key:
        try:
            from quantpilot.judgment.google import GeminiSummarizer

            return GeminiSummarizer(api_key=settings.google_api_key)
        except (ImportError, ValueError) as e:
            log.warning("gemini summarizer unavailable", extra={"error": type(e).__name__})
    else:
        log.info("QP_GOOGLE_API_KEY 없음: 뉴스 요약은 제목 절단으로")
    return TitleSummarizer()


def make_news_collector(settings: Any, repo: NewsRepo) -> NewsCollector:
    """설정 파일(QP_NEWS_FILE, 없으면 패키지 기본값)의 피드·키워드로 수집기를 만든다."""
    cfg = load_news_config(settings.news_file)
    return NewsCollector(
        cfg.feeds,
        make_summarizer(settings),
        repo,
        keywords=cfg.keywords,
        stock_symbols=cfg.stock_symbols,
        dart_api_key=settings.dart_api_key,
    )


def make_daily_reviewer(settings: Any, answerer: Any = None) -> Reviewer | None:
    """일일 리뷰 함수 (stats → (요약, 비용USD)). Claude 키·SDK가 없으면 None."""
    if answerer is None:
        if not settings.anthropic_api_key:
            log.info("QP_ANTHROPIC_API_KEY 없음: 일일 리뷰는 통계만")
            return None
        try:
            from quantpilot.judgment.anthropic import ClaudeAnswerer

            answerer = ClaudeAnswerer(api_key=settings.anthropic_api_key)
        except (ImportError, ValueError) as e:
            log.warning("claude reviewer unavailable", extra={"error": type(e).__name__})
            return None

    async def review(stats: dict[str, Any]) -> tuple[str, float | None]:
        try:
            text, cost = await answerer.answer(DAILY_REVIEW_QUESTION, {"stats": stats})
        except Exception as e:  # noqa: BLE001 — 리뷰 실패로 통계 저장까지 막지 않는다
            log.warning("daily review call failed", extra={"error": type(e).__name__})
            return f"리뷰 모델 호출 실패({type(e).__name__}) — 통계만 기록", None
        return text, cost

    return review
