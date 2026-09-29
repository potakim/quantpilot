"""거래 비용 모델. 백테스트와 PaperBroker가 같은 모델을 쓴다 — 비용 0 백테스트는 만들 수 없다."""
from __future__ import annotations

from dataclasses import dataclass

from quantpilot.core.models import Market, Side


@dataclass(frozen=True)
class CostModel:
    fee_rate: float          # 편도 수수료율
    slippage_rate: float     # 시장가 체결 시 불리한 방향으로 미끄러지는 비율 (1틱 + 호가 소진 근사)
    sell_tax_rate: float     # 매도 시 세금 (증권거래세 등). 양도세는 연 정산이라 여기서 빼지 않는다
    tick_size: float = 0.0   # 호가 단위 (0이면 무시)

    def fill_price(self, ref_price: float, side: Side) -> float:
        adj = ref_price * self.slippage_rate
        px = ref_price + adj if side == Side.BUY else ref_price - adj
        if self.tick_size > 0:
            px = round(px / self.tick_size) * self.tick_size
        return px

    def fee(self, gross: float) -> float:
        return gross * self.fee_rate

    def tax(self, gross: float, side: Side) -> float:
        return gross * self.sell_tax_rate if side == Side.SELL else 0.0

    def round_trip_rate(self) -> float:
        return 2 * self.fee_rate + 2 * self.slippage_rate + self.sell_tax_rate


# 기획서 '규제·비용·리스크 고지' 표의 기본값
PRESETS: dict[Market, CostModel] = {
    Market.UPBIT: CostModel(fee_rate=0.0005, slippage_rate=0.0005, sell_tax_rate=0.0),
    Market.KRX:   CostModel(fee_rate=0.00015, slippage_rate=0.0005, sell_tax_rate=0.0020),
    Market.US:    CostModel(fee_rate=0.0010, slippage_rate=0.0003, sell_tax_rate=0.0),
}

ZERO = CostModel(0.0, 0.0, 0.0)   # 테스트 전용. 엔진은 allow_zero_cost=True 없이는 거부한다.


def preset(market: Market | str) -> CostModel:
    return PRESETS[Market(market)]
