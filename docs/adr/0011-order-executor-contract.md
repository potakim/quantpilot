# 0011 · OrderExecutor 계약: 반환 타입·원장 기록 주체·페이퍼 영속화·레이트리밋

상태: 승인 (2026-09-30)

## 맥락
P1-05에서 ADR 0010의 자리표시자 `DirectExecutor`(risk.check → submit, 재시도·원장 없음)를 실제 `OrderExecutor`로 채운다. 04 §5.2의 스케치와 이미 머지된 코드 사이에 네 군데가 어긋난다.

1. 04 §5.2는 `execute() -> ExecResult`인데, `TickRunner`가 쓰는 `core/ports.py`의 `OrderExecutor` Protocol은 `Fill | Order`를 돌려받는다(ADR 0010).
2. 04 §5.1은 `PersistentPaperBroker`가 "포지션·원장을 DB에 쓴다"고 하고, 04 §5.2는 `OrderExecutor`가 `ledger.record`를 한다고 한다. 둘 다 하면 체결이 두 번 들어간다. 또 `BrokerAdapter.submit`은 동기인데 repo는 비동기다.
3. `BrokerAdapter`에는 제출한 주문의 체결 여부를 물어볼 메서드가 없다. 04 §5.2의 "체결 확인 루프"를 돌릴 수 없다.
4. `ch:fills` 발행은 이미 `TickRunner`가 `fill` 토픽으로 한다.

## 결정
1. **반환 타입은 `Fill | Order`다.** `core/ports.py`의 Protocol을 그대로 만족해 `TickRunner`에 `DirectExecutor` 대신 꽂을 수 있다. `ExecResult`는 만들지 않는다.
2. **원장(orders·fills)은 `OrderExecutor`가 쓴다.** 실브로커·페이퍼 모두 같다. 브로커에 닿은 주문은 `ledger.save_order`(상태가 바뀔 때마다), 체결은 `ledger.record`. 리스크에서 거부된 주문은 브로커에 닿지 않았으므로 orders에 쓰지 않고 `signals.outcome = risk_rejected`만 남긴다(`signal_id`와 `SignalRepo`가 있을 때).
3. **`PersistentPaperBroker`는 가상 계좌 상태(포지션·현금)만 DB에 쓴다.** 체결로 바뀐 심볼을 모아 두었다가 `async persist()`에서 `PositionRepo.upsert`, 현금은 `settings`의 `paper.cash.<market>`. `OrderExecutor`는 체결 뒤 브로커에 `persist`가 있으면 부른다. 재시작 시 `async restore()`로 복원한다.
4. **`BrokerAdapter.order_status(order_id)`를 추가한다.** 기본 구현은 `None`(모름). `None`이면 체결 확인 루프를 돌지 않고 대기 주문을 그대로 돌려준다. `PaperBroker`는 대기 큐와 원장으로 답한다.
5. **발행은 `TickRunner` 몫으로 둔다.** `OrderExecutor`는 이벤트를 발행하지 않는다.
6. **재시도 횟수**: retryable 오류는 첫 시도 + 재시도 3회(0.5·1·2초 지수 백오프, `RateLimited.retry_after`가 더 길면 그만큼). 전부 실패하면 `risk.api_error()`. 청산 주문은 재시도 10회(대기 상한 4초)이고 실패 시 `CRITICAL` 로그를 남긴다(알림 채널은 P1-10). non-retryable(4xx)은 재시도 없이 거부하고 API 오류로 세지 않는다.
7. **체결 확인**: 시장가는 최대 30초, 지정가는 `limit_ttl`(기본 60초) 동안 0.5초 간격으로 `order_status`를 본다. 시간이 지나면 취소하고, 시장가는 새 주문 id로 1회 재주문한다.
8. **레이트리밋**: 그룹(`upbit:order` 등)별 슬라이딩 윈도, 거래소 한도의 80%(내림, 최소 1). 인메모리 구현이 기본이고, 여러 프로세스가 같은 계좌를 쓰게 되면 `RedisSlidingWindowLimiter`(redis 클라이언트 주입, import 없음)로 바꾼다. 페이퍼 브로커는 `group=None`으로 한도를 건너뛴다.
9. **원장·DB 쓰기 실패는 주문 결과를 바꾸지 않는다.** 체결은 이미 일어났으므로 `CRITICAL` 로그를 남기고 `Fill`을 돌려준다. 어긋난 DB 상태는 Reconciler(P1-10)가 잡는다.

## 결과
- `TickRunner` 배선을 바꾸지 않고 `DirectExecutor` → `OrderExecutor`로 교체할 수 있다. 백테스트는 계속 `DirectExecutor`(원장·대기 없음)를 쓴다.
- `engine/main.py`의 페이퍼 배선에 DB 세션을 넣는 일은 이번 카드에 없다(운영 compose, P1-14).
- 브로커 어댑터(업비트 등)는 `order_status`를 구현해야 체결 확인 루프가 돈다.
