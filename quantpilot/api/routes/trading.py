"""03 §2.4 거래·포지션.

api는 브로커를 들고 있지 않다. 수동 주문·청산·취소는 RiskManager 사전 검사 뒤 허브 큐
(`q:orders:<market>`)로 엔진에 넘기고, 엔진이 OrderExecutor(= RiskManager.check → BrokerAdapter.submit)로
실행한다 (불변식 #9, ADR 0017 §1). 사전 검사에서 거부되면 큐에 넣지 않는다.
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import datetime, timedelta
from typing import Any, Literal
from uuid import uuid4

import pandas as pd
from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from quantpilot.api import metrics, queries
from quantpilot.api.auth import require_user
from quantpilot.api.deps import Deps, DepsDep
from quantpilot.api.errors import ApiError
from quantpilot.api.routes.system import halted_map, link_of
from quantpilot.backtest.costs import preset
from quantpilot.core import clock
from quantpilot.core.models import Market, Order, OrderType, Side
from quantpilot.execution.risk import RiskDecision, RiskManager, RiskRules
from quantpilot.realtime import keys as hk

log = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_user)])

TF_SPAN = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "1d": 1440}


def _market(m: str) -> Market:
    try:
        return Market(m)
    except ValueError:
        raise ApiError(400, "INVALID_PARAM", f"알 수 없는 시장: {m}") from None


async def price_of(deps: Deps, market: Market, symbol: str) -> float | None:
    """현재가: 허브 px 키 → 최근 1분봉 종가 → 없으면 None."""
    px = await deps.hub.get(hk.px(market, symbol))
    if px is not None:
        return float(px)
    from quantpilot.db.repo import SqlCandleRepo
    from quantpilot.scheduler.backup import last_close

    return await last_close(SqlCandleRepo(deps.sessions), market, symbol)


async def month_start(deps: Deps, market: Market) -> dict[str, Any] | None:
    """scheduler month_roll이 저장한 월초 평가액."""
    from quantpilot.db.repo import SqlConfigRepo
    from quantpilot.scheduler.jobs.health import month_start_key

    return await SqlConfigRepo(deps.sessions).get_setting(month_start_key(market))


async def risk_manager(deps: Deps, market: Market) -> RiskManager:
    """사전 검사용 RiskManager: 엔진과 같은 코드 상수 규칙 + 월초 평가액 + 할트 상태."""
    now = deps.utcnow()
    risk = RiskManager()
    ms = await month_start(deps, market)
    if ms and ms.get("month") == f"{now.year:04d}-{now.month:02d}":
        risk.month_start_equity = float(ms["equity"])
        risk._month = (now.year, now.month)
    halt = await link_of(deps).halt_reason(market)
    if halt:
        risk.halted_reason = halt
    return risk


def _account_equity(acct: Any) -> float:
    try:
        return float(acct.equity())
    except KeyError:
        return float(acct.cash())


@router.get("/portfolio")
async def portfolio(
    deps: DepsDep,
) -> dict[str, Any]:
    """시장별 현금·평가액·포지션·오늘 손익, 월 손익, 월 한도, 할트."""
    from quantpilot.db.repo import SqlConfigRepo, SqlOpsRepo

    ops = SqlOpsRepo(deps.sessions)
    now = deps.utcnow()
    by: dict[str, Any] = {}
    month_pnl: dict[str, float | None] = {}
    for m in Market:
        acct = await deps.account(m)
        eq = _account_equity(acct)
        since = metrics.day_start(now, m)
        by[m.value] = {
            "cash": float(acct.cash()),
            "equity": eq,
            "positions": [queries_position(p) for p in acct.positions().values()],
            "today_pnl": metrics.today_pnl(eq, await ops.equity_snapshots_since(m, since)),
        }
        ms = await month_start(deps, m)
        month_pnl[m.value] = eq / float(ms["equity"]) - 1 if ms and ms.get("equity") else None
    fx = await deps.hub.get("fx:usdkrw") or await SqlConfigRepo(deps.sessions).get_setting(
        "fx.usdkrw"
    )
    krw = by["upbit"]["equity"] + by["krx"]["equity"]
    total = krw + by["us"]["equity"] * float(fx) if fx else None
    return {
        "total_equity_krw": total if total is not None else krw,
        "today_pnl_krw": _today_pnl_krw(by, fx),
        "total_includes_us": fx is not None,
        "fx": {"usdkrw": fx, "source": "hub fx:usdkrw / settings fx.usdkrw" if fx else None},
        "by_market": by,
        "month_pnl": month_pnl,
        "month_limit": RiskRules().monthly_loss_limit,
        "halted": await halted_map(deps),
    }


def _today_pnl_krw(by: dict[str, Any], fx: Any) -> float | None:
    """시장별 오늘 손익 합(원). 미국은 환율이 있을 때만 더한다. 하나도 없으면 None."""
    parts = []
    for m, rate in (("upbit", 1.0), ("krx", 1.0), ("us", float(fx) if fx else None)):
        t = by[m]["today_pnl"]
        if t is not None and rate is not None:
            parts.append(t["amount"] * rate)
    return sum(parts) if parts else None


@router.get("/portfolio/equity")
async def portfolio_equity(
    deps: DepsDep,
    market: str = "upbit",
    days: int = 30,
) -> dict[str, Any]:
    """자산 곡선(equity_snapshots, 1시간/하루 묶음) + BTC 보유 벤치마크 (ADR 0020 §2)."""
    from quantpilot.db.repo import SqlCandleRepo, SqlOpsRepo

    m = _market(market)
    if days not in metrics.EQUITY_DAYS:
        raise ApiError(400, "INVALID_PARAM", f"days는 {list(metrics.EQUITY_DAYS)} 중 하나")
    end = clock.to_local(deps.utcnow(), m)
    start = end - timedelta(days=days)
    snaps = await SqlOpsRepo(deps.sessions).equity_snapshots_since(m, start, end)
    points = metrics.downsample(snaps, days)
    bench = None
    sym = metrics.BENCHMARK_SYMBOL.get(m)
    if sym and not points.empty:
        repo = SqlCandleRepo(deps.sessions)
        bars = await repo.load(m, sym, "1m", start - timedelta(days=1), end)
        closes = pd.Series([b.close for b in bars], index=pd.DatetimeIndex([b.ts for b in bars]))
        vals = metrics.benchmark(points, closes) if bars else None
        if vals is not None:
            bench = metrics.series_points(pd.Series(vals, index=points.index), m)
    return {
        "market": m.value,
        "days": days,
        "points": metrics.series_points(points, m),
        "benchmark": bench,
        "source": "equity_snapshots",
    }


def queries_position(p: Any, price: float | None = None) -> dict[str, Any]:
    """Position → 응답 dict (현재가가 있으면 미실현 손익)."""
    return {
        "market": p.market.value if p.market else None,
        "symbol": p.symbol,
        "strategy": p.strategy,
        "qty": p.qty,
        "avg_price": p.avg_price,
        "stop": p.stop,
        "opened_at": queries.iso(p.opened_at),
        "price": price,
        "unrealized": p.unrealized_pnl(price) if price is not None else None,
    }


@router.get("/positions")
async def positions(
    deps: DepsDep,
    market: str | None = None,
) -> list[dict]:
    """포지션 목록 + 전략·손절가·미실현."""
    from quantpilot.db.repo import SqlPositionRepo

    repo = SqlPositionRepo(deps.sessions)
    markets = [_market(market)] if market else list(Market)
    out = []
    for m in markets:
        for p in await repo.all(m):
            out.append(queries_position(p, await price_of(deps, m, p.symbol)))
    return out


@router.get("/orders")
async def list_orders(
    deps: DepsDep,
    market: str | None = None,
    status: str | None = None,
    limit: int = 50,
    before: str | None = None,
) -> dict[str, Any]:
    """주문 목록."""
    return await queries.orders(
        deps.sessions, market=market, status=status, limit=limit, before=before
    )


@router.get("/fills")
async def list_fills(
    deps: DepsDep,
    market: str | None = None,
    strategy: str | None = None,
    from_: str | None = Query(None, alias="from"),
    to: str | None = None,
    limit: int = 50,
    before: int | None = None,
) -> dict[str, Any]:
    """체결 원장."""
    return await queries.fills(
        deps.sessions,
        market=market,
        strategy=strategy,
        frm=from_,
        to=to,
        limit=limit,
        before=before,
    )


class OrderRequest(BaseModel):
    market: Market
    symbol: str
    side: Side
    qty: float | None = None
    amount: float | None = None
    type: OrderType = OrderType.MARKET
    limit_price: float | None = None
    stop: float | None = None
    strategy: str = "manual"
    reason: str = ""
    horizon: Literal["intraday", "swing", "long"] = "swing"


async def _precheck_and_queue(
    deps: Deps, order: Order, horizon: str, price: float
) -> tuple[dict[str, Any], RiskDecision]:
    """RiskManager 사전 검사 → 통과면 큐에 넣는다. 거부면 409 HALTED / 422 RISK_REJECTED."""
    market = Market(order.market)
    acct = await deps.account(market)
    risk = await risk_manager(deps, market)
    d = risk.check(
        order,
        equity=_account_equity(acct),
        price=price,
        positions=dict(acct.positions()),
        horizon=horizon,
        now=deps.utcnow(),
    )
    if not d.allowed:
        log.info("manual order rejected", extra={"symbol": order.symbol, "reason": d.reason})
        if d.reason.startswith("halted"):
            raise ApiError(409, "HALTED", d.reason, {"risk": asdict(d)})
        raise ApiError(422, "RISK_REJECTED", d.reason, {"risk": asdict(d)})
    order.qty = d.qty
    item = {
        "id": order.id,
        "market": market.value,
        "symbol": order.symbol,
        "side": order.side.value,
        "qty": order.qty,
        "type": order.type.value,
        "limit_price": order.limit_price,
        "stop": order.stop,
        "strategy": order.strategy,
        "reason": order.reason,
    }
    await deps.hub.push(
        hk.orders_queue(market),
        {
            "op": "submit",
            "order": item,
            "horizon": horizon,
            "risk": asdict(d),
            "queued_at": deps.utcnow().isoformat(),
        },
    )
    log.info("manual order queued", extra={"symbol": order.symbol, "strategy": order.strategy})
    return {**item, "status": "queued"}, d


@router.post("/orders", status_code=201)
async def create_order(
    deps: DepsDep,
    req: OrderRequest,
) -> JSONResponse:
    """수동 주문. 리스크 게이트 통과 시 201 {order, risk}, 거부 시 422."""
    if (req.qty is None) == (req.amount is None):
        raise ApiError(400, "INVALID_PARAM", "qty와 amount 중 정확히 하나")
    if req.type == OrderType.LIMIT and not req.limit_price:
        raise ApiError(400, "INVALID_PARAM", "지정가 주문은 limit_price 필요")
    price = await price_of(deps, req.market, req.symbol)
    if price is None:
        raise ApiError(409, "NO_PRICE", f"시세 없음: {req.symbol}")
    ref = req.limit_price or price
    qty = req.qty if req.qty is not None else req.amount / ref  # type: ignore[operator]
    if qty <= 0:
        raise ApiError(400, "INVALID_PARAM", "수량은 0보다 커야 한다")
    order = Order(
        req.symbol,
        req.side,
        qty,
        req.type,
        req.limit_price,
        req.strategy or "manual",
        req.reason or "manual",
        req.stop,
        id=uuid4().hex[:12],
        market=req.market,
    )
    item, d = await _precheck_and_queue(deps, order, req.horizon, ref)
    return JSONResponse({"order": item, "risk": asdict(d)}, status_code=201)


@router.delete("/orders/{order_id}", status_code=202)
async def cancel_order(
    deps: DepsDep,
    order_id: str,
) -> JSONResponse:
    """미체결 주문 취소 요청 (엔진이 브로커 cancel)."""
    o = await queries.order(deps.sessions, order_id)
    if o is None:
        raise ApiError(404, "NOT_FOUND", f"주문 없음: {order_id}")
    if o["status"] not in ("pending", "partial"):
        raise ApiError(409, "CONFLICT", f"미체결 주문이 아니다: {o['status']}")
    await deps.hub.push(hk.orders_queue(o["market"]), {"op": "cancel", "order_id": order_id})
    return JSONResponse({"order_id": order_id, "status": "cancel_requested"}, status_code=202)


@router.post("/positions/{market}/{symbol}/close", status_code=201)
async def close_position(
    deps: DepsDep,
    market: str,
    symbol: str,
) -> JSONResponse:
    """시장가 청산 (리스크 게이트의 exit 경로 — 할트·비중 규칙과 무관, 불변식 #6)."""
    m = _market(market)
    acct = await deps.account(m)
    pos = acct.positions().get(symbol)
    if pos is None or not pos.is_open:
        raise ApiError(404, "NOT_FOUND", f"열린 포지션 없음: {symbol}")
    price = await price_of(deps, m, symbol)
    if price is None:
        raise ApiError(409, "NO_PRICE", f"시세 없음: {symbol}")
    order = Order(
        symbol,
        Side.SELL,
        float(pos.qty),
        strategy=pos.strategy or "manual",
        reason="manual close",
        market=m,
    )
    item, d = await _precheck_and_queue(deps, order, "swing", price)
    return JSONResponse({"order": item, "risk": asdict(d)}, status_code=201)


@router.get("/quotes/{market}/{symbol}")
async def quote(
    deps: DepsDep,
    market: str,
    symbol: str,
) -> dict[str, Any]:
    """현재가·호가 5단계·전략 상태."""
    m = _market(market)
    price = await price_of(deps, m, symbol)
    if price is None:
        raise ApiError(409, "NO_PRICE", f"시세 없음: {symbol}")
    cost = preset(m)
    return {
        "market": m.value,
        "symbol": symbol,
        "price": price,
        "orderbook": await deps.hub.get(hk.ob(m, symbol)),
        "strategy": await deps.hub.get(hk.strategy_state(m, symbol)),
        "fee_rate": cost.fee_rate,
        "tax_rate_sell": cost.sell_tax_rate,
    }


@router.get("/candles/{market}/{symbol}")
async def candles(
    deps: DepsDep,
    market: str,
    symbol: str,
    tf: str = "5m",
    limit: int = 500,
    before: str | None = None,
) -> list[dict[str, Any]]:
    """캔들 (시각은 UTC). 요청 tf가 없으면 1분봉을 합쳐서 만든다."""
    from quantpilot.db.repo import SqlCandleRepo

    m = _market(market)
    if tf not in TF_SPAN:
        raise ApiError(400, "INVALID_PARAM", f"tf는 {sorted(TF_SPAN)} 중 하나")
    limit = queries.clamp(limit)
    end_utc = queries.parse_ts(before) or deps.utcnow()
    end = clock.to_local(end_utc, m)
    repo = SqlCandleRepo(deps.sessions)
    bars: list[Any] = []
    for widen in (1, 8, 64, 512):  # 빈 구간(휴장·수집 공백)이 있으면 창을 넓힌다
        start = end - timedelta(minutes=TF_SPAN[tf] * limit * widen)
        bars = await repo.load(m, symbol, tf, start, end)
        if not bars and tf != "1m":
            bars = _resample(await repo.load(m, symbol, "1m", start, end), tf)
        if len(bars) >= limit:
            break
    return [
        {
            "ts": queries.iso(clock.to_utc(b.ts, m)) if isinstance(b.ts, datetime) else b.ts,
            "o": b.open,
            "h": b.high,
            "l": b.low,
            "c": b.close,
            "v": b.volume,
        }
        for b in bars[-limit:]
    ]


def _resample(bars: list[Any], tf: str) -> list[Any]:
    from quantpilot.core.events import BarClosed

    if not bars:
        return []
    df = pd.DataFrame(
        [(b.ts, b.open, b.high, b.low, b.close, b.volume) for b in bars],
        columns=["ts", "open", "high", "low", "close", "volume"],
    ).set_index("ts")
    rule = {"5m": "5min", "15m": "15min", "1h": "1h", "1d": "1D"}[tf]
    agg = (
        df.resample(rule, label="left", closed="left")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        .dropna()
    )
    b0 = bars[0]
    return [
        BarClosed(b0.market, b0.symbol, tf, ts.to_pydatetime(), *map(float, row))
        for ts, row in agg.iterrows()
    ]
