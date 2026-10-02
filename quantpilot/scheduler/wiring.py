"""scheduler 부품 배선 — 뉴스 수집기·일일 리뷰 모델·08:10 사전 심사 (04 §8, ADR 0021·0022).

키가 없거나 SDK가 설치되지 않았으면 키 없이 도는 대체물로 내려간다:
- 요약기: Gemini(QP_GOOGLE_API_KEY) → 없으면 제목 절단 요약(TitleSummarizer)
- 일일 리뷰: Claude(QP_ANTHROPIC_API_KEY) → 없으면 None (daily_review가 통계만 저장)
- 사전 심사: 판단 모델·리뷰어는 엔진 파이프라인과 같은 설정(QP_JUDGE_PROVIDER·QP_LLM_PROVIDERS)
키 값은 로그에 남기지 않는다 (불변식 #10).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

from quantpilot.core.clock import to_local
from quantpilot.core.errors import JudgeError
from quantpilot.core.models import JudgeResult, Market
from quantpilot.core.news import NewsRepo, Summarizer
from quantpilot.data.events import events_from_dart
from quantpilot.data.news import (
    ALL_SYMBOLS,
    NewsCollector,
    TitleSummarizer,
    load_news_config,
)
from quantpilot.features.builder import news_only_state
from quantpilot.judgment.base import JudgeProvider, LLMProvider, LLMVerdict, State
from quantpilot.judgment.pipeline import make_reviewer
from quantpilot.judgment.prompts import REVIEW_TIMEOUT_S
from quantpilot.judgment.stub import StubJudge
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


# ---------- 08:10 사전 심사 (ADR 0004·0022) ----------
PRESCREEN_SIGNAL = "08:10 사전 심사 — 오늘 09:00부터 하루 변동성 돌파 진입 후보"
PRESCREEN_RULE = (
    "vol_breakout 사전 심사: 오늘(09:00~다음 날 09:00) 이 코인의 변동성 돌파 진입을 "
    "막아야 할 뉴스·이벤트 위험이 있으면 approve=false"
)
PRESCREEN_WINDOW = timedelta(hours=24)


class Prescreen:
    """대상 코인마다 판단 모델 → LLM 리뷰어 전원 승인일 때만 오늘 진입 허용 (ADR 0004).

    결과는 settings 우편함(`engine.prescreen.upbit`)에 거래일과 함께 쓰고 엔진이 읽는다.
    리뷰어 타임아웃·오류는 hold(= 제외)다. 판단 모델 오류는 확신도 0 답변으로 리뷰어에게 넘긴다.
    수량·가격은 묻지 않는다 (불변식 #7).
    """

    def __init__(
        self,
        judge: JudgeProvider,
        reviewers: Sequence[LLMProvider],
        news: NewsRepo,
        symbols: Sequence[str],
        *,
        calendar: Any = None,
        timeout: float = REVIEW_TIMEOUT_S,
    ) -> None:
        self.judge = judge
        self.reviewers = list(reviewers)
        self.news = news
        self.symbols = tuple(symbols)
        self.calendar = calendar  # data.events.EventCalendar | None
        self.timeout = timeout

    async def _review(self, r: LLMProvider, state: State, jr: JudgeResult) -> LLMVerdict:
        try:
            return await asyncio.wait_for(r.areview(state, jr, PRESCREEN_RULE), self.timeout)
        except Exception as e:  # noqa: BLE001 — 타임아웃·어댑터 오류는 그 모델 hold
            return LLMVerdict(r.name, False, f"리뷰 실패({type(e).__name__}) → hold")

    async def screen(self, now_utc: datetime) -> tuple[dict[str, str], float]:
        """({제외 심볼: 사유}, 비용USD)."""
        recent = await self.news.recent(now_utc - PRESCREEN_WINDOW)
        dart_events = events_from_dart(recent)
        blocked: dict[str, str] = {}
        cost = 0.0
        for sym in self.symbols:
            news = [n for n in recent if sym in n.symbols or ALL_SYMBOLS in n.symbols]
            events = [
                e
                for e in [*dart_events, *(self._calendar_events(sym, now_utc))]
                if e.applies_to(sym)
            ]
            state = news_only_state(
                Market.UPBIT, sym, "vol_breakout", PRESCREEN_SIGNAL, news, events, now_utc
            )
            try:
                jr = await self.judge.ajudge(state)
            except JudgeError as e:
                jr = JudgeResult({}, 0.0, model=self.judge.name, raw={"error": type(e).__name__})
            verdicts = await asyncio.gather(*(self._review(r, state, jr) for r in self.reviewers))
            cost += jr.cost_usd + sum(v.cost_usd for v in verdicts)
            no = [v for v in verdicts if not v.approve]
            if no:
                blocked[sym] = f"{no[0].model}: {no[0].reason}"[:120]
        return blocked, cost

    def _calendar_events(self, symbol: str, now_utc: datetime) -> list[Any]:
        if self.calendar is None:
            return []
        return list(
            self.calendar.within(symbol, now_utc - PRESCREEN_WINDOW, now_utc + PRESCREEN_WINDOW)
        )

    async def __call__(self, ctx: Any) -> None:
        """scheduler 훅 `upbit_prescreen`: 심사 → 우편함 기록 → 제외가 있으면 info 알림."""
        now = ctx.utcnow()
        blocked, cost = await self.screen(now)
        day = to_local(now, Market.UPBIT).date().isoformat()  # 08:10 → 그날 09:00 거래일
        await ctx.link.set_prescreen(Market.UPBIT, day, blocked)
        log.info(
            "prescreen done",
            extra={"day": day, "blocked": sorted(blocked), "cost_usd": round(cost, 4)},
        )
        if blocked:
            lines = "\n".join(f"- {s}: {r}" for s, r in sorted(blocked.items()))
            await ctx.notifier.send("info", f"[사전 심사 {day}] 오늘 진입 제외\n{lines}")


def make_judge(settings: Any) -> JudgeProvider:
    """설정의 판단 모델. typesafe가 아니면 스텁 (build_pipeline과 같은 선택)."""
    if settings.judge_provider == "typesafe":
        from quantpilot.judgment.typesafe import TypeSafeJudge

        return TypeSafeJudge.from_settings(settings)
    return StubJudge()


def make_prescreen(settings: Any, repo: NewsRepo) -> Prescreen:
    """vol_breakout 대상 코인 사전 심사 훅. 리뷰어는 `QP_LLM_PROVIDERS`와 같다."""
    from quantpilot.data.events import EventCalendar
    from quantpilot.strategies import create

    return Prescreen(
        make_judge(settings),
        [make_reviewer(n) for n in settings.llm_providers],
        repo,
        create("vol_breakout").symbols,
        calendar=EventCalendar.from_yaml(settings.events_file),
    )
