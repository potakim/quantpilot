# 0021 · 뉴스·이벤트 배선: DB 경유 엔진 캐시, 기본 피드 설정 파일, 키 없을 때의 대체물

상태: 제안 (2026-10-02)

## 맥락
P1-06(t8)에서 `FeatureBuilder`·`NewsCollector`·`NewsCache`·`EventCalendar`를, P1-08(t10)에서 Claude·Gemini 어댑터를 만들었다. 하지만 배선이 빠져 있었다. 엔진(`engine/main.py::build_upbit_paper`)은 뉴스·지표 없는 `StubFeatureBuilder`를 썼고, scheduler(`build_context`)는 `news`·`reviewer`를 넣지 않았다. 그래서 `news_collect` 잡은 아무것도 하지 않았고, `daily_review`는 "리뷰 모델 미연결"로 통계만 저장했다.

04 §3은 "피처 빌더는 수집기가 비동기로 채운 메모리 캐시를 읽는다"고 적었지만, 수집기(scheduler)와 피처 빌더(engine)는 **다른 컨테이너**다(01 §2). 메모리를 공유하지 않으므로 둘을 잇는 경로를 정해야 한다. 피드 목록과 종목 키워드도 코드·문서 어디에도 없었다.

## 결정
1. **뉴스는 DB(`news_items`)를 거쳐 엔진으로 간다.** scheduler의 `news_collect`(매시 :05)가 수집·요약해 `SqlNewsRepo`에 저장한다. 엔진은 `NewsRefresher`가 엔진 타이머에서 5분마다 최근 48시간 뉴스를 `NewsCache`로 읽어 온다. 피처 빌더는 동기 `CacheNewsSource`로 그 캐시만 읽는다. 따라서 수집부터 판단 입력까지 최대 지연은 수집 주기(1시간) + 5분이다. DB 오류는 로그만 남기고 매매 루프를 멈추지 않는다.
2. **엔진은 실제 `FeatureBuilder`를 쓴다.** 지표(등급·백분위), 뉴스 최대 3건, 이벤트 캘린더(`QP_EVENTS_FILE`, 기본 `data/events.yaml`, 없으면 빈 캘린더)가 들어간다. DART 위험 공시(`events_from_dart`)는 갱신 때 캘린더에도 넣는다. 룩어헤드 규칙(가격 지정 진입은 현재 봉 제외)은 `FeatureBuilder`가 이미 지킨다.
3. **피드·키워드는 YAML 설정이다.** 패키지 기본값은 `quantpilot/data/news_sources.yaml`이고 `QP_NEWS_FILE`로 바꾼다. 기본 피드는 2026-10-02에 `parse_rss`로 실제로 읽히는 것을 확인한 것만 넣었다(코인 영문 4, 국내 코인 2, 국내 경제 2, DART). 키워드 매칭은 소문자 '포함' 검사라서, 짧은 약어(SOL·ADA·SEC·ETH·리플)는 일반 단어에 섞여 오탐한다. 그래서 정식 이름을 쓰고, 오탐 사례를 테스트로 고정한다.
4. **시간대 표시 없는 피드 날짜는 피드별 오프셋으로 읽는다** (`Feed.naive_utc_offset_hours`). 연합인포맥스는 `2026-10-02 12:02:02`(KST)를 보내는데, 기존 코드는 이를 UTC로 봐서 9시간 미래 기사가 되었고, 24시간 창에서 9시간 늦게 나타났다.
5. **키가 없으면 키 없는 대체물로 돈다.** 요약기는 Gemini(`QP_GOOGLE_API_KEY`)가 없거나 SDK가 없으면 `TitleSummarizer`(제목 100자 절단, 위험 플래그 없음)를 쓴다. 일일 리뷰는 Claude(`QP_ANTHROPIC_API_KEY`)가 있으면 `ClaudeAnswerer`로 06 §5의 사후 리뷰 질문을 하고, 없으면 기존처럼 통계만 저장한다. 리뷰 호출이 실패해도 통계 저장은 막지 않는다.
6. **게이팅 기본값은 바꾸지 않는다.** `QP_JUDGE_PROVIDER=stub`이면 엔진은 여전히 `StubPipeline(gating=False)`이다(ADR 0010). 뉴스는 판단 로그(`judgments.state`)에 남지만 사이징은 바꾸지 않는다. 뉴스가 진입을 막는 것은 `typesafe`로 바꾸고 키를 넣었을 때부터다. A/B(G2)는 실제 판단 모델로만 의미가 있기 때문이다.

## 결과
- 판단 입력 state에 실제 지표·뉴스·이벤트가 들어간다. 판단 로그에서 "어떤 뉴스를 보고 판단했는지"를 사후에 확인할 수 있다.
- `TitleSummarizer`는 위험 플래그를 달지 못한다. 그래서 키 없이는 위험 뉴스 우선 정렬이 동작하지 않고 최신순만 남는다.
- RSS 주소는 매체 사정으로 바뀌거나 막힐 수 있다. 한 피드가 실패해도 수집은 계속되며(기존 동작), 로그에 피드 이름과 오류 타입이 남는다. 주기적으로 `QP_NEWS_FILE`을 점검해야 한다.
- 08:10 사전 심사(ADR 0004)와 아침 브리핑은 이 ADR 범위 밖이다. 사전 심사는 후속 카드에서 한다.
