"""엔진 재시작 전 안전 확인 (docs/07 §2).

엔진은 막는 사유가 없을 때만 재시작한다 (ADR 0029): 재시작 복원이 없는 시장의 열린 포지션, 실시간 엔진이 있는
시장의 위험 시간대(KRX 장중 09:05~15:15, 업비트 시간 청산 08:55~09:05 KST). 복원되는 시장의 포지션은 알림만.
scripts/deploy.sh가 `python -m quantpilot.ops.deploy_guard`로 부르고, 종료 코드가 0이 아니면 `--force`를 요구한다.
출력에는 DB URL·키를 남기지 않는다 (불변식 #10).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime, time

from quantpilot.core.clock import TIMEZONES
from quantpilot.core.models import Market

# KRX 보유분이 있을 수 있는 시간대 (docs/07 §2) — KRX 실시간 엔진이 있을 때만 막는다 (ADR 0029)
BLACKOUT_KST = (time(9, 5), time(15, 15))
# 업비트 시간 청산(09:00 KST) 앞뒤 — 청산 명령·처리와 재시작이 겹치지 않게 매일 막는다
UPBIT_EXIT_KST = (time(8, 55), time(9, 5))
# 시간 청산 직후라 업비트 포지션이 가장 적은 시각
RECOMMENDED_KST = (time(9, 10), time(10, 0))
# 실시간 엔진이 도는 시장 (1단계: 업비트만)
LIVE: frozenset[Market] = frozenset({Market.UPBIT})
# 재시작해도 포지션·손절선·처리 표시를 복원하는 시장 (ADR 0028) — 열린 포지션은 알림만
RESTART_SAFE: frozenset[Market] = frozenset({Market.UPBIT})


@dataclass
class GuardResult:
    """재시작 가능 여부와 막는 사유."""

    reasons: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """막는 사유가 없으면 True."""
        return not self.reasons


def in_blackout(now_utc: datetime) -> bool:
    """평일 KST 09:05~15:15이면 True."""
    kst = now_utc.astimezone(TIMEZONES[Market.KRX])
    start, end = BLACKOUT_KST
    return kst.weekday() < 5 and start <= kst.time() < end


def in_upbit_exit_window(now_utc: datetime) -> bool:
    """매일 KST 08:55~09:05(업비트 시간 청산 앞뒤)이면 True."""
    kst = now_utc.astimezone(TIMEZONES[Market.KRX]).time()
    start, end = UPBIT_EXIT_KST
    return start <= kst < end


def evaluate(
    now_utc: datetime,
    open_positions: dict[str, int],
    *,
    live: frozenset[Market] = LIVE,
    restart_safe: frozenset[Market] = RESTART_SAFE,
) -> GuardResult:
    """현재 시각과 시장별 열린 포지션 수로 재시작 가능 여부를 판정한다 (ADR 0029)."""
    res = GuardResult()
    held = {m: n for m, n in open_positions.items() if n > 0}
    safe = {v.value for v in restart_safe}
    blocking = {m: n for m, n in held.items() if m not in safe}
    if blocking:
        res.reasons.append(
            "열린 포지션 있음: " + ", ".join(f"{m}={n}" for m, n in sorted(blocking.items()))
        )
    restored = {m: n for m, n in held.items() if m in safe}
    if restored:
        res.notes.append(
            "열린 포지션은 재시작 뒤 복원됨(ADR 0028): "
            + ", ".join(f"{m}={n}" for m, n in sorted(restored.items()))
        )
    if Market.KRX in live and in_blackout(now_utc):
        res.reasons.append("KRX 장중 시간대 (KST 09:05~15:15)")
    if Market.UPBIT in live and in_upbit_exit_window(now_utc):
        res.reasons.append("업비트 시간 청산 앞뒤 (KST 08:55~09:05)")
    kst = now_utc.astimezone(TIMEZONES[Market.KRX]).time()
    lo, hi = RECOMMENDED_KST
    if not (lo <= kst < hi):
        res.notes.append("권장 재시작 시각은 KST 09:10~10:00 (시간 청산 직후)")
    return res


async def count_open_positions(db_url: str) -> dict[str, int]:
    """positions 표의 시장별 열린 포지션 수."""
    from quantpilot.db.repo import SqlPositionRepo
    from quantpilot.db.session import make_sessions

    sessions = make_sessions(db_url)
    try:
        repo = SqlPositionRepo(sessions)
        return {m.value: len(await repo.all(m)) for m in Market}
    finally:
        await sessions.kw["bind"].dispose()


def main(argv: list[str] | None = None) -> int:
    """판정 결과를 출력하고 종료 코드로 알린다 (0 가능, 3 막힘, 4 확인 불가)."""
    p = argparse.ArgumentParser(prog="deploy_guard", description="엔진 재시작 전 안전 확인")
    p.add_argument("--db-url", default=None, help="기본 settings.db_url")
    a = p.parse_args(argv)
    from quantpilot.config import settings

    try:
        positions = asyncio.run(count_open_positions(a.db_url or settings.db_url))
    except Exception as e:  # noqa: BLE001 — URL(비밀번호)은 출력하지 않는다
        print(f"BLOCK: 포지션 확인 실패 ({type(e).__name__})")
        return 4
    res = evaluate(datetime.now(UTC), positions)
    for r in res.reasons:
        print(f"BLOCK: {r}")
    for n in res.notes:
        print(f"NOTE: {n}")
    if res.ok:
        print("OK: 엔진 재시작 가능")
        return 0
    return 3


if __name__ == "__main__":
    sys.exit(main())
