# 09 · 1단계 작업 분해 (코인 페이퍼, 4주)

목표: 업비트 5코인 변동성 돌파를 **실시간 페이퍼**로 4주 돌리며 판단 모델 게이팅을 붙이고, 관문 G2를 판정할 수 있는 원장·리포트를 만든다. 각 이슈는 PR 하나. Claude Code에 넘길 때는 이슈 번호·완료 기준·관련 문서 절을 그대로 붙인다.

의존 순서: P1-01 → 02 → 03 → 04 → (05, 06 병렬) → 07 → 08 → 09 → (10, 11 병렬) → 12 → 13 → 14 (14의 CI 부분은 01 직후 먼저 올려도 됨)

| # | 제목 | 입력 | 산출물 | 완료 기준 | 문서 |
| --- | --- | --- | --- | --- | --- |
| P1-01 | DB 스키마·마이그레이션·repo | 02 문서 | `db/models.py`, `db/mappers.py`, `db/repo.py`, alembic 초기 마이그레이션, `docker compose up db` | `alembic upgrade head` 성공, repo 단위 테스트(SQLite 인메모리) 통과, dataclass↔ORM 왕복 테스트 | 02, 04 §9 |
| P1-02 | MarketClock·이벤트·오류 타입 | 04 §1 | `core/clock.py`, `core/events.py`, `core/errors.py` | KRX·NYSE 휴장·DST 테스트, `is_last_session_of_month` 테스트 | 04 §1, 05 §5 |
| P1-03 | 업비트 웹소켓 + 캔들 집계 | 04 §6 | `data/upbit_ws.py`, `data/aggregator.py`, `data/store.py` | 재접속 테스트(가짜 서버), 1분봉 집계가 REST 일봉과 일치(네트워크 테스트), 30초 무응답 시 `DataStale` | 01 §4.1, 04 §6 |
| P1-04 | TickRunner + 백테스터 통합 | 04 §7 | `engine/tick.py`, `engine/main.py`, Backtester가 TickRunner 사용 | 불변식 #2 테스트(같은 데이터 → 같은 체결), 기존 23 테스트 유지 | 01 §3, 04 §7 |
| P1-05 | PersistentPaperBroker + OrderExecutor + RateLimiter | 04 §5 | `execution/persistent_paper.py`, `execution/executor.py`, `execution/ratelimit.py` | 불변식 #9, 재시도·타임아웃 테스트, 원장 INSERT 확인 | 04 §5.2 |
| P1-06 | FeatureBuilder + 뉴스 수집·요약 | 04 §3, 06 §3 | `features/builder.py`, `data/news.py`, `data/events.py`, `judgment/google.py`(Summarizer) | state ≤ 400토큰 테스트, 뉴스 중복 제거 테스트, 요약 계약 테스트(fixture) | 06 §3 |
| P1-07 | TypeSafe Jev 어댑터 + 질문 v1 YAML | 06 §2 | `judgment/typesafe.py`, `judgment/questions/v1.yaml`, `judgment/pricing.py` | 계약 테스트(실응답 fixture), 3초 타임아웃 → hold, confidence=min 규칙 테스트 | 06 §2, §7 |
| P1-08 | Claude·Gemini 리뷰어 + JudgmentPipeline | 06 §4·5 | `judgment/anthropic.py`, `judgment/google.py`(Reviewer), `judgment/pipeline.py`, `judgment/prompts/review_v1.md` | JSON 파싱 실패 → hold, 30초 타임아웃 → hold, 병렬 호출 테스트, 비용 계산 테스트 | 06 §4·5 |
| P1-09 | 스케줄러 (09:00 청산·목표가·뉴스·리뷰·24h 실현·월 롤) | 04 §8 | `scheduler/main.py`, `scheduler/jobs/*.py` | 잡 등록 테스트, `upbit_daily_exit`가 TickRunner.on_time_exit 호출, 엔진 하트비트 백업 모드 테스트 | 04 §8, 05 §5 |
| P1-10 | Reconciler + 알림 | 04 §5.4, 07 §6 | `execution/reconciler.py`, `notify/telegram.py` | 불일치 → 할트 테스트, critical 반복 알림 테스트 | 07 §7.3 |
| P1-11 | 보정 지표 + A/B 섀도 원장 + 리포트 | 06 §6 | `judgment/calibration.py`, 섀도 PaperBroker, `qp report ab` | Brier·ECE 손계산 fixture와 일치, 섀도 원장이 ON과 같은 신호 수 | 06 §6, 08 §5 |
| P1-12 | API v1 재구성 + WS 허브 | 03 | `api/routes/*.py`, `api/ws.py`, JWT | 03 문서의 모든 엔드포인트 통합 테스트(httpx AsyncClient), WS 구독·팬아웃 테스트, 불변식 #6·#10 | 03 |
| P1-13 | Next.js 대시보드·AI 판단 로그 화면 | 03 §4, 디자인 캔버스 | `web/` (App Router, TS), 대시보드·거래·AI 로그 3화면 | 디자인 캔버스와 동일 구성, WS 실시간 갱신, Lighthouse 접근성 90+ | 03 §4 |
| P1-14 | 운영: compose(paper) · 배포 스크립트 · 백업 · CI | 07 | `deploy/`, `.github/workflows/ci.yml`, `scripts/backup.sh` | VPS에서 `up -d` 후 `/health` OK, CI 그린 | 07 |

## 완료 기준 공통

- `pytest -m "not network"` 그린, `ruff` 클린.
- 새 모듈은 04 문서의 인터페이스 시그니처와 일치. 다르면 문서 먼저 수정 + ADR.
- 로그에 키 없음(불변식 #10 테스트).
- PR 본문에 "문서 절 / 테스트 / 수동 확인 방법" 세 항목.

## 4주 운영 계획 (P1-09까지 끝난 뒤)

| 주 | 내용 |
| --- | --- |
| 1 | 페이퍼 가동, 게이팅 ON/OFF 섀도 동시 기록, 매일 리뷰 읽기. 버그 수정만 |
| 2 | 판단 로그 검토: 보류된 신호의 사후 수익률, 하드블록 빈도. 질문 문구 v2 후보 메모(적용은 안 함) |
| 3 | 계속 기록. Brier·ECE 중간 확인. n < 20이면 대상 코인 확대 검토 |
| 4 | `qp report ab --weeks 4` → G2 판정. 통과면 2단계(KIS 모의) 착수, 아니면 원인별 조치(질문 v2 / 임계값 / 전략 파라미터 — 단 파라미터 변경은 시도 카운터에 기록) |
