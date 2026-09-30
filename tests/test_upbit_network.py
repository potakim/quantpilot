"""P1-03 실데이터 확인: 업비트 REST 1분봉을 체결로 풀어 집계한 일봉 = REST 일봉. 기본 실행에서 제외."""

from __future__ import annotations

from datetime import timedelta

import pytest

from quantpilot.core.events import TradeEvent
from quantpilot.core.models import Market
from quantpilot.data.aggregator import CandleAggregator
from quantpilot.data.loader import upbit_candles

pytestmark = pytest.mark.network


def test_minute_bars_aggregate_to_rest_daily():
    daily = upbit_candles("KRW-BTC", "1d", count=3)
    day = daily.index[-2]  # 어제(완결된) 일봉, 시작 09:00 KST
    end_utc = (day + timedelta(days=1) - timedelta(hours=9)).to_pydatetime()
    minutes = upbit_candles("KRW-BTC", "1m", count=1440, end=end_utc)
    minutes = minutes[(minutes.index >= day) & (minutes.index < day + timedelta(days=1))]
    assert len(minutes) > 1000

    agg = CandleAggregator("1d", Market.UPBIT)
    for ts, row in minutes.iterrows():
        t = ts.to_pydatetime()
        # 1분봉 하나를 시가·고가·저가·종가 체결 4건으로 풀고 거래량은 종가 체결에 싣는다
        for sec, px, qty in [
            (0, row.open, 0.0),
            (15, row.high, 0.0),
            (30, row.low, 0.0),
            (45, row.close, row.volume),
        ]:
            agg.on_trade(TradeEvent(Market.UPBIT, "KRW-BTC", t + timedelta(seconds=sec), px, qty))
    [bar] = agg.on_timer(day.to_pydatetime() + timedelta(days=1, seconds=2))

    ref = daily.loc[day]
    assert bar.ts == day.to_pydatetime()
    assert (bar.open, bar.high, bar.low, bar.close) == (ref.open, ref.high, ref.low, ref.close)
    assert bar.volume == pytest.approx(ref.volume, rel=1e-6)
