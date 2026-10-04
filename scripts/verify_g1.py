"""관문 G1 확인 — 로직은 quantpilot/ops/g1.py (ADR 0024·0030). 배포 이미지에서는 `qp gate g1`을 쓴다.

사용 (먼저 데이터를 받아 둘 것):
    qp fetch yfinance SPY VEU AGG BIL --start 2005-01-01
    qp fetch upbit KRW-BTC KRW-ETH KRW-SOL KRW-XRP KRW-ADA --count 3500
    python scripts/verify_g1.py          # 통과 0, 미달 1 (DB에 쓰려면 qp gate g1 --write)
"""

from __future__ import annotations

import sys

from quantpilot.backtest import AttemptTracker
from quantpilot.config import settings
from quantpilot.data import CandleCache
from quantpilot.ops.g1 import run_all


def main() -> int:
    """두 전략을 판정하고 결과를 출력한다. 전부 통과면 0."""
    results = run_all(CandleCache(settings.cache_dir), AttemptTracker(settings.attempts_file))
    for r in results:
        print(f"{'G1 PASS' if r.ok else 'G1 미달'}  {r.line}")
    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
