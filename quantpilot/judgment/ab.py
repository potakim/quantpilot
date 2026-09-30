"""게이팅 ON/OFF A/B 비교와 관문 G2 판정 (06 §6.2, 08 §5, ADR 0016). 1단계 P1-11.

원장별 자산 곡선은 체결을 처음부터 재생하고 봉 종가로 평가해서 만든다. equity_snapshots는 ON 계좌만 있으므로
두 원장을 같은 방법으로 재기 위해서다. 판정: n ≥ 20이고 MDD_ON < MDD_OFF이고 Brier < 0.25 → 통과.
n < 20이면 "판정 보류". 수익률은 참고(ON이 낮아도 통과 — 목표는 방어).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pandas as pd

from quantpilot.backtest.metrics import drawdown
from quantpilot.core.models import Fill, Side
from quantpilot.judgment.calibration import BRIER_COIN, calibration

MIN_SAMPLES = 20


@dataclass(frozen=True)
class BookStats:
    """원장 하나의 구간 성과. mdd는 양수 비율 (0.12 = 12% 낙폭)."""

    ret: float
    mdd: float
    n_trades: int
    cost: float


def equity_curve(
    fills: Sequence[Fill], prices: Mapping[str, pd.Series], initial_cash: float
) -> pd.Series:
    """체결을 처음부터 재생해 가격 시각마다 (현금 + 보유 평가액)을 낸다. 가격이 없던 심볼은 체결가로."""
    fills = sorted(fills, key=lambda f: f.ts)
    index = sorted({ts for s in prices.values() for ts in s.index} | {f.ts for f in fills})
    cash = float(initial_cash)
    qty: dict[str, float] = {}
    last: dict[str, float] = {}
    it = iter(fills)
    nxt = next(it, None)
    out = []
    for ts in index:
        while nxt is not None and nxt.ts <= ts:
            signed = nxt.qty if nxt.side == Side.BUY else -nxt.qty
            cash -= signed * nxt.price + nxt.fee + nxt.tax
            qty[nxt.symbol] = qty.get(nxt.symbol, 0.0) + signed
            last[nxt.symbol] = nxt.price
            nxt = next(it, None)
        for sym, s in prices.items():
            if ts in s.index:
                last[sym] = float(s.loc[ts])
        out.append(cash + sum(q * last.get(sym, 0.0) for sym, q in qty.items()))
    return pd.Series(out, index=pd.DatetimeIndex(index), dtype=float)


def book_stats(
    fills: Sequence[Fill],
    prices: Mapping[str, pd.Series],
    initial_cash: float,
    start: datetime,
    end: datetime,
) -> BookStats:
    """[start, end] 구간의 수익률·MDD·체결 수·비용. 곡선은 계좌 시작부터 재생한다."""
    curve = equity_curve(fills, prices, initial_cash)
    before = curve[curve.index < pd.Timestamp(start)]
    window = curve[(curve.index >= pd.Timestamp(start)) & (curve.index <= pd.Timestamp(end))]
    base = float(before.iloc[-1]) if len(before) else float(initial_cash)
    window = pd.concat([pd.Series([base], index=[pd.Timestamp(start)]), window])
    in_win = [f for f in fills if start <= f.ts <= end]
    return BookStats(
        ret=float(window.iloc[-1]) / base - 1,
        mdd=float(-drawdown(window).min()),
        n_trades=len(in_win),
        cost=sum(f.fee + f.tax for f in in_win),
    )


def hold_outcomes(judgments: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """hold된 신호의 사후 24h 수익률 — "안 산 게 맞았나" (08 §5)."""
    rets = [
        float(j["realized_ret_24h"])
        for j in judgments
        if j.get("gate") == "hold" and j.get("realized_ret_24h") is not None
    ]
    if not rets:
        return {"n": 0, "mean_ret": None, "right_share": None}
    return {
        "n": len(rets),
        "mean_ret": sum(rets) / len(rets),
        "right_share": sum(r <= 0 for r in rets) / len(rets),
    }


def g2_verdict(on: BookStats, off: BookStats, brier: float | None, n: int) -> tuple[str, str]:
    """관문 G2: ("pass" | "fail" | "pending", 이유)."""
    if n < MIN_SAMPLES or brier is None:
        return "pending", f"표본 {n}건 < {MIN_SAMPLES} — 판정 보류"
    reasons = []
    if not on.mdd < off.mdd:
        reasons.append(f"MDD ON {on.mdd:.2%} ≥ OFF {off.mdd:.2%}")
    if not brier < BRIER_COIN:
        reasons.append(f"Brier {brier:.4f} ≥ {BRIER_COIN}")
    return ("fail", "; ".join(reasons)) if reasons else ("pass", "MDD_ON < MDD_OFF, Brier < 0.25")


def ab_report(
    judgments: Sequence[Mapping[str, Any]],
    on: BookStats,
    off: BookStats,
    *,
    start: datetime,
    end: datetime,
    signals: int,
) -> dict[str, Any]:
    """03 `/judgments/ab` 모양에 보정·hold·판정을 더한 요약."""
    cal = calibration(judgments)
    verdict, why = g2_verdict(on, off, cal["brier"], cal["n"])
    return {
        "start": start,
        "end": end,
        "signals": signals,
        "on": vars(on),
        "off": vars(off),
        "calibration": cal,
        "hold": hold_outcomes(judgments),
        "g2": verdict,
        "g2_pass": verdict == "pass",
        "g2_reason": why,
    }


def render_markdown(r: Mapping[str, Any]) -> str:
    """`qp report ab` 마크다운."""
    cal, hold = r["calibration"], r["hold"]
    badge = {"pass": "✅ 통과", "fail": "❌ 미통과", "pending": "⏸ 판정 보류"}[r["g2"]]
    lines = [
        f"# 게이팅 A/B 리포트 ({r['start']:%Y-%m-%d} ~ {r['end']:%Y-%m-%d})",
        "",
        f"**G2: {badge}** — {r['g2_reason']}",
        "",
        f"진입 신호 {r['signals']}건 (ON·OFF 같은 신호)",
        "",
        "| 원장 | 수익률 | MDD | 체결 수 | 비용 |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for name, key in (("ON (게이팅)", "on"), ("OFF (섀도)", "off")):
        b = r[key]
        lines.append(
            f"| {name} | {b['ret']:+.2%} | {b['mdd']:.2%} | {b['n_trades']} | {b['cost']:,.0f} |"
        )
    lines += ["", "## 판단 모델 보정", ""]
    if cal["n"] == 0:
        lines.append("실현 수익률이 채워진 판단이 없다.")
    else:
        flag = "" if cal["monotonic"] else " — ⚠ 보정 불량(구간별 적중률이 단조 증가하지 않음)"
        lines += [
            f"Brier {cal['brier']:.4f} (기준 {BRIER_COIN}) · ECE {cal['ece']:.4f} · n {cal['n']}{flag}",
            "",
            "| 확신도 구간 | n | 적중률 | 평균 확신도 |",
            "| --- | ---: | ---: | ---: |",
        ]
        for b in cal["buckets"]:
            hr = "—" if b["hit_rate"] is None else f"{b['hit_rate']:.0%}"
            ac = "—" if b["avg_conf"] is None else f"{b['avg_conf']:.2f}"
            lines.append(f"| {b['range']} | {b['n']} | {hr} | {ac} |")
    lines += ["", "## hold된 신호의 사후 수익률", ""]
    if hold["n"] == 0:
        lines.append("사후 수익률이 채워진 hold 신호가 없다.")
    else:
        lines.append(
            f"{hold['n']}건 · 평균 24h 수익률 {hold['mean_ret']:+.2%} · "
            f"안 산 게 맞은 비율 {hold['right_share']:.0%}"
        )
    return "\n".join(lines) + "\n"
