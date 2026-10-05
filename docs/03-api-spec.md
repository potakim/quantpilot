# 03 · API 명세

FastAPI, base `/api/v1`. 인증은 `Authorization: Bearer <JWT>` (단일 사용자, 로그인은 `.env`의 `QP_ADMIN_PASSWORD`). 모든 응답은 JSON, 시각은 ISO-8601 UTC. 0단계 `api/app.py`의 라우트(`/paper/*`, `/backtest`, `/judge/preview`)는 임시이며 1단계 P1-12에서 아래 형태로 교체한다. 백테스트 응답의 시도 횟수 키는 코드의 `distinct_attempts`를 따른다.

## 1. 공통

### 오류 형식

```json
{ "error": { "code": "RISK_REJECTED", "message": "종목 비중 상한 25% 도달", "detail": {...} } }
```

| HTTP | code | 상황 |
| --- | --- | --- |
| 400 | `INVALID_PARAM` | ParamSpec 범위 밖, 알 수 없는 파라미터 |
| 400 | `KEY_MISSING` | 고른 판단 모델·리뷰어의 API 키가 없음 (detail `keys`: 키 이름 목록, ADR 0032) |
| 401 | `UNAUTHORIZED` | 토큰 없음·만료 |
| 403 | `CONFIRMATION_REQUIRED` | 실전 전환·키 변경에 2차 확인 필요 |
| 404 | `NOT_FOUND` | 전략·주문 없음 |
| 409 | `NO_PRICE` / `HALTED` / `GATE_LOCKED` / `MARKET_NOT_LIVE` | 시세 없음 / 할트 중 / 관문 미통과 / 실시간 엔진이 없는 시장 (ADR 0031) |
| 422 | `RISK_REJECTED` | RiskManager 거부 (detail에 RiskDecision) |
| 502 | `BROKER_ERROR` / `DATA_ERROR` / `JUDGE_ERROR` | 외부 API 실패 |
| 503 | `LIVE_ACCOUNT_MISSING` | 실전 모드(`QP_PAPER=false`)인데 실계좌가 아직 연결되지 않음 — 계좌를 읽는 `/portfolio`·`POST /orders`·포지션 청산·정합성 수락 (실계좌 연결은 2단계) |

### 페이지네이션

목록은 `?limit=50&before=<cursor>` (cursor = 마지막 행의 id 또는 ts). 응답에 `next_cursor`.

## 2. REST

### 2.1 시스템

| 메서드 | 경로 | 설명 |
| --- | --- | --- |
| GET | `/health` | `{ok, version, paper, env, engine_alive, ws_connected:{upbit,kis}, halted:{upbit,krx,us}, live_markets:["upbit"]}` — `live_markets`는 실시간 엔진이 도는 시장 (ADR 0031) |
| POST | `/auth/login` | `{password}` → `{token, expires_at}` |
| GET | `/settings` | settings 테이블 전체 + RiskRules(읽기 전용, `locked:true`) + `judge`: 지금 유효한 AI 판단 설정 `{provider, llm_models, hold_below, full_above, keys:{typesafe, claude, gemini}, active}`. 저장값이 없으면 환경변수 값, `keys`는 키 유무(true/false)만, `active`는 엔진이 실제로 쓰는 `{provider, llm_models, ts}`(`engine.judge.upbit`, 없으면 null) (ADR 0032) |
| PATCH | `/settings` | `{key: value, ...}` — 허용 키만: `gate.*`, `judge.provider`, `llm.models`, `news.enabled`, `notify.*`. `gate.*`는 엔진이 하트비트(5초)마다 읽어 바로 반영, `judge.provider`·`llm.models`는 엔진 재시작 때 적용. `news.enabled`(bool)는 다음 정시 뉴스 수집부터 Gemini 요약을 켜고 끈다 — 꺼도 수집은 계속하고 제목 요약으로 저장(ADR 0035), `GET /settings`의 `judge.news_summary`가 유효값. 연결되지 않은 `laya`는 400 `INVALID_PARAM`, 키가 없는 모델은 400 `KEY_MISSING` (ADR 0032) |

### 2.2 전략

