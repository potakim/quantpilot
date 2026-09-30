"""뉴스 수집·요약 (04 data 표, 01 §4, 02 news_items). 스케줄 `news_collect` 매시 :05 (04 §10).

흐름: 피드 수집(RSS·Atom·DART 공시 목록) → 최근 것만 → 중복 제거(`raw_hash`, 배치 안 + 저장소)
→ 종목·키워드 매칭(매칭 없으면 버림) → 요약기로 100자 요약·위험 플래그 → 저장.
중복·미매칭은 요약 호출 **전에** 걸러 비용을 아낀다.

- HTTP(`httpx`)는 이 어댑터 안에서만 쓰고 `fetch`로 주입할 수 있다. 테스트는 가짜 fetch를 쓴다
- DART 키는 `settings.dart_api_key`(QP_DART_API_KEY)로만 받는다. 요청 URL·예외 메시지에 키가
  섞일 수 있으므로 로그에는 피드 이름과 오류 타입만 남긴다
- XML은 stdlib `xml.etree`로 읽되 DOCTYPE/ENTITY가 있는 문서는 거부한다(엔티티 폭탄 방지)
- 시각은 UTC tz-aware (core/news.py)
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET

from quantpilot.core.news import NewsItem, NewsRepo, Summarizer

try:
    import httpx
except ImportError:  # pragma: no cover - httpx는 기본 의존성이지만 어댑터 규칙대로 감싼다
    httpx = None

log = logging.getLogger(__name__)

ALL_SYMBOLS = "*"  # 거시 뉴스: 모든 종목에 해당
MAX_BYTES = 2_000_000
MAX_AGE = timedelta(hours=48)
DART_LIST_URL = "https://opendart.fss.or.kr/api/list.json"
DART_VIEW_URL = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"

# fetch(url, params) → 응답 본문 텍스트
Fetch = Callable[[str, Mapping[str, str]], Awaitable[str]]


@dataclass(frozen=True)
class Feed:
    """수집 대상. kind='rss'(RSS 2.0·Atom) 또는 'dart'(OpenDART 공시 목록 JSON)."""

    name: str
    url: str
    kind: str = "rss"


async def httpx_fetch(url: str, params: Mapping[str, str]) -> str:
    """기본 fetch. 10초 타임아웃, 응답 2MB 상한."""
    if httpx is None:  # pragma: no cover
        raise ImportError("httpx가 필요합니다")
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
        resp = await client.get(url, params=dict(params))
        resp.raise_for_status()
        if len(resp.content) > MAX_BYTES:
            raise ValueError("response too large")
        return resp.text


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child_text(el: ET.Element, *names: str) -> str:
    for child in el:
        if _local_name(child.tag) in names:
            if _local_name(child.tag) == "link" and child.get("href"):
                return child.get("href", "")
            return (child.text or "").strip()
    return ""


def _parse_ts(text: str) -> datetime | None:
    if not text:
        return None
    try:
        ts = parsedate_to_datetime(text)  # RSS pubDate (RFC 822)
    except (TypeError, ValueError):
        try:
            ts = datetime.fromisoformat(text)  # Atom (RFC 3339)
        except ValueError:
            return None
    return ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts.astimezone(UTC)


def parse_rss(text: str, source: str, *, fetched_at: datetime) -> list[NewsItem]:
    """RSS 2.0 `<item>`·Atom `<entry>` → NewsItem. 날짜가 없으면 수집 시각."""
    head = text[:4096].upper()
    if "<!DOCTYPE" in head or "<!ENTITY" in text.upper():
        raise ValueError("DTD/entity not allowed")
    root = ET.fromstring(text)
    out: list[NewsItem] = []
    for el in root.iter():
        if _local_name(el.tag) not in ("item", "entry"):
            continue
        title = _child_text(el, "title")
        if not title:
            continue
        out.append(
            NewsItem(
                ts=_parse_ts(_child_text(el, "pubDate", "published", "updated", "date"))
                or fetched_at,
                source=source,
                title=title,
                url=_child_text(el, "link") or None,
                body=_child_text(el, "description", "summary", "content"),
            )
        )
    return out


def parse_dart(text: str, source: str, *, stock_symbols: Mapping[str, str]) -> list[NewsItem]:
    """OpenDART list.json → NewsItem. stock_code를 stock_symbols로 종목에 바로 매칭한다.

    rcept_dt(YYYYMMDD, KST)는 그날 00:00 KST로 본다 (공시 시각은 목록에 없다).
    """
    data = json.loads(text)
    status = str(data.get("status", ""))
    if status == "013":  # 조회된 데이터 없음
        return []
    if status != "000":
        raise ValueError(f"dart status {status}")
    out: list[NewsItem] = []
    for row in data.get("list", []):
        rcept_no = str(row.get("rcept_no", ""))
        day = str(row.get("rcept_dt", ""))
        try:
            ts = datetime.strptime(day, "%Y%m%d").replace(tzinfo=UTC) - timedelta(hours=9)
        except ValueError:
            continue
        symbol = stock_symbols.get(str(row.get("stock_code", "")).strip())
        title = f"{row.get('corp_name', '')} {row.get('report_nm', '')}".strip()
        out.append(
            NewsItem(
                ts=ts,
                source=source,
                title=title,
                url=DART_VIEW_URL.format(rcept_no=rcept_no) if rcept_no else None,
                symbols=[symbol] if symbol else [],
            )
        )
    return out


def match_symbols(item: NewsItem, keywords: Mapping[str, Sequence[str]]) -> list[str]:
    """제목·본문에 키워드가 있는 종목. keywords[ALL_SYMBOLS]는 거시 키워드(모든 종목)."""
    text = f"{item.title} {item.body}".lower()
    found = list(item.symbols)
    for symbol, words in keywords.items():
        if symbol not in found and any(w.lower() in text for w in words if w):
            found.append(symbol)
    return found


class NewsCache:
    """메모리 뉴스 저장소 — NewsRepo(수집기가 씀)이자 NewsSource(피처 빌더가 읽음).

    `retention`보다 오래된 뉴스는 add 때 버린다. DB 저장은 db/news_repo.SqlNewsRepo.
    """

    def __init__(self, retention: timedelta = MAX_AGE) -> None:
        self.retention = retention
        self._items: dict[str, NewsItem] = {}

    async def known_hashes(self, hashes: Sequence[str]) -> set[str]:
        """이미 가진 raw_hash."""
        return {h for h in hashes if h in self._items}

    async def add(self, items: Sequence[NewsItem]) -> int:
        """raw_hash가 새로운 것만 넣는다."""
        n = 0
        for item in items:
            if item.raw_hash not in self._items:
                self._items[item.raw_hash] = item
                n += 1
        if self._items:
            newest = max(i.ts for i in self._items.values())
            cutoff = newest - self.retention
            self._items = {h: i for h, i in self._items.items() if i.ts >= cutoff}
        return n

    async def recent(self, since: datetime) -> list[NewsItem]:
        """since 이후 전체 (시간순)."""
        return sorted((i for i in self._items.values() if i.ts >= since), key=lambda i: i.ts)

    def warm(self, items: Sequence[NewsItem]) -> None:
        """재시작 시 DB에서 읽은 최근 뉴스로 채운다."""
        for item in items:
            self._items.setdefault(item.raw_hash, item)

    def __len__(self) -> int:
        return len(self._items)

    # NewsSource
    def recent_for(self, symbol: str, since: datetime, until: datetime) -> list[NewsItem]:
        """이 종목(또는 전 종목 대상) 뉴스, 시간순."""
        return sorted(
            (
                i
                for i in self._items.values()
                if since <= i.ts <= until and (symbol in i.symbols or ALL_SYMBOLS in i.symbols)
            ),
            key=lambda i: i.ts,
        )


class CacheNewsSource:
    """NewsCache를 피처 빌더용 NewsSource로 감싼다 (`recent(symbol, since, until)`)."""

    def __init__(self, cache: NewsCache) -> None:
        self.cache = cache

    def recent(self, symbol: str, since: datetime, until: datetime) -> list[NewsItem]:
        """이 종목에 매칭된 뉴스."""
        return self.cache.recent_for(symbol, since, until)


class NewsCollector:
    """피드 수집 → 중복 제거 → 매칭 → 요약 → 저장."""

    def __init__(
        self,
        feeds: Sequence[Feed],
        summarizer: Summarizer,
        repo: NewsRepo,
        *,
        keywords: Mapping[str, Sequence[str]] | None = None,
        stock_symbols: Mapping[str, str] | None = None,
        dart_api_key: str | None = None,
        fetch: Fetch | None = None,
        max_age: timedelta = MAX_AGE,
    ) -> None:
        self.feeds = list(feeds)
        self.summarizer = summarizer
        self.repo = repo
        self.keywords = dict(keywords or {})
        self.stock_symbols = dict(stock_symbols or {})
        self._dart_key = dart_api_key
        self.fetch = fetch or httpx_fetch
        self.max_age = max_age

    def __repr__(self) -> str:
        return f"NewsCollector(feeds={[f.name for f in self.feeds]})"

    def _dart_key_value(self) -> str:
        if self._dart_key is None:
            from quantpilot.config import settings

            self._dart_key = settings.dart_api_key
        return self._dart_key

    async def _fetch_feed(self, feed: Feed, now: datetime) -> list[NewsItem]:
        try:
            if feed.kind == "dart":
                key = self._dart_key_value()
                if not key:
                    log.warning("dart key missing, feed skipped", extra={"feed": feed.name})
                    return []
                kst_day = (now + timedelta(hours=9)).strftime("%Y%m%d")
                params = {
                    "crtfc_key": key,
                    "bgn_de": kst_day,
                    "end_de": kst_day,
                    "page_count": "100",
                }
                text = await self.fetch(feed.url, params)
                return parse_dart(text, feed.name, stock_symbols=self.stock_symbols)
            text = await self.fetch(feed.url, {})
            return parse_rss(text, feed.name, fetched_at=now)
        except Exception as e:  # noqa: BLE001 — 피드 하나 실패로 멈추지 않음, 메시지엔 키가 섞일 수 있음
            log.warning("feed fetch failed", extra={"feed": feed.name, "error": type(e).__name__})
            return []

    async def collect(self, now: datetime | None = None) -> list[NewsItem]:
        """한 번 수집해 새로 저장한 뉴스를 돌려준다."""
        now = now or datetime.now(UTC)
        batches = await asyncio.gather(*(self._fetch_feed(f, now) for f in self.feeds))
        cutoff = now - self.max_age

        fresh: dict[str, NewsItem] = {}
        for item in (i for batch in batches for i in batch):
            if item.ts >= cutoff and item.raw_hash not in fresh:
                fresh[item.raw_hash] = item
        known = await self.repo.known_hashes(list(fresh))
        candidates = [i for h, i in fresh.items() if h not in known]

        todo: list[NewsItem] = []
        for item in candidates:
            item.symbols = match_symbols(item, self.keywords)
            if item.symbols:
                todo.append(item)

        for item in todo:
            s = await asyncio.to_thread(self.summarizer.summarize, item.title, item.body)
            item.summary, item.risk_flags, item.risk_score = (
                s.summary,
                list(s.risk_flags),
                s.risk_score,
            )
        added = await self.repo.add(todo)
        log.info(
            "news collected",
            extra={
                "fetched": sum(len(b) for b in batches),
                "duplicates": len(fresh) - len(candidates),
                "unmatched": len(candidates) - len(todo),
                "stored": added,
            },
        )
        return todo
