"""FastAPI 앱. 0단계 라우트: 헬스, 전략 목록·스키마, 백테스트 실행, 페이퍼 브로커 상태·주문, AI 판단 미리보기.

실행: uvicorn quantpilot.api.app:app --reload
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from quantpilot import __version__
from quantpilot.backtest import AttemptTracker, Backtester, preset
from quantpilot.config import settings
from quantpilot.core.models import Market, Order, OrderType, Side
from quantpilot.data import CandleCache, load, synthetic
from quantpilot.execution import PaperBroker, RiskManager
from quantpilot.judgment import AlwaysApprove, State, StubJudge, StubLLM, decide
from quantpilot.strategies import REGISTRY, create

app = FastAPI(
    title="QuantPilot",
    version=__version__,
    description="코드가 계산하고, 모델은 판단하고, 코드가 실행한다",
)

# 0단계: 프로세스 내 단일 페이퍼 브로커 (1단계에서 DB·Redis로 이동)
_paper = {
    Market.UPBIT: PaperBroker(Market.UPBIT, preset("upbit"), settings.initial_cash_krw),
    Market.KRX: PaperBroker(Market.KRX, preset("krx"), settings.initial_cash_krw),
    Market.US: PaperBroker(Market.US, preset("us"), settings.initial_cash_usd),
}
_risk = RiskManager()
_cache = CandleCache(settings.cache_dir)


@app.get("/health")
def health() -> dict:
    return {"ok": True, "version": __version__, "paper": settings.paper, "env": settings.env}


# ---------- 전략 ----------
@app.get("/strategies")
def strategies() -> list[dict]:
    return [cls().describe() for cls in REGISTRY.values()]


@app.get("/strategies/{name}")
def strategy_detail(name: str) -> dict:
    if name not in REGISTRY:
        raise HTTPException(404, f"unknown strategy {name}")
    return REGISTRY[name]().describe()


# ---------- 백테스트 ----------
class BacktestRequest(BaseModel):
    strategy: str
    params: dict[str, Any] = Field(default_factory=dict)
    source: str = "synthetic"  # synthetic | upbit | yfinance | fdr
    symbols: list[str] | None = None
    start: str | None = None
    initial_cash: float | None = None
    holdout_months: int | None = None
    unlock_holdout: bool = False  # 실전 전환 직전 1회만
    refresh: bool = False


@app.post("/backtest")
def backtest(req: BacktestRequest) -> dict:
    if req.strategy not in REGISTRY:
        raise HTTPException(404, f"unknown strategy {req.strategy}")
    try:
        strat = create(req.strategy, **req.params)
    except ValueError as e:
        raise HTTPException(422, str(e))
    symbols = tuple(req.symbols or strat.symbols)
    strat.symbols = symbols
    tf = "5m" if strat.timeframe == "5m" else "1d"
    if req.source == "synthetic":
        data = (
            {s: synthetic.intraday_5m(synthetic.seed_of(s) % 997, days=60) for s in symbols}
            if tf == "5m"
            else synthetic.universe(symbols, periods=2000, start=req.start or "2017-01-01")
        )
    else:
        try:
            data = {
                s: load(
                    req.source,
                    s,
                    tf,
                    cache=_cache,
                    refresh=req.refresh,
                    **({"start": req.start} if req.start and req.source != "upbit" else {}),
                )
                for s in symbols
            }
        except Exception as e:  # noqa: BLE001 — 네트워크·미설치 라이브러리
            raise HTTPException(502, f"data load failed: {e}")
    cash = req.initial_cash or (
        settings.initial_cash_usd if strat.market == Market.US else settings.initial_cash_krw
    )
    hold = (
        req.holdout_months
        if req.holdout_months is not None
        else (0 if tf == "5m" else settings.holdout_months)
    )
    bt = Backtester(
        preset(strat.market),
        cash,
        holdout_months=hold,
        unlock_holdout=req.unlock_holdout,
        allow_short=bool(strat.params.get("allow_short", False)),
    )
    res = bt.run(strat, data, attempts=AttemptTracker(settings.attempts_file))
    eq = res.equity
    step = max(1, len(eq) // 500)
    return {
        **res.summary(),
        "cost_model": preset(strat.market).__dict__,
        "equity": [{"ts": str(t), "v": float(v)} for t, v in eq.iloc[::step].items()],
        "fills": [
            f.__dict__ | {"side": f.side.value, "ts": f.ts.isoformat()} for f in res.fills[-200:]
        ],
    }


# ---------- 페이퍼 브로커 ----------
class OrderRequest(BaseModel):
    market: Market
    symbol: str
    side: Side
    qty: float
    type: OrderType = OrderType.MARKET
    limit_price: float | None = None
    stop: float | None = None
    strategy: str = "manual"
    reason: str = ""
    horizon: str = "swing"


class PriceUpdate(BaseModel):
    market: Market
    symbol: str
    price: float


@app.post("/paper/price")
def paper_price(p: PriceUpdate) -> dict:
    fills = _paper[p.market].on_price(p.symbol, p.price)
    return {"filled": [f.__dict__ | {"side": f.side.value} for f in fills]}


@app.post("/paper/order")
def paper_order(req: OrderRequest) -> dict:
    broker = _paper[req.market]
    try:
        price = broker.last_price(req.symbol)
    except KeyError as e:
        raise HTTPException(409, str(e))
    order = Order(
        req.symbol, req.side, req.qty, req.type, req.limit_price, req.strategy, req.reason, req.stop
    )
    d = _risk.check(
        order,
        equity=broker.equity(),
        price=price,
        positions=dict(broker.positions()),
        horizon=req.horizon,
    )
    if not d.allowed:
        return {"accepted": False, "risk": d.__dict__}
    order.qty = d.qty
    result = broker.submit(order)
    return {
        "accepted": True,
        "risk": d.__dict__,
        "result": result.__dict__
        | ({"side": result.side.value} if hasattr(result, "side") else {}),
    }


@app.get("/paper/{market}")
def paper_state(market: Market) -> dict:
    b = _paper[market]
    try:
        equity = b.equity()
    except KeyError:
        equity = b.cash()
    return {
        "market": market.value,
        "cash": b.cash(),
        "equity": equity,
        "positions": {s: p.__dict__ for s, p in b.positions().items()},
        "pending": [o.__dict__ for o in b.pending()],
        "ledger_tail": [f.__dict__ | {"side": f.side.value} for f in b.ledger[-50:]],
        "risk": {"halted": _risk.halted_reason, "monthly_pnl": _risk.monthly_pnl(equity)},
    }


# ---------- AI 판단 미리보기 ----------
class JudgeRequest(BaseModel):
    state: dict
    gating: bool = True


@app.post("/judge/preview")
def judge_preview(req: JudgeRequest) -> dict:
    st = State(**req.state)
    jr = StubJudge().judge(st)
    llms = [StubLLM("claude-stub"), StubLLM("gemini-stub")] if req.gating else [AlwaysApprove()]
    verdicts = [m.review(st, jr) for m in llms]
    d = decide(
        jr, verdicts, hold_below=settings.gate_hold_below, full_above=settings.gate_full_above
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
