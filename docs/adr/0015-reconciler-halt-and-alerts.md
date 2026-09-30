# 0015 · Reconciler 할트 전달과 critical 알림 반복

상태: 승인 (2026-09-30)

## 맥락
04 §5.4에 따르면 Reconciler는 엔진이 시작할 때와 5분마다 브로커 계좌와 DB 원장을 대조하고, 수량이 맞지 않으면 `risk_events(reconcile_mismatch)`를 남긴 뒤 할트한다. 할트는 사람이 화면에서 "브로커 기준으로 맞추기"를 눌러야만 풀린다. 그런데 `reconcile` 잡은 scheduler 프로세스에서 돌고, 할트를 실제로 적용하는 `RiskManager`는 엔진 프로세스 안에 있다. 두 프로세스는 ADR 0013의 settings 우편함으로만 연결되어 있다. 또 07 §6은 critical 알림을 해결될 때까지 5분마다 반복하라고 요구하는데, P1-09까지의 `Notifier`는 한 번 보내는 기능밖에 없었다.

## 결정
1. **할트 키를 우편함에 추가한다**: `engine.halt.<market>` = `{reason, ts, event_id}`. 쓰는 방법은 `EngineLink.halt/halt_reason/clear_halt`이다.
   - 거는 쪽은 Reconciler(scheduler)이다. 이미 걸려 있으면 처음 기록을 그대로 둔다.
   - 푸는 쪽은 `Reconciler.accept_broker` 하나뿐이다. 이 경로는 사람 조작으로만 호출하며, 화면 버튼은 P1-12에서 붙인다. 불일치가 사라져도 할트는 저절로 풀리지 않는다.
   - 엔진은 `MarketEngine.on_link`에서 하트비트를 쓸 때마다 이 키를 읽어 `RiskManager.halted_reason`에 반영한다. 할트 키가 지워졌을 때는 사유가 `reconcile`로 시작하는 할트만 해제한다. 월 서킷브레이커나 API 할트는 건드리지 않는다.
   - `RiskRules`는 변경하지 않는다(불변식 #6). 청산 주문은 할트 중에도 허용된다.
2. **불일치 판정**: DB 포지션을 심볼별로 합산(전략별 행을 더함)한 뒤 브로커 포지션과 비교한다. 차이가 **최소 주문 단위 이상**이면 불일치로 본다. 단위는 업비트 1e-8, KRX·미국 1주다. 04 §5.4의 "초과"를 "이상"으로 읽었다. KRX에서 1주 차이를 놓치지 않기 위해서다. 미해결 이벤트가 이미 있으면 새로 기록하지 않는다. 현금 차이는 할트 사유로 보지 않고, 마지막 평가액 스냅샷(최대 1분 지연)과 비교해 warning을 한 번만 보낸다.
3. **브로커 기준으로 맞추기**: 전략 행이 하나인 심볼은 그 행의 수량을 고친다. 전략 행이 여럿이거나 없는 심볼은 `strategy="reconciled"` 한 행으로 합친다. 작업이 끝나면 이벤트를 `resolved_at`으로 닫고 할트 키를 지운다.
4. **알림**(`notify/telegram.py`):
   - `TelegramNotifier`는 토큰과 chat id를 `QP_TELEGRAM_*` 환경변수로만 받는다. 본문과 로그에 토큰을 남기지 않으며, 예외는 타입만 기록한다(불변식 #10). httpx가 없으면 알림을 로그로 대신 남긴다.
   - `RepeatingNotifier`는 critical 알림을 `key` 단위로 기억했다가 5분마다 다시 보낸다(`alert_repeat` 잡이 60초마다 `tick`을 호출한다). 이미 반복 중인 key는 다시 보내지 않으며, `resolve(key)`를 호출하면 반복이 멈춘다. 쓰는 key는 `reconcile.<market>`(해제는 accept_broker)와 `heartbeat.<market>`(해제는 하트비트 복구)이다.
   - `CriticalLogHandler`는 엔진과 scheduler의 CRITICAL 로그(청산 주문 실패, 원장 쓰기 실패)를 알림으로 넘긴다. Reconciler는 같은 내용이 두 번 가지 않도록 error 레벨로 기록한다.
   - `Notifier` Protocol은 `core/ports.py`로 옮기고, `send(level, text, *, key=None)`과 `resolve(key)`를 정의한다.

## 결과
- Redis 없이도 할트가 엔진에 전달된다. 전달되기까지 최대 `link_every`(5초)가 걸린다.
- `engine.halt.<market>` 키는 scheduler(거는 쪽)와 사람 조작(푸는 쪽)이 모두 쓰지만, 같은 시점에 동시에 쓰는 경우는 없다.
- 페이퍼 모드에서는 scheduler가 대조하는 "브로커"가 DB 계좌를 복원한 것이므로 결과가 항상 일치한다. 실제로 의미 있는 대조는 실브로커 어댑터가 붙은 뒤부터이며, 그 전에는 단위 테스트로 검증한다.
- scheduler를 재시작하면 반복 알림 상태(메모리)는 사라진다. 대신 시작할 때 도는 reconcile이 미해결 불일치를 다시 알린다. 엔진 프로세스의 CRITICAL 로그 알림은 반복하지 않는다(해제 신호가 없어서다).
- 이메일 채널은 07 §3에 SMTP 변수가 정의될 때까지 보류한다.
