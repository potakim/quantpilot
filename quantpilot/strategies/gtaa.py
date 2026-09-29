"""10개월 이동평균 자산배분 GTAA (Faber 2007).

규칙 (월 1회, 월말 봉)
- 자산 5개에 각 20% 배정
- 월말 종가 > 10개월(≈210거래일) 단순이평이면 보유, 아니면 그 몫은 현금
- 옵션: 현금 대신 단기채 ETF에 둠

Faber 1973~2012: MDD 46% → 10% 미만, 수익률은 매수보유와 비슷.
"""
from __future__ import annotations

from quantpilot.core.models import Market, Target
from quantpilot.strategies.base import Context, ParamSpec, Strategy


class GTAA(Strategy):
    name = "gtaa"
    market = Market.KRX
    timeframe = "1M"
    horizon = "long"
    # KRX 상장 ETF 코드 (TIGER 미국S&P500, KODEX 선진국MSCI, KODEX 미국채10년선물, TIGER 원자재, TIGER 리츠)
    symbols = ("360750", "251350", "308620", "130680", "329200")
    warmup_bars = 220

    @classmethod
    def params_schema(cls) -> list[ParamSpec]:
        return [
            ParamSpec("ma_days", 210, 100, 300, 10, description="이평 기간 (거래일, 10개월≈210)"),
            ParamSpec("cash_symbol", None, description="현금 대신 둘 심볼 (None이면 현금)"),
        ]

    def on_bar(self, ctx: Context) -> list[Target]:
        n = int(self.params["ma_days"])
        avail = [s for s in self.symbols if s in ctx.bars and len(ctx.bars[s]) >= n]
        if not avail:
            return []
        each = 1.0 / len(self.symbols)
        out: list[Target] = []
        cash_w = 0.0
        for s in self.symbols:
            if s not in avail:
                out.append(Target(s, 0.0, reason="no_data"))
                cash_w += each
                continue
            close = ctx.bars[s]["close"]
            above = float(close.iloc[-1]) > float(close.tail(n).mean())
            out.append(Target(s, each if above else 0.0, reason=f"close{'>' if above else '<='}ma{n}"))
            if not above:
                cash_w += each
        cash_sym = self.params["cash_symbol"]
        if cash_sym and cash_sym in ctx.bars:
            out.append(Target(cash_sym, cash_w, reason="cash_parking"))
        return out
