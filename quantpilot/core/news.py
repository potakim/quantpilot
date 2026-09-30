"""뉴스·이벤트 캘린더 타입과 포트 (04 §3, 01 §4, 02 news_items).

뉴스·이벤트는 한 시장에 속하지 않으므로 시각은 **UTC tz-aware**로 둔다. 시장 현지시간과의
변환은 `core/clock.py`에서만 한다(피처 빌더가 봉 시각을 UTC로 바꿔 비교한다).

- `NewsSource`·`EventSource`는 동기 조회다. 피처 빌더(`build`)가 동기라서, 수집기가
  비동기로 채운 메모리 캐시를 읽는다
- `Summarizer`는 제목·본문 → 100자 요약 + 위험 플래그. 수량·가격을 묻지 않는다 (불변식 #7 취지)
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

SUMMARY_MAX_CHARS = 100

# 요약기가 붙일 수 있는 위험 플래그. 목록 밖 값은 버린다 (요약 계약)
RISK_FLAGS: frozenset[str] = frozenset(
    {
        "regulation",  # 규제·제재·소송
        "hack",  # 해킹·도난·보안 사고
        "delisting",  # 상장폐지·거래 유의·투자주의
        "earnings",  # 실적 발표·어닝 쇼크
        "macro",  # 금리·CPI·FOMC 등 거시
        "exchange_outage",  # 거래소 장애·점검·입출금 중단
        "dilution",  # 유상증자·대량 매도·락업 해제
        "legal",  # 횡령·배임·감사의견
    }
)


def raw_hash(title: str, url: str | None) -> str:
    """중복 제거 키. 공백·대소문자를 정규화한 제목 + url의 sha256 (02 news_items.raw_hash)."""
    norm = re.sub(r"\s+", " ", title).strip().lower()
    return hashlib.sha256(f"{norm}\n{(url or '').strip()}".encode()).hexdigest()


@dataclass(frozen=True)
class Summary:
    """요약기 출력 계약. summary ≤ 100자, risk_flags ⊂ RISK_FLAGS, risk_score ∈ [0,1] 또는 None."""

    summary: str
    risk_flags: tuple[str, ...] = ()
    risk_score: float | None = None


@dataclass
class NewsItem:
    """수집한 뉴스 1건. ts는 UTC tz-aware. title·url은 로그용, state에는 summary만 들어간다."""

    ts: datetime
    source: str
    title: str
    url: str | None = None
    body: str = ""
    symbols: list[str] = field(default_factory=list)
    summary: str | None = None
    risk_flags: list[str] = field(default_factory=list)
    risk_score: float | None = None
    raw_hash: str = ""

    def __post_init__(self) -> None:
        if not self.raw_hash:
            self.raw_hash = raw_hash(self.title, self.url)


@dataclass(frozen=True)
class EventItem:
    """캘린더 이벤트 1건 (FOMC·CPI·실적·상장폐지 심사·하드포크·거래소 점검). ts는 UTC tz-aware.

    symbols가 비어 있으면 모든 종목에 해당하는 이벤트(거시·거래소 점검)다.
    """

    ts: datetime
    kind: str
    title: str
    symbols: tuple[str, ...] = ()
    source: str = "manual"

    def applies_to(self, symbol: str) -> bool:
        """이 종목에 해당하는가."""
        return not self.symbols or symbol in self.symbols


class Summarizer(Protocol):
    """뉴스 요약기 (1단계 GeminiSummarizer)."""

    def summarize(self, title: str, body: str) -> Summary:
        """제목·본문 → Summary. 실패해도 예외 대신 보수적 기본값을 돌려준다."""
        ...


class NewsSource(Protocol):
    """피처 빌더가 읽는 최근 뉴스 (동기)."""

    def recent(self, symbol: str, since: datetime, until: datetime) -> Sequence[NewsItem]:
        """since 이상 until 이하, 이 종목에 매칭된 뉴스."""
        ...


class EventSource(Protocol):
    """피처 빌더가 읽는 이벤트 캘린더 (동기)."""

    def within(self, symbol: str, since: datetime, until: datetime) -> Sequence[EventItem]:
        """since 이상 until 이하에 예정된, 이 종목에 해당하는 이벤트."""
        ...


class NewsRepo(Protocol):
    """news_items 저장소."""

    async def known_hashes(self, hashes: Sequence[str]) -> set[str]:
        """이미 저장된 raw_hash."""
        ...

    async def add(self, items: Sequence[NewsItem]) -> int:
        """저장. raw_hash가 겹치는 항목은 건너뛰고 실제로 넣은 수를 돌려준다."""
        ...

    async def recent(self, since: datetime) -> list[NewsItem]:
        """since 이후 뉴스 (시간순)."""
        ...
