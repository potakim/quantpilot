"""AI 판단 설정의 settings 표 덮어쓰기 (ADR 0032).

`PATCH /settings`가 settings 표에 쓰고, 엔진(시작 시 판단 모델·리뷰어, 하트비트마다 임계값)과
`GET /settings`가 읽는다. 저장된 값이 없으면 환경변수(`QP_JUDGE_PROVIDER` 등)를 그대로 쓴다.
"""

from __future__ import annotations

from typing import Any

# settings 표 키 → Settings 필드
JUDGE_KEYS: dict[str, str] = {
    "judge.provider": "judge_provider",
    "llm.models": "llm_providers",
    "gate.hold_below": "gate_hold_below",
    "gate.full_above": "gate_full_above",
}
# 임계값 허용 범위 (06 §2, judgment/pipeline.py와 같은 값)
HOLD_RANGE = (0.3, 0.7)
FULL_RANGE = (0.7, 0.98)


async def judge_overrides(config: Any) -> dict[str, Any]:
    """settings 표에 저장된 AI 판단 설정 {Settings 필드: 값}. 저장되지 않은 키는 빠진다."""
    out: dict[str, Any] = {}
    for key, field in JUDGE_KEYS.items():
        value = await config.get_setting(key)
        if value is not None:
            out[field] = value
    return out


def effective(settings: Any, overrides: dict[str, Any]) -> Any:
    """환경변수 설정 위에 덮어쓴 설정 사본."""
    return settings.model_copy(update=overrides) if overrides else settings


def valid_gate(hold: Any, full: Any) -> bool:
    """임계값 쌍이 허용 범위 안이고 보류 < 전량인가."""
    try:
        h, f = float(hold), float(full)
    except (TypeError, ValueError):
        return False
    return HOLD_RANGE[0] <= h <= HOLD_RANGE[1] and FULL_RANGE[0] <= f <= FULL_RANGE[1] and h < f
