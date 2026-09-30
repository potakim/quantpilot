"""전략에 넘길 봉 히스토리 (04 §7).

백테스트는 전체 DataFrame을 미리 적재하고 커서만 옮긴다(봉마다 복사 없음). 실전은 `CandleStore.load`로
과거 봉을 시드한 뒤 마감된 봉을 덧붙인다. 두 경우 모두 `view()`는 "지금까지 마감된 봉"만 보여준다.
"""

from __future__ import annotations

from collections.abc import Mapping

import pandas as pd

from quantpilot.core.events import BarClosed

COLS = ("open", "high", "low", "close", "volume")


class BarHistory:
    """심볼별 마감 봉 버퍼. ts는 봉 시작 시각(시장 현지 tz-naive)."""

    def __init__(self, frames: Mapping[str, pd.DataFrame] | None = None, *, preloaded: bool = True):
        """frames가 있으면 preloaded=True는 '재생할 전체 데이터', False는 '이미 마감된 과거 봉'."""
        self._frames: dict[str, pd.DataFrame] = {}
        self._cursor: dict[str, int] = {}
        for sym, df in (frames or {}).items():
            self._frames[sym] = df[list(COLS)].astype(float)
            self._cursor[sym] = 0 if preloaded else len(df)

    def append(self, ev: BarClosed) -> None:
        """마감된 봉 1개를 반영한다. 미리 적재된 다음 봉과 같으면 커서만 옮긴다."""
        df = self._frames.get(ev.symbol)
        k = self._cursor.get(ev.symbol, 0)
        ts = pd.Timestamp(ev.ts)
        if df is not None and k < len(df) and df.index[k] == ts:
            self._cursor[ev.symbol] = k + 1
            return
        if df is not None and k > 0 and df.index[k - 1] >= ts:
            raise ValueError(f"{ev.symbol}: 봉 시각 역행 {ts} <= {df.index[k - 1]}")
        row = pd.DataFrame(
            [[ev.open, ev.high, ev.low, ev.close, ev.volume]],
            columns=list(COLS),
            index=pd.DatetimeIndex([ts], name="ts"),
        )
        head = df.iloc[:k] if df is not None else None
        self._frames[ev.symbol] = row if head is None or head.empty else pd.concat([head, row])
        self._cursor[ev.symbol] = k + 1

    def view(self) -> dict[str, pd.DataFrame]:
        """봉이 하나 이상 있는 심볼의 마감 봉 DataFrame."""
        return {s: self._frames[s].iloc[:k] for s, k in self._cursor.items() if k > 0}

    def last_ts(self, symbol: str) -> pd.Timestamp | None:
        """그 심볼의 마지막 마감 봉 시각."""
        k = self._cursor.get(symbol, 0)
        return self._frames[symbol].index[k - 1] if k > 0 else None
