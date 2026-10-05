# 0035 · `news.enabled`는 Gemini 뉴스 요약만 켜고 끈다

상태: 승인 (2026-10-05)

## 맥락
2026-10-05 점검(t44)에서 확인했다.

1. **설정 키만 있다**: `PATCH /settings`는 `news.enabled`(true/false)를 받아 저장한다(03 표). 그런데 이 값을 읽는 곳이 없다. 저장해도 아무것도 바뀌지 않는다.
2. **화면 원본에는 자리가 있다**: `Strategy.dc.html`의 AI 판단 설정 패널은 LLM 체크박스가 셋이다 — Claude Sonnet 5(진입 합의·사후 리뷰), Gemini 3.5 Flash(진입 합의), **Gemini 3.5 Flash-Lite(뉴스 요약 · 1시간)**. 지금 화면(`JudgePanel`)에는 앞의 둘만 있다.
3. **뉴스 흐름**:
   - scheduler `news_collect`(매시 :05)가 피드·DART를 모아 종목에 매칭하고, 요약기로 100자 요약 + 위험 플래그·점수를 만들어 `news_items`에 저장한다.
   - 요약기는 Gemini(`GeminiSummarizer`, `QP_GOOGLE_API_KEY`)다. 키가 없으면 제목을 100자로 자르는 `TitleSummarizer`를 쓴다. 이때 위험 플래그는 비고, 위험 판정은 판단 모델이 제목을 읽고 한다.
   - 엔진은 `news_items`를 5분마다 읽어 판단 재료(뉴스 요약·news_risk)로 쓴다. news_risk는 hard block(불변식 #8)과 이어진다.

"끄면 무엇이 멈추는가"가 정해지지 않았다. 수집까지 멈추면 새 뉴스가 들어오지 않아 news_risk 차단이 사실상 꺼진다.

## 결정
1. **`news.enabled=false`면 Gemini 요약만 멈춘다**:
   - `news_collect`는 회차마다 settings의 `news.enabled`를 읽는다. 값이 `false`면 그 회차는 `TitleSummarizer`로 요약한다(Gemini 호출·비용 0).
   - 수집·매칭·저장은 그대로 한다. 엔진은 계속 새 뉴스 제목을 받고, 판단 모델이 제목으로 위험을 판정하는 경로(키가 없을 때와 같은 경로)가 남는다. hard block 경로는 바뀌지 않는다(불변식 #8).
   - 값이 없거나 `true`면 지금과 같다(키가 있으면 Gemini, 없으면 제목 요약).
2. **적용 시점**: 저장하면 다음 정시 수집(:05)부터. scheduler·엔진 재시작은 필요 없다.
3. **API**: `GET /settings`의 `judge`에 `news_summary`(유효값, 기본 true)를 싣는다. 키 유무는 기존 `keys.gemini`(같은 `QP_GOOGLE_API_KEY`)를 쓴다.
4. **화면**: AI 판단 설정의 LLM 목록에 세 번째 체크박스 "Gemini 3.5 Flash-Lite · 뉴스 요약 · 1시간"(원본 그대로)을 둔다.
   - 체크하면 `news.enabled`를 저장한다. 이 체크박스는 "LLM 합의 (2/2 필요)"의 두 모델 선택(`llm.models`)과 따로 저장된다.
   - 키가 없으면 다른 줄처럼 "키 필요"를 보인다(꺼짐과 같은 제목 요약으로 동작).
   - 바로 적용되는 값이므로 "엔진 재시작 때 적용" 문구의 대상이 아니다.

## 결과
- 운영자가 화면에서 뉴스 요약 비용을 끌 수 있다. 끄더라도 뉴스 제목은 계속 판단 재료로 들어간다.
- 끈 동안 저장된 뉴스는 위험 플래그·점수가 비어 있다(제목 요약과 같다). 다시 켜도 지난 뉴스를 다시 요약하지 않는다.
- 사전 심사(`upbit_prescreen`, LLM 2모델 news_risk)는 이 키와 무관하다.
