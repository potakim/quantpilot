"""판단 모델 보정 지표 (06 §6.1, 04 judgment 표). 1단계 P1-11.

- 정답 y: 진입 후보의 24시간 후 방향 (`direction_hit`, 없으면 `realized_ret_24h > 0`)
- 예측 확률 p: `confidence`(기본) 또는 `answers["signal_quality"]`
- Brier = mean((p − y)²). 기준선 0.25(동전), G2 조건 < 0.25
- ECE = Σ |avg_conf − hit_rate| × n_bucket / N, 동일 폭 10구간
- 확신도 구간별 적중률(0.5~0.7, 0.7~0.9, 0.9+). 적중률이 구간을 따라 내려가면 "보정 불량"

`realized_ret_24h`가 채워진 행만 쓴다. 계산만 한다 — AI에게 아무것도 묻지 않는다 (불변식 #7).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from itertools import pairwise
from typing import Any

BUCKETS: tuple[tuple[float, float], ...] = ((0.5, 0.7), (0.7, 0.9), (0.9, 1.0))
BRIER_COIN = 0.25


@dataclass(frozen=True)
class Sample:
    """보정 표본 1개: 예측 확률 p와 정답 hit."""

    p: float
    hit: bool
    provider: str = ""


@dataclass(frozen=True)
class Bucket:
    """확신도 구간 하나의 집계. n이 0이면 hit_rate·avg_conf는 None."""

    lo: float
    hi: float
    n: int
    hit_rate: float | None
    avg_conf: float | None

    @property
    def label(self) -> str:
        """화면·리포트용 구간 이름 (예: 0.7~0.9, 0.9+)."""
        return f"{self.lo:g}+" if self.hi >= 1.0 else f"{self.lo:g}~{self.hi:g}"


def samples(judgments: Iterable[Mapping[str, Any]], *, p: str = "confidence") -> list[Sample]:
    """판단 행(dict) → 표본. realized_ret_24h가 비어 있거나 p가 없는 행은 뺀다."""
    out = []
    for j in judgments:
        ret = j.get("realized_ret_24h")
        if ret is None:
            continue
        prob = _prob(j, p)
        if prob is None:
            continue
        hit = j.get("direction_hit")
        out.append(
            Sample(prob, bool(ret > 0 if hit is None else hit), str(j.get("provider") or ""))
        )
    return out


def brier(judgments: Iterable[Mapping[str, Any]], *, p: str = "confidence") -> float | None:
    """Brier score. 표본이 없으면 None."""
    ss = samples(judgments, p=p)
    if not ss:
        return None
    return sum((s.p - float(s.hit)) ** 2 for s in ss) / len(ss)


def ece(
    judgments: Iterable[Mapping[str, Any]], bins: int = 10, *, p: str = "confidence"
) -> float | None:
    """Expected Calibration Error, [0,1]을 bins개 동일 폭 구간으로 (마지막 구간은 1.0 포함)."""
    ss = samples(judgments, p=p)
    if not ss:
        return None
    groups: dict[int, list[Sample]] = {}
    for s in ss:
        groups.setdefault(min(int(s.p * bins + 1e-9), bins - 1), []).append(s)
    total = 0.0
    for g in groups.values():
        avg_conf = sum(s.p for s in g) / len(g)
        hit_rate = sum(s.hit for s in g) / len(g)
        total += abs(avg_conf - hit_rate) * len(g)
    return total / len(ss)


def bucket_hit_rates(
    judgments: Iterable[Mapping[str, Any]],
    ranges: Sequence[tuple[float, float]] = BUCKETS,
    *,
    p: str = "confidence",
) -> list[Bucket]:
    """확신도 구간별 적중률. 구간은 [lo, hi), hi가 1.0이면 1.0 포함. 구간 밖(hold 영역)은 뺀다."""
    ss = samples(judgments, p=p)
    out = []
    for lo, hi in ranges:
        g = [s for s in ss if lo <= s.p < hi or (hi >= 1.0 and s.p == hi)]
        if not g:
            out.append(Bucket(lo, hi, 0, None, None))
            continue
        out.append(
            Bucket(lo, hi, len(g), sum(s.hit for s in g) / len(g), sum(s.p for s in g) / len(g))
        )
    return out


def is_monotonic(buckets: Sequence[Bucket]) -> bool:
    """표본이 있는 구간의 적중률이 확신도를 따라 내려가지 않으면 True. False면 "보정 불량" 배지."""
    rates = [b.hit_rate for b in buckets if b.hit_rate is not None]
    return all(a <= b for a, b in pairwise(rates))


def calibration(
    judgments: Iterable[Mapping[str, Any]], *, bins: int = 10, p: str = "confidence"
) -> dict[str, Any]:
    """03 `/judgments/calibration` 모양의 요약: brier·ece·n·buckets·monotonic·by_provider."""
    rows = list(judgments)
    ss = samples(rows, p=p)
    buckets = bucket_hit_rates(rows, p=p)
    by_provider: dict[str, dict[str, Any]] = {}
    for name in sorted({s.provider for s in ss}):
        sub = [j for j in rows if str(j.get("provider") or "") == name]
        by_provider[name] = {
            "brier": brier(sub, p=p),
            "ece": ece(sub, bins, p=p),
            "n": len(samples(sub, p=p)),
        }
    return {
        "brier": brier(rows, p=p),
        "ece": ece(rows, bins, p=p),
        "n": len(ss),
        "buckets": [
            {"range": b.label, "n": b.n, "hit_rate": b.hit_rate, "avg_conf": b.avg_conf}
            for b in buckets
        ],
        "monotonic": is_monotonic(buckets),
        "by_provider": by_provider,
    }


def _prob(j: Mapping[str, Any], p: str) -> float | None:
    if p == "confidence":
        v = j.get("confidence")
    elif p == "signal_quality":
        v = (j.get("answers") or {}).get("signal_quality")
    else:
        raise ValueError(f"예측 확률 키는 confidence | signal_quality: {p!r}")
    return None if v is None else float(v)
