"""텔레그램 알림과 critical 반복 (07 §6 알림 등급).

- `TelegramNotifier`: Bot API `sendMessage`로 보낸다. 토큰·chat id는 `QP_TELEGRAM_*` 환경변수로만 받고,
  본문·로그·예외 메시지에 토큰을 남기지 않는다 (불변식 #10). httpx가 없으면 로그로만 남긴다.
- `RepeatingNotifier`: critical을 key 단위로 기억했다가 `resolve(key)` 전까지 5분마다 다시 보낸다.
  이미 반복 중인 key의 critical은 다시 보내지 않는다(같은 알림을 5분 잡이 또 보내도 한 번).
- `CriticalLogHandler`: CRITICAL 로그(청산 주문 실패·원장 쓰기 실패 등)를 알림으로 넘긴다.
이메일 채널은 아직 없다 (07 §3에 SMTP 변수가 정의되면 추가).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from quantpilot.core.ports import Notifier

try:  # 외부 SDK는 어댑터 안에서만, 없으면 로그로 대체
    import httpx
except ImportError:  # pragma: no cover — 기본 설치에 포함(fastapi 의존)
    httpx = None  # type: ignore[assignment]

log = logging.getLogger(__name__)

API = "https://api.telegram.org"
REPEAT_EVERY = timedelta(minutes=5)
_PREFIX = {"info": "ℹ️", "warning": "⚠️", "critical": "🚨"}


def _utcnow() -> datetime:
    return datetime.now(UTC)


class LogNotifier:
    """알림을 로그로만 남긴다 (텔레그램 미설정 시)."""

    async def send(self, level: str, text: str, *, key: str | None = None) -> None:
        """level에 맞는 로그 레벨로 남긴다. critical도 error로 — CriticalLogHandler 재귀를 막는다."""
        lv = logging.ERROR if level == "critical" else logging.getLevelName(level.upper())
        log.log(lv, text, extra={"notify": level, "alert": key})

    async def resolve(self, key: str) -> None:
        """반복이 없으니 할 일 없음."""


class TelegramNotifier:
    """Telegram Bot API로 보낸다. 전송 실패는 로그(예외 타입만)로 남기고 삼킨다."""

    def __init__(
        self, token: str, chat_id: str, *, client: Any = None, timeout: float = 10.0
    ) -> None:
        if not token or not chat_id:
            raise ValueError("telegram token·chat_id가 필요하다 (QP_TELEGRAM_*)")
        self._token = token
        self.chat_id = chat_id
        self._client = client
        self.timeout = timeout

    def __repr__(self) -> str:  # 토큰이 repr·로그에 찍히지 않게
        return f"TelegramNotifier(chat_id={self.chat_id!r})"

    def _scrub(self, text: str) -> str:
        return text.replace(self._token, "***")

    async def send(self, level: str, text: str, *, key: str | None = None) -> None:
        """메시지 1건을 보낸다."""
        body = {
            "chat_id": self.chat_id,
            "text": f"{_PREFIX.get(level, '')} [{level}] {self._scrub(text)}".strip(),
            "disable_web_page_preview": True,
        }
        url = f"{API}/bot{self._token}/sendMessage"
        try:
            if self._client is not None:
                resp = await self._client.post(url, json=body, timeout=self.timeout)
            elif httpx is None:
                log.warning(
                    "httpx 없음, 알림을 로그로", extra={"notify": level, "text": body["text"]}
                )
                return
            else:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    resp = await client.post(url, json=body)
            if resp.status_code >= 400:
                log.error("telegram send failed", extra={"status": resp.status_code, "alert": key})
        except Exception as e:  # noqa: BLE001 — 예외 메시지엔 URL(토큰)이 섞이므로 타입만
            log.error("telegram send failed", extra={"error": type(e).__name__, "alert": key})

    async def resolve(self, key: str) -> None:
        """반복은 RepeatingNotifier 몫."""


class RepeatingNotifier:
    """critical을 resolve 전까지 every마다 다시 보낸다. `tick()`은 scheduler `alert_repeat` 잡이 부른다."""

    def __init__(
        self,
        inner: Notifier,
        *,
        every: timedelta = REPEAT_EVERY,
        utcnow: Callable[[], datetime] = _utcnow,
    ) -> None:
        self.inner = inner
        self.every = every
        self.utcnow = utcnow
        self.active: dict[str, tuple[str, datetime]] = {}

    async def send(self, level: str, text: str, *, key: str | None = None) -> None:
        """보낸다. critical이면 key(없으면 text)로 기억한다. 이미 반복 중인 key면 보내지 않는다."""
        if level == "critical":
            k = key or text
            if k in self.active:
                return
            self.active[k] = (text, self.utcnow())
        await self.inner.send(level, text, key=key)

    async def resolve(self, key: str) -> None:
        """key의 반복을 멈춘다."""
        self.active.pop(key, None)
        await self.inner.resolve(key)

    async def tick(self) -> int:
        """마지막 전송 후 every가 지난 critical을 다시 보낸다. 다시 보낸 건수."""
        now = self.utcnow()
        n = 0
        for k, (text, last) in list(self.active.items()):
            if now - last >= self.every:
                self.active[k] = (text, now)
                await self.inner.send("critical", f"[repeat] {text}", key=k)
                n += 1
        return n


class CriticalLogHandler(logging.Handler):
    """CRITICAL 로그를 notifier로 넘긴다. 실행 중인 이벤트 루프가 없으면 버린다(로그는 이미 남았다)."""

    FIELDS = ("market", "symbol", "strategy", "side", "order_id")

    def __init__(self, notifier: Notifier) -> None:
        super().__init__(logging.CRITICAL)
        self.notifier = notifier
        self._tasks: set[asyncio.Task[None]] = set()

    def emit(self, record: logging.LogRecord) -> None:
        """레코드 1건 → critical 알림 (메시지 + 구조화 필드 일부)."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        extra = " ".join(
            f"{f}={getattr(record, f)}" for f in self.FIELDS if getattr(record, f, None) is not None
        )
        text = f"{record.name}: {record.getMessage()} {extra}".strip()
        task = loop.create_task(self.notifier.send("critical", text, key=f"log.{text}"))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)


def from_settings(settings: Any) -> Notifier:
    """설정에 텔레그램 토큰·chat id가 있으면 TelegramNotifier, 없으면 LogNotifier."""
    token, chat = settings.telegram_bot_token, settings.telegram_chat_id
    if token and chat:
        return TelegramNotifier(token, chat)
    log.info("telegram 미설정, 알림은 로그로")
    return LogNotifier()
