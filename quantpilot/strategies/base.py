"""전략 플러그인 인터페이스.

규칙
- 전략은 '계산'만 한다. 주문·수량·리스크는 RiskManager와 Broker의 몫이다.
- 백테스트와 실전이 같은 `on_bar`를 호출한다. 전략 코드에 시간을 아는 분기(if live:)를 두지 않는다.
- `on_bar`는 현재 봉까지의 히스토리를 받는다. Target.price를 지정하는 전략(장중 진입)은
  현재 봉의 종가를 계산에 쓰면 안 된다 (룩어헤드). 현재 봉의 open/high/low만 허용.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from quantpilot.core.models import Market, Position, Target


@dataclass
class Context:
    """한 봉 시점의 컨텍스트. bars[symbol] = 현재 봉까지의 OHLCV DataFrame (DatetimeIndex, 오름차순)."""

    ts: pd.Timestamp
    bars: Mapping[str, pd.DataFrame]
    positions: Mapping[str, Position]
    equity: float
    params: dict = field(default_factory=dict)

    def history(self, symbol: str, n: int | None = None) -> pd.DataFrame:
        df = self.bars[symbol]
        return df if n is None else df.tail(n)

    def prev(self, symbol: str) -> pd.DataFrame:
        """현재 봉을 제외한 히스토리 (장중 진입 전략용)."""
        return self.bars[symbol].iloc[:-1]

    def current(self, symbol: str) -> pd.Series:
        return self.bars[symbol].iloc[-1]


@dataclass(frozen=True)
class ParamSpec:
    name: str
    default: Any
    min: Any = None
    max: Any = None
    step: Any = None
    choices: tuple = ()
    description: str = ""


class Strategy(ABC):
    """모든 전략의 베이스. 서브클래스는 name/market/timeframe/symbols/params_schema/on_bar를 정의한다."""

    name: str = "base"
    market: Market = Market.UPBIT
    timeframe: str = "1d"  # "1d" | "5m" | "1M"(월간 판단)
    horizon: str = "swing"  # "intraday" | "swing" | "long"  (단타 합산 상한 20% 적용 대상 구분)
    symbols: tuple[str, ...] = ()
    warmup_bars: int = 30  # 이만큼 봉이 쌓이기 전에는 on_bar를 호출하지 않는다

    def __init__(self, **params: Any):
        schema = {p.name: p for p in self.params_schema()}
        unknown = set(params) - set(schema)
        if unknown:
            raise ValueError(f"{self.name}: 알 수 없는 파라미터 {sorted(unknown)}")
        self.params: dict[str, Any] = {k: p.default for k, p in schema.items()}
        self.params.update(params)
        for k, v in self.params.items():
            spec = schema[k]
            if spec.min is not None and v < spec.min:
                raise ValueError(f"{self.name}.{k}={v} < min {spec.min}")
            if spec.max is not None and v > spec.max:
                raise ValueError(f"{self.name}.{k}={v} > max {spec.max}")
            if spec.choices and v not in spec.choices:
                raise ValueError(f"{self.name}.{k}={v} not in {spec.choices}")

    @classmethod
    @abstractmethod
    def params_schema(cls) -> list[ParamSpec]: ...

    @abstractmethod
    def on_bar(self, ctx: Context) -> list[Target]:
        """현재 봉에서 원하는 목표 비중 목록. 비어 있으면 '변경 없음'."""

    def describe(self) -> dict:
        return {
            "name": self.name,
            "market": self.market.value,
            "timeframe": self.timeframe,
            "horizon": self.horizon,
            "symbols": list(self.symbols),
            "params": dict(self.params),
            "schema": [s.__dict__ for s in self.params_schema()],
        }
