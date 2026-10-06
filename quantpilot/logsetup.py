"""프로세스 로그 설정 (ADR 0029, 07 §5) — 엔진·scheduler 진입점과 API가 함께 쓴다.

- 형식: `QP_LOG_FORMAT=json`(배포)이면 한 줄에 JSON 하나 `{ts, level, logger, msg, …extra, exc}`,
  `text`(기본, 로컬)면 사람이 읽는 한 줄 + `key=value`. 둘 다 `extra={...}` 구조화 필드를 싣는다.
- 비밀 가리기(불변식 #10): 필드 이름에 key·secret·token·password가 들어가면 값을 `***`로 바꾼다.
- httpx는 INFO에서 요청 URL 전체를 남긴다. 텔레그램 봇 토큰(URL 경로)·DART 키(쿼리)가 URL에 들어가므로
  그 로거를 WARNING으로 올린다.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

# URL에 비밀이 실릴 수 있는 외부 라이브러리 로거
QUIET = ("httpx", "httpcore")
# 이름에 이 낱말이 들어간 extra 필드는 값을 가린다
SECRET_WORDS = ("key", "secret", "token", "password")
MASK = "***"
# uvicorn이 자기 핸들러를 다는 로거 — 루트로 올려 같은 형식으로 찍는다
UVICORN = ("uvicorn", "uvicorn.error", "uvicorn.access")

# LogRecord가 원래 가진 속성 — 그 밖의 속성이 extra 필드다
_STD = set(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {"message", "asctime"}


def _is_secret(name: str) -> bool:
    low = name.lower()
    return any(w in low for w in SECRET_WORDS)


def extra_fields(record: logging.LogRecord) -> dict[str, Any]:
    """레코드의 extra 필드(비밀은 가림)."""
    return {
        k: (MASK if _is_secret(k) else v)
        for k, v in vars(record).items()
        if k not in _STD and not k.startswith("_")
    }


class JsonFormatter(logging.Formatter):
    """한 줄 JSON: ts(UTC ISO), level, logger, msg, extra 필드, exc(예외가 있을 때)."""

    def format(self, record: logging.LogRecord) -> str:
        """레코드 → JSON 한 줄."""
        out: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for k, v in extra_fields(record).items():
            out.setdefault(k, v)
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, ensure_ascii=False, default=str)


class TextFormatter(logging.Formatter):
    """사람이 읽는 한 줄 + extra 필드 `key=value`."""

    def __init__(self) -> None:
        super().__init__("%(asctime)s %(levelname)s %(name)s: %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        """레코드 → 텍스트 한 줄(예외는 다음 줄들)."""
        line = super().format(record)
        extra = extra_fields(record)
        if not extra:
            return line
        head, sep, tail = line.partition("\n")
        pairs = " ".join(f"{k}={v}" for k, v in extra.items())
        return f"{head} {pairs}{sep}{tail}"


def setup_logging(
    level: int = logging.INFO, fmt: str | None = None, *, uvicorn: bool = False
) -> None:
    """루트 로거를 설정하고 비밀이 새는 로거를 WARNING으로 올린다. fmt가 없으면 settings.log_format.

    uvicorn=True면 uvicorn 로거의 자체 핸들러를 떼고 루트로 올린다(API 프로세스).
    """
    if fmt is None:
        from quantpilot.config import settings

        fmt = settings.log_format
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter() if fmt == "json" else TextFormatter())
    handler._quantpilot = True  # type: ignore[attr-defined] — 다시 불러도 이 핸들러만 바꾼다
    root = logging.getLogger()
    for h in [h for h in root.handlers if getattr(h, "_quantpilot", False)]:
        root.removeHandler(h)
    root.addHandler(handler)
    root.setLevel(level)
    for name in QUIET:
        logging.getLogger(name).setLevel(logging.WARNING)
    if uvicorn:
        for name in UVICORN:
            lg = logging.getLogger(name)
            lg.handlers = []
            lg.propagate = True