| 메서드 | 경로 | 설명 |
| --- | --- | --- |
| GET | `/strategies` | 등록 전략 + 설정 병합: `[{name, market, timeframe, horizon, symbols, params, schema, enabled, allocation, paper, cost_model, status:{position, month_pnl, mdd_30d}, gate:{...}}]`. `cost_model`은 그 시장의 비용 모델(백테스터·PaperBroker와 같은 값, ADR 0033). 설정 행이 없으면 권장 조합 기본값(변동성 돌파 켜짐·0.15, GEM 꺼짐·0.40, GTAA 꺼짐·0.35, ORB 꺼짐·0) — 엔진도 같은 값을 쓴다 (ADR 0032). `month_pnl`은 월초(시장 현지) 이후 전략 손익률, `mdd_30d`는 최근 30일 최대 낙폭(양수 비율). 전략 체결 재생 + 1시간 종가 평가, 자본 기준은 allocation × 월초 평가액(없으면 초기 자금). 해당 기간 체결이나 시세가 없으면 null (ADR 0020) |
| GET | `/strategies/{name}` | 단일 |
| PATCH | `/strategies/{name}` | `{enabled?, params?, allocation?, symbols?}` — params는 ParamSpec 검증, allocation 합 ≤ 1, intraday 합 ≤ 0.2 (설정 행이 없는 전략은 기본 배분으로 합에 들어간다). 엔진은 하트비트마다 enabled·allocation·params를 읽는다: 꺼짐·배분 0은 진입만 막고, 사이징은 평가액 × allocation. symbols는 시세 구독 범위라 엔진에 아직 반영하지 않는다 (ADR 0032) |
| POST | `/strategies/{name}/reset-params` | 기본값 복원 |
| POST | `/strategies/{name}/go-live` | 페이퍼 → 실전. 관문 미통과면 409 `GATE_LOCKED` + `{missing:[...]}`. 통과 시 403 `CONFIRMATION_REQUIRED` → `{confirm_password}` 재요청 |
| POST | `/strategies/{name}/go-paper` | 실전 → 페이퍼 (즉시, 확인 불필요. 보유 포지션은 유지하고 신규 진입만 페이퍼) |

### 2.3 백테스트

| 메서드 | 경로 | 설명 |
| --- | --- | --- |
| POST | `/backtests` | 요청: `{strategy, params?, symbols?, source, start?, end?, initial_cash?, unlock_holdout?}` → 202 `{id, status:"queued"}`. `start`가 있으면 그날부터 받는다(업비트는 필요한 개수만큼, 캐시가 더 늦게 시작하면 다시 받음, ADR 0033 §11). 실행은 워커 스레드, 진행은 WS `backtest:{id}` `{progress, stage, done?, status?, error?}` |
| GET | `/backtests/{id}` | `{id, strategy, source, params, symbols, created_at, period_start, period_end, status, progress, error, metrics, cost_model, holdout_cutoff, unlocked_holdout, attempt_no, attempts:{distinct_attempts, warn_after, overfit_warning}, warnings, equity:[{ts,v}], drawdown:[{ts,v}], data_end, benchmark:{symbol, label, points:[{ts,v}], drawdown:[{ts,v}]}\|null, periods:[{key, label, start, end, cagr, bench_cagr, mdd, bench_mdd, excess}], fills_tail:[...]}`. 곡선은 최대 500점. 벤치마크 = 대표 종목을 처음에 사서 들고 있기(비용 없음, GEM=SPY·변동성 돌파=KRW-BTC·GTAA=360750·ORB=QQQ), `periods`는 전체 곡선으로 계산한 전체 기간·최근 3년·최근 1년(곡선보다 긴 구간은 뺀다), `data_end`는 홀드아웃으로 자르기 전 데이터의 마지막 시각. ADR 0033 이전 결과는 `benchmark`·`data_end`가 null (ADR 0033) |
| GET | `/backtests?strategy=` | 목록 (attempt_no 포함) |
| GET | `/backtests/{id}/report.csv` | 체결 전체 CSV |

`unlock_holdout=true`는 전략당 1회만 허용하고 `backtests.unlocked_holdout`에 남긴다. 두 번째 요청은 409.

### 2.4 거래·포지션

