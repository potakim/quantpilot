# CLAUDE.md — QuantPilot 작업 지침

초보자용 AI 퀀트 트레이딩 플랫폼. **코드가 계산하고, 모델은 판단하고, 코드가 실행한다.**
작업 전에 `docs/00-overview.md`를 읽고, 맡은 영역의 문서(`docs/04-modules.md` 등)를 확인한다. 프론트 작업은 `docs/10-ui-design.md`와 `docs/design/*.dc.html`(화면 원본)을 반드시 연다. 기획 배경은 `docs/90-planning-brief.md`.

## 명령

```bash
uv pip install -e ".[data,dev]"     # 설치 (infra·ai·brokers는 필요할 때)
pytest -q                            # 전체 테스트 — 커밋 전 반드시 통과
ruff check . && ruff format .        # 린트·포맷
qp backtest <strategy> [--source upbit|yfinance|fdr|synthetic] [-p k=0.5]
qp serve --reload                    # http://127.0.0.1:8000/docs
```

## 절대 깨면 안 되는 불변식 (코드로 강제되어 있고, 테스트가 있다)

1. 전략(`Strategy.on_bar`)은 `Target(비중)`만 돌려준다. 수량·주문·리스크 판단을 전략 안에 넣지 않는다.
2. 백테스트와 실전은 같은 `on_bar`를 호출한다. 전략에 `if live:` 같은 모드 분기를 두지 않는다.
3. `Target.price`를 쓰는 전략은 현재 봉의 `close`를 계산에 쓰지 않는다 (룩어헤드).
4. 비용 0 백테스트는 `allow_zero_cost=True` 없이는 예외. `CostModel`은 백테스터와 `PaperBroker`가 공유한다.
5. 마지막 `holdout_months`(기본 12)는 잠긴다. `unlock_holdout=True`는 실전 전환 직전 1회.
6. `RiskRules`는 코드 상수다. API·화면·AI·설정 파일 어디서도 더 느슨하게 바꿀 수 없다. 청산 주문은 서킷브레이커·할트·비중 규칙과 무관하게 허용된다(자전거래·주문 빈도 검사만 적용).
7. `JudgeResult`는 확률·확신도만 담는다. 수량·가격·손절을 AI에게 묻는 코드를 쓰지 않는다.
8. `hard_blocks`(news_risk·event_ahead)와 LLM 2모델 합의는 확신도보다 우선한다.
9. 모든 주문은 `RiskManager.check` → `BrokerAdapter.submit` 순서. 브로커를 직접 호출하는 경로를 만들지 않는다.
10. 거래소 키는 환경변수(`QP_*`)로만 주입. 로그·원장·API 응답에 키가 찍히지 않는다.

## 코딩 규칙

- Python 3.11+, 타입 힌트 필수, `from __future__ import annotations`. 공개 함수에는 한 줄 docstring(한국어).
- 코어(`core`·`strategies`·`backtest`·`execution`·`judgment`)는 pandas·numpy 외 의존성 금지. 외부 SDK는 어댑터(`data/`, `judgment/<provider>.py`, `execution/<broker>.py`)에서만 import하고 `try/except ImportError`로 감싼다.
- 시간은 tz-naive KST가 아니라 **각 시장의 현지시간을 tz-naive로** 저장하고, DB에는 UTC(`timestamptz`)로 저장한다. 변환은 `core/clock.py`(1단계)에서만.
- 금액은 `float`로 두되 원화는 정수 원 단위로 반올림해 주문한다. 코인 수량은 소수 8자리.
- 새 전략: `Strategy` 상속 → `strategies/__init__.py` REGISTRY 등록 → `tests/test_backtest.py`의 파라미터화 테스트가 자동으로 돈다. 공개 백테스트 수치와 출처를 docstring에 적는다.
- 새 브로커·판단 모델 어댑터: 인터페이스(`BrokerAdapter`, `JudgeProvider`, `LLMProvider`)만 구현하고, 페이퍼/스텁과 같은 테스트를 통과시킨다.
- 로그는 `logging` + 구조화 필드(`extra={"symbol":..., "strategy":...}`). print 금지.
- 테스트는 합성 데이터(`data/synthetic.py`)로 결정적으로. 네트워크를 타는 테스트는 `@pytest.mark.network`로 표시하고 기본 실행에서 제외.

## 작업 방식

- 작업 단위는 `docs/09-phase1-workplan.md`의 이슈 번호를 따른다. 이슈 하나 = PR 하나 = 완료 기준 충족.
- 설계와 다르게 구현해야 하면 먼저 `docs/adr/`에 ADR을 추가하고 문서를 고친 뒤 코드를 바꾼다.
- 실데이터·실계좌를 건드리는 코드는 항상 `settings.paper == True` 경로에서 먼저 검증한다.
- 커밋 메시지: 한국어 한 줄 요약 + 본문에 이슈 번호. 예: `P1-03 업비트 웹소켓 체결가 수신 (#12)`.
