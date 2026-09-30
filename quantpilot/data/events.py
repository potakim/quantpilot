"""이벤트 캘린더 (04 §3·data 표, 05 §5, 07 §6). 수동 YAML + DART 공시.

- 수동 YAML: FOMC·CPI·실적 발표·하드포크·업비트 점검 공지 등. 형식:

  ```yaml
  events:
    - ts: 2026-10-29T18:00:00Z     # tz 포함 권장
      kind: fomc
      title: FOMC 금리 결정
    - ts: 2026-10-02 03:00          # tz 없으면 market 현지시간
      market: upbit
      kind: exchange_maintenance
      title: 업비트 정기 점검
      symbols: []                   # 비우면 모든 종목
  ```
- DART: 공시 제목에 상장폐지·관리종목·거래정지 등이 있으면 그 종목의 이벤트로 넣는다(공시 시각 기준)
- `pyyaml`은 이 어댑터 안에서만 import한다(try/except ImportError)
- 시각은 UTC tz-aware (core/news.py). tz-naive 입력의 변환은 core/clock.py
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from quantpilot.core import clock
from quantpilot.core.models import Market
from quantpilot.core.news import EventItem, NewsItem

try:
    import yaml
except ImportError:  # pragma: no cover - uvicorn[standard]가 끌어오지만 어댑터 규칙대로 감싼다
    yaml = None

log = logging.getLogger(__name__)

# DART 공시 제목 키워드 → 이벤트 종류
DART_EVENT_KEYWORDS: Mapping[str, str] = {
    "상장폐지": "delisting_review",
    "상장적격성": "delisting_review",
    "관리종목": "delisting_review",
    "매매거래정지": "trading_halt",
    "투자주의환기": "delisting_review",
    "영업(잠정)실적": "earnings",
    "감사보고서제출": "earnings",
}


def _to_utc(value: Any, market: str | None) -> datetime:
    if isinstance(value, datetime):
        ts = value
    elif isinstance(value, date):
        ts = datetime(value.year, value.month, value.day)  # noqa: DTZ001 — 아래에서 현지→UTC
    else:
        ts = datetime.fromisoformat(str(value))
    if ts.tzinfo is not None:
        return ts.astimezone(UTC)
    if market:
        return clock.to_utc(ts, Market(market))
    return ts.replace(tzinfo=UTC)


def parse_calendar(data: Mapping[str, Any]) -> list[EventItem]:
    """`{"events": [...]}` → EventItem. ts·kind·title이 없는 항목은 ValueError."""
    out = []
    for i, row in enumerate(data.get("events") or []):
        if not all(row.get(k) for k in ("ts", "kind", "title")):
            raise ValueError(f"events[{i}]: ts·kind·title이 필요합니다")
        out.append(
            EventItem(
                ts=_to_utc(row["ts"], row.get("market")),
                kind=str(row["kind"]),
                title=str(row["title"]),
                symbols=tuple(str(s) for s in row.get("symbols") or ()),
                source="manual",
            )
        )
    return out


def events_from_dart(items: Iterable[NewsItem]) -> list[EventItem]:
    """DART 공시 뉴스 중 위험 키워드가 제목에 있는 것 → 그 종목 이벤트."""
    out = []
    for item in items:
        if not item.symbols:
            continue
        kind = next((k for word, k in DART_EVENT_KEYWORDS.items() if word in item.title), None)
        if kind:
            out.append(EventItem(item.ts, kind, item.title, tuple(item.symbols), item.source))
    return out


class EventCalendar:
    """이벤트 목록 (core.news.EventSource 구현)."""

    def __init__(self, events: Sequence[EventItem] = ()) -> None:
        self._events: list[EventItem] = []
        self.add(events)

    @classmethod
    def from_yaml(cls, path: str | Path) -> EventCalendar:
        """수동 YAML 파일에서 읽는다. 파일이 없으면 빈 캘린더."""
        p = Path(path)
        if not p.exists():
            log.info("event calendar file missing", extra={"path": str(p)})
            return cls()
        if yaml is None:  # pragma: no cover
            raise ImportError("pyyaml이 필요합니다")
        return cls(parse_calendar(yaml.safe_load(p.read_text(encoding="utf-8")) or {}))

    def add(self, events: Iterable[EventItem]) -> int:
        """중복(같은 시각·종류·제목·종목)은 건너뛰고 추가한 수를 돌려준다."""
        seen = set(self._events)
        n = 0
        for e in events:
            if e not in seen:
                self._events.append(e)
                seen.add(e)
                n += 1
        self._events.sort(key=lambda e: e.ts)
        return n

    def within(self, symbol: str, since: datetime, until: datetime) -> list[EventItem]:
        """since 이상 until 이하, 이 종목에 해당하는 이벤트 (시간순)."""
        return [e for e in self._events if since <= e.ts <= until and e.applies_to(symbol)]

    def __len__(self) -> int:
        return len(self._events)
