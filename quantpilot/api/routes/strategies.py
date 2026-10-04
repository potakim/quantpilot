"""03 §2.2 전략: 목록·단일·설정 변경·기본값 복원·실전/페이퍼 전환.

전략 코드(REGISTRY)와 DB 설정(strategy_configs)을 합쳐 보여 준다. 설정 행이 없으면 권장 조합 기본값
(`strategies.DEFAULT_CONFIG`, ADR 0032) — 엔진도 같은 값을 쓴다.
allocation 합 ≤ 1, intraday 합 ≤ 0.2 (RiskRules.max_intraday_weight와 같은 값, 더 느슨하게 못 둔다).
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from quantpilot.api import gates, metrics
from quantpilot.api.auth import require_user
from quantpilot.api.deps import Deps, DepsDep
from quantpilot.api.errors import ApiError
from quantpilot.backtest.costs import preset
from quantpilot.core import clock
from quantpilot.core.models import Market
from quantpilot.execution.risk import RiskRules
from quantpilot.strategies import REGISTRY, create, strategy_config
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


async def _market_stats(
    deps: Deps, market: Market, rows: dict[str, dict[str, Any]]
) -> dict[str, dict[str, float | None]]:
    """시장 하나의 전략별 `{month_pnl, mdd_30d}` (ADR 0020 §3). 체결 조회 1회, 심볼별 봉 조회 1회."""
    from quantpilot.api.routes.trading import month_start
    from quantpilot.db.repo import SqlCandleRepo, SqlLedger

    now_utc = deps.utcnow()
    now = clock.to_local(now_utc, market)
    fills = await SqlLedger(deps.sessions).fills(market)  # 섀도 원장 제외 (shadow=False)
    if not fills:
        return {}
    month0 = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    start = min(min(f.ts for f in fills), now - timedelta(days=30), month0)
    candles = SqlCandleRepo(deps.sessions)
    prices = {}
    for sym in sorted({f.symbol for f in fills}):
        closes = metrics.hourly_closes(await candles.load(market, sym, "1m", start, now))
        if not closes.empty:
            prices[sym] = closes
    ms = await month_start(deps, market)
    if ms and ms.get("month") == f"{now_utc.year:04d}-{now_utc.month:02d}" and ms.get("equity"):
        base = float(ms["equity"])
    else:
        s = deps.settings
        base = float(s.initial_cash_usd if market == Market.US else s.initial_cash_krw)
    by: dict[str, list[Any]] = {}
    for f in fills:
        by.setdefault(f.strategy, []).append(f)
    out = {}
    for name, fs in by.items():
        alloc = strategy_config(name, rows.get(name))["allocation"] if name in REGISTRY else 0.0
        out[name] = metrics.strategy_stats(fs, prices, alloc * base, month_start=month0, now=now)
    return out


async def _view(
    deps: Deps,
    name: str,
    row: dict[str, Any] | None,
    stats: dict[str, dict[str, float | None]] | None = None,
) -> dict[str, Any]:
    from quantpilot.db.repo import SqlPositionRepo

    cls = _cls(name)
    if stats is None:
        stats = await _market_stats(deps, cls.market, {name: row or {}})
    st = stats.get(name) or {}
    cfg = strategy_config(name, row)
    strat = _instance(cls, cfg["params"])
    d = strat.describe()
    positions = [
        p for p in await SqlPositionRepo(deps.sessions).all(cls.market) if p.strategy == name
    ]
    g1 = await gates.g1_for(deps, name)
    d.update(
        {
            "symbols": cfg["symbols"],
            "enabled": cfg["enabled"],
            "allocation": cfg["allocation"],
            "paper": cfg["paper"],
            # 백테스터·PaperBroker가 쓰는 비용 모델 — 실행 전 화면 표시용 (ADR 0033, 불변식 #4)
            "cost_model": dict(preset(cls.market).__dict__),
            "status": {
                "position": {p.symbol: p.qty for p in positions},
                "month_pnl": st.get("month_pnl"),
                "mdd_30d": st.get("mdd_30d"),
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
    stats: dict[Market, dict[str, dict[str, float | None]]] = {}
    out = []
    for n in REGISTRY:
        m = REGISTRY[n].market
        if m not in stats:
            stats[m] = await _market_stats(deps, m, rows)
        out.append(await _view(deps, n, rows.get(n), stats[m]))
    return out


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
    merged = {**strategy_config(name, row), **changes}
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
        alloc = {n: strategy_config(n, rows.get(n))["allocation"] for n in REGISTRY}
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
