"""전략 레지스트리. 새 전략은 Strategy를 상속하고 여기 등록하면 API·백테스터·페이퍼가 모두 인식한다."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from quantpilot.strategies.base import Context, ParamSpec, Strategy
from quantpilot.strategies.gem import DualMomentumGEM
from quantpilot.strategies.gtaa import GTAA
from quantpilot.strategies.orb import OpeningRangeBreakout
from quantpilot.strategies.vol_breakout import VolBreakout

REGISTRY: dict[str, type[Strategy]] = {
    cls.name: cls for cls in (VolBreakout, DualMomentumGEM, GTAA, OpeningRangeBreakout)
}


def create(name: str, **params) -> Strategy:
    if name not in REGISTRY:
        raise KeyError(f"unknown strategy {name!r}; available: {sorted(REGISTRY)}")
    return REGISTRY[name](**params)


# 설정 행(strategy_configs)이 없는 전략의 (켜짐, 배분) — 화면 원본의 권장 조합 (ADR 0032).
# 2단계 시장(ADR 0031) 전략은 꺼 둔다: 엔진을 붙였을 때 저절로 사지 않게.
DEFAULT_CONFIG: dict[str, tuple[bool, float]] = {
    "vol_breakout": (True, 0.15),
    "gem": (False, 0.40),
    "gtaa": (False, 0.35),
    "orb": (False, 0.0),
}


def strategy_config(name: str, row: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """저장된 설정 행을 기본값 위에 얹은 유효 설정 {enabled, allocation, params, symbols, paper}."""
    enabled, allocation = DEFAULT_CONFIG.get(name, (False, 0.0))
    if row:
        enabled = bool(row.get("enabled", enabled))
        allocation = float(row.get("allocation") or 0.0)
    return {
        "enabled": enabled,
        "allocation": allocation,
        "params": dict((row or {}).get("params") or {}),
        "symbols": list((row or {}).get("symbols") or REGISTRY[name].symbols),
        "paper": bool((row or {}).get("paper", True)),
    }


__all__ = [
    "DEFAULT_CONFIG",
    "GTAA",
    "REGISTRY",
    "Context",
    "DualMomentumGEM",
    "OpeningRangeBreakout",
    "ParamSpec",
    "Strategy",
    "VolBreakout",
    "create",
    "strategy_config",
]
