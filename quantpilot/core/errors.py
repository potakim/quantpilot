"""엔진 공통 예외 (04 §1, §5.1). 어댑터는 외부 SDK 예외를 여기 타입으로 바꿔 던진다."""

from __future__ import annotations

from datetime import date


class QuantPilotError(Exception):
    """QuantPilot 예외의 공통 부모."""


class BrokerError(QuantPilotError):
    """브로커 호출 실패. retryable이면 OrderExecutor가 백오프 재시도한다 (5xx·타임아웃)."""

    def __init__(self, message: str = "", *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


class RateLimited(BrokerError):
    """HTTP 429. retry_after초(모르면 None) 뒤 재시도할 수 있다."""

    def __init__(self, message: str = "", *, retry_after: float | None = None) -> None:
        super().__init__(message, retryable=True)
        self.retry_after = retry_after


class JudgeTimeout(QuantPilotError):
    """판단 모델·LLM 응답 시간 초과. 파이프라인은 hold로 처리한다."""


class DataStale(QuantPilotError):
    """시세가 너무 오래 갱신되지 않음 (예: 웹소켓 30초 무응답)."""


class CalendarOutOfRange(QuantPilotError, ValueError):
    """내장 시장 캘린더 표 범위 밖 날짜 (ADR 0009). 어댑터 캘린더를 주입해야 한다."""

    def __init__(self, day: date, first: date, last: date) -> None:
        super().__init__(f"{day}: 내장 캘린더 범위({first} ~ {last}) 밖")
        self.day = day
