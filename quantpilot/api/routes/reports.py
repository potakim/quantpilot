"""03 §2.6 리뷰·리포트 + 정합 해제("브로커 기준으로 맞추기", ADR 0015).

할트를 푸는 API 경로는 `POST /reconcile/{market}/accept-broker` 하나뿐이고, Reconciler.accept_broker를
그대로 부른다 (불변식 #6 — 리스크 규칙을 바꾸는 엔드포인트는 없다). 사람 조작이라 비밀번호 재확인을 받는다.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from quantpilot.api import gates, queries
from quantpilot.api.auth import require_user
from quantpilot.api.deps import DepsDep
from quantpilot.api.errors import ApiError
from quantpilot.api.routes.system import link_of
from quantpilot.core.models import Market

log = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_user)])


@router.get("/reviews")
async def reviews(
    deps: DepsDep,
    limit: int = 30,
) -> list[dict[str, Any]]:
    """일일 매매 일지."""
    return await queries.reviews(deps.sessions, limit=limit)


@router.get("/reports/gates")
async def report_gates(
    deps: DepsDep,
) -> dict[str, Any]:
    """관문 G1~G4 현재 상태."""
    return await gates.all_gates(deps)


@router.get("/risk/events")
async def risk_events(
    deps: DepsDep,
    limit: int = 50,
    before: int | None = None,
) -> dict[str, Any]:
    """리스크 이벤트."""
    return await queries.risk_events(deps.sessions, limit=limit, before=before)


@router.get("/costs/ai")
async def costs_ai(
    deps: DepsDep,
    month: str | None = None,
) -> dict[str, Any]:
    """월 AI 비용 (provider별 호출 수·USD)."""
    if month is not None:
        parts = month.split("-")
        if len(parts) != 2 or not all(p.isdigit() for p in parts) or not 1 <= int(parts[1]) <= 12:
            raise ApiError(400, "INVALID_PARAM", "month는 YYYY-MM")
    return await queries.costs_ai(deps.sessions, month=month)


class AcceptBrokerRequest(BaseModel):
    confirm_password: str | None = None


@router.post("/reconcile/{market}/accept-broker")
async def accept_broker(
    deps: DepsDep,
    market: str,
    req: AcceptBrokerRequest,
) -> dict[str, Any]:
    """DB 포지션을 브로커 기준으로 덮고 정합 이벤트 해결·할트 해제 (Reconciler.accept_broker)."""
    from quantpilot.db.repo import SqlPositionRepo, SqlRiskEventRepo
    from quantpilot.execution.reconciler import Reconciler
    from quantpilot.notify.telegram import LogNotifier

    try:
        m = Market(market)
    except ValueError:
        raise ApiError(400, "INVALID_PARAM", f"알 수 없는 시장: {market}") from None
    if not req.confirm_password or not deps.auth.check_password(req.confirm_password):
        raise ApiError(403, "CONFIRMATION_REQUIRED", "confirm_password가 필요하다")
    rec = Reconciler(
        SqlPositionRepo(deps.sessions),
        SqlRiskEventRepo(deps.sessions),
        link_of(deps),
        deps.notifier or LogNotifier(),
        utcnow=deps.utcnow,
    )
    fixed = await rec.accept_broker(m, await deps.account(m))
    log.warning("reconcile accept_broker via api", extra={"market": m.value, "fixed": fixed})
    return {"market": m.value, "fixed": fixed, "halted": await link_of(deps).halt_reason(m)}
