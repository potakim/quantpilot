"""캔들 저장·조회 (04 §6). 집계기가 확정한 봉을 DB(candles)에 쓰고, 전략·백테스터용 DataFrame으로 읽는다.

- DB 접근은 `core/repos.CandleRepo` 구현(`db/repo.SqlCandleRepo`)을 주입받는다
- `load()` 결과는 loader.py와 같은 모양: columns=[open, high, low, close, volume],
  DatetimeIndex(이름 ts, 오름차순, 시장 현지 tz-naive, 봉 시작 시각)
- 같은 구간을 다시 읽으면 메모리 캐시에서 돌려준다. 그 심볼·주기에 upsert가 오면 캐시를 버린다
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from collections.abc import Iterable
from datetime import datetime

import pandas as pd

from quantpilot.core.events import BarClosed
from quantpilot.core.models import Market
from quantpilot.core.repos import CandleRepo
from quantpilot.data.loader import COLS

log = logging.getLogger(__name__)

_CacheKey = tuple[str, str, datetime, datetime]


def bars_to_frame(bars: Iterable[BarClosed]) -> pd.DataFrame:
    """BarClosed 목록 → OHLCV DataFrame (loader.py 규격)."""
    rows = [(b.ts, b.open, b.high, b.low, b.close, b.volume) for b in bars]
    df = pd.DataFrame(rows, columns=["ts", *COLS]).set_index("ts")
    df.index = pd.DatetimeIndex(df.index, name="ts")
    return df.astype(float).sort_index()


class CandleStore:
    """시장 하나의 캔들 저장소. 봉 쓰기(upsert)와 구간 읽기(load) + 조회 캐시."""

    def __init__(self, repo: CandleRepo, market: Market, *, cache_size: int = 64) -> None:
        self.repo = repo
        self.market = Market(market)
        self.cache_size = cache_size
        self._cache: OrderedDict[_CacheKey, pd.DataFrame] = OrderedDict()

    async def upsert(self, bars: Iterable[BarClosed], *, source: str = "ws") -> int:
        """봉을 저장한다(같은 봉이면 덮어씀). 저장한 봉 수를 돌려준다."""
        batch = list(bars)
        for b in batch:
            if Market(b.market) != self.market:
                raise ValueError(f"{self.market.value} 저장소에 {b.market} 봉")
        if not batch:
            return 0
        n = await self.repo.upsert(batch, source=source)
        self._invalidate({(b.symbol, b.timeframe) for b in batch})
        log.debug("캔들 저장", extra={"count": n, "source": source})
        return n

    async def load(self, symbol: str, tf: str, start: datetime, end: datetime) -> pd.DataFrame:
        """[start, end) 구간 봉을 DataFrame으로. 캐시에 있으면 DB를 부르지 않는다."""
        key = (symbol, tf, start, end)
        hit = self._cache.get(key)
        if hit is not None:
            self._cache.move_to_end(key)
            return hit.copy()
        df = bars_to_frame(await self.repo.load(self.market, symbol, tf, start, end))
        self._cache[key] = df
        if len(self._cache) > self.cache_size:
            self._cache.popitem(last=False)
        return df.copy()

    def _invalidate(self, keys: set[tuple[str, str]]) -> None:
        for k in [k for k in self._cache if (k[0], k[1]) in keys]:
            del self._cache[k]
