"""실전 모드(QP_PAPER=false)에서 계좌를 읽는 API는 500 대신 503 LIVE_ACCOUNT_MISSING으로 알린다.

실계좌 연결은 2단계다. 페이퍼 계좌 복원(restore_account)은 settings.paper=False면 안전장치로 예외를 내는데,
그대로 두면 대시보드 총자산·수동 주문·청산이 이유를 알 수 없는 500이 됐다.
"""

from __future__ import annotations

import httpx
import pytest
from api_helpers import API, PASSWORD, make_settings, memory_sessions

from quantpilot.api.app import create_app
from quantpilot.core.models import Market
from quantpilot.realtime import keys as hk
from quantpilot.realtime.hub import MemoryHub

ACCOUNT_CALLS = [
    ("GET", "/portfolio", None),
    (
        "POST",
        "/orders",
        {"market": "upbit", "symbol": "KRW-BTC", "side": "buy", "type": "market", "amount": 10000},
    ),
    ("POST", "/positions/upbit/KRW-BTC/close", None),
]


@pytest.fixture
async def live_client(tmp_path, monkeypatch):
    from quantpilot.config import settings as global_settings

    monkeypatch.setattr(global_settings, "paper", False)
    engine, sessions = await memory_sessions()
    hub = MemoryHub()
    await hub.set(hk.px(Market.UPBIT, "KRW-BTC"), 100_000_000.0)
    app = create_app(settings=make_settings(tmp_path, paper=False), sessions=sessions, hub=hub)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.post(f"{API}/auth/login", json={"password": PASSWORD})
        client.headers["Authorization"] = f"Bearer {r.json()['token']}"
        yield client
    for t in list(app.state.tasks):
        t.cancel()
    app.state.backtest_pool.shutdown(wait=True)
    await engine.dispose()


@pytest.mark.parametrize(
    ("method", "path", "body"), ACCOUNT_CALLS, ids=[p for _, p, _ in ACCOUNT_CALLS]
)
async def test_account_endpoints_say_live_account_missing(live_client, method, path, body):
    """계좌를 읽는 API: 503 + 원인 코드·안내 문구 (화면은 message를 그대로 보여 준다)."""
    r = await live_client.request(method, f"{API}{path}", json=body)
    assert r.status_code == 503, r.text
    err = r.json()["error"]
    assert err["code"] == "LIVE_ACCOUNT_MISSING"
    assert "2단계" in err["message"]


async def test_other_endpoints_still_work_in_live_mode(live_client):
    """계좌를 읽지 않는 화면 API는 실전 모드에서도 그대로 — 헤더 배지는 /health의 paper를 쓴다."""
    r = await live_client.get(f"{API}/health")
    assert r.status_code == 200 and r.json()["paper"] is False
    for path in ("/positions", "/strategies", "/portfolio/equity?market=upbit"):
        r = await live_client.get(f"{API}{path}")
        assert r.status_code == 200, (path, r.text)
