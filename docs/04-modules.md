# 04 · 모듈 설계

패키지 의존 방향 (화살표 방향으로만 import 가능):

```
api ──▶ db ──▶ core
engine ──▶ strategies, features, judgment, execution, data, db ──▶ core
scheduler ──▶ engine, execution, data, db
backtest ──▶ strategies, core           (CostModel은 backtest/costs.py에 있고 execution/paper.py가 이를 import한다 — 1단계에서 core/costs.py로 이동)
judgment ──✕──▶ execution        (금지. 테스트로 강제)
strategies ──✕──▶ 그 외 전부      (core만)
```

## 1. core

`models.py`(0단계 완료) + 1단계 추가:

- `clock.py` — `MarketClock(market)`: `now()`, `is_open(ts)`, `next_open()`, `next_close()`, `session_bounds(date)`, `to_local(ts_utc)`, `to_utc(ts_local)`. 휴장일·특수 세션은 `core/calendars.py` 내장 표(2020~2027, 범위 밖은 `CalendarOutOfRange`), 서머타임은 표준 라이브러리 `zoneinfo`. 업비트는 24시간이며 세션은 09:00 KST 경계. `exchange_calendars`는 `data/calendars.py` 선택 어댑터로만 쓴다 (ADR 0009).
- `events.py` — 엔진 내부 이벤트 dataclass: `TradeEvent`, `BarClosed`, `SignalEvent`, `JudgmentEvent`, `OrderEvent`, `FillEvent`, `RiskEvent`.
- `news.py` — `NewsItem`·`EventItem`·`Summary`(요약 계약: ≤100자, `RISK_FLAGS` 안의 플래그, 위험도 0~1|None), `raw_hash()`, Protocol `Summarizer`·`NewsSource`·`EventSource`·`NewsRepo`. 뉴스·이벤트 시각은 시장에 속하지 않으므로 UTC tz-aware.
- `errors.py` — `BrokerError(retryable: bool)`, `RateLimited(retry_after)`, `JudgeTimeout`, `DataStale`, `CalendarOutOfRange`.

## 2. strategies (0단계 완료)

인터페이스는 `strategies/base.py` 그대로. 1단계 추가 규칙:

