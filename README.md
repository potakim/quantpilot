# QuantPilot

초보자용 AI 퀀트 트레이딩 플랫폼 — **코드가 계산하고, 모델은 판단하고, 코드가 실행한다.**

기획서: Claude Docs "AI 퀀트 트레이딩 플랫폼 기획서" · 화면: Claude Design "QuantPilot 화면 디자인"

**설계 문서는 [`docs/00-overview.md`](docs/00-overview.md)부터.** 작업 지침은 [`CLAUDE.md`](CLAUDE.md), 1단계 작업 분해는 [`docs/09-phase1-workplan.md`](docs/09-phase1-workplan.md).

## 현재 상태: 1단계 개발 완료 · 코인 페이퍼 운영 준비

1단계 이슈 P1-01~P1-14와 후속 카드는 모두 `main`에 들어왔다. 진행 기록은 `docs/09-phase1-workplan.md`와 ADR(`docs/adr/`)에 있다.

| 영역 | 상태 | 위치 |
| --- | --- | --- |
| 전략 4개 (변동성 돌파·GEM·GTAA·ORB) | 백테스트 모두 가능. **실시간은 변동성 돌파(업비트 페이퍼)만** — KRX·미국 엔진과 KIS·Alpaca 어댑터는 2단계 | `quantpilot/strategies/` |
| 백테스터 | `TickRunner`로 실전과 같은 루프, 비용 모델 강제, 12개월 홀드아웃, 시도 카운터 | `quantpilot/backtest/`, `quantpilot/engine/` |
| 실행 | 영속 PaperBroker · OrderExecutor · RateLimiter · RiskManager · Reconciler | `quantpilot/execution/` |
| 판단 계층 | TypeSafe Jev 어댑터, Claude·Gemini 리뷰어, 게이팅, 08:10 사전 심사. **기본값은 스텁**(`QP_JUDGE_PROVIDER=stub`, 판단은 기록만) | `quantpilot/judgment/` |
| 뉴스·이벤트 | RSS·DART 수집 → DB → 엔진 판단 입력 (ADR 0021) | `quantpilot/data/news.py`, `quantpilot/features/` |
| 엔진·스케줄러·API·화면 | 업비트 웹소켓 엔진, APScheduler 잡, API v1 + WS + JWT, Next.js 대시보드·거래·AI 판단 로그·모바일 | `quantpilot/engine/`, `scheduler/`, `api/`, `web/` |
| 운영 | paper compose, 배포 가드, 백업, CI(pytest·ruff·web·이미지) | `deploy/`, `scripts/`, `.github/workflows/` |

관문 G1은 통과했다(2026-10-03, ADR 0024: GEM은 같은 기간 공개 수치, 변동성 돌파는 독립 기준 구현과 대조).

아직 확인하지 않은 것: 실제 API 키로의 외부 호출(TypeSafe·Anthropic·Gemini·DART·텔레그램), VPS에서의 TimescaleDB·2프로세스 종단 실행.

## 시작하기 (Linux · WSL)

배포 서버와 CI는 리눅스다. Windows에서는 WSL에서 작업하는 것을 권장한다(네이티브 Windows에서도 테스트는 돌지만, 백업 스크립트처럼 POSIX 전용 테스트는 건너뛴다).

```bash
uv venv && source .venv/bin/activate      # 없으면: pip install uv
uv pip install -e ".[data,dev]"           # 코어 + 데이터 로더 + 테스트 (실행 환경은 .[data,infra,ai])
pytest -q                                  # 네트워크 테스트는 기본 제외 (-m network)
ruff check . && ruff format --check .

# 합성 데이터로 파이프라인 확인
qp backtest gem
qp backtest vol_breakout -p k=0.6
qp judge --news "상장폐지 검토"           # 스텁 판단: hard block → 보류

# 실데이터 (인터넷 필요)
qp fetch upbit KRW-BTC KRW-ETH KRW-SOL KRW-XRP KRW-ADA --count 3500
qp fetch yfinance SPY ACWX AGG BIL --start 2005-01-01
qp backtest vol_breakout --source upbit
python scripts/verify_g1.py                # 관문 G1

# 게이팅 A/B 리포트 (관문 G2, 페이퍼 운영 기록 필요)
qp report ab --weeks 4

# API 서버 (개발용)
qp serve --reload                          # http://127.0.0.1:8000/docs
```

