> 원본: Claude Docs "AI 퀀트 트레이딩 플랫폼 기획서" (2026-09-28, rev 17) 마크다운 내보내기.
> 다이어그램(AI 판단 구조, 하루 타임라인, 시스템 구성도, 로드맵)은 위젯이라 텍스트로만 남는다 — 구조 설명은 docs/01, 04, 05 참조.

# AI 퀀트 트레이딩 플랫폼 기획서

Sep 28, 2026 · @김기환

## 개요와 목표

투자 초보자가 검증된 퀀트 규칙 4개와 AI 판단 계층을 결합해 코인·국내주식·미국주식을 자동 거래하는 플랫폼(가칭 QuantPilot)을 만든다. 전략은 코드로 고정된 규칙이고, AI는 "지금 이 규칙을 실행해도 되는가"를 판단해 진입을 걸러주거나 포지션 크기를 조절하는 역할만 맡는다.

| 항목 | 결정 |
| --- | --- |
| 대상 사용자 | 투자 경험 1년 미만, 코딩 없이 전략을 켜고 끌 수 있어야 하는 개인 투자자 |
| 대상 시장 | 업비트 KRW 코인, KRX 국내주식, 미국주식. 1차 MVP는 업비트 + 한국투자증권(KIS) |
| 거래 주기 | 장기(월 1회 리밸런싱) 2개 + 단타(1일 보유·당일 청산) 2개 |
| AI 구성 | 판단 모델(Jev 또는 오픈소스 Laya) + LLM(Claude·Gemini) 2단 구조 |
| 기술 스택 | Python FastAPI 백엔드 + Next.js 프론트엔드 |
| 이번 문서 범위 | 전략 선정, AI 판단 설계, 아키텍처, API 제약, 화면 구성, 로드맵. 코드는 다음 단계 |

설계 원칙은 세 가지다. 첫째, 규칙이 우선이다: 모든 진입·청산은 백테스트 가능한 코드 규칙이며 AI가 규칙 없이 매매하지 않는다. 둘째, 코드가 계산하고 모델은 판단하고 코드가 실행한다: 가격·지표·리스크 한도 산술은 전부 코드에 두고, 모델은 레짐·뉴스 같은 정성 판단만 확률로 답한다. 셋째, 안전장치가 먼저다: 거래당 손실 1%, 월 누적 −5% 중단 같은 계좌 단위 서킷브레이커가 어떤 전략보다 위에 있다.

이 문서는 소프트웨어 설계 자료이며 투자 권유가 아니다. 인용한 백테스트 수치는 과거 결과이고 미래 수익을 보장하지 않으며, 실제 투자 손실의 책임은 사용자에게 있다.

## 초보 투자자용 추천 전략 4선

장기 전략 2개(듀얼 모멘텀, 10개월선 자산배분)에 자본의 70\~80%를, 단타 전략 2개(코인 변동성 돌파, 미국 ORB)에 10\~20%를 배정하는 조합을 기본값으로 제안한다. 선정 기준은 네 가지다: 규칙이 5줄 이내로 설명되고, 공개된 백테스트나 논문 근거가 있고, 최대 낙폭(MDD)이 바이앤홀드보다 작으며, 자동화했을 때 사람의 개입이 필요 없어야 한다.

