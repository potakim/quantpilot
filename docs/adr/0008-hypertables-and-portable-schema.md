# 0008 · 하이퍼테이블은 ts가 PK에 들어가는 테이블만, 스키마는 SQLite에서도 돈다

상태: 승인 (2026-09-29)

## 맥락
02 문서는 `signals`·`fills`를 `id bigserial primary key`로 두고 `create_hypertable`을 건다. TimescaleDB는 하이퍼테이블의 모든 유니크 인덱스(PK 포함)에 파티션 컬럼(`ts`)이 들어가야 해서 이 구성은 마이그레이션에서 실패한다. PK를 `(id, ts)`로 바꾸면 이번에는 `judgments.signal_id → signals(id)`, `fills.order_id`처럼 `id` 하나만 가리키는 외래 키가 성립하지 않는다.

또 P1-01 완료 기준은 "repo 단위 테스트(SQLite 인메모리)"인데, 02 문서의 `jsonb`·`text[]`·하이퍼테이블은 PostgreSQL 전용이다.

## 결정
1. 하이퍼테이블은 PK에 `ts`가 이미 들어가 있는 `candles`·`equity_snapshots`만 둔다. `signals`·`fills`는 일반 테이블로 두고 `ts` 인덱스를 단다. 둘 다 하루 수십~수백 행이라 청크 분할의 이득이 없다.
2. ORM 타입은 방언별 변형을 쓴다: `jsonb` → PostgreSQL에서 `JSONB`, 그 외 `JSON`. `text[]` → PostgreSQL에서 `ARRAY(Text)`, 그 외 `JSON`. `timestamptz` → `DateTime(timezone=True)`.
3. TimescaleDB 전용 구문(`create extension`, `create_hypertable`, 압축 정책, GIN 인덱스)은 마이그레이션에서 `dialect == "postgresql"`일 때만 실행한다.
4. 코어는 `core/repos.py`의 Protocol(`Ledger`, `SignalRepo`, `JudgmentRepo`, `PositionRepo`, `ConfigRepo`)만 알고, 구현은 `db/repo.py`의 `Sql*` 클래스(`SqlLedger` 등)다. 이름이 겹치지 않도록 구현 쪽에 접두어를 붙였다.
5. 시간 변환 전용 모듈인 `core/clock.py`(P1-02)가 생기기 전까지 `db/mappers.py`는 tz-naive 시각을 UTC로 간주해 저장하고, 읽을 때 UTC tz-naive로 돌려준다. P1-02에서 시장별 현지시간 변환으로 교체한다.

## 결과
- `signals`·`fills`의 오래된 행 압축은 적용되지 않는다. 양이 늘면 월별 파티션을 따로 검토한다.
- 테스트는 SQLite로 돌지만, TimescaleDB 전용 경로는 실제 PostgreSQL에서 `alembic upgrade head`로 따로 확인해야 한다.
