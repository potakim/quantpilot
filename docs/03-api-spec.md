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
| 401 | `UNAUTHORIZED` | 토큰 없음·만료 |
| 403 | `CONFIRMATION_REQUIRED` | 실전 전환·키 변경에 2차 확인 필요 |
| 404 | `NOT_FOUND` | 전략·주문 없음 |
| 409 | `NO_PRICE` / `HALTED` / `GATE_LOCKED` | 시세 없음 / 할트 중 / 관문 미통과 |
| 422 | `RISK_REJECTED` | RiskManager 거부 (detail에 RiskDecision) |
| 502 | `BROKER_ERROR` / `DATA_ERROR` / `JUDGE_ERROR` | 외부 API 실패 |

### 페이지네이션

목록은 `?limit=50&before=<cursor>` (cursor = 마지막 행의 id 또는 ts). 응답에 `next_cursor`.

## 2. REST

### 2.1 시스템

| 메서드 | 경로 | 설명 |
| --- | --- | --- |
| GET | `/health` | `{ok, version, paper, env, engine_alive, ws_connected:{upbit,kis}, halted:{upbit,krx,us}}` |
| POST | `/auth/login` | `{password}` → `{token, expires_at}` |
| GET | `/settings` | settings 테이블 전체 + RiskRules(읽기 전용, `locked:true`) |
| PATCH | `/settings` | `{key: value, ...}` — 허용 키만: `gate.*`, `judge.provider`, `llm.models`, `news.enabled`, `notify.*` |

### 2.2 전략

| 메서드 | 경로 | 설명 |
| --- | --- | --- |
| GET | `/strategies` | 등록 전략 + 설정 병합: `[{name, market, timeframe, horizon, symbols, params, schema, enabled, allocation, paper, status:{position, month_pnl, mdd_30d}, gate:{...}}]` |
| GET | `/strategies/{name}` | 단일 |
| PATCH | `/strategies/{name}` | `{enabled?, params?, allocation?, symbols?}` — params는 ParamSpec 검증, allocation 합 ≤ 1, intraday 합 ≤ 0.2 |
| POST | `/strategies/{name}/reset-params` | 기본값 복원 |
| POST | `/strategies/{name}/go-live` | 페이퍼 → 실전. 관문 미통과면 409 `GATE_LOCKED` + `{missing:[...]}`. 통과 시 403 `CONFIRMATION_REQUIRED` → `{confirm_password}` 재요청 |
| POST | `/strategies/{name}/go-paper` | 실전 → 페이퍼 (즉시, 확인 불필요. 보유 포지션은 유지하고 신규 진입만 페이퍼) |

### 2.3 백테스트

| 메서드 | 경로 | 설명 |
| --- | --- | --- |
| POST | `/backtests` | 요청: `{strategy, params?, symbols?, source, start?, end?, initial_cash?, unlock_holdout?}` → 202 `{id, status:"queued"}`. 실행은 워커 스레드, 진행은 WS `backtest.progress` |
| GET | `/backtests/{id}` | `{status, metrics, cost_model, holdout_cutoff, attempts:{distinct_attempts, warn_after, overfit_warning}, warnings, equity:[{ts,v}], drawdown:[{ts,v}], fills_tail:[...], public_reference?:{cagr, mdd, source, within_20pct}}` |
| GET | `/backtests?strategy=` | 목록 (attempt_no 포함) |
| GET | `/backtests/{id}/report.csv` | 체결 전체 CSV |

`unlock_holdout=true`는 전략당 1회만 허용하고 `backtests.unlocked_holdout`에 남긴다. 두 번째 요청은 409.

### 2.4 거래·포지션

| 메서드 | 경로 | 설명 |
| --- | --- | --- |
| GET | `/portfolio` | `{total_equity_krw, by_market:{upbit:{cash,equity,positions:[...]}, krx:..., us:...}, month_pnl, month_limit, halted:{...}}` (USD는 환율 환산, 환율 출처 명시) |
| GET | `/positions?market=` | 포지션 목록 + 전략·손절가·미실현 |
| GET | `/orders?market=&status=&limit=` | 주문 목록 |
| GET | `/fills?market=&strategy=&from=&to=` | 원장 |
| POST | `/orders` | 수동 주문 `{market, symbol, side, qty|amount, type, limit_price?, stop?}` → RiskManager 통과 시 201 `{order, risk}`, 거부 시 422. **수동 주문도 리스크 게이트를 탄다** |
| DELETE | `/orders/{id}` | 미체결 취소 |
| POST | `/positions/{market}/{symbol}/close` | 시장가 청산 (리스크 게이트의 exit 경로) |
| GET | `/quotes/{market}/{symbol}` | 현재가·호가 5단계·전략 상태(목표가, 이평 스코어) |
| GET | `/candles/{market}/{symbol}?tf=5m&limit=500&before=` | 캔들 |

### 2.5 AI 판단

| 메서드 | 경로 | 설명 |
| --- | --- | --- |
| GET | `/judgments?from=&to=&market=&strategy=&outcome=&limit=` | 판단 로그 (signals ⋈ judgments ⋈ llm_verdicts) |
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

## 3. WebSocket `/ws`

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
| `strategy.status` | 전략별 `{name, enabled, position, next_action:{at, what}}` | 변경 시 |

클라이언트 → 서버: `{"subscribe": [...]}`, `{"unsubscribe": [...]}`, `{"ping": 1}`. 30초 무응답 시 서버가 끊는다.

## 4. 프론트엔드 화면 ↔ API 매핑

| 화면 | 사용 API |
| --- | --- |
| 대시보드 | `/portfolio`, `/strategies`, `/judgments?limit=5`, `/reports/gates`, WS `portfolio`·`judgments`·`strategy.status` |
| 거래·차트 | `/quotes`, `/candles`, `/orders`, `/positions`, `/judgments?symbol=`, POST `/orders`, WS `ticks`·`orderbook`·`fills` |
| 전략 설정 | `/strategies`, PATCH, `/backtests?strategy=`, `/reports/gates`, `/settings` |
| AI 판단 로그 | `/judgments`, `/judgments/{id}`, `/judgments/calibration`, `/judgments/ab`, POST `/ask` |
| 백테스트 | POST `/backtests`, GET `/backtests/{id}`, WS `backtest:{id}` |
| 설정 | `/settings`, 키 등록(POST `/settings/keys`, 값은 저장만 하고 절대 반환하지 않음) |
