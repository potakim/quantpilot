"""캔들 데이터 적재. 소스별 로더 + 로컬 캐시(parquet, 없으면 csv).

- 업비트 일봉·분봉: REST (키 불필요). 200개씩 페이지네이션, 초당 10회 제한 준수
- 미국 일봉: yfinance (선택 설치)
- 국내 일봉: FinanceDataReader (선택 설치)
- 미국 1분봉·5분봉: Alpaca (1단계에서 추가)

모든 로더는 columns=[open, high, low, close, volume], DatetimeIndex(오름차순, tz-naive)를 돌려준다.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)
COLS = ["open", "high", "low", "close", "volume"]


class CandleCache:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            import pyarrow  # noqa: F401

            self.ext = "parquet"
        except ImportError:
            self.ext = "csv"

    def path(self, source: str, symbol: str, tf: str) -> Path:
        safe = symbol.replace("/", "_")
        return self.root / source / f"{safe}_{tf}.{self.ext}"

    def load(self, source: str, symbol: str, tf: str) -> pd.DataFrame | None:
        p = self.path(source, symbol, tf)
        if not p.exists():
            return None
        if self.ext == "parquet":
            return pd.read_parquet(p)
        return pd.read_csv(p, index_col=0, parse_dates=True)

    def save(self, source: str, symbol: str, tf: str, df: pd.DataFrame) -> Path:
        p = self.path(source, symbol, tf)
        p.parent.mkdir(parents=True, exist_ok=True)
        if self.ext == "parquet":
            df.to_parquet(p)
        else:
            df.to_csv(p)
        return p


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    df = df[COLS].astype(float).sort_index()
    df = df[~df.index.duplicated(keep="last")]
    if getattr(df.index, "tz", None) is not None:
        df.index = df.index.tz_localize(None)
    df.index.name = "ts"
    return df


# ---------------- 업비트 ----------------
UPBIT = "https://api.upbit.com/v1"


def upbit_candles(
    market: str, tf: str = "1d", count: int = 1000, *, end: datetime | None = None, client=None
) -> pd.DataFrame:
    """tf: '1d' | '1m' | '5m' | '15m' | '60m' | '1w'. count개(최대 수천)를 200개씩 뒤로 가며 받는다."""
    import httpx

    client = client or httpx.Client(timeout=10)
    if tf == "1d":
        url, params = f"{UPBIT}/candles/days", {}
    elif tf.endswith("m"):
        url, params = f"{UPBIT}/candles/minutes/{int(tf[:-1])}", {}
    elif tf == "1w":
        url, params = f"{UPBIT}/candles/weeks", {}
    else:
        raise ValueError(tf)
    frames = []
    to = end
    remaining = count
    while remaining > 0:
        q = {"market": market, "count": min(200, remaining), **params}
        if to is not None:
            q["to"] = to.strftime("%Y-%m-%dT%H:%M:%S")
        r = client.get(url, params=q)
        if r.status_code == 429:
            time.sleep(1.0)
            continue
        r.raise_for_status()
        rows = r.json()
        if not rows:
            break
        df = pd.DataFrame(rows)
        df["ts"] = pd.to_datetime(df["candle_date_time_kst"])
        df = df.rename(
            columns={
                "opening_price": "open",
                "high_price": "high",
                "low_price": "low",
                "trade_price": "close",
                "candle_acc_trade_volume": "volume",
            }
        ).set_index("ts")
        frames.append(df[COLS])
        to = pd.to_datetime(rows[-1]["candle_date_time_utc"]).to_pydatetime()
        remaining -= len(rows)
        time.sleep(0.11)  # 10 req/s 제한
    if not frames:
        return pd.DataFrame(columns=COLS)
    return _normalize(pd.concat(frames))


# ---------------- 미국 (yfinance) ----------------
def yfinance_daily(symbol: str, start: str = "2000-01-01", end: str | None = None) -> pd.DataFrame:
    try:
        import yfinance as yf
    except ImportError as e:
        raise ImportError("pip install 'quantpilot[data]'  (yfinance)") from e
    df = yf.download(symbol, start=start, end=end, auto_adjust=True, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0].lower() for c in df.columns]
    else:
        df.columns = [c.lower() for c in df.columns]
    return _normalize(df)


# ---------------- 국내 (FinanceDataReader) ----------------
def fdr_daily(code: str, start: str = "2010-01-01", end: str | None = None) -> pd.DataFrame:
    try:
        import FinanceDataReader as fdr
    except ImportError as e:
        raise ImportError("pip install 'quantpilot[data]'  (finance-datareader)") from e
    df = fdr.DataReader(code, start, end)
    df.columns = [c.lower() for c in df.columns]
    return _normalize(df)


# ---------------- 통합 ----------------
def load(
    source: str,
    symbol: str,
    tf: str = "1d",
    *,
    cache: CandleCache | None = None,
    refresh: bool = False,
    **kw,
) -> pd.DataFrame:
    if cache and not refresh:
        hit = cache.load(source, symbol, tf)
        if hit is not None and len(hit):
            return hit
    if source == "upbit":
        df = upbit_candles(symbol, tf, **kw)
    elif source == "yfinance":
        df = yfinance_daily(symbol, **kw)
    elif source == "fdr":
        df = fdr_daily(symbol, **kw)
    else:
        raise ValueError(f"unknown source {source}")
    if cache:
        cache.save(source, symbol, tf, df)
    return df
