"""배포용 이벤트 캘린더 deploy/events.yaml (t25) — 실제 로더로 읽히고 compose가 꽂아 준다."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import yaml

from quantpilot.data.events import EventCalendar

ROOT = Path(__file__).resolve().parents[1]
EVENTS = ROOT / "deploy" / "events.yaml"


def _cal() -> EventCalendar:
    return EventCalendar.from_yaml(EVENTS)


def test_deploy_events_parse_and_cover_macro_kinds():
    cal = _cal()
    every = cal.within(
        "KRW-BTC", datetime(2026, 1, 1, tzinfo=UTC), datetime(2028, 1, 1, tzinfo=UTC)
    )
    assert len(every) == len(cal) and len(cal) >= 9  # symbols 비움 = 모든 종목에 해당
    assert {e.kind for e in every} == {"fomc", "cpi", "bok_rate"}


def test_us_events_follow_daylight_saving():
    """10월 FOMC는 EDT(UTC-4), 12월은 EST(UTC-5) — 현지 14:00을 core/clock이 UTC로 바꾼다."""
    fomc = [
        e
        for e in _cal().within(
            "x", datetime(2026, 10, 1, tzinfo=UTC), datetime(2026, 12, 31, tzinfo=UTC)
        )
        if e.kind == "fomc"
    ]
    assert [e.ts for e in fomc] == [
        datetime(2026, 10, 28, 18, 0, tzinfo=UTC),
        datetime(2026, 12, 9, 19, 0, tzinfo=UTC),
    ]


def test_date_only_event_is_local_midnight():
    (bok,) = [
        e
        for e in _cal().within(
            "x", datetime(2026, 10, 20, tzinfo=UTC), datetime(2026, 10, 23, tzinfo=UTC)
        )
        if e.kind == "bok_rate"
    ]
    assert bok.ts == datetime(2026, 10, 21, 15, 0, tzinfo=UTC)  # 2026-10-22 00:00 KST


def test_compose_mounts_event_calendar_into_app_services():
    compose = yaml.safe_load((ROOT / "deploy" / "compose.yml").read_text(encoding="utf-8"))
    app = compose["x-app"]
    assert app["environment"]["QP_EVENTS_FILE"] == "/app/config/events.yaml"
    assert "./events.yaml:/app/config/events.yaml:ro" in app["volumes"]
    for name in ("engine", "scheduler"):
        assert (
            compose["services"][name]["environment"]["QP_EVENTS_FILE"] == "/app/config/events.yaml"
        )
        assert "./events.yaml:/app/config/events.yaml:ro" in compose["services"][name]["volumes"]