- `Strategy.horizon`이 `intraday`인 전략은 `Target.stop`을 반드시 준다 (RiskManager 1% 룰 계산용). 테스트로 강제.
- 월간 전략(`timeframe="1M"`)의 `on_bar`는 엔진이 **월 마지막 거래일 15:20(KRX) / 15:55 ET(미국)** 봉에서만 호출한다. 판단은 `MarketClock.is_last_session_of_month(ts)`.
- `Strategy.describe()`에 `public_reference: {cagr, mdd, source}`를 추가해 화면과 G1 스크립트가 쓴다.
- `Strategy.state(ctx)`는 화면 표시용 심볼별 상태(`{심볼: {이름: 값}}`, 기본 빈 dict)다. 주문·사이징에 쓰지 않으며 `on_bar`의 출력(`Target`)과 무관하다(불변식 #1). 변동성 돌파만 `{target, ma_score}`를 돌려주고, `on_bar`와 같은 함수로 계산한다.

## 3. features (1단계 신규)

```python
class FeatureBuilder:  # features/builder.py — core.ports.FeatureBuilder Protocol 구현
    def __init__(self, market, *, news: NewsSource | None = None, events: EventSource | None = None,
                 spread: Callable[[str], float | None] | None = None, max_tokens: int = 400): ...
    def build(self, *, symbol: str, strategy: str, target: Target, ctx: Context) -> State
```

뉴스·이벤트는 인자가 아니라 생성자로 주입한 소스(`NewsSource`·`EventSource`, `core/news.py`)에서 `ctx.ts` 기준으로 읽는다. `build`가 동기라서 수집기가 비동기로 채운 메모리 캐시(`data/news.NewsCache`)를 읽는다. Protocol 시그니처는 P1-04(ADR 0010)의 것을 그대로 쓴다.

- 숫자는 **등급·백분위**로 바꾼다: `vol_pctl_20d`(0~100, 20봉 변동성의 최근 252개 중 백분위), `ma_score`(0~1, 5·10·20·60 이평 위 비율), `volume_ratio`(당일/직전 20봉 평균, 소수 1자리), `spread_bps`(스프레드 공급자가 있을 때만), `dist_from_high_20d_pct`, `rsi2`(정수). 데이터가 모자라는 피처는 넣지 않는다.
- `target.price`가 있으면(장중 가격 도달 진입) 현재 봉을 빼고 계산한다 (불변식 #3).
- 뉴스는 최근 24시간 중 요약이 있는 것만, 위험 플래그 있는 것 우선 → 최신순, 최대 3건, 각 100자. 제목·링크는 로그(DEBUG)에만.
- `events_24h`: 캘린더(FOMC·CPI·실적·상장폐지 심사·하드포크·거래소 점검)에서 **앞뒤** 24시간 내 항목, `kind: 제목 (+5h)` / 지난 것은 `(-3h)`. 캘린더는 `data/events.py`가 수동 YAML + DART 공시로 채운다.
- 출력 `State.render()`는 400토큰 이내. 초과하면 뉴스부터 자르고, 그래도 넘으면 이벤트를 줄인다. 토큰 수는 `features.count_tokens`(보수적 추정: 영문 4자당 1, 숫자·기호·한글 글자당 1). 테스트: `count_tokens(state.render()) <= 400`.

## 4. judgment

0단계 인터페이스 유지. `JudgeProvider`에 비동기 `ajudge()`가 추가됐다(기본 구현은 `judge()` 호출, ADR 0012). 1단계 구현체:

| 파일 | 클래스 | 비고 |
| --- | --- | --- |
| `typesafe.py` | `TypeSafeJudge(api_key, model="jev-latest", timeout=3.0, *, base_url, questions_version="v1")` | `POST https://api.typesafe.ai/v1/systemone`. 원자 질문 6개를 한 요청에. 응답의 `confidence`는 질문별 최솟값을 `JudgeResult.confidence`로 (보수적). score(0..N-1 단계)는 `/(N-1)`로 0~1. `ajudge()`는 총 3초 초과 → `JudgeTimeout`, HTTP·계약 위반 → `JudgeError` (ADR 0012). 키는 `from_settings()`로 `QP_TYPESAFE_API_KEY`에서 |
| `questions/` | `load_questions("v1") -> QuestionSet` | `v1.yaml`의 `instructions`·`criteria`와 `prompt_hash`. 키·종류·옵션은 `base.py::DEFAULT_QUESTIONS`와 같아야 함 |
| `pricing.py` | `cost_usd(model, input_tokens, output_tokens)`, `price_for(model)` | USD/100만 토큰 단가표. 모델명 최장 접두사로 찾고, 없으면 `ValueError` |
| `laya.py` | `LayaJudge(base_url)` | 자체 호스팅 HTTP. 같은 스키마 |
| `anthropic.py` | `ClaudeReviewer(model="claude-sonnet-5")` | `review()` + `daily_review()` + `answer_question()` |
| `google.py` | `GeminiReviewer(model="gemini-3.5-flash")`, `GeminiSummarizer(model="gemini-3.5-flash-lite")` | 리뷰 / 뉴스 요약 |
| `pipeline.py` | `JudgmentPipeline(judge, reviewers, bus=...)`, `build_pipeline(settings)` | `evaluate(signal, state) -> JudgmentEvent` — 하드블록 → 게이트 → LLM 합의(병렬, 30초 타임아웃) → `decide()`. 일일 AI 예산·judge_down 발행 (ADR 0014) |
| `calibration.py` | `brier(judgments, *, p="confidence")`, `ece(judgments, bins=10)`, `bucket_hit_rates(judgments, ranges)`, `is_monotonic(buckets)`, `calibration(judgments) -> dict` | `realized_ret_24h`가 채워진 행만. p는 `confidence` 또는 `signal_quality`. 표본이 없으면 None (ADR 0016) |
| `ab.py` | `equity_curve(fills, prices, cash)`, `book_stats(...) -> BookStats`, `g2_verdict(on, off, brier, n)`, `ab_report(...)`, `render_markdown(report)` | 원장별 체결 재생 곡선으로 수익률·MDD·체결 수·비용. n < 20이면 판정 보류. CLI `qp report ab --weeks 4` (ADR 0016) |

LLM 프롬프트 계약은 06 문서. 모든 프로바이더는 `cost_usd`를 계산해 돌려준다(토큰 × 단가표 `judgment/pricing.py`).

## 5. execution

### 5.1 BrokerAdapter 구현체 (1~2단계)

| 파일 | 클래스 | 라이브러리 | 특이사항 |
| --- | --- | --- | --- |
| `paper.py` | `PaperBroker` | — | 0단계 완료. 1단계: 포지션·현금을 DB에 저장·복원하는 `PersistentPaperBroker`(`persistent_paper.py`, `restore()`·`persist()`). 주문·체결 원장은 `OrderExecutor`가 쓴다 (ADR 0011). 섀도 계좌는 `book="shadow"` + `SettingsPositionRepo`(settings jsonb) (ADR 0016) |
| `upbit.py` | `UpbitBroker` | pyupbit 또는 직접 REST(JWT HS512) | 주문 12/s, 조회 30/s. `order-test`로 사전 검증. 체결 확인은 `/v1/order` 폴링 0.5s |
| `kis.py` | `KISBroker(paper: bool)` | python-kis | 토큰 24h(`TokenManager`), 실전 20건/s·모의 2건/s, 체결통보 WS. 국내·해외 같은 클래스, 시장 파라미터 |
| `alpaca.py` | `AlpacaPaperBroker` | alpaca-py | 페이퍼 전용. 200 req/min |

공통 계약:

- `submit()`은 **동기적으로 거래소 접수까지**만 보장하고 `Order(status=pending)`를 돌려줄 수 있다. 체결은 `OrderExecutor`가 확인한다.
- 예외는 `BrokerError(retryable)`로 통일. HTTP 429 → `RateLimited`, 5xx·타임아웃 → retryable, 4xx(잔고 부족·잘못된 수량) → non-retryable.
- `order_status(order_id)`는 체결이면 `Fill`, 대기·취소면 `Order`, 모르면 `None`(기본). `None`이면 `OrderExecutor`가 체결 확인 루프를 돌지 않는다 (ADR 0011).
- `positions()`·`cash()`는 거래소 조회(캐시 2초). `Reconciler`가 DB 포지션과 대조한다.
- 수량·가격은 거래소 호가 단위·최소 주문 금액에 맞게 어댑터가 반올림하고, 반올림 결과 0이면 `qty<=0`로 거부.

### 5.2 OrderExecutor (1단계 신규)

```python
class OrderExecutor:
    def __init__(self, broker, risk: RiskGate, ledger: Ledger, limiter: RateLimiter, *,
                 signals: SignalRepo | None = None, group: str | None = None,
                 confirm_timeout=30.0, limit_ttl=60.0, poll_interval=0.5)
    async def execute(self, order: Order, *, equity, price, positions, horizon, intraday_exposure) -> Fill | Order
```

1. `risk.check()` → 거부면 `signals.outcome = risk_rejected` 기록 후 반환.
2. `limiter.acquire(group)` (슬라이딩 윈도, 거래소별 한도의 80%. 기본 인메모리, 여러 프로세스면 Redis. 페이퍼는 `group=None`).
3. `broker.submit()` — retryable 오류는 첫 시도 + 재시도 3회(0.5·1·2초 지수 백오프, 429의 `retry_after`가 더 길면 그만큼). 모두 실패 → `risk.api_error()`. non-retryable은 재시도 없이 거부.
4. pending이면 `broker.order_status` 폴링(0.5초) 최대 30초. 시장가 미체결이면 취소 후 새 주문 id로 1회 재주문. 지정가는 `limit_ttl`(기본 60초)까지 대기 후 취소.
5. Fill 확정 → `ledger.save_order`(filled) → `ledger.record(fill)` → 브로커 `persist()`(있으면, 페이퍼 계좌 상태) → `signals.outcome = filled`. `fill` 이벤트 발행은 `TickRunner` 몫. 원장 쓰기 실패는 CRITICAL 로그만 남기고 결과를 바꾸지 않는다.

반환은 `Fill | Order`다(`core/ports.py`의 Protocol, ADR 0011). `TickRunner`에 `DirectExecutor` 대신 그대로 꽂는다.

청산 주문(보유 포지션을 줄이는 매도)은 1·2를 건너뛰지 않되(`risk.check`가 exit는 항상 허용), 재시도 횟수를 10회(대기 상한 4초)로 늘리고 실패 시 `CRITICAL` 로그를 남긴다(텔레그램 알림은 P1-10).

### 5.3 RiskManager (0단계 완료)

1단계 추가: `intraday_exposure`를 엔진이 계산해 넘긴다(horizon=intraday 전략들의 현재 포지션 가치 합). 월 서킷브레이커의 월 경계는 `RiskManager`가 `check()` 호출 시 UTC 월 기준으로 자동 롤한다(0단계). `scheduler.month_roll`은 롤 직후 `month_start_equity`를 DB `settings`에 저장해 재시작에도 유지하는 역할만 한다.

### 5.4 Reconciler (1단계 신규)

엔진 시작 시와 매 5분: 브로커 `positions()`·`cash()`와 DB `positions`·`equity`를 대조. 수량 차이 > 최소 주문 단위면 `risk_events(reconcile_mismatch)` + 할트. 사람이 화면에서 "브로커 기준으로 맞추기"를 눌러야 해제.

구현(`execution/reconciler.py`, ADR 0015): scheduler 시작 시와 `reconcile` 잡(5분)이 돈다. 심볼별 수량 차이가 최소 주문 단위(업비트 1e-8, KRX·미국 1주) **이상**이면 불일치로 보고, 미해결 이벤트가 없을 때만 `risk_events`에 기록한다. 이어서 settings 우편함의 `engine.halt.<market>` 할트 키를 쓰고 critical 알림을 보낸다. 엔진은 `on_link`에서 할트 키를 읽어 `RiskManager.halted_reason`에 반영한다. 해제 경로는 `Reconciler.accept_broker` 하나뿐이다: DB를 브로커 기준으로 덮어쓰고, 이벤트를 닫고, 할트 키를 지운다. 현금 차이는 warning만 보낸다.

## 6. data

| 파일 | 내용 |
| --- | --- |
| `loader.py` | 0단계 완료 (REST 백필) |
| `upbit_ws.py` | `UpbitStream(symbols, on_trade=...)` — public WS `trade`·`orderbook`. `run()`: 재접속 백오프(기본 1·2·5·5·5초, 다 쓰면 `DataStale`), 30초 무메시지면 재접속. 체결 → `TradeEvent` 콜백, 호가는 `orderbook(symbol)` 최신 스냅샷. `ensure_fresh()`: 30초 넘게 시세 없으면 `DataStale` |
| `kis_ws.py` | `KISStream(app_key, symbols)` — 체결가·호가·체결통보. approval_key 발급, 41건 제한 관리 |
| `aggregator.py` | `CandleAggregator(tf, market)` — `on_trade()` → 새 구간 첫 체결이면 직전 봉 `BarClosed`, `on_timer(now)` → 마감+2초 지난 봉 확정. 체결 없는 구간은 봉 없음, 확정된 구간의 늦은 체결은 버림. 일봉 경계는 업비트 09:00 KST |
| `store.py` | `CandleStore(repo: CandleRepo, market)` — `upsert(bars)`, `load(symbol, tf, start, end)` → OHLCV DataFrame(loader 규격, `[start, end)`), 구간 캐시(upsert 시 해당 심볼·주기 폐기) |
| `news.py` | `NewsCollector(feeds, summarizer, repo, keywords=, stock_symbols=)` — RSS·Atom·DART 공시 목록 수집(`httpx`, `fetch` 주입 가능), 48시간 넘은 것 제외, 중복 제거(`raw_hash`, 배치 안 + 저장소), 종목·키워드 매칭(`"*"`는 거시 키워드 → 전 종목, 미매칭은 버림), 그 뒤에만 요약기 호출. DOCTYPE/ENTITY 있는 XML 거부. `NewsCache`(메모리 저장소) + `CacheNewsSource`(피처 빌더용). DART 키는 `QP_DART_API_KEY`. 피드·키워드는 `load_news_config(QP_NEWS_FILE)`(기본 `data/news_sources.yaml`), 시간대 없는 피드 날짜는 `Feed.naive_utc_offset_hours`. 키 없는 요약기 `TitleSummarizer`. 엔진용 `NewsRefresher` — scheduler가 DB에 쌓은 뉴스를 5분마다 캐시로 (ADR 0021) |
| `events.py` | `EventCalendar` — 수동 YAML(`EventCalendar.from_yaml`, tz 없는 시각은 `market` 현지시간) + `events_from_dart`(상장폐지·관리종목·거래정지·잠정실적 공시 → 그 종목 이벤트) |

## 7. engine (1단계 신규)

```python
class TickRunner:
    """시장 하나의 틱 루프. 01-architecture §3의 1~8단계."""
    def __init__(self, market, strategies, feature_builder, pipeline, executor, risk, clock, bus,
                 *, cost, shadow=None)   # shadow: 게이팅 OFF 섀도 executor (ADR 0016)
    async def on_bar_closed(self, ev: BarClosed) -> None
    async def on_bars_closed(self, evs: Sequence[BarClosed]) -> None  # 같은 시각 봉 묶음 (ADR 0010)
    async def on_time_exit(self, strategy_name: str) -> None      # scheduler가 호출
    async def on_stop_check(self, ev: TradeEvent) -> None          # 손절·트레일링 (봉 마감 전, 체결가마다)
```

- 시장별 `TickRunner` 1개, `asyncio.TaskGroup`으로 실행. 전략 간 순서는 등록 순, 같은 심볼에 두 전략이 반대 target을 내면 **청산이 먼저**.
- `on_stop_check`는 체결가마다 돌지만 판단 모델·LLM을 호출하지 않는다. 진입 target의 `stop` 이탈 시 즉시 exit target.
- `feature_builder`·`pipeline`·`executor`·`risk`·`clock`·`bus`는 `core/ports.py`의 Protocol이다. 봉 히스토리는 `engine/history.py::BarHistory`(백테스트는 미리 적재한 DataFrame의 커서, 실전은 `CandleStore.load`로 시드 후 봉마다 추가).
- `shadow` executor를 주면 ON이 발행한 전략 신호를 같은 틱에 섀도 계좌로도 낸다(판단은 한 번, 섀도 배수 1.0, 자기 `risk.check → submit`, 손절·시간 청산은 원장별). 섀도 체결은 버스에 발행하지 않는다. 페이퍼 엔진은 항상 섀도를 둔다 (06 §6.2, ADR 0016).
- 실시간 엔진은 1분봉을 받는다. 일봉·월간 전략은 `engine/daily.py::DailyRollup`이 분봉을 거래일(업비트 09:00 KST) 일봉으로 묶은 표(마지막 행 = 진행 중인 오늘 봉, 시작 시 업비트 REST 일봉 60개로 시드)로 평가하고, (전략, 심볼)마다 거래일당 진입 한 번·청산 한 번만 처리한다 (ADR 0025).
- 재시작 (ADR 0028):
  - `MarketEngine.restore()`가 계좌 → 월초 평가액(`month_start_equity.<market>`) → 손절선(positions의 `stop`) → 오늘 처리 표시(우편함 `engine.done.<market>` + 오늘 연 포지션) 순으로 되살린다.
  - DB가 있으면 확정 1분봉을 `CandleStore`로 candles 표에 저장한다. 저장에 실패해도 매매는 계속한다.
  - 체결 기준가는 일봉 모드에서도 지금 들어온 분봉의 범위로 클립한다.
- 전략 설정 (ADR 0032):
  - 엔진은 자기 시장에 등록된 전략을 모두 만든다. `restore()`와 하트비트마다 `strategy_configs`를 읽어 `TickRunner.apply_configs`로 넘긴다. 행이 없으면 `strategies.DEFAULT_CONFIG`(권장 조합)를 쓴다.
  - 사이징 기준은 평가액 × allocation이고, 리스크 검사에는 계좌 전체 평가액을 넘긴다. 꺼졌거나 allocation 0인 전략은 진입 target만 버리고(판단 모델 호출 없음) 청산·손절·시간 청산은 처리한다. params가 바뀌면 전략을 다시 만든다.
  - 확신도 임계값(`gate.*`)은 하트비트마다 판단 파이프라인에 반영한다. 판단 모델·리뷰어(`judge.provider`·`llm.models`)는 시작할 때 settings 표 값을 환경변수 위에 덮어써 만들고, 실제로 쓰는 값을 `engine.judge.<market>`에 기록한다. 덮어쓴 값으로 만들지 못하면 환경변수 설정으로 시작하고 CRITICAL을 남긴다.
  - `apply_configs`를 부르지 않는 백테스트는 모든 전략이 켜짐·배분 1.0이다.
- 화면 표시 (허브가 있을 때만, 실패해도 매매는 계속):
  - 하트비트마다 바뀐 전략 상태를 WS `strategy.status`로 보낸다 (ADR 0034).
  - 타이머(1초)마다 `UpbitStream`이 받은 호가 중 바뀐 종목만 허브 `ob:{market}:{symbol}`(TTL 10초)와 WS `orderbook:{market}:{symbol}`로 보낸다(`{asks, bids}` 5단계, 02 §5·03 §3). 같은 스냅샷은 다시 쓰지 않으므로 시세가 끊기면 10초 뒤 호가가 사라진다.
  - 하트비트마다 `TickRunner.strategy_state(now)`로 심볼별 전략 상태를 허브 `st:{market}:{symbol}`(TTL 60초)에 쓴다. 변동성 돌파는 `{target, ma_score}`(오늘 목표가·이평 스코어)이고, `on_bar`와 같은 거래일 일봉·같은 계산(`VolBreakout._levels`)이라 화면 목표가가 실제 진입가와 같다. 오늘 일봉이 아직 없는 심볼(09:00 직후 첫 분봉 전)은 쓰지 않는다. `/quotes`의 `strategy`가 이 키를 읽어 거래 화면 목표가 점선·돌파 문구·관심 종목 부제를 그린다. `/schedule`의 목표가는 1분봉으로 시가를 근사하므로 이 값과 조금 다를 수 있고, 진입에 쓰이는 값은 이쪽이다.
- 실시간 페이퍼 엔진(`engine/main.py::build_upbit_paper`)의 피처 빌더는 `features/builder.py::FeatureBuilder`다. 뉴스는 `NewsRefresher`가 엔진 타이머에서 DB `news_items`를 5분마다 `NewsCache`로 옮기고, 이벤트는 `QP_EVENTS_FILE` YAML + DART 위험 공시다 (ADR 0021). 판단 모델이 `stub`이면 뉴스는 판단 로그에만 남고 사이징은 바뀌지 않는다.
- 백테스터는 이 `TickRunner`를 `PaperBroker` + `DirectExecutor` + `StubPipeline(StubJudge, gating=False)` + `ReplayClock` + `UnrestrictedRisk`(기본, `apply_risk=True`면 `RiskManager`)로 돌린다. 0단계 `Backtester.run`의 인라인 루프는 제거했다. 배선 결정과 0단계 대비 수치 차이는 ADR 0010.

## 8. scheduler (1단계 신규)

APScheduler(AsyncIOScheduler), 잡은 DB에 영속(`SQLAlchemyJobStore`).

| 잡 | 시각(KST) | 동작 |
| --- | --- | --- |
| `upbit_daily_exit` | 09:00:00 | 변동성 돌파 보유분 시장가 청산 → 목표가 재계산 → 목표가를 로그로 남김 (scheduler에는 허브가 없다. 화면의 오늘 목표가는 REST `/schedule`, WS `strategy.status`는 엔진이 보낸다 — ADR 0034) |
| `krx_close_orders` | 15:20:00 (거래일) | GTAA·(월말, 2단계)GEM-KRX 종가 단일가 주문 |
| `us_orb_entry_window` | 22:35 (서머타임) / 23:35 (표준시) | ORB 진입 창 열기 |
| `us_eod_exit` | 04:55 (서머타임) / 05:55 (표준시) | ORB 청산 |
| `gem_rebalance` | 월 마지막 거래일 미국장 마감 5분 전 | GEM 리밸런싱 |
| `kis_token_refresh` | 08:00 | 토큰 재발급, 실패 시 국내·미국 휴무 플래그 |
| `upbit_prescreen` | 08:10 | 대상 코인별 판단 모델 + LLM 리뷰어 전원 승인 사전 심사 → 당일 제외 목록을 settings 우편함 `engine.prescreen.upbit`에 (ADR 0004·0022, `scheduler/wiring.py::Prescreen`) |
| `morning_brief` | 08:30 | Claude 아침 브리핑 알림: 일정·보유·리스크 |
| `news_collect` | 매시 :05 | 뉴스 수집·요약 (settings `news.enabled=false`면 그 회차는 제목 요약 — ADR 0035) |
| `fill_realized_24h` | 매시 :10 | `judgments.realized_ret_24h` 채우기 |
| `daily_review` | 20:30 | Claude 사후 리뷰 → `daily_reviews`, 알림 |
| `equity_snapshot` | 매분 | `equity_snapshots` |
| `reconcile` | 5분 | Reconciler |
| `month_roll` | 매월 1일 09:00 (UTC 00:00) | `month_start_equity` DB 저장 (롤 자체는 RiskManager) |
| `engine_heartbeat` | 30초 | 엔진 하트비트 확인, 90초 없으면 알림 + 시간 청산 백업 모드 |
| `alert_repeat` | 매분 | 미해결 critical 알림을 5분마다 재전송 (07 §6, ADR 0015) |

engine ↔ scheduler는 P1-12 Redis 전까지 DB `settings` 우편함(`engine/link.py::SettingsEngineLink`)으로 하트비트와 시간 청산 명령을 주고받는다. 정상일 때는 엔진이 명령을 받아 `TickRunner.on_time_exit`를 부르고, 하트비트가 끊기면 scheduler가 DB 계좌로 만든 `TickRunner`의 `on_time_exit`를 직접 부른다(ADR 0013). 잡 표는 `scheduler/registry.py::JOBS`에 있다.

부품 배선은 `scheduler/wiring.py`(ADR 0021·0022): `news_collect`는 `make_news_collector`(피드 설정 + Gemini 요약기, 키 없으면 `TitleSummarizer`) → `SqlNewsRepo`, `daily_review`는 `make_daily_reviewer`(Claude `ClaudeAnswerer`, 키 없으면 통계만). 키 값은 로그에 남지 않는다. `upbit_prescreen` 훅은 `make_prescreen`(엔진과 같은 판단 모델·리뷰어 설정).

## 9. db

`models.py`(SQLAlchemy), `mappers.py`(dataclass ↔ ORM), `repo.py`(`SqlCandleRepo`, `SqlLedger`(`shadow=True`면 섀도 원장 행만), `SqlSignalRepo`, `SqlJudgmentRepo`, `SqlPositionRepo`, `SqlRiskEventRepo`, `SqlConfigRepo`), `migrations/`(alembic). 코어는 `core/repos.py`의 Protocol(`CandleRepo`, `Ledger`, `SignalRepo`, `JudgmentRepo`, `PositionRepo`, `RiskEventRepo`, `ConfigRepo`)만 알고 구현은 주입 (ADR 0008).

## 10. api

0단계 `app.py`를 `api/routes/{system,strategies,backtests,trading,judgments,reports}.py`로 분할했다(P1-12, ADR 0017). `app.py::create_app(settings, sessions, hub, calibration, answerer, account_source, ...)`가 부품을 주입받고, 모듈의 `app`은 설정대로 만든다. 상태는 전부 DB·허브에서 읽는다.

- `auth.py`: 표준 라이브러리 HS256 JWT(`QP_JWT_SECRET` 32바이트 이상, `QP_ADMIN_PASSWORD`). `/health`·`/auth/login`만 인증 없음.
- `errors.py`: `{"error": {code, message, detail}}`. 검증 실패는 400 `INVALID_PARAM`.
- `deps.py`: `Deps`, `CalibrationSource`(t13이 구현), `Answerer`(`StubAnswerer`, `judgment/anthropic.py::ClaudeAnswerer`).
- `queries.py`(목록·상세 조회, `limit`·`before` 페이지네이션), `gates.py`(G1~G4 근거 모음).
- 수동 주문·청산·취소: RiskManager 사전 검사 → `q:orders:<market>` → 엔진 `engine/orders.py::ManualOrderConsumer`가 `OrderExecutor`로 실행.
- 백테스트: `ThreadPoolExecutor(1)`, 진행률은 허브 키 `bt:<id>` + WS `backtest:<id>`. 결과 곡선·체결은 `data_dir/backtests/<id>.json`.
- `ws.py`: `/api/v1/ws`. `WsHub`가 허브를 한 번 구독하고 연결별 채널로 팬아웃. 첫 메시지 인증 실패 4401, 무응답 30초 4408.

`realtime/`: `hub.py`(`MemoryHub`·`RedisHub`·`make_hub`), `bus.py`(`HubBus` — 엔진 EventBus → 허브 채널, `EventRecorder` — signals·judgments·llm_verdicts·risk_events 기록), `keys.py`(02 §2 키·채널 이름).

## 11. notify (1단계 신규)

`Notifier.send(level, title, body)` — 채널: 텔레그램 봇(기본), 이메일(선택). `critical`(청산 실패·할트·정합 불일치)은 5분 간격 재알림. 알림 본문에 금액은 넣되 키·주문번호는 넣지 않는다.
