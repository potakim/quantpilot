"""FeatureBuilder — 전략 컨텍스트 → 판단 모델 입력 state (04 §3, 06 §3).

- 숫자는 등급·백분위·소수 1자리로 바꾼다. 원시 가격·수량은 넣지 않는다(모델이 계산하려 들지 않도록)
- `target.price`가 있는 전략(장중 가격 도달 진입)은 현재 봉을 빼고 계산한다 (불변식 #3, 룩어헤드)
- 뉴스: 최근 24시간, 위험 플래그 있는 것 우선 → 최신순, 최대 3건, 각 100자. 요약문만 넣고 제목·링크는 로그에만
- `events_24h`: 캘린더에서 앞뒤 24시간 안의 항목 (지난 공시·점검도 위험 신호라서)
- `State.render()`는 400토큰 이내. 넘으면 뉴스부터 자르고, 그래도 넘으면 이벤트를 줄인다

`core.ports.FeatureBuilder` Protocol(`build(*, symbol, strategy, target, ctx)`)을 그대로 따른다.
뉴스·이벤트·스프레드 공급자는 생성자로 주입한다 (ADR 0010 결정 8).
"""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd

from quantpilot.core import clock
from quantpilot.core.models import Market, Target
from quantpilot.core.news import SUMMARY_MAX_CHARS, EventItem, EventSource, NewsItem, NewsSource
from quantpilot.judgment.base import State

log = logging.getLogger(__name__)

MAX_TOKENS = 400
MAX_NEWS = 3
WINDOW = timedelta(hours=24)
MA_WINDOWS = (5, 10, 20, 60)
VOL_LOOKBACK = 252  # vol_pctl_20d 비교 구간 (약 1년)
_EVENT_TITLE_CHARS = 60

SpreadFn = Callable[[str], float | None]

_TOKEN_RE = re.compile(r"[A-Za-z]+|\d|[^\sA-Za-z\d]")


def count_tokens(text: str) -> int:
    """보수적 토큰 추정. 영문 단어는 4자당 1토큰(올림), 숫자·기호·비ASCII(한글 등)는 글자당 1토큰.

    실제 토크나이저(BPE)는 대개 이보다 적게 센다 — 이 값으로 400 이내면 실제로도 이내다.
    """
    n = 0
    for tok in _TOKEN_RE.findall(text):
        n += math.ceil(len(tok) / 4) if tok.isascii() and tok.isalpha() else 1
    return n


def _clean(text: str, limit: int) -> str:
    """한 줄로 정리하고 limit자로 자른다. state 구분자(" | ", 따옴표)가 깨지지 않게 바꾼다."""
    text = re.sub(r"\s+", " ", text).replace('"', "'").replace("|", "/").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def compute_features(df: pd.DataFrame, spread_bps: float | None = None) -> dict[str, Any]:
    """OHLCV(마지막 행이 기준 봉) → 등급화 피처. 데이터가 모자라는 피처는 넣지 않는다."""
    out: dict[str, Any] = {}
    if df.empty:
        return out
    close = df["close"].astype(float)
    last = float(close.iloc[-1])

    rets = close.pct_change()
    vol = rets.rolling(20).std().dropna().tail(VOL_LOOKBACK)
    if len(vol) >= 20:
        out["vol_pctl_20d"] = round(float((vol <= vol.iloc[-1]).mean()) * 100)

    mas = [close.tail(w).mean() for w in MA_WINDOWS if len(close) >= w]
    if mas:
        out["ma_score"] = round(sum(last > m for m in mas) / len(mas), 2)

    if "volume" in df and len(df) >= 21:
        base = float(df["volume"].iloc[-21:-1].mean())
        if base > 0:
            out["volume_ratio"] = f"{float(df['volume'].iloc[-1]) / base:.1f}x"

    if spread_bps is not None and np.isfinite(spread_bps):
        out["spread_bps"] = round(spread_bps)

    if len(df) >= 20:
        high = float(df["high"].tail(20).max())
        if high > 0:
            out["dist_from_high_20d_pct"] = round((last / high - 1) * 100, 1)

    if len(close) >= 3:
        diff = close.diff().dropna()
        up = diff.clip(lower=0).ewm(alpha=0.5, adjust=False).mean().iloc[-1]
        down = (-diff.clip(upper=0)).ewm(alpha=0.5, adjust=False).mean().iloc[-1]
        rsi = 100.0 if down == 0 else 100 - 100 / (1 + up / down)
        out["rsi2"] = round(rsi)
    return out


