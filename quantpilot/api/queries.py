"""API 읽기 전용 조회 (02 스키마 → 03 응답). 시각은 UTC ISO-8601로 내보낸다.

목록은 `?limit=&before=<cursor>` 페이지네이션 (03 §1): cursor는 id 열이 있으면 id, 주문은 ts.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import func, select

from quantpilot.db.models import (
    BacktestRow,
    DailyReviewRow,
    FillRow,
    JudgmentRow,
    LlmVerdictRow,
    OrderRow,
    RiskEventRow,
    SignalRow,
    StrategyConfigRow,
)

MAX_LIMIT = 500


def iso(ts: datetime | date | None) -> str | None:
    """DB 시각 → UTC ISO. SQLite가 tz를 잃은 naive 값은 UTC로 본다."""
    if ts is None:
        return None
    if isinstance(ts, datetime):
        return (ts if ts.tzinfo else ts.replace(tzinfo=UTC)).astimezone(UTC).isoformat()
    return ts.isoformat()


def parse_ts(s: str | None) -> datetime | None:
    """쿼리 문자열 시각 → UTC aware. 날짜만 오면 그날 0시(UTC)."""
    if not s:
        return None
    try:
        ts = datetime.fromisoformat(s)
    except ValueError as e:
        from quantpilot.api.errors import ApiError

        raise ApiError(400, "INVALID_PARAM", f"시각 형식 오류: {s}") from e
    return (ts if ts.tzinfo else ts.replace(tzinfo=UTC)).astimezone(UTC)


def row_dict(row: Any) -> dict[str, Any]:
    """ORM 행 → dict (시각은 ISO)."""
    out = {}
    for c in row.__mapper__.column_attrs:
        v = getattr(row, c.key)
        out[c.key] = iso(v) if isinstance(v, datetime | date) else v
    return out


def clamp(limit: int) -> int:
    """limit를 1~500으로."""
    return max(1, min(int(limit), MAX_LIMIT))


def page(items: list[dict[str, Any]], limit: int, cursor_key: str) -> dict[str, Any]:
    """limit+1개를 읽어 넘치면 next_cursor를 단다."""
    more = len(items) > limit
    items = items[:limit]
    return {"items": items, "next_cursor": items[-1][cursor_key] if more and items else None}


async def orders(
    sessions: Any, *, market: str | None, status: str | None, limit: int, before: str | None
) -> dict[str, Any]:
    """주문 목록 (최신순)."""
    limit = clamp(limit)
    q = select(OrderRow)
    if market:
        q = q.where(OrderRow.market == market)
    if status:
        q = q.where(OrderRow.status == status)
    if before:
        q = q.where(OrderRow.ts < parse_ts(before))
    async with sessions() as s:
        rows = (await s.scalars(q.order_by(OrderRow.ts.desc()).limit(limit + 1))).all()
    return page([row_dict(r) for r in rows], limit, "ts")


async def order(sessions: Any, order_id: str) -> dict[str, Any] | None:
    """주문 1건."""
    async with sessions() as s:
        row = await s.get(OrderRow, order_id)
    return None if row is None else row_dict(row)


async def fills(
    sessions: Any,
    *,
    market: str | None,
    strategy: str | None,
    frm: str | None,
    to: str | None,
    limit: int,
    before: int | None,
) -> dict[str, Any]:
    """체결 원장 (최신순)."""
    limit = clamp(limit)
    q = select(FillRow)
    if market:
        q = q.where(FillRow.market == market)
    if strategy:
        q = q.where(FillRow.strategy == strategy)
    if frm:
        q = q.where(FillRow.ts >= parse_ts(frm))
    if to:
        q = q.where(FillRow.ts < parse_ts(to))
    if before:
        q = q.where(FillRow.id < before)
    async with sessions() as s:
        rows = (await s.scalars(q.order_by(FillRow.id.desc()).limit(limit + 1))).all()
    return page([row_dict(r) for r in rows], limit, "id")


async def verdicts_for(sessions: Any, ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    """판단 id → llm_verdicts 목록."""
    out: dict[int, list[dict[str, Any]]] = defaultdict(list)
    if not ids:
        return out
    q = select(LlmVerdictRow).where(LlmVerdictRow.judgment_id.in_(ids)).order_by(LlmVerdictRow.id)
    async with sessions() as s:
        for v in (await s.scalars(q)).all():
            out[v.judgment_id].append(row_dict(v))
    return out


def _judgment_view(j: JudgmentRow, sig: SignalRow, strategy: str, verdicts: list) -> dict:
    d = row_dict(j)
    d.pop("state", None)
    return {
        **d,
        "market": sig.market,
        "symbol": sig.symbol,
        "strategy": strategy,
        "kind": sig.kind,
        "outcome": sig.outcome,
        "outcome_reason": sig.outcome_reason,
        "verdicts": verdicts,
    }


async def judgments(
    sessions: Any,
    *,
    frm: str | None,
    to: str | None,
    market: str | None,
    strategy: str | None,
    outcome: str | None,
    symbol: str | None,
    limit: int,
    before: int | None,
) -> dict[str, Any]:
    """판단 로그: signals ⋈ judgments ⋈ llm_verdicts (최신순)."""
    limit = clamp(limit)
    q = (
        select(JudgmentRow, SignalRow, StrategyConfigRow.name)
        .join(SignalRow, JudgmentRow.signal_id == SignalRow.id)
        .join(StrategyConfigRow, SignalRow.strategy_id == StrategyConfigRow.id)
    )
    if frm:
        q = q.where(JudgmentRow.ts >= parse_ts(frm))
    if to:
        q = q.where(JudgmentRow.ts < parse_ts(to))
    if market:
        q = q.where(SignalRow.market == market)
    if strategy:
        q = q.where(StrategyConfigRow.name == strategy)
    if outcome:
        q = q.where(SignalRow.outcome == outcome)
    if symbol:
        q = q.where(SignalRow.symbol == symbol)
    if before:
        q = q.where(JudgmentRow.id < before)
    async with sessions() as s:
        rows = (await s.execute(q.order_by(JudgmentRow.id.desc()).limit(limit + 1))).all()
    vs = await verdicts_for(sessions, [j.id for j, _, _ in rows])
    return page([_judgment_view(j, sig, name, vs[j.id]) for j, sig, name in rows], limit, "id")


async def rule_unmet(
    sessions: Any,
    *,
    frm: str | None,
    to: str | None,
    market: str | None,
    strategy: str | None,
    symbol: str | None,
) -> int | None:
    """규칙 미충족 건수 (ADR 0027): 평가했지만 진입 target을 내지 않은 (전략, 종목), 거래일 단위.

    거래일 시작 시각(업비트는 09:00 KST, 그 밖은 현지 0시)이 [from, to) 안인 거래일만 센다.
    엔진이 아직 아무것도 쓰지 않았으면 None — 화면은 문구를 숨긴다.
    """
    from datetime import timedelta

    from quantpilot.core.clock import UPBIT_DAY_START, to_utc
    from quantpilot.core.models import Market
    from quantpilot.db.repo import SqlConfigRepo
    from quantpilot.engine.link import SettingsEngineLink

    lo, hi = parse_ts(frm), parse_ts(to)
    link = SettingsEngineLink(SqlConfigRepo(sessions))
    total: int | None = None
    for m in Market:
        if market and m.value != market:
            continue
        days = await link.rules(m)
        if not days:
            continue
        total = total or 0
        start = UPBIT_DAY_START if m == Market.UPBIT else timedelta(0)
        for day, by_strategy in days.items():
            begin = to_utc(datetime.fromisoformat(day) + start, m)
            if (lo and begin < lo) or (hi and begin >= hi):
                continue
            for name, rec in by_strategy.items():
                if strategy and name != strategy:
                    continue
                unmet = set(rec.get("evaluated", [])) - set(rec.get("signaled", []))
                total += len({symbol} & unmet) if symbol else len(unmet)
    return total


async def judgment_detail(sessions: Any, judgment_id: int) -> dict[str, Any] | None:
    """판단 상세: state 원문·answers·verdicts·관련 주문·체결·realized_ret_24h."""
    q = (
        select(JudgmentRow, SignalRow, StrategyConfigRow.name)
        .join(SignalRow, JudgmentRow.signal_id == SignalRow.id)
        .join(StrategyConfigRow, SignalRow.strategy_id == StrategyConfigRow.id)
        .where(JudgmentRow.id == judgment_id)
    )
    async with sessions() as s:
        hit = (await s.execute(q)).first()
        if hit is None:
            return None
        j, sig, name = hit
        order_rows = (await s.scalars(select(OrderRow).where(OrderRow.signal_id == sig.id))).all()
        oids = [o.id for o in order_rows]
        fill_rows = (
            (await s.scalars(select(FillRow).where(FillRow.order_id.in_(oids)))).all()
            if oids
            else []
        )
    vs = await verdicts_for(sessions, [j.id])
    out = _judgment_view(j, sig, name, vs[j.id])
    out["state"] = j.state
    out["signal"] = row_dict(sig)
    out["orders"] = [row_dict(o) for o in order_rows]
    out["fills"] = [row_dict(f) for f in fill_rows]
    return out


async def risk_events(sessions: Any, *, limit: int, before: int | None) -> dict[str, Any]:
    """리스크 이벤트 (최신순)."""
    limit = clamp(limit)
    q = select(RiskEventRow)
    if before:
        q = q.where(RiskEventRow.id < before)
    async with sessions() as s:
        rows = (await s.scalars(q.order_by(RiskEventRow.id.desc()).limit(limit + 1))).all()
    return page([row_dict(r) for r in rows], limit, "id")


async def reviews(sessions: Any, *, limit: int) -> list[dict[str, Any]]:
    """일일 매매 일지 (최신순)."""
    q = select(DailyReviewRow).order_by(DailyReviewRow.day.desc()).limit(clamp(limit))
    async with sessions() as s:
        rows = (await s.scalars(q)).all()
    return [{**row_dict(r), "date": iso(r.day)} for r in rows]


async def costs_ai(sessions: Any, *, month: str | None) -> dict[str, Any]:
    """월 AI 비용: 판단 모델(judgments)·LLM(llm_verdicts) 호출 수·USD를 provider별로."""
    now = datetime.now(UTC)
    y, m = (int(x) for x in month.split("-")) if month else (now.year, now.month)
    start = datetime(y, m, 1, tzinfo=UTC)
    end = datetime(y + (m == 12), m % 12 + 1, 1, tzinfo=UTC)
    jq = (
        select(JudgmentRow.provider, func.count(), func.coalesce(func.sum(JudgmentRow.cost_usd), 0))
        .where(JudgmentRow.ts >= start, JudgmentRow.ts < end)
        .group_by(JudgmentRow.provider)
    )
    vq = (
        select(
            LlmVerdictRow.model, func.count(), func.coalesce(func.sum(LlmVerdictRow.cost_usd), 0)
        )
        .join(JudgmentRow, LlmVerdictRow.judgment_id == JudgmentRow.id)
        .where(JudgmentRow.ts >= start, JudgmentRow.ts < end)
        .group_by(LlmVerdictRow.model)
    )
    async with sessions() as s:
        judge = (await s.execute(jq)).all()
        llm = (await s.execute(vq)).all()
    by: dict[str, dict[str, float]] = {}
    for kind, rows in (("judge", judge), ("llm", llm)):
        for name, n, usd in rows:
            by[f"{kind}:{name}"] = {"calls": int(n), "usd": float(usd or 0)}
    return {
        "month": f"{y:04d}-{m:02d}",
        "by_provider": by,
        "total_usd": sum(v["usd"] for v in by.values()),
    }


async def backtests(sessions: Any, *, strategy: str | None, limit: int = 100) -> list[dict]:
    """백테스트 목록 (최신순, attempt_no 포함)."""
    q = select(BacktestRow)
    if strategy:
        q = q.where(BacktestRow.strategy == strategy)
    async with sessions() as s:
        rows = (await s.scalars(q.order_by(BacktestRow.id.desc()).limit(clamp(limit)))).all()
    return [row_dict(r) for r in rows]


async def backtest(sessions: Any, backtest_id: int) -> dict[str, Any] | None:
    """백테스트 1건."""
    async with sessions() as s:
        row = await s.get(BacktestRow, backtest_id)
    return None if row is None else row_dict(row)
