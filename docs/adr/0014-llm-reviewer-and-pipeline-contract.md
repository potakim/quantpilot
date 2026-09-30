# 0014 · LLM 리뷰어·JudgmentPipeline 계약: Claude 샘플링 인자 없음·실패는 hold·prompt_hash 저장 위치

상태: 승인 (2026-09-30)

## 맥락
P1-08에서 Claude·Gemini 리뷰어와 `JudgmentPipeline`을 만든다. 06 §4·§5·§7과 실제 API·기존 코드 사이에 다섯 군데를 정해야 한다.

1. 06 §5는 두 모델 모두 "temperature 0"을 요구한다. 그런데 `claude-sonnet-5`는 temperature·top_p 등 샘플링 인자를 받지 않는다(보내면 400, Anthropic 모델 문서 2026-09-25 기준). Gemini는 받는다.
2. `LLMProvider.review()`는 동기다. 판단 모델(ADR 0012)과 같은 이유로 틱 루프에서는 비동기 호출이 필요하다.
3. 06 §5는 "출력 파싱 실패 → hold"만 정했고, 호출 오류·모델 거부(refusal)·타임아웃을 어떻게 기록할지 정하지 않았다.
4. ADR 0012는 판단 모델 질문 문구의 `prompt_hash`를 `JudgeResult.raw`에만 두고, `judgments` 테이블 어디에 저장할지는 P1-08에서 정하기로 했다. `judgments`에는 `prompt_hash` 열이 없고, `llm_verdicts`에는 있다.
5. 06 §4 표는 전략마다 LLM 합의 시점이 다르다(vol_breakout은 08:10 사전 심사만, orb는 진입마다, gem·gtaa는 리밸런싱마다).

## 결정
1. **Claude 요청에는 샘플링 인자를 넣지 않는다.** 대신 `output_config.format`(JSON 스키마 `{"approve": bool, "reason": str}`)과 `thinking: {"type": "disabled"}`로 응답 모양을 고정하고, `max_tokens`는 200으로 둔다. Gemini는 06 §5대로 `temperature: 0`, `max_output_tokens: 200`, `response_mime_type: application/json`으로 보낸다. 두 모델에 보내는 프롬프트 본문은 `judgment/prompts/review_v1.md` 하나로 같다(합의의 의미). 결정성은 Claude 쪽에서 보장되지 않으므로 "같은 state → 같은 답" 테스트는 판단 모델에만 둔다.
2. **`LLMProvider.areview(state, judge, rule="")`를 추가한다.** 기본 구현은 `review()`를 부른다(스텁 동작 동일). `ClaudeReviewer`는 `AsyncAnthropic`, `GeminiReviewer`는 `client.aio`를 쓴다. SDK 재시도는 끈다(`max_retries=0`) — 재시도하면 30초 예산을 넘긴다.
3. **리뷰 실패는 모두 그 모델의 hold다.** 어댑터는 파싱 실패·계약 위반·호출 오류·refusal을 예외 대신 `approve=False` 판정(이유에 원인 이름만, 예외 메시지는 싣지 않음)으로 돌려준다. 파이프라인은 모델별로 `asyncio.wait_for(…, 30초)`를 걸고, 타임아웃·어댑터가 놓친 예외도 hold 판정으로 바꾼다. 2/2 approve일 때만 통과한다.
4. **판단 모델 `prompt_hash`는 `judgments.state` jsonb의 `prompt_hash` 키에 둔다.** `SqlJudgmentRepo.add`가 `JudgeResult.raw["prompt_hash"]`를 state에 합친다. 마이그레이션을 두지 않는다. 리뷰 프롬프트의 `prompt_hash`(문구 sha256 앞 16자리)는 `LLMVerdict.prompt_hash`에 실어 기존 `llm_verdicts.prompt_hash` 열에 저장한다.
5. **파이프라인은 `llm_strategies`(기본 orb·gem·gtaa)에 속한 신호만 LLM에 묻는다.** vol_breakout의 08:10 사전 심사는 스케줄러 잡 몫이고, 진입 시점에는 판단 모델 게이트만 적용한다. hard_blocks나 확신도 게이트가 이미 hold면 LLM을 부르지 않는다(불변식 #8, 비용 절감). 게이팅 OFF(A/B 섀도, ADR 0010)도 LLM을 부르지 않는다.
6. **하루 AI 비용(판단 모델 + LLM)이 `settings.ai_budget_usd_daily`(기본 $2)에 닿으면 그날 LLM 합의를 멈추고 hold로 처리한다.** 판단 모델 호출은 계속한다(06 §7). 날짜는 신호 시각(시장 현지)의 날짜다.
7. **`TypeSafeJudge.consecutive_timeouts`가 10에 닿으면 파이프라인이 `RiskEvent("judge_down")`를 `warning` 토픽으로 한 번 발행한다.** 10 미만으로 돌아오면 다시 알릴 수 있다. `risk_events` 테이블 저장과 텔레그램 알림은 P1-10(알림)·P1-12(WS 허브)의 구독자가 맡는다.

## 결과
- 엔진은 `settings.judge_provider`로 파이프라인을 고른다(`build_pipeline`). 기본 `stub`은 지금과 같이 게이팅 OFF `StubPipeline`이라 백테스트·페이퍼 수치가 바뀌지 않는다. `typesafe`로 바꾸면 `llm_providers`(예: `["claude", "gemini"]`)의 리뷰어가 붙는다.
- `JudgmentEvent.verdicts`로 리뷰 판정이 이벤트에 실린다. `judgments`·`llm_verdicts` 저장 배선은 판단 로그를 기록하는 쪽(P1-12)에서 한다.
- `llm.require_all=false` 임시 완화(06 §7)와 `daily_review`·`answer_question`(04 표)은 이번 카드에 넣지 않았다.
- 계약 테스트는 가짜 SDK 클라이언트로 돈다. 실제 응답 모양은 `@pytest.mark.network` 테스트 2건으로 키를 받으면 확인한다.
