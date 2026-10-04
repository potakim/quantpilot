"""03 §2.3 백테스트: 비동기 실행(워커 스레드 1개) + 진행률 WS `backtest:{id}`.

요청 즉시 backtests 행을 만들어 id를 돌려주고(202), 실행이 끝나면 지표·시도 횟수를 채운다.
자산곡선·체결 전체는 `data_dir/backtests/<id>.json`(equity_path)에 둔다.
`unlock_holdout=true`는 전략당 1회 (불변식 #5) — 두 번째 요청은 409 GATE_LOCKED.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from quantpilot.api import queries
from quantpilot.api.auth import require_user
from quantpilot.api.deps import Deps, DepsDep
from quantpilot.api.errors import ApiError
from quantpilot.api.routes.strategies import create_checked
from quantpilot.backtest import AttemptTracker, Backtester, preset
from quantpilot.backtest.attempts import WARN_AFTER
from quantpilot.core.models import Market
from quantpilot.realtime import keys as hk
from quantpilot.realtime.bus import message
from quantpilot.realtime.hub import to_jsonable

log = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_user)])

SOURCES = ("synthetic", "upbit", "yfinance", "fdr")
EQUITY_POINTS = 500
FILLS_TAIL = 200


class BacktestRequest(BaseModel):
    strategy: str
    params: dict[str, Any] = Field(default_factory=dict)
    symbols: list[str] | None = None
    source: str = "synthetic"
    start: str | None = None
    end: str | None = None
    initial_cash: float | None = None
    unlock_holdout: bool = False


def load_data(req: BacktestRequest, strat: Any, cache_dir: Path) -> dict[str, pd.DataFrame]:
    """전략·소스에 맞는 봉 데이터 (블로킹, 워커 스레드에서)."""
    from quantpilot.data import CandleCache, load, synthetic

    symbols = tuple(strat.symbols)
    tf = "5m" if strat.timeframe == "5m" else "1d"
    if req.source == "synthetic":
        if tf == "5m":
            data = {s: synthetic.intraday_5m(synthetic.seed_of(s) % 997, days=60) for s in symbols}
        else:
            data = synthetic.universe(symbols, periods=2000, start="2017-01-01")
    else:
        cache = CandleCache(cache_dir)
        kw = {"start": req.start} if req.start and req.source != "upbit" else {}
        data = {s: load(req.source, s, tf, cache=cache, **kw) for s in symbols}
    if req.start or req.end:
        data = {s: df.loc[req.start : req.end] for s, df in data.items()}
    return data


def run_blocking(req: BacktestRequest, settings: Any) -> dict[str, Any]:
    """백테스트 1회 (블로킹). 결과 dict: summary·equity·fills·attempts."""
    strat = create_checked(req.strategy, req.params)
    if req.symbols:
        strat.symbols = tuple(req.symbols)
    data = load_data(req, strat, settings.cache_dir)
    tf = "5m" if strat.timeframe == "5m" else "1d"
    cash = req.initial_cash or (
        settings.initial_cash_usd if strat.market == Market.US else settings.initial_cash_krw
    )
    bt = Backtester(
        preset(strat.market),
        cash,
        holdout_months=0 if tf == "5m" else settings.holdout_months,
        unlock_holdout=req.unlock_holdout,
        allow_short=bool(strat.params.get("allow_short", False)),
    )
    res = bt.run(strat, data, attempts=AttemptTracker(settings.attempts_file))
    return {
        "metrics": res.metrics.to_dict(),
        "holdout_cutoff": res.holdout_cutoff.date() if res.holdout_cutoff is not None else None,
        "warnings": list(res.warnings),
        "attempts": res.attempts or {},
        "equity": [{"ts": t.isoformat(), "v": float(v)} for t, v in res.equity.items()],
        "fills": [to_jsonable(f) for f in res.fills],
    }


async def _progress(deps: Deps, bid: int, progress: float, stage: str, **extra: Any) -> None:
    state = {
        "status": extra.pop("status", "running"),
        "progress": progress,
        "stage": stage,
        **extra,
    }
    await deps.hub.set(hk.backtest(bid), state)
    data = {"progress": progress, "stage": stage, **extra}
    await deps.hub.publish(hk.backtest_channel(bid), message(None, data))


async def run_job(deps: Deps, pool: Any, bid: int, req: BacktestRequest) -> None:
    """워커 스레드에서 실행하고 행·파일·허브를 갱신한다."""
    from quantpilot.db.models import BacktestRow

    await _progress(deps, bid, 0.1, "running")
    loop = asyncio.get_running_loop()
    try:
        out = await loop.run_in_executor(pool, run_blocking, req, deps.settings)
    except Exception as e:  # noqa: BLE001 — 실패는 상태로 알린다
        log.warning("backtest failed", extra={"strategy": req.strategy, "error": type(e).__name__})
        await _progress(deps, bid, 1.0, "failed", status="failed", done=True, error=str(e))
        return
    path = Path(deps.settings.data_dir) / "backtests" / f"{bid}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    blob = {k: out[k] for k in ("equity", "fills", "warnings", "attempts")}
    path.write_text(json.dumps(blob, ensure_ascii=False, default=str), encoding="utf-8")
    eq = out["equity"]
    async with deps.sessions.begin() as s:
        row = await s.get_one(BacktestRow, bid)
        row.metrics = out["metrics"]
        row.attempt_no = int(out["attempts"].get("distinct_attempts", 0))
        row.holdout_cutoff = out["holdout_cutoff"]
        row.period_start = date.fromisoformat(eq[0]["ts"][:10]) if eq else None
        row.period_end = date.fromisoformat(eq[-1]["ts"][:10]) if eq else None
        row.equity_path = str(path)
    await _progress(deps, bid, 1.0, "done", status="done", done=True)


@router.post("/backtests", status_code=202)
async def create_backtest(
    deps: DepsDep,
    req: BacktestRequest,
    request: Request,
) -> JSONResponse:
    """백테스트 요청 → 202 {id, status:"queued"}."""
    from quantpilot.db.models import BacktestRow

    if req.source not in SOURCES:
        raise ApiError(400, "INVALID_PARAM", f"source는 {SOURCES} 중 하나")
    strat = create_checked(req.strategy, req.params)
    from quantpilot.data.loader import DAILY_ONLY

    if req.source in DAILY_ONLY and strat.timeframe not in (
        "1d",
        "1M",
    ):  # 월간 전략도 일봉으로 돈다
        raise ApiError(
            400,
            "INVALID_PARAM",
            f"{req.source}는 일봉만 제공 — {req.strategy}({strat.timeframe})는 synthetic으로 돌린다",
        )
    if req.unlock_holdout:
        async with deps.sessions() as s:
            used = await s.scalar(
                select(BacktestRow.id).where(
                    BacktestRow.strategy == req.strategy, BacktestRow.unlocked_holdout.is_(True)
                )
            )
        if used is not None:
            raise ApiError(409, "GATE_LOCKED", "홀드아웃 해제는 전략당 1회", {"backtest_id": used})
    row = BacktestRow(
        strategy=req.strategy,
        params=dict(strat.params),
        symbols=list(req.symbols or strat.symbols),
        source=req.source,
        unlocked_holdout=req.unlock_holdout,
        cost_model=dict(preset(strat.market).__dict__),
        metrics={},
        attempt_no=0,
    )
    async with deps.sessions.begin() as s:
        s.add(row)
        await s.flush()
        bid = row.id
    await deps.hub.set(hk.backtest(bid), {"status": "queued", "progress": 0.0})
    app = request.app
    task = asyncio.create_task(run_job(deps, app.state.backtest_pool, bid, req))
    app.state.tasks.add(task)
    task.add_done_callback(app.state.tasks.discard)
    return JSONResponse({"id": bid, "status": "queued"}, status_code=202)


@router.get("/backtests")
async def list_backtests(
    deps: DepsDep,
    strategy: str | None = None,
) -> list[dict[str, Any]]:
    """백테스트 목록 (attempt_no 포함)."""
    return await queries.backtests(deps.sessions, strategy=strategy)


def _drawdown(eq: list[dict[str, Any]]) -> list[dict[str, Any]]:
    peak, out = float("-inf"), []
    for p in eq:
        peak = max(peak, p["v"])
        out.append({"ts": p["ts"], "v": p["v"] / peak - 1 if peak > 0 else 0.0})
    return out


async def _row_and_blob(deps: Deps, bid: int) -> tuple[dict[str, Any], dict[str, Any] | None]:
    row = await queries.backtest(deps.sessions, bid)
    if row is None:
        raise ApiError(404, "NOT_FOUND", f"백테스트 없음: {bid}")
    path = row.get("equity_path")
    blob = (
        json.loads(Path(path).read_text(encoding="utf-8")) if path and Path(path).exists() else None
    )
    return row, blob


@router.get("/backtests/{bid}")
async def get_backtest(
    deps: DepsDep,
    bid: int,
) -> dict[str, Any]:
    """상태·지표·시도 횟수·자산곡선(최대 500점)·낙폭·체결 끝부분."""
    # 허브 상태를 행보다 먼저 읽는다. run_job은 파일·행·허브 순으로 쓰므로
    # 허브가 done이면 행·파일은 이미 채워져 있다 (반대 순서면 done+빈 metrics 가능).
    state = await deps.hub.get(hk.backtest(bid)) or {}
    row, blob = await _row_and_blob(deps, bid)
    status = state.get("status") or ("done" if blob is not None else "unknown")
    out: dict[str, Any] = {
        "id": bid,
        "strategy": row["strategy"],
        "status": status,
        "progress": state.get("progress"),
        "error": state.get("error"),
        "metrics": row["metrics"],
        "cost_model": row["cost_model"],
        "holdout_cutoff": row["holdout_cutoff"],
        "unlocked_holdout": row["unlocked_holdout"],
        "attempt_no": row["attempt_no"],
    }
    if blob is None:
        return out
    eq = blob["equity"]
    step = max(1, -(-len(eq) // EQUITY_POINTS))  # 최대 EQUITY_POINTS점
    att = blob.get("attempts") or {}
    out.update(
        {
            "attempts": {
                "distinct_attempts": att.get("distinct_attempts", row["attempt_no"]),
                "warn_after": att.get("warn_after", WARN_AFTER),
                "overfit_warning": bool(att.get("overfit_warning", False)),
            },
            "warnings": blob.get("warnings", []),
            "equity": eq[::step],
            "drawdown": _drawdown(eq)[::step],
            "fills_tail": blob["fills"][-FILLS_TAIL:],
        }
    )
    return out


@router.get("/backtests/{bid}/report.csv")
async def backtest_csv(
    deps: DepsDep,
    bid: int,
) -> Response:
    """체결 전체 CSV."""
    _, blob = await _row_and_blob(deps, bid)
    if blob is None:
        raise ApiError(409, "NOT_READY", "백테스트가 아직 끝나지 않았다")
    buf = io.StringIO()
    cols = ["ts", "symbol", "side", "qty", "price", "fee", "tax", "strategy", "reason", "order_id"]
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    for f in blob["fills"]:
        w.writerow(f)
    return Response(
        buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="backtest-{bid}.csv"'},
    )
