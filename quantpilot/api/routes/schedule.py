"""오늘 일정 (ADR 0020 §4): scheduler 잡 표(JOBS)의 cron에서 계산한 오늘(KST) 실행 시각.

읽기 전용이다 — 이벤트를 발행하지 않고 scheduler·realtime 배선을 바꾸지 않는다. 변동성 돌파 목표가는
scheduler 잡과 같은 함수(`exits.breakout_targets`)로 계산한다.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends

from quantpilot.api import metrics, queries
from quantpilot.api.auth import require_user
from quantpilot.api.deps import DepsDep
from quantpilot.core import clock
from quantpilot.core.models import Market

log = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_user)])

# 잡 이름 → (시장, 화면 문구). 여기 없는 잡(매시·interval)은 일정에 싣지 않는다.
JOB_LABELS: dict[str, tuple[Market | None, str]] = {
    "upbit_daily_exit": (Market.UPBIT, "변동성 돌파 보유분 청산 · 목표가 재계산"),
    "krx_close_orders": (Market.KRX, "GTAA 종가 단일가 주문"),
    "us_orb_entry_window": (Market.US, "ORB 진입 창 열림"),
    "us_eod_exit": (Market.US, "ORB 장 마감 전 청산"),
    "gem_rebalance": (Market.US, "GEM 월말 리밸런싱 확인"),
    "kis_token_refresh": (Market.KRX, "한국투자증권 토큰 재발급"),
    "upbit_prescreen": (Market.UPBIT, "뉴스 위험 사전 심사"),
    "morning_brief": (None, "AI 아침 브리핑"),
    "daily_review": (None, "오늘 매매 사후 리뷰"),
    "month_roll": (None, "월초 평가액 저장"),
}


def _won(v: float) -> str:
    return f"₩{round(v):,}"


async def _breakout_item(deps: Any, now_utc: Any) -> dict[str, Any]:
    """vol_breakout 목표가 항목. 가장 최근 09:00(KST) 기준, 봉이 없으면 목표가 계산 불가."""
    from quantpilot.db.repo import SqlCandleRepo
    from quantpilot.scheduler.jobs.exits import breakout_targets

    now = clock.to_local(now_utc, Market.UPBIT)
    nine = now.replace(hour=9, minute=0, second=0, microsecond=0)
    anchor = nine if now >= nine else nine - timedelta(days=1)
    strat, _, targets = await breakout_targets(SqlCandleRepo(deps.sessions), anchor)
    if targets:
        what = " · ".join(f"{t['symbol']} 목표가 {_won(t['target'])} 돌파 시 진입" for t in targets)
        at = now_utc
    else:
        sym = ", ".join(strat.symbols)
        what = f"{sym} 목표가 계산 불가 (1분봉 없음)"
        at = clock.to_utc(anchor + timedelta(days=1), Market.UPBIT)
    return {
        "name": strat.name,
        "market": Market.UPBIT.value,
        "next_action": {"at": queries.iso(at), "what": what},
        "done": False,
        "targets": targets,
    }


@router.get("/schedule")
async def schedule(deps: DepsDep) -> list[dict[str, Any]]:
    """오늘(KST 0~24시) 예약 작업 + 변동성 돌파 목표가. 지난 작업은 done=true."""
    from quantpilot.scheduler.registry import JOBS

    now_utc = deps.utcnow()
    day0 = metrics.day_start(now_utc, Market.UPBIT)
    start, end = (
        clock.to_utc(day0, Market.UPBIT),
        clock.to_utc(day0 + timedelta(days=1), Market.UPBIT),
    )
    items = []
    for spec in JOBS:
        if spec.trigger != "cron" or spec.name not in JOB_LABELS:
            continue
        market, what = JOB_LABELS[spec.name]
        for at in metrics.cron_fires(spec.fields, start, end):
            if market in (Market.KRX, Market.US):
                local = clock.to_local(at, market)
                if not clock.MarketClock(market).is_session(local.date()):
                    continue  # 휴장일
            items.append(
                {
                    "name": spec.name,
                    "market": market.value if market else None,
                    "next_action": {"at": queries.iso(at), "what": what},
                    "done": at <= now_utc,
                }
            )
    items.append(await _breakout_item(deps, now_utc))
    items.sort(key=lambda i: i["next_action"]["at"])
    return items
