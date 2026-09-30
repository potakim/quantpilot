"""게이팅 A/B 리포트·G2 판정·`qp report ab` (P1-11, 06 §6.2, 08 §5)."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

pytest.importorskip("aiosqlite")

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from quantpilot.core.clock import to_local
from quantpilot.core.events import BarClosed
from quantpilot.core.models import Fill, JudgeResult, Market, Order, Side, Target
from quantpilot.db.models import Base
from quantpilot.db.repo import (
    SqlCandleRepo,
    SqlConfigRepo,
    SqlJudgmentRepo,
    SqlLedger,
    SqlSignalRepo,
)
from quantpilot.judgment.ab import BookStats, book_stats, equity_curve, g2_verdict, hold_outcomes

M = Market.UPBIT
T = [(pd.Timestamp("2026-09-01") + pd.Timedelta(hours=h)).to_pydatetime() for h in range(5)]


def _fill(ts, side, qty, px, fee=0.0, sym="X"):
    return Fill("o", sym, side, qty, px, fee, 0.0, ts, market=M)


# ── 순수 계산 ─────────────────────────────────────────
def test_equity_curve_and_book_stats_match_hand_computation():
    prices = {"X": pd.Series([100.0, 100, 120, 90, 110], index=pd.DatetimeIndex(T))}
    fills = [_fill(T[1], Side.BUY, 5, 100)]
    curve = equity_curve(fills, prices, 1000)
    assert list(curve) == [1000, 1000, 1100, 950, 1050]
    st = book_stats(fills, prices, 1000, T[0], T[4])
    assert st.ret == pytest.approx(0.05)
    assert st.mdd == pytest.approx(1 - 950 / 1100)
    assert (st.n_trades, st.cost) == (1, 0.0)


def test_book_stats_window_starts_from_equity_before_window():
    prices = {"X": pd.Series([100.0, 200, 200, 100, 100], index=pd.DatetimeIndex(T))}
    fills = [_fill(T[0], Side.BUY, 5, 100, fee=1.0), _fill(T[4], Side.SELL, 5, 100, fee=1.0)]
    st = book_stats(fills, prices, 1000, T[2], T[4])
    # 창 시작 전 평가액 = 499 + 5×200 = 1499 → 창 끝 998
    assert st.ret == pytest.approx(998 / 1499 - 1)
    assert st.mdd == pytest.approx(1 - 998 / 1499)
    assert (st.n_trades, st.cost) == (1, 1.0)  # 창 안 체결만


@pytest.mark.parametrize(
    ("on_mdd", "off_mdd", "brier", "n", "want"),
    [
        (0.05, 0.10, 0.20, 19, "pending"),
        (0.05, 0.10, None, 25, "pending"),
        (0.05, 0.10, 0.20, 20, "pass"),
        (0.10, 0.10, 0.20, 20, "fail"),
        (0.05, 0.10, 0.25, 20, "fail"),
    ],
)
def test_g2_verdict(on_mdd, off_mdd, brier, n, want):
    on, off = BookStats(0.0, on_mdd, 1, 0.0), BookStats(0.0, off_mdd, 1, 0.0)
    assert g2_verdict(on, off, brier, n)[0] == want


def test_hold_outcomes_asks_whether_not_buying_was_right():
    js = [
        {"gate": "hold", "realized_ret_24h": -0.02},
        {"gate": "hold", "realized_ret_24h": 0.01},
        {"gate": "hold", "realized_ret_24h": None},
        {"gate": "full", "realized_ret_24h": 0.05},
    ]
    assert hold_outcomes(js) == {
        "n": 2,
        "mean_ret": pytest.approx(-0.005),
        "right_share": 0.5,
    }


# ── DB → 리포트 → CLI ────────────────────────────────
async def _seed(sessions, end_local: datetime) -> None:
    """25개 판단(확신도 높으면 적중), ON은 급락 전 청산·섀도는 급락을 맞는 원장."""
    sid = await SqlConfigRepo(sessions).upsert_strategy(
        name="vol_breakout", market=M, allocation=0.3, symbols=["KRW-BTC"]
    )
    signals, judgments = SqlSignalRepo(sessions), SqlJudgmentRepo(sessions)
    t0 = end_local - timedelta(days=20)
    for i in range(25):
        ts = t0 + timedelta(hours=i)
        sig = await signals.add(
            strategy_id=sid, market=M, target=Target("KRW-BTC", 0.3), kind="entry", ts=ts
        )
        hit = i % 5 != 0  # 20/25 적중
        conf = 0.92 if hit else 0.55
        gate = "full" if conf >= 0.9 else "hold"
        jid = await judgments.add(
            signal_id=sig,
            result=JudgeResult({"signal_quality": conf}, conf, model="jev"),
            state={},
            gate=gate,
            blocks=[],
            ts=ts,
        )
        await judgments.set_realized(jid, 0.01 if hit else -0.01, hit)
    # 봉: 100 → (5일째) 70 → (끝) 90, 1시간 간격 1분봉
    candles = SqlCandleRepo(sessions)
    bars = []
    for h in range(20 * 24):
        ts = t0 + timedelta(hours=h)
        px = 100.0 if h < 5 * 24 else (70.0 if h < 10 * 24 else 90.0)
        bars.append(BarClosed(M, "KRW-BTC", "1m", ts, px, px, px, px, 1.0))
    await candles.upsert(bars)
    buy_ts, sell_ts = t0 + timedelta(hours=1), t0 + timedelta(days=4)
    for ledger, sell in ((SqlLedger(sessions), True), (SqlLedger(sessions, shadow=True), False)):
        orders = [(buy_ts, Side.BUY)] + ([(sell_ts, Side.SELL)] if sell else [])
        for ts, side in orders:
            o = Order("KRW-BTC", side, 30_000, market=M, ts=ts, strategy="vol_breakout")
            await ledger.save_order(o)
            await ledger.record(
                Fill(o.id, "KRW-BTC", side, 30_000, 100.0, 1_500.0, 0.0, ts, market=M)
            )


@pytest.fixture
def db_url(tmp_path):
    path = tmp_path / "ab.db"
    sync = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(sync)
    sync.dispose()
    return f"sqlite+aiosqlite:///{path}"


def _sessions(url):
    return async_sessionmaker(create_async_engine(url), expire_on_commit=False)


def test_build_ab_report_from_db(db_url):
    from quantpilot.cli import build_ab_report

    now = datetime(2026, 9, 30, 3, 0, tzinfo=UTC)
    end_local = to_local(now, M)
    sessions = _sessions(db_url)

    async def go():
        await _seed(sessions, end_local)
        return await build_ab_report(sessions, M, weeks=4, now=now, initial_cash=10_000_000)

    r = asyncio.run(go())
    assert r["signals"] == 25 and r["calibration"]["n"] == 25
    assert r["calibration"]["brier"] == pytest.approx((20 * 0.08**2 + 5 * 0.55**2) / 25)
    assert r["on"]["n_trades"] == 2 and r["off"]["n_trades"] == 1
    assert r["on"]["mdd"] < r["off"]["mdd"]
    # 고점 = 창 시작 평가액 1천만 → 수수료 1,500 + 3만주×(100−70) 하락
    assert r["off"]["mdd"] == pytest.approx((1_500 + 900_000) / 10_000_000)
    assert r["hold"]["n"] == 5 and r["hold"]["right_share"] == 1.0
    assert r["g2"] == "pass" and r["g2_pass"]


def test_qp_report_ab_prints_markdown_and_json(db_url, tmp_path, capsys):
    from quantpilot.cli import main

    end_local = to_local(datetime.now(UTC), M)
    asyncio.run(_seed(_sessions(db_url), end_local - timedelta(hours=1)))
    out = tmp_path / "ab.md"
    assert main(["report", "ab", "--weeks", "4", "--db-url", db_url, "--out", str(out)]) == 0
    text = capsys.readouterr().out
    assert "# 게이팅 A/B 리포트" in text and "**G2: ✅ 통과**" in text
    assert "| ON (게이팅) |" in text and "| OFF (섀도) |" in text
    assert "진입 신호 25건" in text and "| 0.9+ | 20 | 100% | 0.92 |" in text
    assert "hold된 신호" in text and out.read_text(encoding="utf-8") == text.rstrip("\n") + "\n"

    assert main(["report", "ab", "--db-url", db_url, "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["g2_pass"] is True and data["signals"] == 25


def test_empty_db_reports_pending(db_url, capsys):
    from quantpilot.cli import main

    assert main(["report", "ab", "--db-url", db_url]) == 0
    text = capsys.readouterr().out
    assert "판정 보류" in text and "실현 수익률이 채워진 판단이 없다" in text
