"""프로세스 로그 설정 (ADR 0029) — 엔진·scheduler 진입점이 함께 쓴다.

httpx는 INFO에서 요청 URL 전체를 남긴다. 텔레그램 봇 토큰(URL 경로)·DART 키(쿼리)가 URL에 들어가므로
그 로거를 WARNING으로 올린다 (불변식 #10).
"""

from __future__ import annotations

import logging

# URL에 비밀이 실릴 수 있는 외부 라이브러리 로거
QUIET = ("httpx", "httpcore")


def setup_logging(level: int = logging.INFO) -> None:
    """루트 로거를 설정하고 비밀이 새는 로거를 WARNING으로 올린다."""
    logging.basicConfig(level=level)
    for name in QUIET:
        logging.getLogger(name).setLevel(logging.WARNING)