def select_news(items: list[NewsItem], limit: int = MAX_NEWS) -> list[NewsItem]:
    """요약이 있는 뉴스 중 위험 플래그 있는 것 우선, 그다음 최신순으로 limit건."""
    usable = [n for n in items if n.summary]
    usable.sort(key=lambda n: (not n.risk_flags, -n.ts.timestamp()))
    return usable[:limit]


def render_events(events: list[EventItem], now_utc: datetime) -> str:
    """이벤트를 `kind: 제목 (+Nh)`로 이어 붙인다(지난 이벤트는 -Nh). 없으면 'none'."""
    if not events:
        return "none"
    parts = []
    for e in sorted(events, key=lambda e: e.ts):
        hours = int((e.ts - now_utc).total_seconds() / 3600)
        parts.append(f"{e.kind}: {_clean(e.title, _EVENT_TITLE_CHARS)} ({hours:+d}h)")
    return "; ".join(parts)


class FeatureBuilder:
    """진입 target 하나에 대한 state를 만든다 (core.ports.FeatureBuilder 구현)."""

    def __init__(
        self,
        market: Market | str,
        *,
        news: NewsSource | None = None,
        events: EventSource | None = None,
        spread: SpreadFn | None = None,
        max_tokens: int = MAX_TOKENS,
    ) -> None:
        self.market = Market(market)
        self.news = news
        self.events = events
        self.spread = spread
        self.max_tokens = max_tokens

    def build(self, *, symbol: str, strategy: str, target: Target, ctx: Any) -> State:
        """ctx(strategies.base.Context)의 봉 히스토리로 피처를 만들고 뉴스·이벤트를 붙인다."""
        # 불변식 #3: 가격 지정 진입은 현재 봉(close 포함)을 보지 않는다
        df = ctx.prev(symbol) if target.price is not None else ctx.history(symbol)
        spread = self.spread(symbol) if self.spread is not None else None
        features = compute_features(df, spread)

        ts = ctx.ts.to_pydatetime() if isinstance(ctx.ts, pd.Timestamp) else ctx.ts
        now_utc = clock.to_utc(ts, self.market)
        since = now_utc - WINDOW

        news = select_news(list(self.news.recent(symbol, since, now_utc))) if self.news else []
        events = list(self.events.within(symbol, since, now_utc + WINDOW)) if self.events else []
        for n in news:
            log.debug(
                "state news",
                extra={"symbol": symbol, "strategy": strategy, "title": n.title, "url": n.url},
            )

        state = State(
            self.market.value,
            symbol,
            strategy,
            signal=_clean(target.reason or "entry", 80),
            features=features,
            news_summary=self._news_text(news),
            events_24h=render_events(events, now_utc),
        )
        return self._fit(state, news)

    @staticmethod
    def _news_text(news: list[NewsItem]) -> str:
        return " | ".join(f'"{_clean(n.summary or "", SUMMARY_MAX_CHARS)}"' for n in news)

    def _fit(self, state: State, news: list[NewsItem]) -> State:
        """400토큰을 넘으면 뉴스를 뒤(우선순위 낮은 것)부터 빼고, 그래도 넘으면 이벤트를 줄인다."""
        while count_tokens(state.render()) > self.max_tokens and news:
            news = news[:-1]
            state.news_summary = self._news_text(news)
        if count_tokens(state.render()) > self.max_tokens and state.events_24h != "none":
            parts = state.events_24h.split("; ")
            while len(parts) > 1 and count_tokens(state.render()) > self.max_tokens:
                parts = parts[:-1]
                state.events_24h = "; ".join(parts) + " …"
            if count_tokens(state.render()) > self.max_tokens:
                state.events_24h = "present (truncated)"
        if count_tokens(state.render()) > self.max_tokens:
            log.warning(
                "state over token budget",
                extra={"symbol": state.symbol, "strategy": state.strategy},
            )
        return state
