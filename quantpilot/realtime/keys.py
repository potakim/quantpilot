"""허브 키·채널 이름 (02 §2). 엔진·API가 같은 이름을 쓰도록 여기서만 만든다."""

from __future__ import annotations

from quantpilot.core.models import Market


def _m(market: Market | str) -> str:
    return Market(market).value


def px(market: Market | str, symbol: str) -> str:
    """마지막 체결가 키 (TTL 60s)."""
    return f"px:{_m(market)}:{symbol}"


def ob(market: Market | str, symbol: str) -> str:
    """최우선 호가 5단계 키 (TTL 10s)."""
    return f"ob:{_m(market)}:{symbol}"


def eq(market: Market | str) -> str:
    """현재 평가액 키."""
    return f"eq:{_m(market)}"


def feed(market: Market | str) -> str:
    """시세 웹소켓 연결 상태 키 (엔진이 체결을 받을 때 TTL 60s로 갱신)."""
    return f"feed:{_m(market)}"


def strategy_state(market: Market | str, symbol: str) -> str:
    """전략 상태(목표가·이평 스코어 등) 키."""
    return f"st:{_m(market)}:{symbol}"


def orders_queue(market: Market | str) -> str:
    """수동 주문 큐 (ADR 0017 §1)."""
    return f"q:orders:{_m(market)}"


def backtest(job_id: int | str) -> str:
    """백테스트 진행 상태 키."""
    return f"bt:{job_id}"


def ticks_channel(market: Market | str, symbol: str) -> str:
    """시세 WS 채널."""
    return f"ticks:{_m(market)}:{symbol}"


def orderbook_channel(market: Market | str, symbol: str) -> str:
    """호가 WS 채널."""
    return f"orderbook:{_m(market)}:{symbol}"


def backtest_channel(job_id: int | str) -> str:
    """백테스트 진행 WS 채널."""
    return f"backtest:{job_id}"
