"""엔진이 전략 설정(켜기/끄기·배분·파라미터)과 확신도 임계값을 따른다 (ADR 0032)."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from types import SimpleNamespace

import pandas as pd
import pytest
from api_helpers import memory_sessions
from test_engine_restart import DAY, FixedClock, _breakout_bars, _db_engine
from test_live_daily import _rising_days
from test_tick import CountingPipeline, Scripted, _bar, _runner

from quantpilot.core.models import Market, Side, Target
from quantpilot.engine.link import SettingsEngineLink
from quantpilot.execution.risk import RiskManager
from quantpilot.strategies import DEFAULT_CONFIG, create, strategy_config

T0 = pd.Timestamp("2024-01-02")
T1 = T0 + pd.Timedelta(days=1)


def _run(runner, clock, ts, *syms, px=100.0):
    clock.set(ts.to_pydatetime())
    asyncio.run(runner.on_bars_closed([_bar(s, ts, px) for s in syms]))


def _notional(broker, side=Side.BUY):
    return [round(f.qty * f.price) for f in broker.ledger if f.side == side]


# ---------- 기본 설정 ----------
def test_strategy_config_defaults_and_row_override():
    """설정 행이 없으면 권장 조합, 있으면 행 값 (API·엔진이 같은 함수)."""
    assert DEFAULT_CONFIG["vol_breakout"] == (True, 0.15)
    cfg = strategy_config("gem")
    assert (cfg["enabled"], cfg["allocation"], cfg["params"]) == (False, 0.40, {})
    assert cfg["symbols"] == list(create("gem").symbols)
    row = {"enabled": False, "allocation": 0.1, "params": {"k": 0.6}, "symbols": ["KRW-BTC"]}
    cfg = strategy_config("vol_breakout", row)
    assert (cfg["enabled"], cfg["allocation"], cfg["params"]) == (False, 0.1, {"k": 0.6})


# ---------- 사이징 ----------
def test_without_configs_backtest_sizing_is_unchanged():
    """apply_configs를 부르지 않으면(백테스트) 배분 1.0 — 목표 비중 × 평가액."""
    runner, broker, _, clock = _runner([Scripted("a", ("AAA",), {T0: [Target("AAA", 0.5)]})])
    _run(runner, clock, T0, "AAA")
    assert _notional(broker) == [500_000]


def test_allocation_scales_entry_size():
    """배정 자본 = 평가액 × 배분. 비중 0.5 × 배분 0.2 × 100만 = 10만."""
    runner, broker, _, clock = _runner([Scripted("a", ("AAA",), {T0: [Target("AAA", 0.5)]})])
    runner.apply_configs({"a": {"enabled": True, "allocation": 0.2}})
    _run(runner, clock, T0, "AAA")
    assert _notional(broker) == [100_000]


def test_risk_check_still_uses_full_account_equity():
    """종목 상한(25%)은 계좌 전체 기준 — 배정 자본(50만) 기준이면 12.5만으로 깎였을 것이다."""
    risk = RiskManager()
    script = {T0: [Target("AAA", 1.0)]}
    runner, broker, _, clock = _runner([Scripted("a", ("AAA",), script)], risk=risk)
    runner.apply_configs({"a": {"enabled": True, "allocation": 0.5}})
    _run(runner, clock, T0, "AAA")
    assert _notional(broker) == [250_000]


# ---------- 꺼짐·배분 0 ----------
def test_disabled_strategy_skips_entries_and_judgment_but_still_exits():
    """꺼진 전략: 진입 target은 판단 모델도 부르지 않고 버리고, 청산(비중 0)은 처리한다."""
    script = {T0: [Target("AAA", 0.5)], T1: [Target("AAA", 0.0), Target("BBB", 0.5)]}
    pipeline = CountingPipeline()
    runner, broker, bus, clock = _runner([Scripted("a", ("AAA", "BBB"), script)], pipeline=pipeline)
    runner.apply_configs({"a": {"enabled": True, "allocation": 0.2}})
    _run(runner, clock, T0, "AAA", "BBB")
    runner.apply_configs({"a": {"enabled": False, "allocation": 0.2}})
    _run(runner, clock, T1, "AAA", "BBB")
    assert [(f.symbol, f.side) for f in broker.ledger] == [("AAA", Side.BUY), ("AAA", Side.SELL)]
    assert pipeline.calls == 1  # T0 진입 한 번뿐
    signals = [e.target.symbol for t, e in bus.events if t == "signal"]
    assert signals == ["AAA", "AAA"]  # BBB 진입 신호는 남기지 않는다


@pytest.mark.parametrize(
    "configs",
    [{"a": {"enabled": True, "allocation": 0.0}}, {}],
    ids=["allocation_zero", "missing_is_off"],
)
def test_zero_allocation_or_missing_config_blocks_entries(configs):
    pipeline = CountingPipeline()
    script = {T0: [Target("AAA", 0.5)]}
    runner, broker, bus, clock = _runner([Scripted("a", ("AAA",), script)], pipeline=pipeline)
    runner.apply_configs(configs)
    _run(runner, clock, T0, "AAA")
    assert broker.ledger == [] and pipeline.calls == 0
    assert not [e for t, e in bus.events if t == "signal"]


def test_disabled_strategy_is_not_counted_in_rule_stats():
    """꺼진 전략의 평가는 규칙 미충족 집계(ADR 0027)에 넣지 않는다."""
    runner, _, _, clock = _runner([Scripted("a", ("AAA",), {})])
    runner.apply_configs({"a": {"enabled": False, "allocation": 0.2}})
    _run(runner, clock, T0, "AAA")
    assert runner.rule_days == {}
    runner.apply_configs({"a": {"enabled": True, "allocation": 0.2}})
    _run(runner, clock, T1, "AAA")
    assert "a" in runner.rule_days[T1.date().isoformat()]


# ---------- 파라미터 ----------
def test_params_change_recreates_strategy_and_keeps_old_on_invalid(caplog):
    runner, _, _, _ = _runner([create("vol_breakout")])
    cfg = {"enabled": True, "allocation": 0.15}
    runner.apply_configs({"vol_breakout": {**cfg, "params": {"k": 0.6}}})
    assert runner.strategies[0].params["k"] == 0.6
    with caplog.at_level(logging.ERROR):
        runner.apply_configs({"vol_breakout": {**cfg, "params": {"k": 99}}})
    assert runner.strategies[0].params["k"] == 0.6
    assert "파라미터 반영 실패" in caplog.text
    runner.apply_configs({"vol_breakout": {**cfg, "params": {}}})
    assert runner.strategies[0].params["k"] == 0.5


# ---------- MarketEngine 동기화 ----------
class MemConfig:
    def __init__(self, rows=(), settings=None):
        self.rows = list(rows)
        self.d = dict(settings or {})

    async def get_setting(self, key, default=None):
        return self.d.get(key, default)

    async def set_setting(self, key, value):
        self.d[key] = value

    async def strategies(self, market=None):
        return [r for r in self.rows if market is None or r["market"] == Market(market).value]


def _mem_engine(config):
    from quantpilot.engine.main import build_upbit_paper

    eng = build_upbit_paper()
    eng.link = SettingsEngineLink(config)
    eng.clock = FixedClock(DAY)
    eng.runner.pipeline = SimpleNamespace(hold_below=0.5, full_above=0.9)
    return eng


def test_engine_builds_all_upbit_strategies(no_events):
    from quantpilot.engine.main import upbit_strategies

    assert upbit_strategies() == ("vol_breakout",)


def test_restore_and_heartbeat_apply_strategy_configs(no_events):
    """시작 직후(restore)와 하트비트마다 설정을 읽는다. 행이 없으면 기본 설정."""
    config = MemConfig()
    eng = _mem_engine(config)
    asyncio.run(eng.restore())
    assert eng.runner.allocation("vol_breakout") == 0.15
    assert eng.runner.entries_allowed("vol_breakout")
    config.rows = [
        {"name": "vol_breakout", "market": "upbit", "enabled": False, "allocation": 0.1,
         "params": {"k": 0.6}, "symbols": ["KRW-BTC"], "paper": True}
    ]  # fmt: skip
    asyncio.run(eng.on_link(DAY + timedelta(seconds=10)))
    assert eng.runner.allocation("vol_breakout") == 0.1
    assert not eng.runner.entries_allowed("vol_breakout")
    assert eng.runner.strategies[0].params["k"] == 0.6


def test_heartbeat_applies_gate_thresholds_and_ignores_invalid(no_events, caplog):
    config = MemConfig(settings={"gate.hold_below": 0.6})
    eng = _mem_engine(config)
    asyncio.run(eng.restore())
    assert (eng.runner.pipeline.hold_below, eng.runner.pipeline.full_above) == (0.6, 0.9)
    config.d.update({"gate.hold_below": 0.65, "gate.full_above": 0.6})  # 보류 ≥ 전량
    with caplog.at_level(logging.WARNING):
        asyncio.run(eng.on_link(DAY + timedelta(seconds=10)))
    assert (eng.runner.pipeline.hold_below, eng.runner.pipeline.full_above) == (0.6, 0.9)
    assert "범위 밖" in caplog.text


@pytest.fixture
def no_events(monkeypatch, tmp_path):
    from quantpilot.config import settings

    monkeypatch.setattr(settings, "events_file", tmp_path / "none.yaml")


# ---------- DB 엔진 통합 ----------
def test_db_engine_sizes_breakout_by_default_allocation_and_honors_disable(no_events):
    """설정 행 없음 → 배분 15%로 산다. 꺼짐을 저장하면 다음 하트비트부터 사지 않는다."""

    async def go():
        from quantpilot.db.repo import SqlConfigRepo

        _, sessions = await memory_sessions()
        seed = {"KRW-BTC": _rising_days(40, "2026-09-28")}
        p = float(seed["KRW-BTC"]["close"].iloc[-1])
        eng = await _db_engine(sessions, {k: v.copy() for k, v in seed.items()}, DAY)
        equity = eng.runner.executor.equity()
        for b in _breakout_bars(p):
            await eng.runner.on_bars_closed([b])
        fills = [e.fill for t, e in eng.runner.bus.events if t == "fill"]
        assert [f.side for f in fills] == [Side.BUY]
        # 코인 하나 최대 비중 1/5 → 배정 자본(15%) 안에서 계좌의 3% 이하
        assert 0 < fills[0].qty * fills[0].price <= 0.15 * equity / 5 + 1

        # 꺼짐 저장 → 다음 하트비트에 엔진이 읽는다
        await SqlConfigRepo(sessions).upsert_strategy(
            name="vol_breakout",
            market=Market.UPBIT,
            allocation=0.15,
            symbols=list(create("vol_breakout").symbols),
            enabled=False,
        )
        await eng.on_link(DAY + timedelta(minutes=5))
        assert not eng.runner.entries_allowed("vol_breakout")

    asyncio.run(go())


def test_engine_start_uses_saved_judge_and_falls_back_when_unusable(no_events, caplog):
    """시작할 때 settings 표의 판단 설정으로 만들고, 실제로 쓰는 모델을 우편함에 쓴다.
    저장값으로 만들 수 없으면 환경변수 설정으로 되돌리고 CRITICAL을 남긴다 (ADR 0032)."""

    async def go():
        from quantpilot.db.repo import SqlConfigRepo
        from quantpilot.engine.link import active_judge_key
        from quantpilot.engine.main import _build_with_overrides

        _, sessions = await memory_sessions()
        config = SqlConfigRepo(sessions)
        await config.set_setting("gate.hold_below", 0.55)
        await _build_with_overrides(sessions, None, {})
        active = await config.get_setting(active_judge_key(Market.UPBIT))
        assert (active["provider"], active["llm_models"]) == ("stub", ["stub", "stub"])

        await config.set_setting("judge.provider", "laya")  # API를 거치지 않은 잘못된 값
        with caplog.at_level(logging.CRITICAL):
            eng = await _build_with_overrides(sessions, None, {})
        assert "환경변수 설정으로 시작" in caplog.text
        assert eng.runner.pipeline is not None
        active = await config.get_setting(active_judge_key(Market.UPBIT))
        assert active["provider"] == "stub"

    asyncio.run(go())
