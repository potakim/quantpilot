# 01 · 시스템 아키텍처

## 1. 구성 요소

```
┌────────────────────────────── Next.js 프론트엔드 (Vercel 또는 같은 VPS) ──────────────────────────────┐
│ 대시보드 · 거래/차트 · 전략 설정 · AI 판단 로그 · 백테스트 · 포트폴리오 · 설정           │
└──────────────────────────────┬─────────────── REST + WebSocket ──────────────────────────┘
                               │
┌──────────────────────────────▼─────────────── FastAPI 백엔드 (Python, 단일 VPS, Docker) ────────────┐
│  api/            REST 라우트 · WS 허브 · 인증(JWT) · 설정                                     │
│  engine/         틱 루프 · 전략 엔진 · 피처 빌더 · 판단 계층 · 리스크 매니저 · 주문 실행기     │
│  scheduler/      APScheduler: 시장 캘린더 · 시간 기반 청산 · 토큰 갱신 · 일일 리뷰           │
│  data/           시세 수집(WS·REST) · 캔들 집계 · 뉴스 수집 · 캐시                            │
│  backtest/       vectorbt(파라미터 탐색) + 자체 이벤트 리플레이(검증)                        │
└───────┬──────────────────────┬────────────────────────────┬────────────────────────────────┘
        │ SQL                  │ pub/sub · 큐 · 카운터        │ HTTPS · WSS
┌───────▼────────┐    ┌────────▼────────┐          ┌─────────▼──────────────────────────────────┐
│ TimescaleDB    │    │ Redis           │          │ 외부: 업비트 · KIS · Alpaca(paper)          │
│ 캔들·원장·판단  │    │ 실시간 상태·큐    │          │       TypeSafe Jev · Laya · Claude · Gemini │
│ 로그·설정      │    │ 레이트리밋 카운터 │          │       뉴스 RSS · 공시                        │
└────────────────┘    └─────────────────┘          └────────────────────────────────────────────┘
```

## 2. 프로세스 모델

VPS 한 대에서 Docker Compose로 5개 컨테이너를 띄운다. 프로세스를 나누는 기준은 **장애 격리**다. API가 죽어도 매매 엔진이 살아 있어야 하고, 엔진이 죽어도 청산 스케줄은 돌아야 한다.

| 컨테이너 | 역할 | 죽으면 |
| --- | --- | --- |
| `api` | FastAPI + WS 허브. 화면 요청, 백테스트 요청(별도 워커 스레드) | 화면만 안 보임. 매매는 계속 |
| `engine` | 시세 구독 → 틱 루프 → 전략·판단·리스크·주문. 시장별 asyncio 태스크 | 신규 진입 중단. `scheduler`가 감지해 알림 + 보유 포지션은 `scheduler`의 시간 청산으로 정리 |
| `scheduler` | APScheduler. 캘린더, 09:00/15:20/04:55 청산, KIS 토큰 갱신, 20:30 리뷰, 헬스체크 | 시간 청산이 안 됨 → `engine`이 자체 백업 타이머로 청산 (이중화) |
| `db` | TimescaleDB (PostgreSQL 16 + timescaledb) | 원장 기록 실패 → 엔진은 신규 진입 중단, 청산은 진행하고 원장을 로컬 파일에 임시 기록 |
| `redis` | pub/sub(시세·체결 이벤트), 주문 큐, 레이트리밋 카운터, 실시간 상태 캐시 | 엔진은 인메모리 폴백으로 계속. 화면 실시간 갱신만 중단 |

`engine`과 `scheduler`는 같은 코드베이스의 다른 엔트리포인트(`quantpilot.engine.main`, `quantpilot.scheduler.main`)다. 0단계 코드의 `api/app.py`가 갖고 있는 인메모리 `PaperBroker`는 1단계에서 `engine`으로 옮기고, `api`는 DB·Redis만 읽는다.

## 3. 틱 실행 순서 (핵심 시퀀스)

한 번의 틱(코인 1분봉 마감, 주식 5분봉 마감, 또는 월간 전략의 월말 봉)에서 `engine`이 하는 일. 이 순서는 `docs/04-modules.md`의 `TickRunner`가 구현한다.

```
시세 이벤트 (WS 체결가 / 봉 마감)
  │
  ├─ 1. 캔들 집계 · 지표 갱신 ─────────────── 코드, ~10ms
  │     CandleAggregator.on_trade → Bar 마감 시 아래로
  │
  ├─ 2. 전략 평가 ─────────────────────────── 코드, ~1ms/전략
  │     for s in active_strategies(market): targets = s.on_bar(ctx)
  │     targets 비어 있으면 여기서 끝 (판단 모델 호출 없음 → 비용 0)
  │
  ├─ 3. 리스크 사전 검사 ────────────────────  코드
  │     서킷브레이커·할트 상태면 진입 target 제거 (청산 target은 유지)
  │
  ├─ 4. 판단 모델 (진입 target에만) ────────── Jev/Laya, 100~500ms
  │     state = FeatureBuilder.build(symbol, ctx, news)
  │     jr = judge.judge(state)          # 원자 질문 6개 일괄
  │     blocks = hard_blocks(jr)         # news_risk·event_ahead
  │     gate = gate(jr.confidence)       # HOLD / HALF / FULL
  │     HOLD 또는 blocks 있으면 → 판단 로그 기록 후 그 target 폐기
  │
  ├─ 5. LLM 합의 (5분봉 이상 전략만, 4를 통과한 target에만) ── 5~15초, 병렬
  │     verdicts = gather(claude.review(state, jr), gemini.review(state, jr))
  │     둘 다 approve 아니면 → 판단 로그 기록 후 폐기
  │     타임아웃(30초) = hold
  │
  ├─ 6. 사이징 · 리스크 게이트 ──────────────  코드
  │     qty = weight × size_multiplier × 배정자본 / price
  │     d = risk.check(order, equity=, price=, positions=, horizon=, intraday_exposure=)   # 키워드 전용
  │     d.allowed False → 거부 사유 기록
  │
  ├─ 7. 주문 · 체결 확인 ────────────────────  코드, 브로커 API
  │     OrderExecutor.submit(order) → 레이트리밋 큐 → BrokerAdapter.submit
  │     체결 통보(WS) 또는 폴링으로 Fill 확정 → 원장 INSERT (신호·확률·확신도·체결가·수수료)
  │
  └─ 8. 이벤트 발행 ────────────────────────── Redis pub/sub → api → 화면 WS
```

