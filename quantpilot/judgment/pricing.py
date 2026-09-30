"""AI 호출 단가표 (04 §4). 모든 프로바이더는 토큰 수 × 단가로 cost_usd를 계산해 돌려준다.

단가는 USD / 100만 토큰. 출처와 확인일을 함께 적고, 바뀌면 이 표만 고친다.
- Jev: 입력 $0.042/M, 출력 무료 — https://typesafe.ai/blog/introducing-system-one-models-and-jev (2026-09-30 확인)
- Claude·Gemini: 기획서(90 문서) 기준값. 실제 호출 어댑터(P1-08)에서 공식 단가표와 다시 대조한다.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Price:
    """USD / 100만 토큰."""

    input_per_m: float
    output_per_m: float


# 키는 모델 이름의 접두사. 응답 모델명(jev-1.13.0)·요청 별칭(jev-latest) 모두 가장 긴 접두사로 찾는다.
PRICES: dict[str, Price] = {
    "jev": Price(0.042, 0.0),
    "claude-sonnet-5": Price(2.0, 10.0),
    "gemini-3.5-flash-lite": Price(0.30, 2.50),
    "gemini-3.5-flash": Price(1.50, 9.0),
}


def price_for(model: str) -> Price:
    """모델 이름에 맞는 단가. 표에 없으면 ValueError — 비용을 0으로 삼키지 않는다."""
    matches = [k for k in PRICES if model == k or model.startswith(k + "-")]
    if not matches:
        raise ValueError(f"단가표에 없는 모델: {model!r}")
    return PRICES[max(matches, key=len)]


def cost_usd(model: str, input_tokens: int, output_tokens: int = 0) -> float:
    """토큰 수로 호출 비용(USD)을 계산한다."""
    if input_tokens < 0 or output_tokens < 0:
        raise ValueError("토큰 수는 음수일 수 없다")
    p = price_for(model)
    return (input_tokens * p.input_per_m + output_tokens * p.output_per_m) / 1_000_000
