# 0001 · 판단 모델은 확률만 주고 수량·가격은 코드가 정한다

상태: 승인 (2026-09-29)

## 맥락
TypeSafe Jev 문서는 "Jev is not a calculator", 카운팅·날짜 비교가 불안정하다고 명시한다. LLM도 산술과 수량 결정에서 오류가 잦고, 무엇보다 AI가 낸 수량을 그대로 주문하면 사후에 "왜 그만큼 샀는지"를 재현할 수 없다.

## 결정
`JudgeResult`는 원자 질문의 확률과 confidence만 담는다. 수량은 `weight × size_multiplier × 배정자본 / price`로 코드가 계산하고, `RiskManager`가 상한을 건다. LLM은 approve/hold와 이유만 낸다. 이 경계는 `judgment/`가 `execution/`을 import하지 못하게 하는 테스트로 강제한다.

## 결과
- AI 교체(Jev → Laya → 다른 모델)가 코어에 영향 없음.
- 사후 리뷰에서 모든 수량이 규칙으로 설명됨.
- AI가 "이 종목은 두 배 사라"류의 판단을 할 수 없음 — 의도된 제한.
