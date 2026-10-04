"""관문 G1 확인 (ADR 0024·0030): 비용 포함 백테스트가 같은 기간·같은 조건의 기준과 맞는가.

- GEM: 같은 기간·같은 변형의 공개 수치와 ±20% (CAGR, 월말 MDD, 월간 샤프). 공개 자료는 월 단위로
  계산하므로 MDD·샤프도 월말 값으로 비교한다
- 변동성 돌파: 같은 기간 공개 수치가 없어 독립 기준 구현(backtest/reference.py)과 ±10% 대조
  (CAGR, MDD, 진입 횟수 ±3%) — "엔진이 규칙대로 도는가"를 본다. 수익성은 G2(페이퍼)가 본다
- 두 전략 모두 서로 다른 파라미터 시도 7회 이하 (ADR 0007)
- 홀드아웃(마지막 12개월)은 풀지 않는다 (불변식 #5)

`qp gate g1 --write`는 결과를 settings `gate_report.g1.<전략>`에 쓴다 — `/reports/gates`가 읽는다 (ADR 0030).
먼저 데이터를 받아 둘 것:
    qp fetch yfinance SPY VEU AGG BIL --start 2005-01-01
    qp fetch upbit KRW-BTC KRW-ETH KRW-SOL KRW-XRP KRW-ADA --count 3500
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pandas as pd

from quantpilot.backtest import AttemptTracker, Backtester, preset
from quantpilot.backtest.reference import (
    cagr,
    max_drawdown,
    monthly_sharpe,
    reference_vol_breakout,
    within,
)
from quantpilot.core.models import Side
from quantpilot.data import CandleCache, load
from quantpilot.strategies import create

TOL_PUBLIC = 0.20
TOL_REFERENCE = 0.10
TOL_ENTRIES = 0.03
MAX_ATTEMPTS = 7

# 같은 기간·같은 변형 공개 수치 (ADR 0024). 개인 사이트·비용 가정 미공개 → 같은 자릿수 확인용
GEM_PUBLIC = {
    "params": {"lookbacks": (12,), "equity_intl": "VEU"},
    "symbols": ("SPY", "VEU", "AGG", "BIL"),
    "start": "2008-01-01",
    "cagr": 0.091,
    "mdd_monthly": -0.20,
    "sharpe_monthly": 0.74,
    "source": "https://quant4free.com/analysis/dual-momentum/ (2008–2026, 12개월 룩백, 2026-10-03 확인)",
}
# 참고: 원래 기획서 수치 (기간이 달라 판정에 쓰지 않는다)
CONTEXT = {
    "gem": "Antonacci 1974–2013 연 17.4%·MDD −22.7% (cxoadvisory), 우리 6/9/12 앙상블은 같은 기간 공개 수치 없음",
    "vol_breakout": "강환국 BTC 2013.10–2018.3 연 17.4%·MDD 6.5% (업비트 데이터와 기간 거의 안 겹침)",
}


@dataclass
class G1Result:
    """전략 하나의 G1 판정. metrics_ok는 지표 비교만, ok는 시도 횟수까지 포함."""

    strategy: str
    metrics_ok: bool
    attempts: int
    line: str
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """지표 기준 충족 + 시도 7회 이하."""
        return self.metrics_ok and self.attempts <= MAX_ATTEMPTS

    def report(self, now: datetime) -> dict[str, Any]:
        """settings `gate_report.g1.<전략>`에 쓰는 값 (api/gates.py가 읽는다)."""
        return {"within_20pct": self.metrics_ok, **self.evidence, "checked_at": now.isoformat()}


def check_gem(cache: CandleCache, tracker: AttemptTracker) -> G1Result:
    """GEM 12개월·VEU 변형을 공개 수치와 같은 기간·월 단위로 비교."""
    ref = GEM_PUBLIC
    strat = create("gem", **ref["params"])
    strat.symbols = ref["symbols"]
    data = {s: load("yfinance", s, "1d", cache=cache) for s in strat.symbols}
    res = Backtester(preset(strat.market), holdout_months=12).run(strat, data)
    eq = res.equity.dropna()
    eq = eq[eq.index >= ref["start"]]
    got = {
        "cagr": cagr(eq),
        "mdd_monthly": max_drawdown(eq, "ME"),
        "sharpe_monthly": monthly_sharpe(eq),
    }
    ok = all(within(got[k], ref[k], TOL_PUBLIC) for k in got)
    n = tracker.count("gem")
    line = (
        f"gem(12개월·VEU) {eq.index[0].date()}~{eq.index[-1].date()}: "
        f"CAGR {got['cagr']:+.1%} (기준 {ref['cagr']:+.1%})  "
        f"월말 MDD {got['mdd_monthly']:.1%} (기준 {ref['mdd_monthly']:.1%})  "
        f"월간 샤프 {got['sharpe_monthly']:.2f} (기준 {ref['sharpe_monthly']:.2f})  "
        f"시도 {n}/{MAX_ATTEMPTS}\n"
        f"   출처: {ref['source']}\n   참고: {CONTEXT['gem']}"
    )
    evidence = {
        "basis": f"공개 수치 ±{TOL_PUBLIC:.0%} (같은 기간·12개월·VEU 변형, 월 단위)",
        "period": f"{eq.index[0].date()}~{eq.index[-1].date()}",
        "metrics": got,
        "reference": {k: ref[k] for k in got},
        "source": ref["source"],
    }
    return G1Result("gem", ok, n, line, evidence)


def check_vol_breakout(cache: CandleCache, tracker: AttemptTracker) -> G1Result:
    """변동성 돌파 기본 파라미터: 엔진 vs 독립 기준 구현, 같은 데이터·같은 홀드아웃."""
    strat = create("vol_breakout")
    raw = {s: load("upbit", s, "1d", cache=cache) for s in strat.symbols}
    res = Backtester(preset(strat.market), holdout_months=12).run(strat, raw)
    cut = max(df.index[-1] for df in raw.values()) - pd.DateOffset(months=12)
    data = {s: df[df.index <= cut] for s, df in raw.items()}
    cost = preset(strat.market)
    p = strat.params
    ref_eq, ref_entries = reference_vol_breakout(
        data,
        k=p["k"],
        target_vol=p["target_vol"],
        ma_windows=p["ma_windows"],
        max_weight=p["max_weight"],
        warmup=strat.warmup_bars,
        fee_rate=cost.fee_rate,
        slippage_rate=cost.slippage_rate,
    )
    eng = {"cagr": cagr(res.equity, 10_000_000.0), "mdd": max_drawdown(res.equity)}
    ref = {"cagr": cagr(ref_eq, 10_000_000.0), "mdd": max_drawdown(ref_eq)}
    entries = sum(1 for f in res.fills if f.side == Side.BUY)
    ok = (
        within(eng["cagr"], ref["cagr"], TOL_REFERENCE)
        and within(eng["mdd"], ref["mdd"], TOL_REFERENCE)
        and within(entries, ref_entries, TOL_ENTRIES)
    )
    n = tracker.count("vol_breakout")
    line = (
        f"vol_breakout(5코인) {res.metrics.start}~{res.metrics.end}: "
        f"엔진 CAGR {eng['cagr']:+.2%} / 기준 구현 {ref['cagr']:+.2%}  "
        f"MDD {eng['mdd']:.2%} / {ref['mdd']:.2%}  진입 {entries} / {ref_entries}  "
        f"시도 {n}/{MAX_ATTEMPTS}\n"
        f"   참고: {CONTEXT['vol_breakout']}"
    )
    evidence = {
        "basis": (f"독립 기준 구현 ±{TOL_REFERENCE:.0%} (CAGR·MDD), 진입 횟수 ±{TOL_ENTRIES:.0%}"),
        "period": f"{res.metrics.start}~{res.metrics.end}",
        "metrics": {**eng, "entries": entries},
        "reference": {**ref, "entries": ref_entries},
    }
    return G1Result("vol_breakout", ok, n, line, evidence)


CHECKS: dict[str, Callable[[CandleCache, AttemptTracker], G1Result]] = {
    "gem": check_gem,
    "vol_breakout": check_vol_breakout,
}


def run_all(cache: CandleCache, tracker: AttemptTracker) -> list[G1Result]:
    """두 전략을 판정한다. 데이터가 없거나 네트워크가 막히면 그 전략만 미달."""
    out = []
    for name, check in CHECKS.items():
        try:
            out.append(check(cache, tracker))
        except Exception as e:  # noqa: BLE001 — 데이터 없음·네트워크 등은 그 전략만 미달
            msg = f"{name}: 데이터 없음 ({type(e).__name__}) → qp fetch 먼저"
            out.append(G1Result(name, False, tracker.count(name), msg))
    return out


async def write_reports(config: Any, results: list[G1Result], now: datetime | None = None) -> int:
    """판정이 끝난 전략(증거가 있는 것)만 settings에 쓴다. 쓴 개수를 돌려준다."""
    from quantpilot.api.gates import g1_key

    now = now or datetime.now(UTC)
    written = 0
    for r in results:
        if not r.evidence:  # 데이터 없음 — 이전 결과를 지우지 않는다
            continue
        await config.set_setting(g1_key(r.strategy), r.report(now))
        written += 1
    return written
