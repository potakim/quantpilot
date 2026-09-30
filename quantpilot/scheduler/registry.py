"""잡 표 (04 §8)와 등록.

잡은 선언형 `JobSpec` 목록이다. APScheduler에는 모듈 수준 함수 `run_job(name)`을 등록한다 — 잡 저장소
(SQLAlchemyJobStore)가 함수 참조와 인자(name)만 저장하면 되도록. 의존성은 `install(ctx)`로 넣는다.

시간대: KST 잡은 Asia/Seoul, 미국장 잡은 America/New_York cron이라 서머타임이 자동 반영된다
(09:35 ET = 22:35/23:35 KST, 15:55 ET = 04:55/05:55 KST). 조기 폐장일(13:00 ET)은 12:55 ET 잡이 맡는다.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from functools import partial
from typing import Any, Protocol

from quantpilot.scheduler.context import JobContext, run_hook
from quantpilot.scheduler.jobs import data, exits, health

log = logging.getLogger(__name__)

KST = "Asia/Seoul"
ET = "America/New_York"
UTC = "UTC"
# scheduler가 재시작 등으로 늦어도 1시간 안이면 한 번(coalesce) 실행한다 — 09:00 청산을 놓치지 않게
MISFIRE_GRACE = 3600


@dataclass(frozen=True)
class JobSpec:
    """잡 1개: 이름·트리거(cron|interval)·트리거 인자·본문."""

    name: str
    trigger: str
    fields: dict[str, Any]
    func: Callable[[JobContext], Awaitable[None]]
    doc: str = ""
    variants: tuple[dict[str, Any], ...] = field(
        default=()
    )  # 같은 본문을 다른 시각에도 (조기 폐장)


def _hook(name: str) -> Callable[[JobContext], Awaitable[None]]:
    return partial(run_hook, name=name)


JOBS: tuple[JobSpec, ...] = (
    JobSpec("upbit_daily_exit", "cron", {"hour": 9, "minute": 0, "second": 0, "timezone": KST},
            exits.upbit_daily_exit, "변동성 돌파 청산 → 목표가 재계산 → strategy.status"),
    JobSpec("krx_close_orders", "cron", {"day_of_week": "mon-fri", "hour": 15, "minute": 20, "timezone": KST},
            _hook("krx_close_orders"), "GTAA 종가 단일가 주문 (KRX 엔진 이후)"),
    JobSpec("us_orb_entry_window", "cron", {"day_of_week": "mon-fri", "hour": 9, "minute": 35, "timezone": ET},
            _hook("us_orb_entry_window"), "ORB 진입 창 (미국 엔진 이후)"),
    JobSpec("us_eod_exit", "cron", {"day_of_week": "mon-fri", "hour": 15, "minute": 55, "timezone": ET},
            exits.us_eod_exit, "ORB 청산",
            variants=({"day_of_week": "mon-fri", "hour": 12, "minute": 55, "timezone": ET},)),
    JobSpec("gem_rebalance", "cron", {"day_of_week": "mon-fri", "hour": 15, "minute": 55, "timezone": ET},
            exits.gem_rebalance, "월 마지막 거래일 GEM 리밸런싱",
            variants=({"day_of_week": "mon-fri", "hour": 12, "minute": 55, "timezone": ET},)),
    JobSpec("kis_token_refresh", "cron", {"hour": 8, "minute": 0, "timezone": KST},
            _hook("kis_token_refresh"), "KIS 토큰 재발급 (KIS 어댑터 이후)"),
    JobSpec("upbit_prescreen", "cron", {"hour": 8, "minute": 10, "timezone": KST},
            _hook("upbit_prescreen"), "LLM 2모델 news_risk 사전 심사 (P1-08 이후, ADR 0004)"),
    JobSpec("morning_brief", "cron", {"hour": 8, "minute": 30, "timezone": KST},
            _hook("morning_brief"), "Claude 아침 브리핑 (P1-08 이후)"),
    JobSpec("news_collect", "cron", {"minute": 5, "timezone": KST}, data.news_collect, "뉴스 수집·요약"),
    JobSpec("fill_realized_24h", "cron", {"minute": 10, "timezone": KST},
            data.fill_realized_24h, "judgments.realized_ret_24h"),
    JobSpec("daily_review", "cron", {"hour": 20, "minute": 30, "timezone": KST},
            data.daily_review, "사후 리뷰 → daily_reviews"),
    JobSpec("equity_snapshot", "interval", {"seconds": 60}, health.equity_snapshot, "equity_snapshots"),
    JobSpec("reconcile", "interval", {"seconds": 300}, _hook("reconcile"), "Reconciler (P1-10 이후)"),
    JobSpec("month_roll", "cron", {"day": 1, "hour": 0, "minute": 0, "timezone": UTC},
            health.month_roll, "month_start_equity 저장"),
    JobSpec("engine_heartbeat", "interval", {"seconds": 30}, health.engine_heartbeat,
            "하트비트 90초 없으면 알림 + 시간 청산 백업 모드"),
)  # fmt: skip

SPECS: dict[str, JobSpec] = {s.name: s for s in JOBS}

_ctx: JobContext | None = None


def install(ctx: JobContext) -> None:
    """잡이 쓸 의존성을 넣는다."""
    global _ctx
    _ctx = ctx


async def run_job(name: str) -> None:
    """등록된 잡 하나를 실행한다. 실패는 로그로 남기고 삼킨다 — 한 잡의 실패가 다른 잡을 멈추지 않게."""
    if _ctx is None:
        raise RuntimeError("scheduler context 미설치 (registry.install)")
    try:
        await SPECS[name].func(_ctx)
    except Exception as e:  # noqa: BLE001 — 메시지엔 키가 섞일 수 있어 타입만 남긴다
        log.error("job failed", extra={"job": name, "error": type(e).__name__})
        await _ctx.notifier.send("warning", f"job failed: {name} ({type(e).__name__})")


class Scheduler(Protocol):
    """APScheduler의 add_job 모양 (테스트는 가짜를 쓴다)."""

    def add_job(self, func: Any, trigger: str, **kwargs: Any) -> Any:
        """잡 1개를 등록한다."""
        ...


def register(scheduler: Scheduler, specs: tuple[JobSpec, ...] = JOBS) -> list[str]:
    """잡 표 전체를 등록하고 등록한 잡 id 목록을 돌려준다. 재시작 시 같은 id를 덮어쓴다."""
    ids: list[str] = []
    for spec in specs:
        for i, fields in enumerate((spec.fields, *spec.variants)):
            job_id = spec.name if i == 0 else f"{spec.name}#{i}"
            scheduler.add_job(
                run_job,
                spec.trigger,
                args=[spec.name],
                id=job_id,
                name=spec.name,
                replace_existing=True,
                coalesce=True,
                max_instances=1,
                misfire_grace_time=MISFIRE_GRACE,
                **fields,
            )
            ids.append(job_id)
    return ids
