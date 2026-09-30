"""관문 G1~G4 현재 상태 (00 §관문, 03 §2.6 `/reports/gates`, ADR 0017 §5).

API는 판정 근거를 모으기만 하고 규칙을 새로 만들지 않는다. 근거가 없으면 pass=false와 사유를 둔다.
- G1: `scripts/verify_g1.py`가 settings `gate_report.g1.<전략>`에 남긴 결과 + 시도 횟수(≤7)
- G2: CalibrationSource(t13)의 A/B·Brier. 주입되지 않았으면 pass=false
- G3: 최근 28일 주문 오류율(<1%, 리스크 거부 제외) — 28일 운영 전에는 pass=false
- G4: 최근 8주 서킷브레이커(risk_events monthly*) 미발동 + 실체결 슬리피지 — 페이퍼 단계에서는 pass=false
`gate_report.*`는 PATCH /settings 허용 키가 아니다 — 화면에서 관문을 통과시킬 수 없다.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import func, select

from quantpilot.api.deps import Deps
from quantpilot.backtest.attempts import WARN_AFTER, AttemptTracker
from quantpilot.db.models import JudgmentRow, OrderRow, RiskEventRow
from quantpilot.strategies import REGISTRY

G2_DAYS = 28
G4_WEEKS = 8


def g1_key(strategy: str) -> str:
    """verify_g1 결과가 저장되는 settings 키."""
    return f"gate_report.g1.{strategy}"


async def g1_for(deps: Deps, strategy: str) -> dict[str, Any]:
    """전략 하나의 G1."""
    from quantpilot.db.repo import SqlConfigRepo

    report = await SqlConfigRepo(deps.sessions).get_setting(g1_key(strategy)) or {}
    attempts = AttemptTracker(deps.settings.attempts_file).count(strategy)
    within = report.get("within_20pct")
    ok = bool(within) and attempts <= WARN_AFTER
    reason = None
    if within is None:
        reason = "verify_g1 결과 없음"
    elif attempts > WARN_AFTER:
        reason = f"파라미터 시도 {attempts}회 > {WARN_AFTER}"
    return {
        "pass": ok,
        "evidence": {"within_20pct": within, "distinct_attempts": attempts, **report},
        "reason": reason,
    }


async def g2(deps: Deps) -> dict[str, Any]:
    """G2: 게이팅 ON MDD < OFF, Brier < 0.25. 근거는 CalibrationSource."""
    since = deps.utcnow() - timedelta(days=G2_DAYS)
    async with deps.sessions() as s:
        days = await s.scalar(
            select(func.count(func.distinct(func.date(JudgmentRow.ts)))).where(
                JudgmentRow.ts >= since
            )
        )
    out: dict[str, Any] = {
        "pass": False,
        "mdd_on": None,
        "mdd_off": None,
        "brier": None,
        "days": f"{int(days or 0)}/{G2_DAYS}",
    }
    if deps.calibration is None:
        out["reason"] = "calibration 미연결 (judgment/calibration.py)"
        return out
    ab = await deps.calibration.ab(4)
    cal = await deps.calibration.calibration(4)
    out["mdd_on"] = (ab.get("on") or {}).get("mdd")
    out["mdd_off"] = (ab.get("off") or {}).get("mdd")
    out["brier"] = cal.get("brier")
    out["pass"] = bool(ab.get("g2_pass"))
    return out


async def g3(deps: Deps) -> dict[str, Any]:
    """G3: 주문 오류율 < 1% (리스크 거부는 오류가 아니다)."""
    since = deps.utcnow() - timedelta(days=G2_DAYS)
    async with deps.sessions() as s:
        rows = (
            await s.execute(
                select(OrderRow.status, OrderRow.reject_reason, OrderRow.ts).where(
                    OrderRow.ts >= since
                )
            )
        ).all()
    total = len(rows)
    errors = sum(
        1 for st, reason, _ in rows if st == "rejected" and not (reason or "").startswith("risk:")
    )
    days = len({ts.date() for _, _, ts in rows})
    rate = errors / total if total else None
    ok = total > 0 and days >= G2_DAYS and rate is not None and rate < 0.01
    return {
        "pass": ok,
        "evidence": {"orders": total, "errors": errors, "error_rate": rate, "days": days},
        "reason": None if ok else "28일 운영 근거 부족 또는 오류율 1% 이상",
    }


async def g4(deps: Deps) -> dict[str, Any]:
    """G4: 8주 서킷브레이커 미발동 + 페이퍼 대비 실체결 슬리피지 < 0.1%p."""
    since = deps.utcnow() - timedelta(weeks=G4_WEEKS)
    async with deps.sessions() as s:
        n = await s.scalar(
            select(func.count())
            .select_from(RiskEventRow)
            .where(RiskEventRow.ts >= since, RiskEventRow.kind.like("monthly%"))
        )
    return {
        "pass": False,
        "evidence": {"circuit_breaker_events": int(n or 0), "slippage_diff": None},
        "reason": "실체결 슬리피지 근거 없음 (페이퍼 단계)",
    }


async def all_gates(deps: Deps) -> dict[str, Any]:
    """`/reports/gates` 응답."""
    by = {name: await g1_for(deps, name) for name in REGISTRY}
    return {
        "g1": {"pass": all(v["pass"] for v in by.values()), "evidence": {"by_strategy": by}},
        "g2": await g2(deps),
        "g3": await g3(deps),
        "g4": await g4(deps),
    }


async def go_live_missing(deps: Deps, strategy: str) -> list[str]:
    """실전 전환 전에 통과해야 할 관문(G1·G2, 07 §실전 전환) 중 빠진 것."""
    missing = []
    if not (await g1_for(deps, strategy))["pass"]:
        missing.append("g1")
    if not (await g2(deps))["pass"]:
        missing.append("g2")
    return missing