| 메서드 | 경로 | 설명 |
| --- | --- | --- |
| GET | `/portfolio` | `{total_equity_krw, today_pnl_krw, by_market:{upbit:{active,cash,equity,positions:[...], today_pnl:{amount,pct}\|null}, krx:..., us:...}, month_pnl, month_limit, halted:{...}}` (USD는 환율 환산, 환율 출처 명시). `today_pnl` = 현재 평가액 − 시장 현지 자정 이후 첫 `equity_snapshots` 값, 오늘 스냅샷이 없으면 null. `today_pnl_krw`는 값이 있는 시장의 합(미국은 환율이 있을 때만), 하나도 없으면 null (ADR 0020). 합계·오늘 손익은 `active` 시장만 센다 — 실시간 엔진이 있거나 저장된 계좌(포지션·페이퍼 현금)가 있는 시장 (ADR 0031) |
| GET | `/portfolio/equity?market=upbit&days=30` | 자산 곡선 `{market, days, points:[{ts,v}], benchmark:[{ts,v}]\|null, source:"equity_snapshots"}`. days ∈ {30, 90, 365}(그 밖은 400 `INVALID_PARAM`). 30일은 1시간당, 90·365일은 하루당 마지막 값 1개. benchmark는 같은 시작 자본으로 BTC(KRW-BTC)를 들고만 있었을 때(업비트만, 봉이 없으면 null). 스냅샷이 없으면 `points: []` |
| GET | `/schedule` | 오늘(KST) 예약 작업 `[{name, title, market, next_action:{at, what}, done}]` — scheduler 잡 표(JOBS)의 cron에서 계산한 오늘 실행 시각(UTC ISO), 이미 지난 것은 `done:true`. 변동성 돌파는 1분봉으로 계산한 오늘 목표가를 함께 싣는다(봉이 없으면 "목표가 계산 불가"). 읽기 전용 (ADR 0020). 실시간 엔진이 없는 시장의 잡과 연결되지 않은 잡은 싣지 않는다 (ADR 0031) |
| GET | `/positions?market=` | 포지션 목록 + 전략·손절가·미실현 |
| GET | `/orders?market=&status=&limit=` | 주문 목록 |
| GET | `/fills?market=&strategy=&from=&to=` | 원장 |
| POST | `/orders` | 수동 주문 `{market, symbol, side, qty|amount, type, limit_price?, stop?}` → RiskManager 통과 시 201 `{order(status=queued), risk}`, 거부 시 422(할트 중 409 `HALTED`, 실시간 엔진이 없는 시장은 409 `MARKET_NOT_LIVE` — ADR 0031). **수동 주문도 리스크 게이트를 탄다** — api는 사전 검사 후 주문 큐에 넣고 엔진이 OrderExecutor로 다시 검사·실행한다 (ADR 0017) |
| DELETE | `/orders/{id}` | 미체결 취소 |
| POST | `/positions/{market}/{symbol}/close` | 시장가 청산 (리스크 게이트의 exit 경로) |
| GET | `/quotes/{market}/{symbol}` | 현재가·호가 5단계·전략 상태(목표가, 이평 스코어) + 비용 `fee_rate`(편도 수수료율)·`tax_rate_sell`(매도 세율) — 백테스터·PaperBroker와 같은 CostModel 프리셋 (ADR 0020) |
| GET | `/candles/{market}/{symbol}?tf=5m&limit=500&before=` | 캔들 |

### 2.5 AI 판단

| 메서드 | 경로 | 설명 |
| --- | --- | --- |
| GET | `/judgments?from=&to=&market=&strategy=&outcome=&limit=` | 판단 로그 (signals ⋈ judgments ⋈ llm_verdicts) + `rule_unmet`: 기간 안 거래일(시작 시각 기준)의 규칙 미충족 (전략, 종목) 수, 엔진 기록 전이면 `null` (ADR 0027) |
| GET | `/judgments/{id}` | 상세: state 원문, answers, verdicts, 관련 주문·체결, realized_ret_24h |
| POST | `/judgments/{id}/ask` | `{question}` → Claude가 state·answers·verdicts를 근거로 답변 `{answer, cost_usd}` (화면 "이 판단에 대해 물어보기") |
| GET | `/judgments/calibration?weeks=4` | `{brier, ece, n, buckets:[{range, n, hit_rate, avg_conf}], by_provider}` |
| GET | `/judgments/ab?weeks=4` | 게이팅 ON/OFF 비교 `{on:{ret, mdd, n_trades}, off:{...}, g2_pass:bool}` — OFF는 같은 신호를 게이팅 없이 페이퍼 체결한 섀도 원장 |
| POST | `/judge/preview` | `{state}` → 판단 파이프라인 1회 실행(주문 없음). 개발·데모용 |

