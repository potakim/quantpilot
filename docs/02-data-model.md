# 02 · 데이터 모델

TimescaleDB(PostgreSQL 16)가 영구 저장소, Redis가 실시간 상태다. 모든 `timestamptz`는 UTC. 금액은 `numeric(20,8)` (원화도 소수 허용 — 코인 KRW 마켓의 평균단가), 비율은 `double precision`.

## 1. 테이블

### 1.1 시세

```sql
create table candles (
  ts          timestamptz not null,          -- 봉 시작 (UTC)
  market      text not null,                 -- upbit | krx | us
  symbol      text not null,                 -- KRW-BTC, 005930, QQQ
  tf          text not null,                 -- 1m | 5m | 1d
  open  numeric(20,8) not null, high numeric(20,8) not null,
  low   numeric(20,8) not null, close numeric(20,8) not null,
  volume numeric(24,8) not null,
  source      text not null default 'ws',    -- ws | rest | backfill
  primary key (market, symbol, tf, ts)
);
select create_hypertable('candles', 'ts', chunk_time_interval => interval '7 days');
-- 압축: 1m은 30일, 5m·1d는 무기한 보관
alter table candles set (timescaledb.compress, timescaledb.compress_segmentby = 'market,symbol,tf');
select add_compression_policy('candles', interval '30 days');
```

```sql
create table news_items (
  id          bigserial primary key,
  ts          timestamptz not null,
  source      text not null,                 -- rss:<name> | dart
  symbols     text[] not null default '{}',  -- 매칭된 심볼
  title       text not null,
  url         text,
  summary     text,                          -- LLM 100자 요약
  risk_flags  text[] not null default '{}',  -- delist, hack, lawsuit, regulation, earnings, ...
  risk_score  double precision,              -- 0~1 (LLM)
  raw_hash    text unique                    -- 중복 방지
);
create index on news_items using gin (symbols);
create index on news_items (ts desc);
```

### 1.2 전략·설정

```sql
create table strategy_configs (
  id            serial primary key,
  name          text not null,               -- REGISTRY 키
  market        text not null,
  enabled       boolean not null default false,
  params        jsonb not null default '{}', -- ParamSpec 범위 내 값만 (API가 검증)
  allocation    double precision not null,   -- 총 자본 대비 배정 비율 (0~1)
  symbols       text[] not null,
  paper         boolean not null default true,
  updated_at    timestamptz not null default now(),
  unique (name, market)
);

create table settings (
  key    text primary key,                   -- gate.hold_below, gate.full_above, judge.provider, ...
  value  jsonb not null,
  updated_at timestamptz not null default now()
);
-- 리스크 규칙은 여기 없다. RiskRules는 코드 상수.
```

### 1.3 신호·판단

```sql
create table signals (                       -- 전략이 낸 후보 Target (진입·청산 모두)
  id          bigserial primary key,
  ts          timestamptz not null,
  strategy_id int not null references strategy_configs(id),
  market      text not null, symbol text not null,
  kind        text not null,                 -- entry | exit | rebalance
  weight      double precision not null,
  price_hint  numeric(20,8),
  stop        numeric(20,8),
  reason      text,
  outcome     text not null,                 -- pending | judged_hold | risk_rejected | ordered | filled | expired
  outcome_reason text
);
create index on signals (ts desc);            -- 일반 테이블 (ADR 0008)

create table judgments (                     -- 판단 모델 1회 호출
  id            bigserial primary key,
  signal_id     bigint not null references signals(id),
  ts            timestamptz not null,
  provider      text not null,               -- typesafe:jev-1.13 | laya | stub
  state         jsonb not null,              -- State.render() 원문 + features
  answers       jsonb not null,              -- {regime:{...}, news_risk:0.12, ...}
  confidence    double precision not null,
  gate          text not null,               -- hold | half | full
  blocks        text[] not null default '{}',
  latency_ms    double precision,
  cost_usd      double precision,
  realized_ret_24h double precision,         -- scheduler가 채움 (보정 지표용)
  direction_hit    boolean                   -- 24h 방향 적중 여부
);
create index on judgments (ts desc);

create table llm_verdicts (
  id          bigserial primary key,
  judgment_id bigint not null references judgments(id),
  model       text not null,                 -- claude-sonnet-5 | gemini-3.5-flash
  approve     boolean not null,
  reason      text not null,
  latency_ms  double precision,
  cost_usd    double precision,
  prompt_hash text                           -- 프롬프트 버전 추적
);
```

### 1.4 주문·체결·포지션

