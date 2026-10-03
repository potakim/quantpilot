"""03 §2.5 AI 판단: 로그·상세·물어보기·보정·A/B·미리보기.

보정·A/B 계산은 judgment/calibration.py(t13) 몫이라 여기서 부르지 않는다 — 주입된 CalibrationSource만 쓰고,
없으면 503 NOT_READY. '물어보기'는 주입된 Answerer가 기록(state·answers·verdicts)만 근거로 설명한다.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from quantpilot.api import queries
from quantpilot.api.auth import require_user
from quantpilot.api.deps import Deps, DepsDep
from quantpilot.api.errors import ApiError
from quantpilot.judgment import AlwaysApprove, State, StubJudge, StubLLM, decide

log = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_user)])


def _calibration(deps: Deps) -> Any:
    if deps.calibration is None:
        raise ApiError(503, "NOT_READY", "보정 리포트 미연결 (judgment/calibration.py)")
    return deps.calibration


@router.get("/judgments/calibration")
async def calibration(
    deps: DepsDep,
    weeks: int = 4,
) -> dict[str, Any]:
    """`{brier, ece, n, buckets, by_provider}`."""
    return await _calibration(deps).calibration(weeks)


@router.get("/judgments/ab")
async def ab(
    deps: DepsDep,
    weeks: int = 4,
) -> dict[str, Any]:
    """게이팅 ON/OFF 비교."""
    return await _calibration(deps).ab(weeks)


@router.get("/judgments")
async def list_judgments(
    deps: DepsDep,
    from_: str | None = Query(None, alias="from"),
    to: str | None = None,
    market: str | None = None,
    strategy: str | None = None,
    outcome: str | None = None,
    symbol: str | None = None,
    limit: int = 50,
    before: int | None = None,
) -> dict[str, Any]:
    """판단 로그 (signals ⋈ judgments ⋈ llm_verdicts) + 기간 안의 규칙 미충족 건수 (ADR 0027)."""
    body = await queries.judgments(
        deps.sessions,
        frm=from_,
        to=to,
        market=market,
        strategy=strategy,
        outcome=outcome,
        symbol=symbol,
        limit=limit,
        before=before,
    )
    body["rule_unmet"] = await queries.rule_unmet(
        deps.sessions, frm=from_, to=to, market=market, strategy=strategy, symbol=symbol
    )
    return body


async def _detail(deps: Deps, judgment_id: int) -> dict[str, Any]:
    d = await queries.judgment_detail(deps.sessions, judgment_id)
    if d is None:
        raise ApiError(404, "NOT_FOUND", f"판단 없음: {judgment_id}")
    return d


@router.get("/judgments/{judgment_id}")
async def get_judgment(
    deps: DepsDep,
    judgment_id: int,
) -> dict[str, Any]:
    """판단 상세."""
    return await _detail(deps, judgment_id)


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)


@router.post("/judgments/{judgment_id}/ask")
async def ask(
    deps: DepsDep,
    judgment_id: int,
    req: AskRequest,
) -> dict[str, Any]:
    """기록을 근거로 한 설명 `{answer, cost_usd}`."""
    d = await _detail(deps, judgment_id)
    context = {
        "judgment": {
            k: d.get(k)
            for k in ("gate", "confidence", "blocks", "provider", "symbol", "strategy", "outcome")
        },
        "state": d.get("state"),
        "answers": d.get("answers"),
        "verdicts": [
            {k: v.get(k) for k in ("model", "approve", "reason")} for v in d.get("verdicts", [])
        ],
    }
    try:
        answer, cost = await deps.answerer.answer(req.question, context)
    except Exception as e:  # noqa: BLE001 — 외부 API 실패는 502
        log.warning("ask failed", extra={"error": type(e).__name__})
        raise ApiError(502, "JUDGE_ERROR", f"답변 실패: {type(e).__name__}") from None
    return {"answer": answer, "cost_usd": cost}


class PreviewRequest(BaseModel):
    state: dict[str, Any]
    gating: bool = True


@router.post("/judge/preview")
async def judge_preview(
    deps: DepsDep,
    req: PreviewRequest,
) -> dict[str, Any]:
    """판단 파이프라인 1회(스텁, 주문 없음). 개발·데모용."""
    try:
        st = State(**req.state)
    except TypeError as e:
        raise ApiError(400, "INVALID_PARAM", str(e)) from None
    jr = StubJudge().judge(st)
    llms = [StubLLM("claude-stub"), StubLLM("gemini-stub")] if req.gating else [AlwaysApprove()]
    verdicts = [m.review(st, jr) for m in llms]
    d = decide(
        jr,
        verdicts,
        hold_below=deps.settings.gate_hold_below,
        full_above=deps.settings.gate_full_above,
    )
    return {
        "state": st.render(),
        "judge": jr.__dict__,
        "verdicts": [v.__dict__ for v in verdicts],
        "decision": {
            "gate": d.gate.value,
            "blocks": d.blocks,
            "size_multiplier": d.size_multiplier,
        },
    }