### 2.6 리뷰·리포트

| 메서드 | 경로 | 설명 |
| --- | --- | --- |
| GET | `/reviews?limit=30` | 일일 매매 일지 |
| GET | `/reports/gates` | 관문 G1~G4 현재 상태 `{g1:{pass, evidence}, g2:{pass, mdd_on, mdd_off, brier, days:18/28}, ...}` |
| GET | `/risk/events?limit=` | 리스크 이벤트 |
| GET | `/costs/ai?month=` | AI 비용 집계 (provider별 호출 수·USD) |
| POST | `/reconcile/{market}/accept-broker` | "브로커 기준으로 맞추기" `{confirm_password}` → Reconciler.accept_broker (정합 이벤트 해결·할트 해제, ADR 0015·0017). 할트를 푸는 유일한 API |

## 3. WebSocket `/ws` (`/api/v1/ws`)

연결 시 `{"auth": "<JWT>", "subscribe": ["ticks:upbit:KRW-ETH", "fills", "judgments", "risk", "portfolio", "backtest:<id>"]}`.
서버 → 클라이언트 메시지는 `{"ch": "...", "ts": "...", "data": {...}}`.

| 채널 | data | 빈도 |
| --- | --- | --- |
| `ticks:{market}:{symbol}` | `{price, volume, side, bar_5m:{o,h,l,c,v,ts}}` | 체결마다 (최대 4/s로 스로틀) |
| `orderbook:{market}:{symbol}` | 호가 5단계 | 1/s |
| `fills` | Fill + signal 요약 | 체결마다 |
| `judgments` | 판단 결과 요약 `{id, symbol, strategy, confidence, gate, blocks, verdicts:[{model, approve}]}` | 판단마다 |
| `risk` | risk_events 행 | 발생 시 |
| `portfolio` | `/portfolio` 응답의 변경분 | 5초 |
| `backtest:{id}` | `{progress:0~1, stage}` → 완료 시 `{done:true}` | 진행 중 |
| `strategy.status` | 전략 하나에 `{name, market, enabled, allocation, position:{심볼: 수량}}` — 엔진이 지금 적용 중인 값. 보내는 쪽은 실시간 엔진, 오늘 할 일(목표가)은 REST `/schedule` (ADR 0034) | 변경 시 (엔진 하트비트 5초마다 비교, 시작 시 전부) |

클라이언트 → 서버: `{"subscribe": [...]}`, `{"unsubscribe": [...]}`, `{"ping": 1}`. 30초 무응답 시 서버가 끊는다.

## 4. 프론트엔드 화면 ↔ API 매핑

| 화면 | 사용 API |
| --- | --- |
| 대시보드 | `/portfolio`(오늘 손익 `today_pnl_krw`), `/portfolio/equity`(자산 곡선 30/90/365일), `/schedule`(오늘 일정), `/strategies`(이번 달·MDD), `/judgments?limit=5`, `/reports/gates`, WS `portfolio`·`judgments`·`strategy.status`(일정은 REST `/schedule`에 WS 항목을 덧붙인다) |
| 거래·차트 | `/quotes`(주문 패널 수수료 행 `fee_rate`), `/candles`, `/orders`, `/positions`, `/judgments?symbol=`, POST `/orders`, WS `ticks`·`orderbook`·`fills` |
| 전략 설정 | `/strategies`, PATCH, `/strategies/{name}/reset-params`, `/portfolio`(총 자본·시장 계좌), `/settings`(리스크 규칙·`judge`), PATCH `/settings`, `/costs/ai`, 백테스트 보기 → `/backtests?strategy=` |
| AI 판단 로그 | `/judgments`, `/judgments/{id}`, `/judgments/calibration`, `/judgments/ab`, POST `/ask` |
| 백테스트 | POST `/backtests`, GET `/backtests/{id}`, WS `backtest:{id}` |
| 포트폴리오 | `/portfolio`(시장 계좌·월 손익·할트), `/portfolio/equity?market=`(시장별 자산 곡선), `/positions`(현재가·미실현), `/strategies`(배분), `/health`(`live_markets`), WS `portfolio`·`fills`(받으면 다시 불러온다) |
| 설정 | `/settings`, 키 등록(POST `/settings/keys`, 값은 저장만 하고 절대 반환하지 않음) |
