"""합성 OHLCV 생성기 — 테스트와 오프라인 데모용. 실제 데이터를 대신하지 않는다."""

from __future__ import annotations

import zlib

import numpy as np
import pandas as pd


def seed_of(symbol: str) -> int:
    """심볼 문자열에서 프로세스와 무관하게 항상 같은 시드를 만든다 (내장 hash()는 실행마다 바뀐다)."""
    return zlib.crc32(symbol.encode("utf-8"))


def daily(
    symbol_seed: int,
    start: str = "2018-01-01",
    periods: int = 1500,
    *,
    start_price: float = 100.0,
    drift: float = 0.0004,
    vol: float = 0.02,
    freq: str = "D",
) -> pd.DataFrame:
    rng = np.random.default_rng(symbol_seed)
    idx = pd.date_range(start, periods=periods, freq=freq)
    rets = rng.normal(drift, vol, periods)
    close = start_price * np.exp(np.cumsum(rets))
    open_ = np.r_[start_price, close[:-1]] * (1 + rng.normal(0, vol / 4, periods))
    hi = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, vol / 2, periods)))
    lo = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, vol / 2, periods)))
    volume = rng.lognormal(10, 0.5, periods)
    return pd.DataFrame(
        {"open": open_, "high": hi, "low": lo, "close": close, "volume": volume}, index=idx
    )


def intraday_5m(
    symbol_seed: int,
    days: int = 60,
    start: str = "2024-01-02",
    *,
    start_price: float = 400.0,
    vol: float = 0.0008,
) -> pd.DataFrame:
    """미국 정규장 09:30~15:55 (ET, tz-naive) 5분봉. 하루 78봉."""
    rng = np.random.default_rng(symbol_seed)
    frames = []
    price = start_price
    for d in pd.bdate_range(start, periods=days):
        times = pd.date_range(d + pd.Timedelta(hours=9, minutes=30), periods=78, freq="5min")
        rets = rng.normal(0, vol, 78)
        close = price * np.exp(np.cumsum(rets))
        open_ = np.r_[price, close[:-1]]
        hi = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, vol / 2, 78)))
        lo = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, vol / 2, 78)))
        frames.append(
            pd.DataFrame(
                {
                    "open": open_,
                    "high": hi,
                    "low": lo,
                    "close": close,
                    "volume": rng.lognormal(9, 0.4, 78),
                },
                index=times,
            )
        )
        price = close[-1] * (1 + rng.normal(0, 0.005))  # 갭
    return pd.concat(frames)


def universe(symbols: tuple[str, ...], **kw) -> dict[str, pd.DataFrame]:
    return {s: daily(seed_of(s), **kw) for s in symbols}
