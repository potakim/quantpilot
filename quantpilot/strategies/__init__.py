"""전략 레지스트리. 새 전략은 Strategy를 상속하고 여기 등록하면 API·백테스터·페이퍼가 모두 인식한다."""

from __future__ import annotations

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


__all__ = [
    "GTAA",
    "REGISTRY",
    "Context",
    "DualMomentumGEM",
    "OpeningRangeBreakout",
    "ParamSpec",
    "Strategy",
    "VolBreakout",
    "create",
]