실행 프로세스는 `api`·`engine`(`python -m quantpilot.engine.main`)·`scheduler`(`python -m quantpilot.scheduler.main`)·`web` 넷이다. VPS 배포는 `scripts/deploy.sh`와 `docs/07-operations.md`를 따른다. 환경변수는 `.env.example`을 복사해 쓴다.

## 설계 원칙 (코드에 박힌 것)

1. **전략은 계산만 한다.** `Strategy.on_bar(ctx) -> list[Target]` 는 목표 비중만 돌려준다. 수량·주문·리스크는 `RiskManager` 와 `BrokerAdapter` 의 몫이며, 백테스트와 실전이 같은 `on_bar` 를 호출한다.
2. **비용 0 백테스트는 만들 수 없다.** `Backtester(ZERO)` 는 `allow_zero_cost=True` 없이는 예외. `CostModel` 은 PaperBroker 와 공유한다.
3. **마지막 12개월은 잠겨 있다.** 홀드아웃은 `unlock_holdout=True` 로 실전 전환 직전 1회만 푼다.
4. **파라미터 시도는 세어진다.** `data/backtest_attempts.json` 에 전략별 서로 다른 조합 수를 기록하고 7회 초과 시 경고.
5. **AI는 확률만 준다.** `JudgeResult` 는 확률·확신도뿐이고, 수량·가격·손절은 코드가 정한다. `hard_blocks`(news_risk·event_ahead) 와 2모델 합의는 확신도보다 우선한다.
6. **리스크 규칙은 화면과 AI가 못 바꾼다.** `RiskRules` 는 코드 상수이고, 청산 주문은 서킷브레이커와 무관하게 항상 통과한다.
7. **룩어헤드 금지.** 장중 가격을 지정하는 전략(변동성 돌파·ORB)은 현재 봉의 종가를 쓰지 않는다 (`test_vol_breakout_no_lookahead_on_entry_day`).

## 구조

```
quantpilot/
  core/          모델 · 시계(MarketClock, 휴장표) · 이벤트 · 포트(Protocol)
  strategies/    Strategy · vol_breakout · gem · gtaa · orb
  backtest/      Backtester(TickRunner 재생) · 비용 모델 · 지표 · 시도 카운터
  engine/        TickRunner · MarketEngine(실시간) · settings 우편함
  execution/     PaperBroker · PersistentPaperBroker · OrderExecutor · RiskManager · Reconciler
  judgment/      판단 모델 · LLM 리뷰어 · JudgmentPipeline · 보정 지표 · A/B
  features/      FeatureBuilder (판단 입력 state)
  data/          로더 · 업비트 WS · 캔들 집계 · 뉴스 수집 · 이벤트 캘린더
  db/            SQLAlchemy 모델 · repo · alembic 마이그레이션
  scheduler/     APScheduler 잡 · 부품 배선
  api/ realtime/ REST v1 · WS 허브 · JWT
  notify/ ops/   텔레그램 알림 · 배포 가드
web/             Next.js 화면
deploy/ scripts/ compose · 배포 · 백업 · G1 확인
```

## 다음 (1단계 운영 → 관문 G2)

1. VPS 준비 → `scripts/deploy.sh --dry-run` → 배포 → 첫 실행 체크리스트
2. API 키 등록 후 `QP_JUDGE_PROVIDER=typesafe`로 게이팅 켜기, 네트워크 계약 테스트 실행
3. 4주 페이퍼 운영 (게이팅 ON/OFF 섀도 동시 기록) → `qp report ab --weeks 4`로 G2 판정

## 고지

이 소프트웨어는 투자 자문을 제공하지 않으며 원금 손실이 가능합니다. 개인 본인 계좌에서만 사용하는 것을 전제로 합니다.
