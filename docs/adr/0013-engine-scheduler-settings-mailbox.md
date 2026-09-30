# 0013 · engine ↔ scheduler 연결: settings 우편함·하트비트·백업 시간 청산

상태: 승인 (2026-09-30)

## 맥락
01 §2에 따르면 `engine`과 `scheduler`는 서로 다른 프로세스이고, 엔진이 죽어도 시간 청산은 돌아야 한다. 04 §8은 `upbit_daily_exit`가 청산을, `engine_heartbeat`가 "90초 없으면 알림 + 시간 청산 백업 모드"를 맡는다고 정했다. 그런데 두 프로세스가 명령과 하트비트를 주고받을 Redis 허브는 P1-12 몫이라, P1-09 시점에는 통로가 없다. 또 `TickRunner.on_time_exit`는 엔진 프로세스 안의 객체라서 scheduler가 직접 부를 수 없다.

## 결정
1. **DB `settings` 표를 우편함으로 쓴다** (`engine/link.py::SettingsEngineLink`, `core/ports.py::EngineLink` Protocol).
   - `engine.heartbeat.<market>`: 엔진 타이머(`MarketEngine.on_link`)가 5초마다 UTC 시각을 쓴다.
   - `engine.cmd.<market>.time_exit.<strategy>` = `{id, ts}`: scheduler가 시간 청산을 요청한다.
   - `engine.ack.<market>.time_exit.<strategy>` = id: 명령을 처리한 쪽이 쓴다.
   전략마다 키를 따로 두어서, 한 키를 두 프로세스가 함께 고치지 않는다(명령 키는 scheduler만, ack 키는 처리한 쪽만 쓴다).
2. **정상 경로**: scheduler는 명령만 넣는다. 엔진 타이머가 그 명령을 읽어 `TickRunner.on_time_exit`를 부르고 ack한다.
3. **백업 경로**: 마지막 하트비트가 90초보다 오래됐으면 scheduler가 DB 페이퍼 계좌(`PersistentPaperBroker.restore`, 최신 1분봉 종가로 평가)로 그 전략 하나만 든 `TickRunner`를 만들어 `on_time_exit`를 직접 부르고 ack한다. 주문은 `OrderExecutor`(RiskManager.check → broker.submit)로만 나간다(불변식 #9). `engine_heartbeat`는 끊김·복구를 한 번씩만 알리고(`backup exit armed`), 엔진이 받아 두고 처리하지 못한 명령도 백업으로 처리한다.
4. **엔진 계좌 영속화**: `build_upbit_paper(sessions=...)`는 `PersistentPaperBroker` + `OrderExecutor`(원장 기록)를 쓴다. 백업 청산은 DB에 있는 계좌를 대상으로 하므로, 엔진 계좌가 DB에 있어야 두 경로가 같은 포지션을 본다.
5. **APScheduler는 `scheduler/main.py`에서만 import한다.** 잡 표는 선언형 `JobSpec`이고, 잡 저장소에는 모듈 함수 `registry.run_job(name)`만 등록한다. 미국장 잡은 America/New_York cron이라 서머타임이 자동으로 반영된다. 조기 폐장일에는 12:55 ET 변형 잡이 폐장 5분 전인지 확인한 뒤 돈다.

## 결과
- Redis 없이도 두 프로세스가 연결된다. P1-12에서 `EngineLink`를 Redis 구현으로 바꿔도 잡 코드는 그대로다.
- 명령이 처리되기까지 최대 `link_every`(5초)가 걸린다. 09:00 시가 청산 기준으로는 허용 범위다.
- 엔진이 명령을 실행한 직후, ack를 쓰기 전에 죽으면 백업이 같은 전략을 한 번 더 청산하려 한다. `on_time_exit`는 열린 포지션만 청산하므로 두 번째 호출은 주문을 내지 않는다.
- 부품이 아직 없는 잡(KRX 종가 주문, ORB 진입, GEM, KIS 토큰, 사전 심사, 아침 브리핑, reconcile)은 등록만 하고, 본문이 연결되기 전까지는 로그만 남기고 건너뛴다(`JobContext.hooks`). 뉴스 수집기는 피드 목록·NewsRepo 구현이 준비되면 `build_context`에 붙인다.
- 01 §2의 반대 방향 이중화("scheduler가 죽으면 engine 자체 백업 타이머로 청산")는 이 결정의 범위 밖이다.
