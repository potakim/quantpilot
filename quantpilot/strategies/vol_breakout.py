"""변동성 돌파 + 이동평균 스코어 + 목표 변동성 (강환국 / 래리 윌리엄스).

규칙 (일봉, 1일 보유)
- 목표가 = 오늘 시가 + (전일 고가 - 전일 저가) × K
- 오늘 고가가 목표가에 닿으면 목표가에 매수, 다음 봉 시가(≈다음 날 09:00)에 청산
- 이평 스코어: 종가가 3/5/10/20일 이평 위에 있는 개수 / 4 → 비중에 곱한다 (하락장 필터)
- 목표 변동성: 비중 = min(1, target_vol / 전일 변동폭%) → 변동성이 클수록 작게 산다
- 노이즈 K(옵션): K = 1 - |시가-종가|/(고가-저가)의 20일 평균

출처: hive.blog/kr/@kangcfa/6-mdd-10-1 (0.5% 타겟 + 5일선 필터: 연 17.4%, MDD 6.5%, BTC 2013.10~2018.3)
"""

from __future__ import annotations

import numpy as np

from quantpilot.core.models import Market, Target
from quantpilot.strategies.base import Context, ParamSpec, Strategy


class VolBreakout(Strategy):
    name = "vol_breakout"
    market = Market.UPBIT
    timeframe = "1d"
    horizon = "intraday"
    symbols = ("KRW-BTC", "KRW-ETH", "KRW-SOL", "KRW-XRP", "KRW-ADA")
    warmup_bars = 25

    @classmethod
    def params_schema(cls) -> list[ParamSpec]:
        return [
            ParamSpec("k", 0.5, 0.3, 0.8, 0.05, description="돌파 계수 K"),
            ParamSpec(
                "target_vol", 0.01, 0.002, 0.03, 0.001, description="목표 변동성 (일간, 0.01=1%)"
            ),
            ParamSpec("ma_windows", (3, 5, 10, 20), description="이평 스코어 윈도우"),
            ParamSpec("noise_k", False, choices=(True, False), description="노이즈 K 사용"),
            ParamSpec("max_weight", 1.0, 0.1, 1.0, 0.1, description="코인 하나의 최대 비중"),
        ]

    def on_bar(self, ctx: Context) -> list[Target]:
        out: list[Target] = []
        n_symbols = max(1, len(self.symbols))
        for sym in self.symbols:
            if sym not in ctx.bars:
                continue
            prev = ctx.prev(sym)
            cur = ctx.current(sym)
            if len(prev) < self.warmup_bars:
                continue
            p1 = prev.iloc[-1]
            rng = float(p1["high"] - p1["low"])
            if rng <= 0:
                out.append(Target(sym, 0.0, reason="range=0"))
                continue

            k = float(self.params["k"])
            if self.params["noise_k"]:
                noise = 1 - (prev["open"] - prev["close"]).abs() / (
                    prev["high"] - prev["low"]
                ).replace(0, np.nan)
                k = float(noise.tail(20).mean()) if noise.tail(20).notna().any() else k

            target_price = float(cur["open"]) + rng * k

            # 이평 스코어 (전일 종가 기준 → 룩어헤드 없음)
            close = prev["close"]
            wins = self.params["ma_windows"]
            score = sum(float(close.iloc[-1] > close.tail(w).mean()) for w in wins) / len(wins)

            # 목표 변동성: 전일 변동폭% 대비
            vol_pct = rng / float(p1["close"])
            vol_w = min(1.0, float(self.params["target_vol"]) / vol_pct) if vol_pct > 0 else 0.0

            weight = min(float(self.params["max_weight"]), vol_w * score) / n_symbols

            # 1) 어제 산 것은 오늘 시가(09:00)에 청산 — 포지션이 없으면 엔진이 무시한다
            out.append(Target(sym, 0.0, price=float(cur["open"]), reason="time_exit_09:00"))
            # 2) 오늘 고가가 목표가에 닿았으면 목표가에 진입 (하루 1회)
            hit = float(cur["high"]) >= target_price
            if hit and weight > 0:
                out.append(
                    Target(
                        sym,
                        weight,
                        price=target_price,
                        reason=f"breakout k={k:.2f} score={score:.2f} vol={vol_pct:.3%}",
                        stop=target_price * (1 - 2 * vol_pct),
                    )
                )
        return out
