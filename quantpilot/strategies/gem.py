"""듀얼 모멘텀 GEM (Antonacci) — 6/9/12개월 룩백 앙상블.

규칙 (월 1회, 월말 봉)
- 절대 모멘텀: 미국주식(SPY)의 12개월 수익률 > 단기채(BIL) 수익률이면 주식, 아니면 채권(AGG)
- 상대 모멘텀: 주식이면 SPY vs 해외주식(ACWX) 중 룩백 수익률이 큰 쪽
- 앙상블: 룩백 6·9·12개월 각각 판단해 1/3씩 배분 (한 룩백에 걸린 과최적화 완화)

Petit 2026 재현: 1971~2026 CAGR 15.18%, Sharpe 0.83, MDD −21.7% (S&P 500 11.27% / −50.9%).
2010년 이후 패시브 대비 연 −4.8%p — 방어 목적으로 이해할 것.
"""
from __future__ import annotations

from quantpilot.core.models import Market, Target
from quantpilot.strategies.base import Context, ParamSpec, Strategy

TRADING_DAYS_PER_MONTH = 21


class DualMomentumGEM(Strategy):
    name = "gem"
    market = Market.US
    timeframe = "1M"
    horizon = "long"
    symbols = ("SPY", "ACWX", "AGG", "BIL")
    warmup_bars = 13 * TRADING_DAYS_PER_MONTH

    @classmethod
    def params_schema(cls) -> list[ParamSpec]:
        return [
            ParamSpec("lookbacks", (6, 9, 12), description="룩백 개월 앙상블"),
            ParamSpec("equity_us", "SPY"),
            ParamSpec("equity_intl", "ACWX"),
            ParamSpec("bond", "AGG"),
            ParamSpec("cash", "BIL"),
        ]

    def _ret(self, ctx: Context, sym: str, months: int) -> float:
        close = ctx.bars[sym]["close"]
        n = months * TRADING_DAYS_PER_MONTH
        if len(close) <= n:
            return 0.0
        return float(close.iloc[-1] / close.iloc[-1 - n] - 1)

    def on_bar(self, ctx: Context) -> list[Target]:
        us, intl, bond, cash = (self.params[k] for k in ("equity_us", "equity_intl", "bond", "cash"))
        if any(s not in ctx.bars or len(ctx.bars[s]) < self.warmup_bars for s in (us, intl, bond, cash)):
            return []
        lookbacks = self.params["lookbacks"]
        weights: dict[str, float] = {s: 0.0 for s in (us, intl, bond)}
        share = 1.0 / len(lookbacks)
        for m in lookbacks:
            r_us, r_intl, r_cash = self._ret(ctx, us, m), self._ret(ctx, intl, m), self._ret(ctx, cash, m)
            if r_us > r_cash:                       # 절대 모멘텀 통과 → 주식
                pick = us if r_us >= r_intl else intl
            else:                                   # 실패 → 채권
                pick = bond
            weights[pick] += share
        return [Target(s, round(w, 6), reason=f"gem lookbacks={lookbacks}") for s, w in weights.items()]
