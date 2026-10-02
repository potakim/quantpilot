# 0022 · 08:10 사전 심사: scheduler 심사 → settings 우편함 → 판단 파이프라인 제외

상태: 제안 (2026-10-02)

## 맥락
ADR 0004는 변동성 돌파(1분 반응)에 진입마다 LLM 합의를 걸지 않는 대신, 매일 08:10에 LLM 2모델이 대상 코인별 뉴스 위험을 한 번 심사해 위험 코인을 그날 제외한다고 정했다(06 §4 표: "합의 실패 시 당일 그 코인 제외"). scheduler 잡 `upbit_prescreen`은 등록만 되어 있었고 본문이 없어 "not wired"로 건너뛰었다. 심사는 scheduler에서, 진입 판단은 engine에서 하므로 둘 사이 전달 경로와 "당일"의 경계를 정해야 한다.

## 결정
1. **심사 (`scheduler/wiring.py::Prescreen`)**: vol_breakout 대상 코인마다 최근 24시간 뉴스(DB `news_items`, 그 코인 + 거시 `*`)와 앞뒤 24시간 이벤트(`QP_EVENTS_FILE` + DART 위험 공시)로 봉 없는 state를 만든다(`features/builder.py::news_only_state`, 진입 state와 같은 뉴스 선택·400토큰 규칙). 판단 모델 답변을 함께 붙여 LLM 리뷰어 전원에게 같은 리뷰 프롬프트(`review_v1`)로 묻는다. 전략 규칙 줄은 "오늘 이 코인의 변동성 돌파 진입을 막아야 할 뉴스·이벤트 위험이 있는가"다. **한 모델이라도 approve가 아니면 제외**한다. 타임아웃(모델별 30초)·오류도 hold이므로 제외다. 판단 모델과 리뷰어는 엔진과 같은 설정(`QP_JUDGE_PROVIDER`, `QP_LLM_PROVIDERS`)으로 만든다.
2. **전달**: 결과를 settings 우편함 `engine.prescreen.upbit = {"day", "blocked": {symbol: 사유}, "ts"}`에 쓴다(할트와 같은 경로, ADR 0013·0015). 엔진은 하트비트(5초) 때마다 읽어 판단 파이프라인의 `set_prescreen`에 넣는다.
3. **당일 경계**: 업비트 거래일은 09:00 KST에 바뀐다(`core/clock.py::upbit_trading_day`, 일봉 경계와 같다). 08:10 심사는 그날 날짜를 쓰고, 엔진은 지금 거래일과 같은 날짜의 목록만 쓴다. 그래서 09:00 전에는 어제 심사가, 09:00부터는 오늘 심사가 적용된다. 심사가 돌지 않은 날은 제외가 없다. 심사 결과를 모르고 막지는 않는다.
4. **적용 (`JudgmentPipeline`)**: vol_breakout 진입 신호의 심볼이 제외 목록에 있으면 hold(배수 0)로 끝내고 `blocks`에 `prescreen: <모델>: <사유>`를 남긴다. 판단 모델은 그래도 불러서 판단 로그(보정 지표 표본)를 남긴다. LLM 진입 합의(orb·gem·gtaa)와 다른 전략에는 영향이 없다. 청산은 판단 파이프라인을 거치지 않으므로 항상 허용된다(불변식 #6).
5. **게이팅 OFF에서는 기록만**: `gating=False`이면 차단 사유를 남기되 배수는 1.0이다. `QP_JUDGE_PROVIDER=stub`(StubPipeline)은 `set_prescreen`이 없어 제외를 받지 않는다(ADR 0021 §6). 섀도 원장(게이팅 OFF)은 사전 심사와 무관하게 매매하므로, A/B는 사전 심사까지 포함한 게이팅 효과를 잰다.

## 결과
- 결과는 우편함 값 하나로 날마다 덮어쓴다. 지난 날의 심사 기록은 scheduler 로그(`prescreen done`)와 info 알림에만 남고 DB 이력 테이블은 없다. 제외 때문에 hold된 진입은 `judgments` 행(blocks에 `prescreen:`)으로 남는다.
- 비용: 5코인 × (판단 1 + 리뷰 2) / 일. 사전 심사 비용은 엔진의 일일 AI 예산 집계(`ai_budget_usd_daily`)에 들어가지 않는다(다른 프로세스). 로그의 `cost_usd`로 본다.
- 하루 중 새로 나온 돌발 뉴스는 다음 날 심사 전까지 진입 시점 판단 모델의 `news_risk`(ADR 0004 결과의 한계, 엔진 뉴스 갱신 5분)로만 걸러진다.
