# 0017 · API v1: 수동 주문 큐, 실시간 허브, 표준 라이브러리 JWT

상태: 승인 (2026-09-30)

## 맥락
P1-12는 0단계 `api/app.py`(프로세스 안에 PaperBroker를 둔 임시 라우트)를 03 문서의 `/api/v1` 형태로 바꾸는 카드다. 01 §2는 1단계부터 api가 DB와 Redis만 읽고, 계좌는 engine 프로세스가 쥔다고 정했다. 그런데 03 §2.4의 수동 주문(`POST /orders`)은 "RiskManager 통과 시 201, 거부 시 422"를 동기 응답으로 요구한다. 브로커가 없는 api가 주문을 어떻게 실행할지 정해야 했다. 또 엔진은 지금까지 이벤트를 로그로만 남겼기 때문에(`_LogBus`) 화면 WS로 이어지는 경로가 없었고, judgments·llm_verdicts도 실제로 기록되지 않았다(t10 숙제). 개발 환경에는 redis와 PyJWT가 설치되어 있지 않다.

## 결정
1. **수동 주문은 큐로 엔진에 넘긴다** (운영자 승인).
   - api는 먼저 엔진과 같은 `RiskManager`(코드 상수 규칙, 월초 평가액, 할트 상태)로 사전 검사를 한다. 거부되면 422 `RISK_REJECTED`를, 할트 중이면 409 `HALTED`를 돌려주고 큐에는 넣지 않는다.
   - 통과한 주문은 `q:orders:<market>`에 넣고 201 `{order(status=queued), risk}`를 돌려준다.
   - 엔진의 `engine/orders.py::ManualOrderConsumer`가 `MarketEngine.on_timer`마다 큐를 비운다. 주문은 `OrderExecutor.execute`로 실행하며, 이 안에서 `RiskManager.check → BrokerAdapter.submit`을 거친다(불변식 #9). 따라서 api의 사전 검사는 참고용이고, 실행 직전에 엔진이 다시 검사한다.
   - 청산(`POST /positions/.../close`)과 취소(`DELETE /orders/{id}`)도 같은 큐를 쓴다. 청산은 리스크 게이트의 exit 경로를 타므로 할트 중에도 허용된다(불변식 #6).
2. **실시간 허브**: `core/ports.Hub` Protocol(pub/sub + 상태 키 + 큐)을 두고 구현을 둘 만든다(`realtime/hub.py`).
   - `RedisHub`는 채널 `ch:<name>`과 02 §2의 키 이름을 쓴다. redis 패키지는 이 클래스 안에서만 import한다.
   - `MemoryHub`는 한 프로세스 안에서만 동작하며 테스트와 Redis 없는 개발 환경용이다. `QP_REDIS_URL`이 비어 있거나 redis가 설치되지 않았으면 `make_hub`가 MemoryHub를 준다. 이때 api와 engine은 실시간 데이터·큐를 공유하지 못한다(DB 경로는 그대로 동작).
   - 엔진은 `_LogBus` 대신 `realtime/bus.py::HubBus`를 쓴다. topic을 WS 채널로 바꿔 발행하며, 시세는 심볼당 초당 4건, portfolio는 5초에 1건으로 줄인다.
   - `EventRecorder`는 signal·judgment·warning 이벤트를 signals·judgments·llm_verdicts·risk_events에 쓴다. `JudgmentEvent`에 `state` 필드를 추가해 TickRunner가 판단 입력을 함께 싣게 했다. judge_down RiskEvent는 risk_events와 WS `risk` 채널로 간다. 기록이나 발행이 실패해도 매매는 멈추지 않는다.
   - api의 `api/ws.py::WsHub`는 허브를 프로세스당 한 번 구독하고, 각 연결이 구독한 채널만 골라 보낸다.
   - EngineLink(settings 우편함, ADR 0013·0015)는 그대로 둔다. Redis 구현으로 바꿀 경우 새 ADR이 필요하다.
3. **JWT는 표준 라이브러리 HS256으로 구현한다** (운영자 승인). `api/auth.py`는 alg를 HS256으로 고정하고 `none`과 다른 알고리즘은 거부한다. 서명은 상수 시간으로 비교하고 `exp`는 필수다. `QP_JWT_SECRET`이 32바이트 미만이거나 `QP_ADMIN_PASSWORD`가 비어 있으면 로그인을 503으로 막는다. 비밀번호·시크릿·토큰은 응답과 로그에 남기지 않는다(불변식 #10).
4. **키 등록**(`POST /settings/keys`): 03 §4는 "값은 저장만 하고 절대 반환하지 않음"이라고 적었고, 불변식 #10은 키를 `QP_*` 환경변수로만 주입하라고 한다. 둘을 함께 지키도록 값을 DB가 아니라 `data/keys.env`(권한 0600, `QP_<NAME>=값`)에 쓰고, `Settings`가 재시작할 때 dotenv로 읽게 했다. 응답에는 이름과 `restart_required`만 담는다. 요청에는 2차 확인(`confirm_password`)이 필요하다.
5. **부품이 없는 엔드포인트는 인터페이스를 주입받는 최소 구현으로 둔다** (운영자 승인).
   - `/judgments/calibration`·`/ab`는 `CalibrationSource`를 주입받는다. 이 계산은 t13의 몫이므로 api가 직접 부르지 않으며, 주입되지 않았으면 503 `NOT_READY`를 돌려준다.
   - `/judgments/{id}/ask`는 `Answerer`를 주입받는다. 기본은 스텁이고, `QP_ANTHROPIC_API_KEY`가 있으면 `ClaudeAnswerer`를 쓴다. 답변기에는 기록만 근거로 설명하고 수량·가격은 제시하지 말라고 지시한다(불변식 #7).
   - `/reports/gates`는 근거가 없는 관문을 pass=false와 사유로 표시한다. G1은 `gate_report.g1.<전략>` 설정 행(verify_g1이 쓴다)을 근거로 쓰고, 이 키는 PATCH 허용 목록에 없다.
6. **정합 해제**: 할트를 푸는 API는 `POST /reconcile/{market}/accept-broker` 하나다. 이 경로는 `Reconciler.accept_broker`를 호출하고 2차 확인을 요구한다. 03 문서에 이 행을 추가했다.

## 결과
- api 프로세스는 브로커 SDK를 import하지 않는다. 계좌는 engine 하나가 소유하므로 두 곳의 상태가 어긋나지 않는다.
- 수동 주문 응답은 "대기(queued)"까지만 알려 준다. 체결 여부는 WS `fills` 채널이나 `/fills`로 확인한다. Redis 없이 MemoryHub를 쓰면 다른 프로세스의 엔진에는 큐가 전달되지 않는다.
- `PATCH /settings`의 `gate.*`·`judge.provider`·`llm.models`는 settings 테이블에 저장되지만, 엔진은 아직 환경변수(`QP_GATE_*` 등)로 파이프라인을 만든다. DB 설정을 엔진이 읽게 하는 일은 후속 카드에서 한다.
- 백테스트 진행률은 단계(running·done·failed) 단위로만 보낸다. Backtester에 진행 콜백이 없기 때문이다.
