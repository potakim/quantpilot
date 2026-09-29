"""리스크 매니저 — 모든 주문이 브로커에 닿기 전에 통과해야 하는 관문. AI는 여기에 접근할 수 없다.

규칙 (코드 상수 + 설정, 화면·AI가 바꿀 수 없음)
- 거래당 최대 손실 1% (stop이 있으면 수량을 stop 거리로 계산)
- 월 누적 손실 −5% 도달 시 신규 진입 중단 (청산은 항상 허용)
- 종목당 최대 비중 25%
- 단타(intraday) 전략 합산 상한 20%
- 자전거래·허수주문 방지: 같은 종목에 반대 방향 미체결 주문이 있으면 거부, 같은 종목 주문 생성 초당 N건 제한
- API 오류 연속 3회 → 신규 진입 중단 + 알림 플래그
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime

from quantpilot.core.models import Order, Position, Side


@dataclass(frozen=True)
class RiskRules:
    max_loss_per_trade: float = 0.01
    monthly_loss_limit: float = -0.05
    max_symbol_weight: float = 0.25
    max_intraday_weight: float = 0.20
    max_orders_per_symbol_per_sec: int = 2
    max_consecutive_api_errors: int = 3


@dataclass
class RiskDecision:
    allowed: bool
    qty: float
    reason: str = ""
    adjustments: list[str] = field(default_factory=list)


@dataclass
class RiskManager:
    rules: RiskRules = field(default_factory=RiskRules)
    month_start_equity: float | None = None
    _month: tuple[int, int] | None = None
    _consecutive_api_errors: int = 0
    _order_times: dict[str, deque] = field(default_factory=dict)
    _pending_sides: dict[str, set] = field(default_factory=dict)
    halted_reason: str = ""

    # ---- 월 서킷브레이커 ----
    def roll_month(self, equity: float, now: datetime | None = None) -> None:
        now = now or datetime.now(UTC)
        key = (now.year, now.month)
        if key != self._month:
            self._month, self.month_start_equity = key, equity
            if self.halted_reason.startswith("monthly"):
                self.halted_reason = ""

    def monthly_pnl(self, equity: float) -> float:
        if not self.month_start_equity:
            return 0.0
        return equity / self.month_start_equity - 1

    # ---- API 오류 ----
    def api_error(self) -> None:
        self._consecutive_api_errors += 1
        if self._consecutive_api_errors >= self.rules.max_consecutive_api_errors:
            self.halted_reason = f"api_errors:{self._consecutive_api_errors}"

    def api_ok(self) -> None:
        self._consecutive_api_errors = 0
        if self.halted_reason.startswith("api_errors"):
            self.halted_reason = ""

    # ---- 수량 계산 ----
    def size_by_risk(
        self, equity: float, entry: float, stop: float | None, requested_qty: float
    ) -> tuple[float, str]:
        """stop이 있으면 1% 룰로 수량 상한. 없으면 요청 수량 그대로."""
        if stop is None or entry <= 0 or abs(entry - stop) <= 0:
            return requested_qty, ""
        risk_qty = equity * self.rules.max_loss_per_trade / abs(entry - stop)
        if requested_qty > risk_qty:
            return risk_qty, f"1% 룰: {requested_qty:.6g} → {risk_qty:.6g}"
        return requested_qty, ""

    # ---- 주문 검사 ----
    def check(
        self,
        order: Order,
        *,
        equity: float,
        price: float,
        positions: dict[str, Position],
        horizon: str = "swing",
        intraday_exposure: float = 0.0,
        now: datetime | None = None,
    ) -> RiskDecision:
        now = now or datetime.now(UTC)
        self.roll_month(equity, now)
        pos = positions.get(order.symbol)
        is_exit = order.side == Side.SELL and pos is not None and pos.qty > 0
        adj: list[str] = []
        qty = order.qty

        if is_exit:
            # 청산은 서킷브레이커·비중 규칙과 무관하게 항상 허용 (자전거래 검사만)
            return self._wash_check(order, RiskDecision(True, min(qty, pos.qty), "exit"), now)

        if self.halted_reason:
            return RiskDecision(False, 0.0, f"halted:{self.halted_reason}")

        if self.monthly_pnl(equity) <= self.rules.monthly_loss_limit:
            self.halted_reason = f"monthly_loss:{self.monthly_pnl(equity):.2%}"
            return RiskDecision(False, 0.0, self.halted_reason)

        qty, note = self.size_by_risk(equity, price, order.stop, qty)
        if note:
            adj.append(note)

        held_value = pos.qty * price if pos else 0.0
        max_value = self.rules.max_symbol_weight * equity - held_value
        if qty * price > max_value:
            if max_value <= 0:
                return RiskDecision(
                    False, 0.0, f"종목 비중 상한 {self.rules.max_symbol_weight:.0%} 도달"
                )
            adj.append(f"종목 비중 상한: {qty:.6g} → {max_value / price:.6g}")
            qty = max_value / price

        if horizon == "intraday":
            room = self.rules.max_intraday_weight * equity - intraday_exposure
            if qty * price > room:
                if room <= 0:
                    return RiskDecision(
                        False, 0.0, f"단타 합산 상한 {self.rules.max_intraday_weight:.0%} 도달"
                    )
                adj.append(f"단타 합산 상한: {qty:.6g} → {room / price:.6g}")
                qty = room / price

        if qty <= 0:
            return RiskDecision(False, 0.0, "qty<=0")
        return self._wash_check(order, RiskDecision(True, qty, "ok", adj), now)

    def _wash_check(self, order: Order, d: RiskDecision, now: datetime) -> RiskDecision:
        sides = self._pending_sides.setdefault(order.symbol, set())
        opposite = Side.SELL if order.side == Side.BUY else Side.BUY
        if opposite in sides:
            return RiskDecision(False, 0.0, "자전거래 방지: 반대 방향 미체결 주문 존재")
        q = self._order_times.setdefault(order.symbol, deque(maxlen=64))
        t = time.monotonic()
        while q and t - q[0] > 1.0:
            q.popleft()
        if len(q) >= self.rules.max_orders_per_symbol_per_sec:
            return RiskDecision(False, 0.0, "주문 빈도 제한 (허수주문 방지)")
        q.append(t)
        return d

    def note_pending(self, order: Order, pending: bool) -> None:
        s = self._pending_sides.setdefault(order.symbol, set())
        (s.add if pending else s.discard)(order.side)
