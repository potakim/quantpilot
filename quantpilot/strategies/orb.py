"""5분 오프닝 레인지 브레이크아웃 ORB (Zarattini & Aziz 2023, QQQ).

규칙 (5분봉, 당일 청산)
- 첫 5분봉(09:30~09:35 ET)의 방향(종가>시가면 롱, 반대면 숏) 으로 둘째 봉 시가에 진입
- 손절 = 첫 봉의 반대쪽 극값, 목표 = 10R, 못 닿으면 15:55 종가 청산
- 수량은 거래당 리스크 1% (RiskManager.size_by_risk) — 여기서는 비중 대신 stop을 넘긴다

논문(QQQ 2016~2023): 연 31%, Sharpe 1.12, 승률 24%. 슬리피지 손익분기 ≈ 2.2¢/주 (Brusco 재현) →
페이퍼 전용. 실전 전환은 관문 G2(슬리피지 포함 Sharpe > 0.5) 통과 후.
숏은 국내 증권사 해외주식 계좌에서 불가하므로 기본 롱 전용(allow_short=False).
"""

from __future__ import annotations

import pandas as pd

from quantpilot.core.models import Market, Target
from quantpilot.strategies.base import Context, ParamSpec, Strategy


class OpeningRangeBreakout(Strategy):
    name = "orb"
    market = Market.US
    timeframe = "5m"
    horizon = "intraday"
    symbols = ("QQQ",)
    warmup_bars = 2

    @classmethod
    def params_schema(cls) -> list[ParamSpec]:
        return [
            ParamSpec("risk_per_trade", 0.01, 0.0025, 0.01, 0.0025, description="거래당 리스크"),
            ParamSpec("target_r", 10.0, 2.0, 20.0, 1.0, description="목표 R 배수"),
            ParamSpec("allow_short", False, choices=(True, False)),
            ParamSpec("exit_time", "15:55", description="강제 청산 시각 (ET)"),
        ]

    def on_bar(self, ctx: Context) -> list[Target]:
        out: list[Target] = []
        for sym in self.symbols:
            if sym not in ctx.bars:
                continue
            df = ctx.bars[sym]
            cur = df.iloc[-1]
            ts: pd.Timestamp = df.index[-1]
            day = df[df.index.normalize() == ts.normalize()]
            if len(day) < 2:
                continue  # 첫 봉이 끝나기 전
            first = day.iloc[0]
            pos = ctx.positions.get(sym)
            hhmm = ts.strftime("%H:%M")

            # 강제 청산
            if pos is not None and pos.is_open and hhmm >= self.params["exit_time"]:
                out.append(Target(sym, 0.0, reason="eod_exit"))
                continue

            # 둘째 봉 시가 진입 (하루 1회)
            if len(day) == 2 and (pos is None or not pos.is_open):
                bullish = float(first["close"]) > float(first["open"])
                if bullish:
                    entry, stop = float(cur["open"]), float(first["low"])
                elif self.params["allow_short"]:
                    entry, stop = float(cur["open"]), float(first["high"])
                else:
                    continue
                r = abs(entry - stop)
                if r <= 0:
                    continue
                # 비중은 리스크 기준: weight = risk / (r / entry). RiskManager가 다시 상한을 건다.
                weight = min(1.0, float(self.params["risk_per_trade"]) / (r / entry))
                out.append(
                    Target(
                        sym,
                        weight if bullish else -weight,
                        price=entry,
                        stop=stop,
                        reason=f"orb {'long' if bullish else 'short'} R={r:.2f}",
                    )
                )
                continue

            # 손절 / 목표
            if pos is not None and pos.is_open:
                entry = pos.avg_price
                if pos.qty > 0:
                    stop, target = (
                        float(first["low"]),
                        entry + self.params["target_r"] * (entry - float(first["low"])),
                    )
                    if float(cur["low"]) <= stop:
                        out.append(Target(sym, 0.0, price=stop, reason="stop"))
                    elif float(cur["high"]) >= target:
                        out.append(Target(sym, 0.0, price=target, reason="target_10R"))
                else:
                    stop, target = (
                        float(first["high"]),
                        entry - self.params["target_r"] * (float(first["high"]) - entry),
                    )
                    if float(cur["high"]) >= stop:
                        out.append(Target(sym, 0.0, price=stop, reason="stop"))
                    elif float(cur["low"]) <= target:
                        out.append(Target(sym, 0.0, price=target, reason="target_10R"))
        return out
