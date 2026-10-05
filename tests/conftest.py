"""테스트 공용 설정: DB 연결 하나를 두 곳이 동시에 쥐면 그 테스트를 실패시킨다 (t40).

인메모리 StaticPool 픽스처는 연결 1개를 모든 세션이 같이 쓴다. 한 세션이 커밋 전 쓰기를 든 채로
다른 세션이 그 연결을 빌렸다 반납하면, 반납 때의 rollback이 그 쓰기를 지운다 (t38).
결과는 타이밍에 따라 달라 놓치기 쉬우니 겹친 순간 자체를 잡는다. 세션마다 연결이 따로인
NullPool(`api_helpers.memory_sessions`)은 걸리지 않는다.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

try:
    from sqlalchemy import event
    from sqlalchemy.pool import Pool
except ImportError:  # DB 의존성 없이 도는 환경 — 감시할 연결도 없다
    Pool = None

_held: dict[int, list[Any]] = {}  # id(DBAPI 연결) → [연결(쥐는 동안 id 재사용 방지), 동시 보유 수]
_overlaps: list[int] = []  # 이번 테스트에서 겹친 순간의 동시 보유 수


def _on_checkout(dbapi_conn: Any, _record: Any, _proxy: Any) -> None:
    slot = _held.setdefault(id(dbapi_conn), [dbapi_conn, 0])
    slot[1] += 1
    if slot[1] > 1:
        _overlaps.append(slot[1])


def _on_checkin(dbapi_conn: Any, _record: Any) -> None:
    slot = _held.get(id(dbapi_conn)) if dbapi_conn is not None else None
    if slot is None:
        return
    slot[1] -= 1
    if slot[1] <= 0:
        del _held[id(dbapi_conn)]


if Pool is not None:
    event.listen(Pool, "checkout", _on_checkout)
    event.listen(Pool, "checkin", _on_checkin)


@pytest.fixture
def connection_overlaps() -> list[int]:
    """이번 테스트에서 잡힌 겹침 목록. 일부러 겹치게 하는 테스트는 확인한 뒤 비운다."""
    return _overlaps


@pytest.fixture(autouse=True)
def _no_shared_connection_overlap() -> Iterator[None]:
    """테스트 동안 DB 연결 하나를 두 곳이 동시에 쥐었으면 실패시킨다."""
    _overlaps.clear()
    yield
    if _overlaps:
        n = max(_overlaps)
        _overlaps.clear()
        pytest.fail(
            f"DB 연결 하나를 {n}곳이 동시에 썼다 — 커밋 전 쓰기가 사라질 수 있다. "
            "StaticPool 픽스처라면 api_helpers.memory_sessions로 바꿀 것 (t38·t40)",
            pytrace=False,
        )
