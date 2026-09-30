"""엔진 재시작 전 안전 확인 (docs/07 §2).

엔진은 포지션이 없고 KRX 장중(KST 09:05~15:15)이 아닐 때만 재시작한다.
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

# KRX 보유분이 있을 수 있는 시간대 (docs/07 §2). 권장 재시작 시각은 KST 20:00~22:00
BLACKOUT_KST = (time(9, 5), time(15, 15))
RECOMMENDED_KST = (time(20, 0), time(22, 0))


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


def evaluate(now_utc: datetime, open_positions: dict[str, int]) -> GuardResult:
    """현재 시각과 시장별 열린 포지션 수로 재시작 가능 여부를 판정한다."""
    res = GuardResult()
    held = {m: n for m, n in open_positions.items() if n > 0}
    if held:
        res.reasons.append(
            "열린 포지션 있음: " + ", ".join(f"{m}={n}" for m, n in sorted(held.items()))
        )
    if in_blackout(now_utc):
        res.reasons.append("KRX 장중 시간대 (KST 09:05~15:15)")
    kst = now_utc.astimezone(TIMEZONES[Market.KRX]).time()
    lo, hi = RECOMMENDED_KST
    if not (lo <= kst < hi):
        res.notes.append("권장 재시작 시각은 KST 20:00~22:00")
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
