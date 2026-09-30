# 0016 · 게이팅 OFF 섀도 원장과 A/B 리포트: 신호 미러링·shadow 칼럼·체결 재생 곡선

상태: 승인 (2026-09-30)

## 맥락
06 §6.2는 관문 G2를 판정하기 위해 같은 신호를 두 원장에 기록하라고 한다. 하나는 게이팅을 적용한 ON 원장이고, 다른 하나는 게이팅 없이 전량 페이퍼 체결하는 OFF 원장이다. OFF 원장은 `PaperBroker`를 하나 더 두고, 같은 시세와 같은 리스크 규칙으로 체결한다. 08 §5는 4주 뒤 `qp report ab --weeks 4`로 판정한다. P1-10까지의 구현에는 다음 네 가지가 없었다.

1. 섀도 계좌. `TickRunner`에는 executor가 하나뿐이다.
2. DB에서 섀도 행을 구분할 방법. 06 §6.2는 `paper_shadow=true`라는 칸을 가정했지만, 02 스키마에 그런 칸이 없다.
3. 섀도 계좌의 포지션과 현금을 저장할 곳. `positions`의 PK는 `(market, symbol, strategy)`라서 ON 포지션과 겹친다.
4. 두 원장을 같은 방법으로 잴 자산 곡선. `equity_snapshots`는 scheduler가 ON 계좌만 기록한다.

또 "표본 20건"의 표본이 무엇인지 문서에 정의되어 있지 않았다.

## 결정
1. **섀도 원장은 `TickRunner(shadow=executor)`로 붙인다.**
   - ON이 발행하는 전략 신호 하나하나를 같은 틱, 같은 기준가, 틱 시작 시점의 섀도 평가액으로 섀도 계좌에도 낸다.
   - 판단 파이프라인은 신호마다 한 번만 부른다. 섀도의 배수는 판단 결과와 무관하게 1.0이다. 이는 ADR 0010·0012·0014의 게이팅 OFF 기준선과 같다.
   - 섀도는 자기 `risk.check → submit`을 탄다(불변식 #9). 규칙은 같지만 상태는 따로 두기 위해 `RiskManager`를 별도 인스턴스로 쓴다.
   - `CostModel`은 ON과 같은 인스턴스를 공유한다(불변식 #4).
   - 섀도 executor가 실브로커이거나 ON executor와 같은 객체이면 생성할 때 `ValueError`를 낸다. 07 문서가 말하듯 게이팅 OFF는 페이퍼 섀도에만 존재한다.
2. **포지션에 따라 달라지는 규칙은 원장별로 적용한다.**
   - 손절선(`stops`), `on_stop_check`, `on_time_exit`는 원장마다 따로 돈다.
   - 섀도에만 있는 포지션의 손절·시간 청산은 섀도 원장 안에서만 처리하고 신호를 발행하지 않는다.
   - 섀도의 체결·주문은 버스에 발행하지 않는다. 화면과 알림이 실제 페이퍼 원장과 섞이지 않게 하기 위해서다.
   - `TickRunner.shadow_signals`는 섀도에 넘긴 전략 신호 수이고, ON이 발행한 전략 신호 수와 같다. 이것이 P1-11 완료 기준이다.
   - ON 할트(3단계 사전 검사)가 걸려 제거된 진입 target은 섀도로도 넘어가지 않는다. 섀도는 ON이 실제로 받은 신호만 받는다.
   - scheduler의 백업 시간 청산(ADR 0013, 하트비트가 끊겼을 때)은 ON 계좌만 닫는다. 이때 섀도 포지션은 엔진이 복구된 뒤 다음 시간 청산 명령이나 전략의 청산 신호로 닫힌다.
3. **DB: `orders`·`fills`에 `shadow boolean not null default false`를 추가한다** (마이그레이션 0002).
   - 06의 `paper_shadow`를 이 칸으로 대신한다.
   - `SqlLedger(sessions, shadow=True)`는 섀도 행만 쓰고 읽는다. 기본 `SqlLedger`는 ON 행만 본다. 따라서 일일 리뷰·Reconciler 같은 기존 소비자는 섀도 체결을 보지 않는다.
   - 섀도 executor에는 `signals`를 주지 않는다. `signals.outcome`은 ON 원장의 결과다.
4. **섀도 계좌 상태는 settings jsonb에 둔다.**
   - 현금은 `PersistentPaperBroker(book="shadow")`가 `paper.shadow.cash.<market>`에 둔다.
   - 포지션은 `SettingsPositionRepo`가 `paper.shadow.positions.<market>`에 목록 하나로 둔다.
   - `positions` 테이블의 PK는 바꾸지 않는다. 섀도 계좌는 페이퍼 A/B 기간에만 쓰이고 심볼도 몇 개뿐이라서, 목록 하나를 통째로 다시 쓰는 비용으로 충분하다.
5. **A/B 성과는 체결 재생 곡선으로 잰다** (`judgment/ab.py`).
   - 각 원장의 체결을 계좌 시작부터 재생하고, 1분봉을 1시간 종가로 줄여 평가한다. 이 곡선에서 창 `[now − weeks, now]`의 수익률, MDD(양수 비율), 체결 수, 비용을 낸다.
   - 창 시작 전의 평가액을 창의 기준값으로 쓴다.
   - `equity_snapshots`는 쓰지 않는다. 두 원장을 같은 방법으로 재기 위해서다.
6. **G2 판정의 n은 보정 표본 수로 정한다.** 보정 표본은 `realized_ret_24h`가 채워진 판단이다.
   - n < 20이거나 Brier를 계산할 수 없으면 "판정 보류"다.
   - 그 외에는 `MDD_ON < MDD_OFF`와 `Brier < 0.25`를 둘 다 만족할 때만 통과다. 수익률은 참고용이다.
7. **보정 지표는 순수 함수다** (`judgment/calibration.py`). 함수는 `brier`, `ece(bins=10)`, `bucket_hit_rates`(0.5~0.7 / 0.7~0.9 / 0.9+), `is_monotonic`, `calibration`이다.
   - p는 `confidence`가 기본이고, `answers.signal_quality`를 고를 수 있다.
   - 정답은 `direction_hit`를 쓰고, 이 값이 없으면 `realized_ret_24h > 0`으로 판단한다.
   - 보정 불량 배지는 표본이 있는 구간의 적중률이 확신도를 따라 내려갈 때 붙는다. 같은 값이면 불량으로 보지 않는다.
   - 이 모듈은 AI에게 아무것도 묻지 않는다(불변식 #7).

## 결과
- `shadow=None`이면 `TickRunner`의 동작은 전과 같다. 백테스트는 섀도를 붙이지 않으므로 0단계·ADR 0010 수치가 바뀌지 않는다.
- 페이퍼 엔진(`build_upbit_paper`)은 섀도를 항상 함께 둔다. 판단 모델이 stub이면 ON도 게이팅 OFF이므로 두 원장이 같게 움직인다.
- 엔진은 아직 `signals`·`judgments` 행을 쓰지 않는다. 이벤트는 P1-12 허브 전까지 로그로만 남는다. 따라서 보정 지표와 진입 신호 수는 판단 기록이 DB에 쌓인 뒤부터 의미가 있다. 기록 경로를 누가 만들지는 별도 카드로 다룬다.
- `/judgments/calibration`·`/judgments/ab` API는 P1-12에서 `calibration()`·`ab_report()`를 그대로 감싼다.