| 순위 | 전략 | 한 줄 요약 | 시장 · 주기 | 근거 수치 (출처 기준) | 초보 적합 이유 |
| --- | --- | --- | --- | --- | --- |
| 1 | 듀얼 모멘텀 GEM (장기) | 매월 말 12개월 수익률로 미국주식·해외주식·채권 중 하나만 보유 | 미국 ETF(국내상장 ETF로 변형 가능) · 월 1회 | 1971\~2026 재현 CAGR 15.18%, MDD −21.7% vs S&P 500 11.27%, −50.9% ([Petit 2026](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=7427878)); 2010년 이후 패시브 대비 연 −4.8%p | ETF 3개, 연 1.5회 매매. 룩백 6/9/12개월 앙상블로 파라미터 위험 완화 |
| 2 | 변동성 돌파 + 이평 필터 + 변동성 타겟 (단타) | 시가 + 전일 레인지×0.5 돌파 시 매수, 익일 09:00 매도, 5일선 아래면 쉬기 | 업비트 KRW 상위 3\~5개 코인 · 매일 | BTC 2013.10\~2018.3, 수수료·슬리피지 0.1% 반영: 목표 변동성 1% 무필터 연 30.5%, MDD 27.3%; 0.5% + 5일선 상승장 필터 연 17.4%, MDD 6.5% ([강환국](https://hive.blog/kr/@kangcfa/6-mdd-10-1)); 승률 50\~55% ([코인픽](https://coinpick.com/quant_strategy_7)) | 규칙 한 줄, 시가 청산라 오버나잇 판단 불필요. 수수료 0.05%로 일 단위 매매 감당 |
| 3 | 10개월선 타이밍 자산배분 (Faber GTAA) 또는 영구 포트폴리오 (장기) | 5개 자산 ETF 중 월말 종가가 10개월선 위인 것만 보유, 나머지는 현금 | 미국·국내 ETF · 월 1회 | S&P 500 1901\~2012 MDD 83.66% → 42.24%, GTAA 5자산 MDD 46% → 10% 미만 ([Faber](https://mebfaber.com/wp-content/uploads/2016/05/SSRN-id962461.pdf)); 영구 포트폴리오 57년 CAGR 8.65%, MDD −15.52% ([PortfolioDB](https://www.portfoliodb.com/portfolios/permanent-portfolio)) | 가장 적은 노력. 처음 룰 기반 투자를 익히는 훈련용 |
| 4 | 5분 ORB 시가 범위 돌파 (단타, 조건부) | 첫 5분봉 방향으로 두 번째 봉 시가 진입, 손절 첫 봉 저가, 장 마감 청산 | 미국 QQQ → 숙달 후 개별종목 · 매일 | QQQ 2016\~2023 연 31%, 샤프 1.12, 승률 24% ([Zarattini & Aziz 2023](https://static1.squarespace.com/static/5983d931579fb366729580d8/t/643ed6765176b45506e41a01/1681839734183/SSRN-id4416622.pdf)); 슬리피지 2.2¢/주에서 수익 0 ([독립 재현](https://github.com/giovannibrusco/zarattini-2023-orb-qqq)) | 규칙이 논문으로 공개돼 검증 가능. PDT 폐지로 소액도 가능. 페이퍼 3개월 통과 후에만 실전 |

RSI(2)·볼린저밴드 평균회귀는 승률이 64\~84%로 높지만 손절 없는 설계와 2002년 이후 엣지 감소 때문에 제외했다. 이동평균 크로스는 3번(10개월선)으로 흡수했다. 국내주식 단타는 왕복 비용 약 0.22%(거래세 0.20% + 수수료) 때문에 변동성 돌파를 일봉으로 근사 재현한 사례에서 비용 반영 시 수익이 소멸했으므로([인텔리퀀트](https://www.intelliquant.ai/article/976?forum=0)), 국내주식은 1·3번 장기 전략과 스윙(2\~10일) 변형만 허용한다.

## 전략별 규칙 상세와 공통 리스크 관리

네 전략 모두 "진입 조건 · 청산 조건 · 파라미터 · 쉬는 조건" 네 항목으로 코드화하고, 파라미터는 화면에서 범위 안에서만 바꿀 수 있게 한다.

**1. 듀얼 모멘텀 GEM** — 매월 마지막 거래일 장 마감 직전 1회 판단.

- 절대 모멘텀: S&P 500(SPY 또는 국내상장 S&P500 ETF) 12개월 수익률 > 미 단기국채 수익률이면 주식, 아니면 미국 종합채권(AGG) 100%
- 상대 모멘텀: 주식일 때 S&P 500 vs 선진국 제외미국(ACWI ex-US) 중 12개월 수익률 높은 쪽 100%
- 앙상블: 룩백 6·9·12개월 세 판단을 각 1/3 비중으로 독립 운용(ReSolve 권고 방식)
- 파라미터: 룩백 개월 수, 리밸런싱 요일. 쉬는 조건 없음(채권이 곷 쉬는 상태)

**2. 변동성 돌파 (업비트)** — 매일 09:00 기준 일봉, 코인별 독립 실행.

```latex
\text{목표가} = \text{당일 시가} + (\text{전일 고가} - \text{전일 저가}) \times K, \quad K = 0.5
```

- 진입: 장중 현재가 ≥ 목표가이면 시장가 매수 (하루 1회)
- 청산: 익일 09:00 시가에 전량 시장가 매도
- 필터: 3·5·10·20일 이평선 위에 있으면 각 1점, 평균(0\~1)을 투자 비중 계수로 사용. 0점이면 쉬기
- 포지션 크기: 목표 일변동성(0.5\~1%) ÷ 전일 변동성((고가−저가)/시가) ÷ 코인 수, 상한 1/코인 수
- 파라미터: K, 목표 변동성, 코인 목록(기본 BTC·ETH·시총 상위 1\~3개). 노이즈 비율 K는 코인픽 검증에서 기각돼 옵션으로만 제공

**3. 10개월선 타이밍 자산배분 (Faber GTAA)** — 매월 말 1회.

- 자산 5개 동일비중(각 20%): 미국 대형주, 선진국 주식, 미국 10년 국채, 원자재, 리츠
- 각 자산: 월말 종가 > 10개월 단순이평이면 보유, 아니면 그 몶은 현금(단기채)
- 초심자 버전: 영구 포트폴리오(주식·장기채·금·현금 각 25%, 연 1회 리밸런싱) — 타이밍 없음

**4. 5분 ORB (미국 QQQ)** — 미국 정규장 시작 후 5분.

- 진입: 첫 5분봉이 양봉이면 두 번째 봉 시가에 롱. 음봉이면 관망(초보자는 숙 제외). 시가≈종가면 관망
- 손절: 첫 5분봉 저가. 목표: 10R 또는 장 마감 청산 중 먼저 오는 것
- 포지션 크기: 자본×1% ÷ (진입가 − 손절가), 레버리지 없음(논문은 4배 허용, 초보자는 1배)
- 쉬는 조건: 슬리피지 2¢/주 가정 백테스트 미통과, 지수 역사적 변동성 하위 20% 구간

**공통 리스크 관리** — 전략 코드가 아닌 리스크 매니저 모듈에서 강제하며, 사용자가 끈 수 없다.

| 규칙 | 값 | 근거 |
| --- | --- | --- |
| 거래당 최대 손실 | 자본의 1% (2% 룰의 보수적 버전) | [CME 2% 룰](https://www.cmegroup.com/education/courses/trade-and-risk-management/the-2-percent-rule) |
| 월 누적 손실 서킷브레이커 | −5% 도달 시 그 달 신규 진입 중단 | 자체 설계 |
| 손절 적용 범위 | 모멘텀·돌파 전략에만. 평균회귀에는 포지션 크기로 통제 | [Kaminski & Lo](https://dspace.mit.edu/bitstream/handle/1721.1/114876/Lo_When%20Do%20Stop-Loss.pdf) |
| 트레일링 스탑 | 22일 고가 − 3×ATR(22) (Chandelier Exit), 스윙 보유 시만 | [StockCharts](https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-overlays/chandelier-exit) |
| 포지션 사이징 | 고정 비율 또는 변동성 타겟. 켈리는 하프 켈리 이하, 고급 옵션 | [Kelly criterion](https://en.wikipedia.org/wiki/Kelly_criterion) |
| 종목당 최대 비중 | 25% (단타 전략 합산 상한 20%) | 자체 설계 |
| 자전거래 방지 | 같은 종목에 내 매수·매도 주문 동시 존재 금지, 주문/취소 비율 상한 | 가상자산이용자보호법 시세조종 금지 |

## AI 판단 계층 설계

&#91;embedded content: AI 판단 계층 · 3단계, 9모듈, 1 피드백 루프\]

전략 규칙이 후보 신호를 만들면 판단 모델과 LLM이 "지금 이 규칙을 실행해도 되는가"를 확률로 답하고, 코드로 짠 리스크 게이트가 최종 거부권을 가진다. 세 단계 중 어느 하나도 다른 단계의 일을 대신하지 않는다.

이렇게 나누는 이유는 판단 모델의 공식 한계 때문이다. TypeSafe 문서는 Jev가 "계산기가 아니며 숫자를 세거나 날짜를 비교하지 못한다"고 명시하고([Jev 1.13 jaggedness](https://docs.typesafe.ai/model-jaggedness/jev-1.13)), 커뮤니티 백테스트(2026년 2\~6월, 8,631개 예측)에서 가격 방향 적중률은 49.34%로 베이스라인과 차이가 없었고 확률은 과신 상태였다([TradeRank](https://www.traderank.ai/blog/what-is-jev-typesafe)). 따라서 판단 모델에 가격 예측을 맡기지 않고, 코드가 만든 요약 상태(state)에 대해 정성적 원자 질문만 던져 그 확률을 진입 게이트와 포지션 크기 계수로 쓴다.

**판단 모델(Jev·Laya) 원자 질문 설계** — 단일 엔드포인트 `POST /v1/systemone`에 state + 질문 dict를 한 번에 보낸다. state는 코드가 만든 400토큰 이내 JSON이며, 원시 가격 나열 대신 "20일 변동성 상위 80%" 같은 등급·비율로 바꿔 넣는다.

| 질문 키 | 타입 | 질문 (instructions) | 결과 사용처 |
| --- | --- | --- | --- |
| regime | choice: trend\_up / range / trend\_down | 최근 20일 가격·이평·거래량 요약으로 볼 때 시장 레짐은? | 변동성 돌파 비중 계수, range면 ORB 쉬기 |
| news\_risk | noul | 24시간 내 뉴스·공시에 상장폐지·해킹·규제·실적 쇼크 같은 급락 요인이 있는가? | 확률 0.6 이상이면 진입 차단 |
| liquidity\_stress | noul | 호가 스프레드·체결 강도 요약으로 볼 때 유동성 스트레스 상태인가? | 포지션 절반, 시장가 대신 지정가 |
| event\_ahead | noul | 향후 24시간 내 FOMC·CPI·실적 발표 등 예정 이벤트가 있는가? | 단타 전략 당일 휴무 |
| signal\_quality | score 1\~5 | 이 후보 신호가 전략 규칙의 의도(추세 초기 돌파)에 부합하는 정도 | 기대값 3 미만이면 보류 |
| already\_priced | noul | 호재 뉴스가 이미 가격에 반영된 상태인가? | 참이면 모멘텀 진입 보류 |

**LLM(Claude·Gemini) 역할** — 텍스트 이해가 필요한 곳에만 쓴다.

| 역할 | 모델 (예) | 호출 시점 | 출력 |
| --- | --- | --- | --- |
| 뉴스·공시 3줄 요약 → 판단 모델 state 입력 | Gemini 3.5 Flash-Lite | 매 시간, 종목당 1회 | 요약문 + 위험 키워드 |
| 진입 전 2모델 합의 | Claude Sonnet 5 + Gemini 3.5 Flash | 후보 신호 발생 시 | 각각 {approve / hold, 이유 1줄}. 둘 다 approve일 때만 통과, 불일치는 관망 |
| 사후 리뷰·매매 일지 | Claude Sonnet 5 | 매일 장 마감 후 | 오늘 매매 해설, 규칙 위반 여부, 개선 제안 |
| 사용자 질의 ("오늘 왜 안 샀어?") | Claude | 온디맨드 | 원장·판단 로그 근거로 답변 |

**확신도 게이팅** — TypeSafe 공식 가이드([confidence](https://docs.typesafe.ai/confidence))를 그대로 코드 기본값으로 둔다. confidence 0.5 미만은 "모른다"로 보고 진입을 보류하고, 0.5\~0.9는 계산된 포지션의 절반만, 0.9 이상은 전량 진입한다. 사용자는 임계값 두 개만 화면에서 조절한다.

**비용 추정(개략)** — 코인 5개를 5분마다 판단하면 판단 모델 호출은 하루 1,440회, 호출당 500토큰으로 잡아도 0.72M 토큰, [입력 $0.042/M·출력 무료](https://typesafe.ai/blog/introducing-system-one-models-and-jev)이므로 하루 약 $0.03이다. LLM 합의는 후보 신호를 하루 10회로 잡으면 모델당 약 3만 입력·3천 출력 토큰으로 Claude Sonnet 5($2/$10) 약 $0.09, Gemini 3.5 Flash($1.50/$9) 약 $0.07이고, 시간별 뉴스 요약(Flash-Lite $0.30/$2.50)은 약 $0.07이다. 합계 하루 $0.3 미만, 월 1만 원 안팎이다([Claude 단가](https://platform.claude.com/docs/en/about-claude/pricing), [Gemini 단가](https://ai.google.dev/gemini-api/docs/pricing)).

**검증과 대체 경로** — 페이퍼 원장에 매 신호의 확률·confidence와 이후 실현 수익률을 기록해 Brier score, ECE, confidence 구간별 적중률을 매주 계산한다. 판단 모델 게이팅을 켠 것과 끈 것을 같은 기간 A/B로 돌려 MDD가 줄지 않으면 게이팅을 끈다. 벤더 종속을 피하기 위해 동일 질문 스키마의 오픈소스 [Laya](https://github.com/NandhaKishorM/laya)(Apache 2.0)와 TypeSafe 호환 엔드포인트를 제공하는 [Kev](https://github.com/jaredpalmer/kev)를 base URL 교체만으로 갈아끼울 수 있게 어댑터를 둔다. Laya는 제로샷 정확도가 낮아(0.362, 다수클래스 기준선 0.461 미만) 원장에서 사후 수익률로 자동 라벨링한 데이터로 파인튜닝한 뒤에만 실전에 쓴다.

## 당일 단타 실행 파이프라인

&#91;embedded content: 하루 타임라인 · 시장 3개 + AI 레인, KST\]

업비트는 09:00에 전일 포지션을 청산하고 새 목표가를 계산한 뒤 돌파를 기다리고, KRX는 15:20 종가 단일가에 스윙·GEM 주문을 내며, 미국 ORB는 22:35 진입 후 04:55에 청산한다. 결정 시각이 겹치지 않으므로 스케줄러 하나가 세 시장을 순환하고, KRX 애프터마켓(16:00\~20:00, 지정가만)은 청산 전용으로만 쓴다.

한 번의 틱(코인 1분, 주식 5분봉 마감)에서 실행 순서는 다음과 같다.

1. 데이터 갱신: 웹소켓 체결가·호가를 캤들로 집계하고 지표·피처를 재계산한다 (코드, 10ms대)
2. 전략 평가: 켜진 전략마다 진입·청산 규칙을 평가해 후보 신호를 만든다. 신호가 없으면 여기서 끝난다
3. 판단 모델 호출: 후보 신호가 있을 때만 state를 만들어 원자 질문을 한 번에 보낸다 (100\~500ms). news\_risk·event\_ahead가 참이면 중단
4. LLM 합의: Claude와 Gemini에 동시 질의해 approve/hold를 받는다 (5\~15초). 둘 다 approve일 때만 진행
5. 리스크 게이트: 거래당 1%, 월 손실 한도, 종목 비중, 자전거래·주문 비율을 검사하고 수량을 확정한다. confidence에 따라 절반 또는 전량
6. 주문·체결: 레이트리밋 큐를 거쳐 주문하고 체결을 확인한 뒤, 신호·확률·체결가·수수료를 원장에 적는다
7. 청산 감시: 시간 기반 청산(09:00, 15:20, 04:55)은 스케줄러가, 손절·트레일링은 별도 감시 루프가 매 틱 확인한다

| 제약 | 값 | 설계 결과 |
| --- | --- | --- |
| 신호 → 주문 지연 (판단 모델만) | 2초 이내 목표 | 코인 1분봉 전략까지 가능 |
| 신호 → 주문 지연 (LLM 합의 포함) | 20초 이내 목표 | 5분봉 이상 전략에만 LLM 합의 적용, 틱 단위 초단타 불가 |
| API 오류 연속 3회 | 신규 진입 중단 + 알림 | 보유 포지션 청산 경로는 마지막까지 유지 |
| 판단 모델·LLM 무응답 | 3초 / 30초 타임아웃 | 타임아웃 = hold (진입 안 함), 청산은 AI 없이 실행 |
| KIS 토큰 24시간 만료 | 매일 08:00 자동 갱신 | 갱신 실패 시 국내·미국 전략 당일 휴무 |

## 시스템 아키텍처

&#91;embedded content: 시스템 구성도 · 4계층, 백엔드 모듈 8개\]

프론트엔드는 상태를 보여주고 파라미터를 바꾸는 역할만 하며, 매매 판단과 주문은 전부 백엔드에서 일어난다. 브라우저를 닫아도 자동매매가 계속되고, 거래소 API 키는 백엔드 서버에만 있다.

| 모듈 | 책임 | 주요 기술 선택 |
| --- | --- | --- |
| API 게이트웨이 | REST(설정·조회·백테스트 요청), WebSocket(시세·체결·AI 로그 푸시), 사용자 인증 | FastAPI + Pydantic v2, JWT, 단일 사용자부터 시작 |
| 전략 엔진 | 전략을 플러그인 인터페이스(`on_bar`, `on_tick`, `params_schema`)로 통일, 백테스트와 실전이 같은 코드 실행 | 순수 Python 클래스, pandas 없는 이벤트 루프 |
| 피처 빌더 | 이평·ATR·변동성·레짐 통계와 뉴스 요약을 합쳍 400토큰 이내 state JSON 생성 | pandas-ta, 숫자는 등급·백분위로 변환 |
| 판단 계층 | 판단 모델·LLM 호출, 타임아웃, 프로바이더 교체 | `JudgeProvider` 인터페이스: TypeSafe SDK, Laya(로컬), Kev 호환; `LLMProvider`: Anthropic SDK, Google GenAI SDK |
| 리스크 매니저 | 모든 주문 전 검사, 서킷브레이커, 자전거래·주문 비율 감시 | 코드 상수 + DB 설정, AI는 접근 불가 |
| 주문 실행기 | 시장별 어댑터로 주문·취소·체결 확인, 레이트리밋 큐, 재시도, 페이퍼 모드 | `BrokerAdapter` 인터페이스: pyupbit, python-kis, alpaca-py, PaperBroker |
| 백테스터 | 같은 전략 코드로 과거 재현, 슬리피지·수수료·세금 모델, 파라미터 스윗 횟수 기록 | vectorbt(벡터 파라미터 탐색) + 자체 이벤트 리플레이(장중 진입 검증) |
| 스케줄러 | 시장 캘린더(휴장·서머타임·NXT), 시간 기반 청산, KIS 토큰 갱신, 일일 리뷰 | APScheduler, exchange\_calendars |
| 저장소 | 캤들(1분·5분·일), 주문·체결 원장, 판단 로그(state·확률·confidence·사후 수익률), 설정 | TimescaleDB(PostgreSQL), Redis(실시간 상태·큐·레이트리밋 카운터) |

프론트엔드는 Next.js(App Router) + TypeScript이며 차트는 TradingView Lightweight Charts, 상태는 WebSocket 스트림을 구독하는 클라이언트 스토어로 다룬다. 인프라는 고정 IP가 있는 소형 VPS 1대(업비트 API 키가 허용 IP 등록을 요구)에 Docker Compose로 백엔드·DB·Redis를 올리고, 프론트는 Vercel 또는 같은 서버에 둔다. 거래소 API 키는 환경변수로만 주입하고 출금 권한은 부여하지 않는다.

모든 전략과 어댑터는 페이퍼 모드를 가진다. `PaperBroker`는 실시간 시세로 가상 체결을 내며 슬리피지(시장가 1틱 + 매수 호가 소진 모델)와 수수료를 적용하므로, 실전 전환은 설정 한 줄만 바꾸면 된다.

## 시장별 거래 API 연동과 제약

1차 MVP는 코인은 업비트, 국내·미국 주식은 한국투자증권 KIS 한 계정으로 가고, Alpaca는 미국 ORB 전략의 페이퍼 트레이딩 검증에만 쓴다. 업비트와 빗썸은 모의투자가 없어 코인 페이퍼 모드는 자체 `PaperBroker`로 진행한다.

| API | 인증 | REST / WS | 레이트리밋 | 모의투자 | Python 라이브러리 | 주의 |
| --- | --- | --- | --- | --- | --- | --- |
| [업비트](https://docs.upbit.com/kr/reference/rate-limits) | JWT HS512, KYC + 2채널 인증, 허용 IP 등록 필수 | REST + WS (public/private) | 시세 10회/초/IP, 주문 생성 12회/초, 조회 30회/초. 429는 재시도, 418은 계정 일시 차단 | 없음 (`/v1/orders/test`로 유효성만) | [pyupbit](https://github.com/sharebook-kr/pyupbit), ccxt | 고정 IP 서버 필수. 2026-10-30 약관 개정 예정 (확인 필요) |
| [빗썸 2.0](https://apidocs.bithumb.com/docs/api-요청-수-제한-안내) | JWT Bearer | REST + WS (Private v1은 2026-10-30 종료) | Public 150회/초, Private 140회/초 | 없음 | 공식 SDK 없음, 2.0 직접 래핑 | 업비트와 구조 유사. 2차 확장 |
| [KIS Developers](https://apiportal.koreainvestment.com/intro) | appkey/secret → 토큰 24시간, 발급 1분 1회 | REST + WS (체결가·호가·체결통보, 해외 포함) | 실전 20건/초, 모의 1\~2건/초 (확인 필요), WS 등록 41건/세션 | 있음 (국내 + 해외, 별도 서버) | [python-kis](https://github.com/Soju06/python-kis), mojito, 공식 샘플 | 관심종목 35개 이하. 당일 분봉 30건/호출, 과거 분봉 보관 범위 미확정 → 자체 축적 필수 |
| [키움 REST](https://openapi.kiwoom.com/intro?dummyVal=0) | appkey/secret → 토큰 | REST + WS, OCX 불필요 (2025-03 출시) | 국내 계좌당 5건/초, 실시간 200종목/세션 | 있음 | kiwoom-client, 공식 샘플 | 조건검색 API 장점. 커뮤니티 자료 적어 백업으로 |
| [Alpaca](https://docs.alpaca.markets/docs/paper-trading) | API key/secret | REST + WS | 거래 200회/분, 시세 Basic 200회/분 (IEX) | 있음 (이메일만으로 전 세계 개설, 기본 $100k) | [alpaca-py](https://alpaca.markets/data) | 실계좌는 한국 거주자 개설 가능 여부와 외국환거래규정 확인 필요. 실시간 전 거래소 시세는 $99/월 |

백테스트용 과거 데이터는 국내 일봉은 [FinanceDataReader](https://github.com/financedata-org/FinanceDataReader)와 [pykrx](https://github.com/sharebook-kr/pykrx), 해외 일봉은 [yfinance](https://github.com/ranaroussi/yfinance), 코인 분봉은 업비트 캤들 API로 모으고, 주식 분봉은 서비스 가동 시점부터 KIS 웹소켓으로 직접 축적한다. 미국 1분봉 과거 데이터가 필요한 ORB 백테스트는 Alpaca Basic의 IEX 데이터로 시작하고, 부족하면 [Massive(구 Polygon)](https://massive.com/pricing) Starter($29/월)를 검토한다. 바이낸스 공개 시세는 국내 미등록 거래소이므로 글로벌 코인 가격 참고용으로만 쓴다.

## 화면 구성과 디자인 목업

화면은 일반적인 주식·코인 거래 사이트 구조(상단 시장 탭 + 좌측 네비게이션 + 종목 리스트·차트·호가·주문 패널)를 따르되, 모든 화면에 "페이퍼/실전 모드", "월 손실 한도 게이지", "AI 판단 근거"가 항상 보이도록 했다. 데스크탑 5개와 모바일 1개 화면을 [Claude Design 캔버스](https://claude.ai/artifact/HSufwVcvVZqt1LYs4pL827)에 만들었고, 좌측 메뉴로 화면 간 이동이 되는 클릭 프로토타입이다.

| 화면 | 목적 | 주요 구성 |
| --- | --- | --- |
| 대시보드 | 오늘 상태를 10초 안에 파악 | 총 자산·오늘 손익·실행 중 전략·AI 판단 횟수 KPI, 30일 자산 공선 vs 벤치마크, 오늘 일정 타임라인(09:00 청산, 15:20 주문, 22:35 ORB), 활성 전략 표(ON/OFF 토글), 최근 AI 판단 피드 |
| 거래 · 차트 | 종목 하나를 깊게 보고 주문 | 관심 종목(전략 상태 표시), 호가창, 캔들 차트 위에 변동성 돌파 목표가·이평·진입 마커, 체결/미체결/포지션 탭, 우측 AI 판단 패널(원자 질문 확률 바, confidence, LLM 2모델 이유, 리스크 게이트 체크) + 주문 패널(AI 권장 비중 버튼, 손절가 표시) |
| 전략 설정 | 초보자가 범위 안에서만 조절 | 자본 배분 바, 전략 카드 4개(파라미터 슬라이더, 백테스트 요약, 기본값 복원), ORB 카드의 실전 전환 관문 진행률, 우측 잠긴 리스크 규칙, AI 판단 설정(판단 모델 선택, 확신도 임계값 2개, LLM 합의 구성, 예상 비용) |
| AI 판단 로그 | "왜 샀고 왜 안 샀는가"를 추적 | 보정 지표 카드(Brier, ECE, 확신도 구간별 적중률, 게이팅 A/B), 판단 테이블(시각·종목·판단 모델 결과·confidence·LLM 합의·결과·24시간 후 수익률), 우측 상세(state 입력, 질문별 확률, LLM 이유, "이 판단에 대해 질문" 입력) |
| 백테스트 | 실전 전환 전 검증 | 설정(전략·기간·비용 모델 필수·홀드아웃 잠금·상폐 종목 포함·파라미터 시도 횟수 카운터), 지표 카드(CAGR·MDD·샤프·매매 횟수·2010년 이후 열위 경고), 로그 자산 공선 + 낙폭 차트, 구간별 성과, 실전 전환 전 체크리스트 |
| 모바일 대시보드 | 이동 중 확인과 전략 ON/OFF | 총 자산·손익·월 한도 게이지, 활성 전략 토글, 최근 AI 판단 2건, 하단 탭 5개 |

모든 화면에 공통으로 적용한 규칙은 세 가지다. 색은 국내 관행대로 상승 빨강·하락 파랑을 쓰고 AI 판단 요소만 보라, 리스크 경고는 주황으로 구분한다. 사용자가 바꿀 수 있는 것은 전략 ON/OFF와 파라미터 범위, 확신도 임계값뿐이며 리스크 규칙은 화면에 잠금 표시로 보여준다. 실전 전환 버튼은 관문 조건이 충족될 때까지 비활성 상태로 진행률을 함께 보여준다. 화면의 계좌·손익 숫자는 구성 확인용 샘플이고, 백테스트 화면의 성과 수치만 이 문서에 인용한 공개 자료 기준이다.

## 규제·비용·리스크 고지

국내주식 단타는 왕복 약 0.22%를 매번 이겨야 본전이고, 코인은 0.1%, 미국주식은 슬리피지가 수수료보다 큼. 백테스트 비용 모델은 아래 표의 값을 기본값으로 쓴다.

| 시장 | 거래 비용 (왕복) | 세금 | 거래 시간 (KST) | 규제 포인트 |
| --- | --- | --- | --- | --- |
| 업비트 KRW | 수수료 0.05% × 2 = 0.10% (예약주문 0.139%) | 가상자산 과세 2027-01-01 시행 예정, 기타소득 22%, 연 250만 원 공제 (유예 법안 계류, 확인 필요) | 24시간, 일봉 기준 09:00 | 가상자산이용자보호법: 2026-09-23 금융위가 API 고빈도 자전거래로 거래 활발 가장 사례 고발 → 자전거래·허수주문 방지 로직 필수 |
| KRX 국내주식 | 증권거래세 0.20% (매도, 2026년) + 수수료 0.01\~0.02% × 2 + 유관기관 제비용 ≈ 0.22% | 거래세만 (금투세 폐지) | 정규장 09:00\~15:30, 종가 단일가 15:20\~15:30, KRX 애프터마켓 16:00\~20:00 (2026-09-14부터, 지정가만), NXT 프리 08:00\~08:50 | 가격제한 ±30%, VI(정적 10%, 동적 3\~6%) 2분 단일가, 증권사 SOR이 KRX/NXT 자동 배분 |
| 미국주식 | 증권사 수수료 0.07\~0.25% × 2 + 환전 스프레드; ORB는 슬리피지 2¢/주 가정 | 양도소득세 22%, 연 250만 원 공제, 이듬해 5월 신고 | 정규장 22:30\~05:00 (서머타임), 23:30\~06:00 (표준시) | PDT $25,000 규칙을 일중 마진 기준으로 대체, 사실상 폐지 (FINRA 26-10, 2026-06-04 발효, 2027-10-20까지 단계 적용) → 국내 증권사 해외주식 계좌 적용 시점은 증권사별 확인 필요 |

초보자가 빠지는 함정 다섯 가지를 시스템이 구조적으로 막는다.

1. 과최적화: 백테스튰가 파라미터 시도 횟수를 기록하고, 최근 1년은 홀드아웃으로 잠그며, 일봉 2년 데이터에 변형 7개 이상 시도하면 경고한다 ([Bailey · López de Prado](https://cdar.berkeley.edu/sites/default/files/dhb-risk-2017.pdf))
2. 슬리피지 무시: 모든 백테스트에 시장가 1틱 + 호가 소진 모델을 강제 적용하고, 비용 0 결과는 화면에 표시하지 않는다
3. 생존 편향: 상장폐지·상장유지 코인과 지수 편출 종목을 포함한 종목 유니버스 스냅샷을 날짜별로 보관한다
4. 룩어헤드·재현 오류: 장중 진입 전략은 일봉이 아닌 분봉 이벤트 리플레이로만 검증하고, 신호는 봉 마감 시점 정보로만 계산한다
5. 전략 쇠퇴: 볼린저밴드는 2002년 이후, GEM은 2010년 이후 엣지가 줄었다. 롤링 12개월 성과가 백테스트 하위 10% 밖으로 벗어나면 화면에 "전략 점검 필요" 배지를 표시한다

이 시스템은 투자 자문을 제공하지 않는다. 추천 전략과 백테스트 수치는 공개 자료의 과거 결과이며 미래 수익을 보장하지 않고, 원금 손실이 가능하다. 화면 첫 진입 시와 실전 전환 시 이 고지에 사용자가 동의해야 하며, 타인 자금을 위탁받아 운용하면 자본시장법상 투자일임업 등록 대상이 될 수 있으므로 개인 본인 계좌에서만 사용하는 것을 전제로 한다 (서비스화 시 법률 검토 필요).

## 개발 로드맵

&#91;embedded content: 개발 로드맵 · 5단계, 관문 4개 (제안)\]

자금은 3단계에서 처음 들어가며, 그 전까지는 페이퍼와 모의투자만 쓴다. 각 관문은 화면의 "실전 전환" 버튼을 열기 위한 조건으로 코드에 넣어, 사용자가 기준을 충족하지 않은 전략을 실전에 올리지 못하게 한다.

0단계에서 만드는 것은 데이터 적재(업비트 분봉·일봉, KRX 일봉, 미국 일봉·IEX 1분봉), vectorbt 백테스터와 비용 모델, PaperBroker, 그리고 Next.js 화면 뾼대다. 1단계는 업비트 변동성 돌파를 페이퍼로 돌리면서 판단 모델 게이팅을 붙이고 원장을 쌓는다. 2단계는 KIS 모의투자로 GEM·GTAA 월말 주문과 Alpaca paper의 ORB를 붙이고 LLM 합의를 추가한다. 3단계는 업비트에서만 자본의 10% 이하로 실전을 시작하고, 게이팅 ON/OFF를 같은 기간 비교해 AI 계층이 실제로 MDD를 낮추는지 확인한다.

각 단계의 산출물과 관문 기준은 다음과 같다. **2026-09-29 현재 0단계 코드가 `E:\claude\project\AiTrading\quantpilot`에 있다** (전략 4개, 백테스터, PaperBroker, RiskManager, 판단 계층 스텁, FastAPI, 테스트 23개 통과). 남은 0단계 작업은 실데이터 적재(`qp fetch`)와 `scripts/verify_g1.py`로 관문 G1 확인, 그리고 Next.js 화면 뾼대다.

| 단계 | 산출물 | 관문 통과 기준 |
| --- | --- | --- |
| 0 기반 구축 | 데이터 파이프라인, 백테스터, PaperBroker, 화면 뾼대, 4전략 플러그인 | G1: 비용·슬리피지 포함 백테스트가 공개 수치의 ±20% 이내에서 재현됨 |
| 1 코인 페이퍼 | 업비트 어댑터, 판단 모델 어댑터, 원장·보정 지표 리포트 | G2: 4주 페이퍼에서 게이팅 ON의 MDD가 OFF보다 낮고, 판단 모델 Brier score < 0.25 |
| 2 주식 모의투자 | KIS 어댑터(모의), Alpaca paper 어댑터, LLM 합의 모듈, 시장 캘린더 | G3: 4주간 주문 오류율 1% 미만, 토큰·웹소켓 장애 자동 복구 |
| 3 소액 실전 | 실전 전환 플로우, 알림, A/B 리포트 | G4: 8주간 계좌 서킷브레이커 미발동, 페이퍼 대비 실체결 슬리피지 차이 0.1%p 미만 |
| 4 확장 | 빗썸·키움 어댑터, 다중 사용자, 전략 마켓 | 서비스화 시 법률 검토 선행 |

## 참고 자료

판단 모델과 오픈소스

- [TypeSafe AI — Introducing System One Models and Jev](https://typesafe.ai/blog/introducing-system-one-models-and-jev)
- [Jev 문서: quickstart](https://docs.typesafe.ai/introduction/quickstart) · [choice / score / noul](https://docs.typesafe.ai/primitives/choice) · [confidence](https://docs.typesafe.ai/confidence) · [Jev 1.13 jaggedness](https://docs.typesafe.ai/model-jaggedness/jev-1.13) · [Autoresearch feature discovery 쿡북](https://docs.typesafe.ai/cookbooks/autoresearch_feature_discovery)
- [TradeRank — Jev 트레이딩 백테스트](https://www.traderank.ai/blog/what-is-jev-typesafe) · [jev-trader](https://github.com/buberlo/jev-trader) · [jev-signals](https://github.com/Rmanjini/jev-signals)
- [Laya (오픈소스 Jev 대체)](https://github.com/NandhaKishorM/laya) · [Kev (TypeSafe 호환 로컬)](https://github.com/jaredpalmer/kev) · [awesome-system-one](https://github.com/yanng981/awesome-system-one)
- [TradingAgents](https://github.com/TauricResearch/TradingAgents) · [FinRL](https://github.com/AI4Finance-Foundation/FinRL) · [Qlib](https://github.com/microsoft/qlib) · [freqtrade](https://github.com/freqtrade/freqtrade) · [nautilus\_trader](https://github.com/nautechsystems/nautilus_trader) · [vectorbt](https://github.com/polakowo/vectorbt)

퀀트 전략

- [강환국 — 가상화폐 투자 마법 공식(6) MDD 10% 이하 제한](https://hive.blog/kr/@kangcfa/6-mdd-10-1) · [코인픽 변동성 돌파](https://coinpick.com/quant_strategy_7) · [코인픽 업그레이드판](https://coinpick.com/quant_program/39857) · [인텔리퀀트 국내주식 재현](https://www.intelliquant.ai/article/976?forum=0)
- [Faber — A Quantitative Approach to Tactical Asset Allocation](https://mebfaber.com/wp-content/uploads/2016/05/SSRN-id962461.pdf) · [Edgeful 골든크로스 1960\~2026](https://www.edgeful.com/blog/posts/golden-cross-trading-complete-guide) · [Fang et al. 이평 규칙 표본외 검증](https://www.sciencedirect.com/science/article/abs/pii/S1058330013000396)
- [Antonacci — GEM 확장 백테스트](https://www.optimalmomentum.com/extended-backtest-of-global-equities-momentum/) · [Petit 2026 — GEM 1971\~2026 재현 (SSRN)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=7427878) · [ReSolve — GEM Craftsman's Perspective](https://investresolve.com/global-equity-momentum-executive-summary/) · [PortfolioDB 영구 포트폴리오](https://www.portfoliodb.com/portfolios/permanent-portfolio)
- [StockCharts RSI(2)](https://chartschool.stockcharts.com/table-of-contents/trading-strategies-and-models/trading-strategies/rsi-2) · [StockSoft RSI2 테스트](https://stocksoftresearch.com/rsi-2-trading-strategy/) · [Fang et al. 볼린저밴드 수익성](https://acfr.aut.ac.nz/__data/assets/pdf_file/0007/29896/100009-Popularity-vs-Profitability-BB-August-Final.pdf)
- [Zarattini & Aziz 2023 — Can Day Trading Really Be Profitable?](https://static1.squarespace.com/static/5983d931579fb366729580d8/t/643ed6765176b45506e41a01/1681839734183/SSRN-id4416622.pdf) · [Brusco 독립 재현](https://github.com/giovannibrusco/zarattini-2023-orb-qqq) · [Zarattini·Barbon·Aziz 2024 Stocks in Play](https://ideas.repec.org/p/chf/rpseri/rp2498.html) · [Concretum VWAP](https://concretumgroup.com/volume-weighted-average-price-vwap-the-holy-grail-for-day-trading-systems/)
- [Kaminski & Lo — When Do Stop-Loss Rules Stop Losses?](https://dspace.mit.edu/bitstream/handle/1721.1/114876/Lo_When%20Do%20Stop-Loss.pdf) · [CME 2% 룰](https://www.cmegroup.com/education/courses/trade-and-risk-management/the-2-percent-rule) · [Chandelier Exit](https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-overlays/chandelier-exit) · [Bailey·López de Prado 과최적화](https://cdar.berkeley.edu/sites/default/files/dhb-risk-2017.pdf)

API·규제·비용

- [업비트 rate limits](https://docs.upbit.com/kr/reference/rate-limits) · [업비트 API 키 발급](https://docs.upbit.com/kr/docs/api-key) · [업비트 거래 수수료](https://support.upbit.com/hc/ko/articles/900006143046) · [빗썸 API 요청 수 제한](https://apidocs.bithumb.com/docs/api-요청-수-제한-안내)
- [KIS Developers](https://apiportal.koreainvestment.com/intro) · [공식 샘플 README](https://github.com/koreainvestment/open-trading-api/blob/main/README.md) · [python-kis](https://github.com/Soju06/python-kis) · [키움 REST API](https://openapi.kiwoom.com/intro?dummyVal=0) · [Alpaca paper trading](https://docs.alpaca.markets/docs/paper-trading) · [Alpaca 데이터 플랜](https://alpaca.markets/data)
- [FINRA Regulatory Notice 26-10 (PDT 폐지)](https://www.finra.org/rules-guidance/notices/26-10) · [신한투자증권 NXT 거래시간·VI 안내](https://open.shinhansec.com/mobilealpha/html/CS/NXTPolicyGuide.html) · [미래에셋 매매수수료·제비용](https://securities.miraeasset.com/public/mw/guide/html/20191119095045.html) · [2026 증권거래세](https://glasswallet.com/blog/stock-transaction-tax-guide/)
- [토스뱅크 — 2027 코인 과세](https://www.tossbank.com/articles/coin-tax) · [토스뱅크 — 해외주식 양도소득세](https://www.tossbank.com/articles/overseas-capital-gains-tax) · [뉴스핌 2026-09-23 가상자산 시세조종 고발](https://www.newspim.com/news/view/20260923000895)
- [Claude API 단가](https://platform.claude.com/docs/en/about-claude/pricing) · [Gemini API 단가](https://ai.google.dev/gemini-api/docs/pricing) · [Massive(구 Polygon) 가격](https://massive.com/pricing)
