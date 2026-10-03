"""DailyRollup — 실시간 분봉으로 일봉 전략을 평가하기 위한 거래일 일봉 (ADR 0025).

엔진은 1분봉을 받지만 변동성 돌파·GEM·GTAA는 일봉 규칙이다. 분봉을 그대로 넘기면 "전일"이 "1분 전 봉"이
되어 매분 진입·청산한다. 그래서 거래일 경계(업비트 09:00 KST)로 분봉을 묶어 일봉 표를 만들고, 마지막 행은
"오늘 진행 중인 봉"(시가 = 거래일 첫 가격, 고가·저가 = 지금까지, 종가 = 현재가)으로 둔다. 전략 코드는 그대로다
(불변식 #2): 오늘 고가가 목표가를 넘는 순간 백테스트와 같은 on_bar가 진입 target을 낸다.

시드: 시작할 때 과거 일봉(업비트 REST)을 넣는다. REST 일봉은 오늘 진행 중인 봉까지 주므로 그 시가를 유지한 채
분봉으로 고가·저가·종가만 갱신한다 — 장중에 재시작해도 목표가(오늘 시가 기준)가 바뀌지 않는다.
시각은 시장 현지 tz-naive (core/clock 규칙). 행 인덱스는 거래일 시작 시각(예: 09:00).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta

import pandas as pd

from quantpilot.core.events import BarClosed

COLS = ("open", "high", "low", "close", "volume")


class DailyRollup:
    """심볼별 거래일 일봉 + 진행 중인 오늘 봉."""

    def __init__(
        self, day_start: timedelta, seed: Mapping[str, pd.DataFrame] | None = None
    ) -> None:
        self.day_start = day_start
        self._frames: dict[str, pd.DataFrame] = {}
        for sym, df in (seed or {}).items():
            if len(df):
                self._frames[sym] = df[list(COLS)].astype(float).sort_index()

    def day_of(self, ts: datetime | pd.Timestamp) -> pd.Timestamp:
        """ts가 속한 거래일의 시작 시각 (day_start 전이면 전날 거래일)."""
        t = pd.Timestamp(ts)
        return pd.Timestamp((t - self.day_start).date()) + self.day_start

    def add(self, ev: BarClosed) -> None:
        """분봉 1개를 그 거래일 봉에 반영한다. 지난 거래일의 늦은 분봉은 버린다."""
        day = self.day_of(ev.ts)
        df = self._frames.get(ev.symbol)
        if df is not None and len(df) and df.index[-1] == day:
            i = len(df) - 1
            df.iat[i, 1] = max(df.iat[i, 1], ev.high)
            df.iat[i, 2] = min(df.iat[i, 2], ev.low)
            df.iat[i, 3] = ev.close
            df.iat[i, 4] = df.iat[i, 4] + ev.volume
            return
        if df is not None and len(df) and df.index[-1] > day:
            return
        row = pd.DataFrame(
            [[ev.open, ev.high, ev.low, ev.close, ev.volume]],
            columns=list(COLS),
            index=pd.DatetimeIndex([day], name="ts"),
        )
        self._frames[ev.symbol] = row if df is None or df.empty else pd.concat([df, row])

    def view(self) -> dict[str, pd.DataFrame]:
        """심볼별 일봉 표 (마지막 행 = 진행 중인 오늘 봉)."""
        return dict(self._frames)
