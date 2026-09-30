"""엔진이 죽었을 때 scheduler가 직접 쓰는 계좌·TickRunner (01 §2 "시간 청산 이중화").

DB의 페이퍼 계좌(PersistentPaperBroker)를 되살리고 최신 봉 종가로 시세를 맞춘다.
청산은 TickRunner.on_time_exit → OrderExecutor(RiskManager.check → broker.submit)로만 나간다 (불변식 #9).
"""

from __future__ import annotations

import logging
from datetime import timedelta

from quantpilot.backtest.costs import preset
from quantpilot.config import settings
from quantpilot.core.clock import MarketClock
from quantpilot.core.models import Market
from quantpilot.db.repo import (
    Sessions,
    SqlCandleRepo,
    SqlConfigRepo,
    SqlLedger,
    SqlPositionRepo,
    SqlSignalRepo,
)
from quantpilot.engine.replay import StubFeatureBuilder
from quantpilot.engine.tick import TickRunner
from quantpilot.execution.executor import OrderExecutor
from quantpilot.execution.persistent_paper import PersistentPaperBroker
from quantpilot.execution.ratelimit import NoLimiter
from quantpilot.execution.risk import RiskManager
from quantpilot.judgment.stub import StubPipeline
from quantpilot.scheduler.context import AccountSource, BackupFactory, LogBus
from quantpilot.strategies import create

log = logging.getLogger(__name__)


def _initial_cash(market: Market) -> float:
    return settings.initial_cash_usd if Market(market) == Market.US else settings.initial_cash_krw


async def last_close(candles: SqlCandleRepo, market: Market, symbol: str) -> float | None:
    """가장 최근 1분봉 종가 (최근 1일 안). 없으면 None."""
    now = MarketClock(market).now()
    for back in (timedelta(minutes=10), timedelta(days=1)):
        bars = await candles.load(market, symbol, "1m", now - back, now + timedelta(minutes=1))
        if bars:
            return bars[-1].close
    return None


async def restore_account(sessions: Sessions, market: Market) -> PersistentPaperBroker:
    """DB의 페이퍼 계좌를 되살리고 최신 종가(없으면 평균단가)로 시세를 맞춘다."""
    if not settings.paper:
        raise RuntimeError("백업 청산은 settings.paper=True에서만 (실계좌 배선 전)")
    market = Market(market)
    broker = PersistentPaperBroker(
        market,
        preset(market),
        _initial_cash(market),
        positions=SqlPositionRepo(sessions),
        config=SqlConfigRepo(sessions),
    )
    await broker.restore()
    candles = SqlCandleRepo(sessions)
    now = MarketClock(market).now()
    for sym, pos in broker.positions().items():
        px = await last_close(candles, market, sym)
        if px is None:
            log.warning("최신 종가 없음, 평균단가로 평가", extra={"symbol": sym})
            px = pos.avg_price
        broker.on_price(sym, px, now)
    return broker


def backup_factory(sessions: Sessions) -> BackupFactory:
    """(market, strategy) → 그 전략 하나만 든 백업 TickRunner를 만드는 함수."""

    async def build(market: Market, strategy: str) -> TickRunner:
        broker = await restore_account(sessions, market)
        risk = RiskManager()
        executor = OrderExecutor(
            broker, risk, SqlLedger(sessions), NoLimiter(), signals=SqlSignalRepo(sessions)
        )
        return TickRunner(
            market,
            [create(strategy)],
            StubFeatureBuilder(Market(market).value),
            StubPipeline(),
            executor,
            risk,
            MarketClock(market),
            LogBus(),
            cost=preset(market),
        )

    return build


def account_source(sessions: Sessions) -> AccountSource:
    """market → (현금, 평가액). DB 계좌 기준."""

    async def read(market: Market) -> tuple[float, float]:
        broker = await restore_account(sessions, market)
        return broker.cash(), broker.equity()

    return read