```sql
create table orders (
  id            text primary key,            -- 내부 id (uuid12)
  broker_order_id text,                      -- 거래소 주문번호
  signal_id     bigint references signals(id),
  ts            timestamptz not null,
  market text not null, symbol text not null,
  side          text not null,               -- buy | sell
  type          text not null,               -- market | limit
  qty           numeric(24,8) not null,
  limit_price   numeric(20,8),
  stop          numeric(20,8),
  status        text not null,               -- pending | filled | partial | rejected | cancelled
  reject_reason text,
  risk_adjustments text[] not null default '{}',
  size_multiplier double precision,          -- 게이팅 결과 0.5 / 1.0
  strategy      text not null,
  paper         boolean not null
);

create table fills (                         -- 원장
  id            bigserial primary key,
  order_id      text not null references orders(id),
  ts            timestamptz not null,
  market text not null, symbol text not null,
  side          text not null,
  qty           numeric(24,8) not null,
  price         numeric(20,8) not null,
  fee           numeric(20,8) not null default 0,
  tax           numeric(20,8) not null default 0,
  strategy      text not null,
  reason        text,
  paper         boolean not null
);
create index on fills (ts desc);              -- 일반 테이블 (ADR 0008)

create table positions (                     -- 현재 상태 스냅샷 (재시작 복원용)
  market text not null, symbol text not null, strategy text not null,
  qty         numeric(24,8) not null,
  avg_price   numeric(20,8) not null,
  opened_at   timestamptz,
  stop        numeric(20,8),
  updated_at  timestamptz not null default now(),
  primary key (market, symbol, strategy)
);

create table equity_snapshots (              -- 자산 곡선 (1분)
  ts      timestamptz not null,
  market  text not null,
  cash    numeric(20,8) not null,
  equity  numeric(20,8) not null,
  paper   boolean not null,
  primary key (market, paper, ts)
);
select create_hypertable('equity_snapshots', 'ts');
```

### 1.5 백테스트·운영

```sql
create table backtests (
  id          bigserial primary key,
  ts          timestamptz not null default now(),
  strategy    text not null,
  params      jsonb not null,
  symbols     text[] not null,
  source      text not null,
  period_start date, period_end date,
  holdout_cutoff date,
  unlocked_holdout boolean not null default false,
  cost_model  jsonb not null,
  metrics     jsonb not null,                -- Metrics.to_dict()
  attempt_no  int not null,                  -- 전략별 서로 다른 조합 시도 번호
  equity_path text                           -- data/backtests/<id>.parquet
);

create table risk_events (
  id      bigserial primary key,
  ts      timestamptz not null default now(),
  kind    text not null,                     -- circuit_breaker | api_halt | ws_stale | reconcile_mismatch | judge_down
  detail  jsonb not null,
  resolved_at timestamptz
);

create table daily_reviews (
  date    date primary key,
  summary text not null,                     -- Claude 사후 리뷰 (매매 일지)
  stats   jsonb not null,
  cost_usd double precision
);
```

## 2. Redis 키

| 키 | 타입 | 내용 | TTL |
| --- | --- | --- | --- |
| `px:{market}:{symbol}` | string | 마지막 체결가 | 60s |
| `ob:{market}:{symbol}` | hash | 최우선 호가 5단계 | 10s |
| `bar:{market}:{symbol}:{tf}` | hash | 집계 중인 현재 봉 | 없음 |
| `pos:{market}` | hash | symbol → JSON(Position) | 없음 |
| `eq:{market}` | string | 현재 equity | 없음 |
| `halt:{market}` | string | 할트 사유 (없으면 키 없음) | 없음 |
| `rl:{broker}:{group}` | string(counter) | 레이트리밋 카운터 (초 단위 슬라이딩) | 1s |
| `q:orders:{market}` | list | 주문 큐 (JSON Order) | 없음 |
| `ch:ticks`, `ch:fills`, `ch:judgments`, `ch:risk` | pub/sub | 화면 실시간 이벤트 | — |
| `token:kis` | string | KIS 액세스 토큰 | 23h |

Redis가 죽으면 엔진은 같은 구조의 인메모리 dict로 폴백하고 `risk_events`에 기록한다.

## 3. 보존 정책

| 데이터 | 보존 |
| --- | --- |
| 1분봉 | 30일 후 압축, 2년 후 5분봉만 남기고 삭제 |
| 5분봉·일봉 | 무기한 |
| 원장·주문·판단·LLM | 무기한 (세금 신고·보정 분석) |
| 뉴스 | 1년 |
| equity_snapshots | 1분 → 90일 후 1시간으로 다운샘플 |
| 백테스트 자산 곡선 파일 | 30개 초과 시 오래된 것부터 삭제 (메타는 유지) |

## 4. 0단계 dataclass와의 대응

`core/models.py`의 `Order`·`Fill`·`Position`·`JudgeResult`는 위 테이블 행의 **부분집합**이다(market·signal_id·paper·size_multiplier·stop 등은 1단계 P1-01에서 dataclass에 추가). ORM은 SQLAlchemy 2.0 + asyncpg, 매핑 모듈은 `quantpilot/db/models.py`(1단계). dataclass ↔ ORM 변환 함수는 `db/mappers.py`에 두고, 코어는 ORM을 모른다.