시간 제약: 판단 모델까지 2초 이내, LLM 합의 포함 20초 이내. 1분봉 코인 전략에는 LLM 합의를 적용하지 않는다(시간 초과). 청산(손절·트레일링·시간 청산)은 **판단 모델을 거치지 않는다** — 2·6·7만 탄다.

## 4. 데이터 흐름

### 4.1 시세

| 시장 | 실시간 | 캔들 | 과거 |
| --- | --- | --- | --- |
| 업비트 | WS `trade`·`orderbook` (public, 5 conn/s) | 엔진이 체결가로 1분봉 집계 후 `candles` 저장, 일봉은 REST 보정 | REST `/candles` (200/req, 10 req/s) |
| KIS 국내 | WS 체결가·호가 (41건/세션) | 엔진 집계 + 당일 분봉 REST(30건/호출)로 보정 | 일봉 FDR·pykrx, **분봉은 서비스 시작 후 자체 축적** |
| KIS 해외 / Alpaca | KIS WS 해외체결가 / Alpaca WS(IEX) | 엔진 집계 | 일봉 yfinance, 분봉 Alpaca Basic(IEX) |

캔들은 시장 현지시간 tz-naive로 전략에 전달하고 DB에는 UTC로 저장한다. 하나의 `Bar`가 확정되는 시점은 다음 봉의 첫 체결이 들어왔을 때 또는 봉 마감 + 2초 타이머 중 빠른 쪽이다.

### 4.2 뉴스·공시

시간별로 RSS(코인 주요 매체, 연합인포맥스, DART 공시 API)를 수집해 종목·키워드 매칭 후 Gemini Flash-Lite로 100자 요약과 위험 키워드를 뽑아 `news_items`에 저장한다. 피처 빌더는 최근 24시간 요약 최대 3건을 state에 넣는다.

### 4.3 원장과 판단 로그

`fills`(체결)와 `judgments`(판단)는 `signal_id`로 연결된다. 한 후보 신호는 판단 로그 1행, 체결 0~N행을 갖는다. 진입하지 않은 판단도 기록해야 보정 지표(Brier·ECE)를 계산할 수 있다. 24시간 후 실현 수익률은 `scheduler`가 매일 채운다(`judgments.realized_ret_24h`).

## 5. 장애 시 동작 (요약, 상세는 07-operations)

| 상황 | 엔진 동작 |
| --- | --- |
| 거래소 WS 끊김 | 5초 백오프 재접속(최대 5회), 그동안 REST 폴링. 30초 이상 시세 없으면 신규 진입 중단 |
| 주문 API 오류 연속 3회 | `RiskManager.api_error()` → 할트 + 알림. 청산 경로는 재시도 계속 |
| 판단 모델 타임아웃(3초) | 그 신호는 hold. 연속 10회면 판단 모델 장애 알림, 전략은 계속 평가(진입만 안 함) |
| LLM 타임아웃(30초) | hold |
| DB 쓰기 실패 | 진입 중단, 청산 진행, 원장을 `data/ledger_fallback.jsonl`에 기록 후 복구 시 재적재 |
| KIS 토큰 갱신 실패 | 국내·미국 전략 당일 휴무, 보유 포지션 청산 시도(토큰 만료 전 남은 시간으로), 알림 |
| 엔진 프로세스 다운 | Docker restart. 재시작 시 DB에서 포지션·미체결 복원, 브로커 잔고와 대조(`Reconciler`) 후 불일치면 할트 |

## 6. 보안

- 거래소 키는 `.env` → 환경변수로만. 업비트 키는 **출금 권한 없이**, 허용 IP에 VPS 고정 IP만.
- API는 JWT 단일 사용자. 실전 전환·키 변경은 2차 확인(비밀번호 재입력) 필요.
- AI 계층은 `execution` 패키지를 import할 수 없다(테스트 `test_no_ai_to_execution_import`로 강제, 1단계).
- 로그·원장·API 응답에 키·토큰이 들어가지 않도록 `SecretStr` + 로그 필터.

## 7. 0단계 코드와의 대응

| 문서의 구성 요소 | 0단계 코드 | 1단계에서 |
| --- | --- | --- |
| 전략 엔진 | `strategies/` | 변경 없음 |
| 백테스터 | `backtest/` | vectorbt 탐색기 추가 |
| 리스크·주문 실행기 | `execution/risk.py`, `execution/paper.py` | `OrderExecutor`(큐·재시도), `UpbitBroker` |
| 판단 계층 | `judgment/base.py`, `judgment/stub.py` | `typesafe.py`, `anthropic.py`, `google.py`, `FeatureBuilder` |
| 틱 루프 | 없음 (`Backtester.run` 안에 인라인) | `engine/tick.py` — 백테스터도 이 루프를 재사용하도록 리팩터 |
| 데이터 | `data/loader.py`(REST) | `data/upbit_ws.py`, `CandleAggregator`, DB 저장 |
| API | `api/app.py` (인메모리) | DB·Redis 기반, WS 허브 |
| 스케줄러 | 없음 | `scheduler/` |
