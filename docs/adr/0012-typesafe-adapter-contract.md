# 0012 · TypeSafe Jev 어댑터 계약: score 정규화·비동기 판단·실패는 hold·질문 문구 파일

상태: 승인 (2026-09-30)

## 맥락
P1-07에서 0단계 자리표시자(`StubJudge`)를 실제 TypeSafe Jev 호출로 채운다. TypeSafe 공식 문서(quickstart·primitives/choice·score·confidence, 2026-09-30 조회)의 계약과 기존 설계 사이에 네 군데가 어긋난다.

1. 06 §2는 score 질문을 "0~1"로 적었지만, TypeSafe의 score는 `criteria` 단계(0..N-1)의 확률 가중 평균이다. 단계가 5개면 0~4가 나온다. `hard_blocks`(> 0.5)와 06 §6의 보정 지표는 0~1을 전제한다.
2. `JudgeProvider.judge()`는 동기다. `TickRunner`는 비동기 루프 안에서 파이프라인을 부르므로, 네트워크 호출을 동기로 하면 최대 3초 동안 이벤트 루프(시세 수신·청산)가 멈춘다.
3. 01·06 §7은 "타임아웃 = hold"만 정했고, HTTP 오류·응답 계약 위반(키 누락, 확률 합 ≠ 1 등)을 어떻게 다룰지 정하지 않았다.
4. 06 §2는 질문 문구를 `judgment/questions/v1.yaml`로 빼라고 하지만, 코어(`judgment`)는 pandas·numpy 외 의존성을 두지 않는다. 또 `Question`에는 TypeSafe가 요구하는 `criteria`(choice 옵션 설명, score 단계 설명)가 없다.

## 결정
1. **score는 `score / (N-1)`로 0~1로 바꿔 `JudgeResult.answers`에 넣는다.** N은 질문 문구 파일의 `criteria` 단계 수(v1은 5단계). choice는 옵션별 확률 dict를 그대로 넣는다. 원 응답은 `JudgeResult.raw["answers"]`에 남긴다.
2. **`JudgeProvider.ajudge()`를 추가한다.** 기본 구현은 `judge()`를 그대로 부른다(스텁은 네트워크가 없으므로 동작 동일). `TypeSafeJudge`는 `httpx.AsyncClient` + `asyncio.wait_for`로 연결부터 응답까지 **총** `timeout`(기본 3초)을 건다. `StubPipeline.evaluate`는 `ajudge`를 부른다. 동기 `judge()`는 CLI·API용으로 남기며, 3초를 넘겨 도착한 답도 버린다.
3. **판단 실패는 모두 hold다.** `core/errors.py`에 `JudgeError`를 두고 `JudgeTimeout`을 그 하위로 옮긴다. 어댑터는 타임아웃 → `JudgeTimeout`, HTTP 오류·연결 실패·계약 위반 → `JudgeError`를 던진다. 파이프라인(게이팅 ON)은 `JudgeError`를 받으면 확신도 0의 `JudgeResult`(`raw={"error": 예외 이름}`)로 기록하고 `Gate.HOLD`, 배수 0으로 처리한다. 게이팅 OFF(A/B 섀도 기준선, ADR 0010)는 판단 결과와 무관하게 배수 1.0이다 — 실패도 기록만 한다.
4. **연속 타임아웃 수는 어댑터가 센다** (`TypeSafeJudge.consecutive_timeouts`, 성공 시 0). 10회 → `risk_events(judge_down)` + 알림은 이를 읽는 P1-08 `JudgmentPipeline` 몫이다.
5. **질문 문구 파일은 `judgment/questions/` 하위 모듈이 읽는다.** PyYAML은 그 모듈에서만 `try/except ImportError`로 import하고, 프로젝트 기본 의존성에 `pyyaml`을 명시한다. 키·종류·옵션의 기준은 여전히 `base.py::DEFAULT_QUESTIONS`(코드 상수)이고, YAML은 모델에 보낼 문구(`instructions`, `criteria`)만 담는다. 둘이 어긋나면 테스트와 `payload()`가 실패한다. `prompt_hash`(문구 전체 sha256 앞 16자리)는 `JudgeResult.raw["prompt_hash"]`에 남는다.
6. **단가표에 없는 모델은 시작할 때 실패한다.** `pricing.price_for`는 모르는 모델에 `ValueError`를 던지고(비용을 0으로 삼키지 않음), `TypeSafeJudge`는 생성자에서 요청 모델을 검증한다. 응답 모델명(`jev-1.13.0` 등)이 표에 없으면 요청 모델 단가로 계산한다.

## 결과
- `hard_blocks`·게이트·보정 지표는 모든 프로바이더에서 0~1 score를 받는다. Laya·Kev도 같은 스키마이므로 같은 정규화를 쓴다(Kev는 `base_url`만 바꿔 `TypeSafeJudge`로 쓴다).
- 판단 모델 호출이 틱 루프를 막지 않는다. 스텁 경로의 동작·백테스트 수치는 바뀌지 않는다.
- `judgments` 테이블에 `prompt_hash` 열은 없다. 지금은 `raw`에만 남으며, 판단 로그 저장(P1-08)에서 `state` jsonb 옆에 함께 넣을지 정한다.
- 계약 테스트의 fixture는 공식 문서 스키마로 만든 것이다. 실제 응답과 다를 수 있어 `@pytest.mark.network` 테스트 1건을 두었다 — 키를 발급받으면 한 번 돌려 확인하고, 실응답을 fixture로 교체한다.
