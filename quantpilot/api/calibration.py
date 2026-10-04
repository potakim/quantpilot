"""운영 DB를 읽는 CalibrationSource (ADR 0030) — `/judgments/calibration`·`/judgments/ab`·`/reports/gates` G2.

계산은 `qp report ab`와 같은 `db/reports.build_ab_report`다. 대시보드가 자주 부르므로 주(weeks)별로 짧게 캐시한다.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from quantpilot.core.models import Market

CACHE_SECONDS = 60.0


class DbCalibration:
    """Deps의 세션·시계로 A/B·보정 리포트를 만든다. 1단계는 업비트 한 시장."""

    def __init__(
        self,
        deps: Any,
        *,
        market: Market = Market.UPBIT,
        ttl: float = CACHE_SECONDS,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.deps = deps
        self.market = market
        self.ttl = ttl
        self._monotonic = monotonic
        self._cache: dict[int, tuple[float, dict[str, Any]]] = {}

    async def _report(self, weeks: int) -> dict[str, Any]:
        from quantpilot.db.reports import build_ab_report

        hit = self._cache.get(weeks)
        now = self._monotonic()
        if hit is not None and now - hit[0] < self.ttl:
            return hit[1]
        report = await build_ab_report(
            self.deps.sessions, self.market, weeks=weeks, now=self.deps.utcnow()
        )
        self._cache[weeks] = (now, report)
        return report

    async def calibration(self, weeks: int) -> dict[str, Any]:
        """`{brier, ece, n, buckets, monotonic, by_provider}` (03 §2.5)."""
        return (await self._report(weeks))["calibration"]

    async def ab(self, weeks: int) -> dict[str, Any]:
        """`{on:{ret, mdd, n_trades, cost}, off:{...}, g2_pass, g2, g2_reason, signals, hold, start, end}`."""
        r = await self._report(weeks)
        return {k: v for k, v in r.items() if k != "calibration"}
