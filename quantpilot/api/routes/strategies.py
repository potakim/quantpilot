"""03 §2.2 전략: 목록·단일·설정 변경·기본값 복원·실전/페이퍼 전환.

전략 코드(REGISTRY)와 DB 설정(strategy_configs)을 합쳐 보여 준다. 설정 행이 없으면 기본값·비활성.
allocation 합 ≤ 1, intraday 합 ≤ 0.2 (RiskRules.max_intraday_weight와 같은 값, 더 느슨하게 못 둔다).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from quantpilot.api import gates
from quantpilot.api.auth import require_user
from quantpilot.api.deps import Deps, DepsDep
from quantpilot.api.errors import ApiError
from quantpilot.execution.risk import RiskRules
from quantpilot.strategies import REGISTRY, create
from quantpilot.strategies.base import Strategy

log = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_user)])


def _cls(name: str) -> type[Strategy]:
    if name not in REGISTRY:
        raise ApiError(404, "NOT_FOUND", f"전략 없음: {name}")
    return REGISTRY[name]


async def _rows(deps: Deps) -> dict[str, dict[str, Any]]:
    from quantpilot.db.repo import SqlConfigRepo

    return {r["name"]: r for r in await SqlConfigRepo(deps.sessions).strategies()}


def _instance(cls: type[Strategy], params: dict[str, Any]) -> Strategy:
    try:
        return cls(**params)
    except ValueError as e:
        raise ApiError(400, "INVALID_PARAM", str(e), {"params": params}) from None


async def _view(deps: Deps, name: str, row: dict[str, Any] | None) -> dict[str, Any]:
    from quantpilot.db.repo import SqlPositionRepo

    cls = _cls(name)
    row = row or {}
    strat = _instance(cls, row.get("params") or {})
    d = strat.describe()
    positions = [
        p for p in await SqlPositionRepo(deps.sessions).all(cls.market) if p.strategy == name
    ]
    g1 = await gates.g1_for(deps, name)
    d.update(
        {
            "symbols": list(row.get("symbols") or d["symbols"]),
            "enabled": bool(row.get("enabled", False)),
            "allocation": float(row.get("allocation") or 0.0),
            "paper": bool(row.get("paper", True)),
            "status": {
                "position": {p.symbol: p.qty for p in positions},
                "month_pnl": None,  # 전략별 손익 집계는 리포트(P1-11) 몫
                "mdd_30d": None,
            },
            "gate": {"g1": g1},
        }
    )
    return d


@router.get("/strategies")
async def list_strategies(
    deps: DepsDep,
) -> list[dict[str, Any]]:
    """등록 전략 + 설정 병합."""
    rows = await _rows(deps)
    return [await _view(deps, n, rows.get(n)) for n in REGISTRY]


@router.get("/strategies/{name}")
async def get_strategy(
    deps: DepsDep,
    name: str,
) -> dict[str, Any]:
    """단일 전략."""
    _cls(name)
    return await _view(deps, name, (await _rows(deps)).get(name))


class StrategyPatch(BaseModel):
    enabled: bool | None = None
    params: dict[str, Any] | None = None
    allocation: float | None = None
    symbols: list[str] | None = None


async def _save(deps: Deps, name: str, row: dict[str, Any], **changes: Any) -> None:
    from quantpilot.db.repo import SqlConfigRepo

    cls = _cls(name)
    merged = {
        "allocation": float(row.get("allocation") or 0.0),
        "symbols": list(row.get("symbols") or cls.symbols),
        "params": dict(row.get("params") or {}),
        "enabled": bool(row.get("enabled", False)),
        "paper": bool(row.get("paper", True)),
        **changes,
    }
    await SqlConfigRepo(deps.sessions).upsert_strategy(name=name, market=cls.market, **merged)


@router.patch("/strategies/{name}")
async def patch_strategy(
    deps: DepsDep,
    name: str,
    req: StrategyPatch,
) -> dict[str, Any]:
    """enabled·params·allocation·symbols 변경 (ParamSpec 검증, 배분 합 검사)."""
    cls = _cls(name)
    rows = await _rows(deps)
    row = rows.get(name) or {}
    changes: dict[str, Any] = {}
    if req.params is not None:
        params = {**(row.get("params") or {}), **req.params}
        _instance(cls, params)
        changes["params"] = params
    if req.symbols is not None:
        if not req.symbols or not all(isinstance(s, str) and s for s in req.symbols):
            raise ApiError(400, "INVALID_PARAM", "symbols는 비어 있지 않은 문자열 목록")
        changes["symbols"] = list(req.symbols)
    if req.enabled is not None:
        changes["enabled"] = req.enabled
    if req.allocation is not None:
        if not 0.0 <= req.allocation <= 1.0:
            raise ApiError(400, "INVALID_PARAM", "allocation은 0~1")
        alloc = {n: float(r.get("allocation") or 0.0) for n, r in rows.items() if n in REGISTRY}
        alloc[name] = req.allocation
        if sum(alloc.values()) > 1.0 + 1e-9:
            raise ApiError(
                400, "INVALID_PARAM", "allocation 합이 1을 넘는다", {"sum": sum(alloc.values())}
            )
        intraday = sum(v for n, v in alloc.items() if REGISTRY[n].horizon == "intraday")
        if intraday > RiskRules().max_intraday_weight + 1e-9:
            raise ApiError(400, "INVALID_PARAM", "intraday 전략 allocation 합이 0.2를 넘는다")
        changes["allocation"] = req.allocation
    if not changes:
        raise ApiError(400, "INVALID_PARAM", "바꿀 값이 없다")
    await _save(deps, name, row, **changes)
    log.info("strategy patched", extra={"strategy": name, "fields": sorted(changes)})
    return await _view(deps, name, (await _rows(deps)).get(name))


@router.post("/strategies/{name}/reset-params")
async def reset_params(
    deps: DepsDep,
    name: str,
) -> dict[str, Any]:
    """파라미터 기본값 복원."""
    _cls(name)
    await _save(deps, name, (await _rows(deps)).get(name) or {}, params={})
    return await _view(deps, name, (await _rows(deps)).get(name))


class GoLiveRequest(BaseModel):
    confirm_password: str | None = None


@router.post("/strategies/{name}/go-live")
async def go_live(
    deps: DepsDep,
    name: str,
    req: GoLiveRequest | None = None,
) -> dict[str, Any]:
    """페이퍼 → 실전. 관문 미통과 409, 통과 시 비밀번호 재확인(403) 후 전환."""
    _cls(name)
    missing = await gates.go_live_missing(deps, name)
    if missing:
        raise ApiError(409, "GATE_LOCKED", "관문 미통과", {"missing": missing})
    pw = req.confirm_password if req else None
    if not pw or not deps.auth.check_password(pw):
        raise ApiError(403, "CONFIRMATION_REQUIRED", "confirm_password를 다시 보내야 한다")
    await _save(deps, name, (await _rows(deps)).get(name) or {}, paper=False)
    log.warning("strategy go-live", extra={"strategy": name})
    return await _view(deps, name, (await _rows(deps)).get(name))


@router.post("/strategies/{name}/go-paper")
async def go_paper(
    deps: DepsDep,
    name: str,
) -> dict[str, Any]:
    """실전 → 페이퍼 (즉시, 확인 없음)."""
    _cls(name)
    await _save(deps, name, (await _rows(deps)).get(name) or {}, paper=True)
    log.info("strategy go-paper", extra={"strategy": name})
    return await _view(deps, name, (await _rows(deps)).get(name))


def create_checked(name: str, params: dict[str, Any]) -> Strategy:
    """이름·파라미터로 전략을 만든다 (404·400)."""
    _cls(name)
    try:
        return create(name, **params)
    except ValueError as e:
        raise ApiError(400, "INVALID_PARAM", str(e)) from None
