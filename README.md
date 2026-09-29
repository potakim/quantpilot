# QuantPilot

초보자용 AI 퀀트 트레이딩 플랫폼 — **코드가 계산하고, 모델은 판단하고, 코드가 실행한다.**

기획서: Claude Docs "AI 퀀트 트레이딩 플랫폼 기획서" · 화면: Claude Design "QuantPilot 화면 디자인"

## 현재 상태: 0단계 (기반 구축)

| 모듈 | 상태 | 위치 |
| --- | --- | --- |
| 전략 플러그인 인터페이스 + 4개 전략 (변동성 돌파·GEM·GTAA·ORB) | 완료 | `quantpilot/strategies/` |
| 백테스터 (비용 모델 강제 · 12개월 홀드아웃 · 파라미터 시도 카운터) | 완료 | `quantpilot/backtest/` |
| PaperBroker + RiskManager (1% 룰 · 월 −5% 서킷브레이커 · 비중 25% · 단타 20% · 자전거래 방지) | 완료 | `quantpilot/execution/` |
| 판단 계층 인터페이스 + 스텁 (JudgeProvider / LLMProvider / 확신도 게이팅 / 2모델 합의) | 완료 (스텁) | `quantpilot/judgment/` |
| 데이터 로더 (업비트 REST · yfinance · FDR) + 캐시 | 완료 (실데이터 미검증) | `quantpilot/data/` |
| FastAPI (전략·백테스트·페이퍼·판단 미리보기) | 완료 (미실행) | `quantpilot/api/` |
| Next.js 프론트 · TimescaleDB · Redis · 스케줄러 · 실전 어댑터 | 1~2단계 | — |

관문 **G1** (비용 포함 백테스트가 공개 수치 ±20% 이내 재현) 은 실데이터를 받은 뒤 확인한다.

## 시작하기 (Windows, PowerShell)

```powershell
cd E:\claude\project\AiTrading\quantpilot
uv venv                              # 없으면: pip install uv
.venv\Scripts\activate
uv pip install -e ".[data,dev]"      # 코어 + 데이터 로더 + pytest
pytest                               # 23개 테스트

# 합성 데이터로 파이프라인 확인
qp backtest gem
qp backtest vol_breakout -p k=0.6
qp judge --news "상장폐지 검토"      # AI 판단 스텁: hard block → 보류

# 실데이터 (인터넷 필요)
qp fetch upbit KRW-BTC KRW-ETH KRW-SOL KRW-XRP KRW-ADA --count 3000
qp fetch yfinance SPY ACWX AGG BIL --start 2005-01-01
qp backtest vol_breakout --source upbit
qp backtest gem --source yfinance

# API 서버
qp serve --reload                    # http://127.0.0.1:8000/docs
```

Docker(TimescaleDB·Redis 포함)는 `docker compose up -d` — 1단계에서 실제로 쓰기 시작한다.

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
  core/models.py        Target · Order · Fill · Position · JudgeResult · Gate
  strategies/           base.py(Strategy·Context·ParamSpec) · vol_breakout · gem · gtaa · orb
  backtest/             engine · costs(PRESETS) · metrics · attempts
  execution/            broker(BrokerAdapter) · paper(PaperBroker) · risk(RiskManager)
  judgment/             base(JudgeProvider·LLMProvider·decide) · stub
  data/                 loader(upbit·yfinance·fdr·CandleCache) · synthetic
  api/app.py            FastAPI 라우트
  cli.py                qp backtest | fetch | judge | serve
tests/                  23 tests
```

## 다음 (1단계 · 코인 페이퍼 4주)

- `data/upbit_ws.py` 업비트 웹소켓 체결가 → PaperBroker.on_price
- `judgment/typesafe.py` Jev 어댑터 (`POST /v1/systemone`, jev-latest) · `judgment/anthropic.py` · `judgment/google.py`
- `features/builder.py` state JSON (지표 등급화 · 뉴스 요약)
- 원장 → TimescaleDB, 보정 지표(Brier·ECE) 리포트, 게이팅 ON/OFF A/B
- 관문 G2: 4주 페이퍼에서 게이팅 ON의 MDD < OFF, Brier < 0.25

## 고지

이 소프트웨어는 투자 자문을 제공하지 않으며 원금 손실이 가능합니다. 개인 본인 계좌에서만 사용하는 것을 전제로 합니다.
