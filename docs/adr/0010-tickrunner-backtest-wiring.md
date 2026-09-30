# 0010 · 백테스터를 TickRunner로 돌릴 때의 배선: 규칙 미적용 게이트·게이팅 OFF·매도 수량 캡

상태: 승인 (2026-09-30)

## 맥락
ADR 0002에 따라 P1-04에서 `Backtester.run`의 인라인 루프를 없애고 `TickRunner + PaperBroker + StubJudge + 가짜 clock`으로 돌린다. 모든 주문은 `RiskManager.check → BrokerAdapter.submit` 순서를 탄다(불변식 #9). 그런데 그대로 배선하면 두 가지가 부딪친다.

1. **0단계 수치가 크게 바뀐다.** 0단계 백테스터는 RiskManager를 적용하지 않았다(05 문서 변동성 돌파·ORB 절). 실제 `RiskManager`를 걸면 종목당 25% 상한 때문에 한 종목에 100%를 싣는 GEM은 사실상 다른 전략이 된다. `StubJudge`도 피처가 없으면 확신도 0.75 → `HALF`(0.5배 사이징)라서 모든 진입이 절반이 된다.
2. **0단계 엔진에 과매도 버그가 있다.** 매도 수량을 `|목표가치 − 보유가치| / 체결가`로 계산하는데, 매도 체결가는 슬리피지만큼 기준가보다 낮다. 그래서 전량 청산이 보유량보다 `1/(1−슬리피지)`배 많이 팔고, 남은 미세한 음수 포지션을 다음 청산 target이 다시 사들인다. `PaperBroker`는 보유량을 넘는 매도를 거부하므로 같은 계산을 그대로 옮길 수 없다. 합성 데이터 8개 시나리오에서 이 먼지 체결이 전체 체결의 13~66%였다(예: 변동성 돌파 7,319건 중 3,044건).

또 04 §7의 `on_bar_closed(ev)`는 봉 1개를 받는데, 여러 심볼을 보는 월간 전략(GEM·GTAA)은 같은 시각의 봉이 다 들어온 뒤 한 번만 평가해야 한다.

## 결정
1. **`TickRunner`의 주문 경로는 하나다.** 사이징 → `risk.check` → `broker.submit`. 백테스트·페이퍼·실전 모두 같은 코드를 탄다. 달라지는 것은 주입하는 `risk`·`executor`·`pipeline`·`clock`뿐이다.
2. **백테스트 기본값은 `UnrestrictedRisk`(규칙 미적용 게이트)다.** 0단계와 같은 "전략 자체의 성과"를 잰다. 이 게이트는 `RiskRules`를 바꾸지 않고(불변식 #6과 무관), 페이퍼 브로커와만 조합할 수 있다. 실브로커(`is_paper=False`)와 묶으면 `TickRunner` 생성 시 `ValueError`. 계좌 규칙까지 적용한 결과가 필요하면 `Backtester(apply_risk=True)`로 실제 `RiskManager`를 주입한다.
3. **백테스트의 판단 파이프라인은 `StubPipeline(StubJudge(), gating=False)`다.** 진입 target마다 `StubJudge`를 실제로 호출해 판단 이벤트를 남기되 `size_multiplier`는 1.0(게이팅 OFF, 06 §6 A/B의 OFF 기준선). `gating=True`면 `decide()`의 HOLD/HALF/FULL을 적용한다.
4. **매도 수량은 보유량을 넘지 않는다** (`allow_short=False`일 때). 0단계 과매도 버그를 고친다. 이 때문에 백테스트 수치가 미세하게 바뀐다. 회귀 기준은 "0단계 엔진 + 같은 캡"이며, TickRunner 백테스트는 이 기준과 체결 단위로 일치해야 한다(`.moai/reports/t6/verdict.md`).
5. **`TickRunner.on_bars_closed(events)`를 추가한다.** 같은 시각에 마감된 봉 묶음을 한 번에 받아 전략을 한 번만 평가한다. `on_bar_closed(ev)`는 `on_bars_closed([ev])`다. 실전은 `CandleAggregator.on_timer`가 돌려주는 묶음을 넘긴다.
6. **월간 전략의 "월말 봉" 판단은 clock이 한다.** 실전 `MarketClock.is_last_session_of_month`, 백테스트 `ReplayClock`(데이터 타임라인의 월별 마지막 봉).
7. **`RiskManager`의 주문 빈도 검사는 `check(now=...)` 시각을 쓴다.** 0단계는 `time.monotonic()`이라 가짜 clock으로 빠르게 재생하면 1초에 수백 봉이 지나가 전부 거부됐다. 실전은 `now` 기본값이 현재 시각이라 동작이 같다.
8. `feature_builder`(P1-06)·`pipeline`(P1-07/08)·`executor`(P1-05)는 `core/ports.py`에 Protocol만 둔다. 이번 카드의 구현은 백테스트·페이퍼용 `StubFeatureBuilder`·`StubPipeline`·`DirectExecutor`(risk.check → submit, 재시도·원장 없음)이고 실제 구현은 각 카드 몫이다.

## 결과
- 0단계 대비 백테스트 수치가 과매도 캡만큼 바뀐다(총수익 기준 GEM +0.01%p, ORB −0.3%p 수준, 체결 수는 크게 줄어든다). 표는 verdict에 있다.
- 백테스트는 봉마다 비동기 호출·PaperBroker를 거쳐 0단계보다 느리다. 파라미터 탐색은 여전히 vectorbt 몫이다(ADR 0002).
- 백테스트는 아직 `on_stop_check`를 부르지 않는다(봉 안 체결 순서를 모르므로). 05 문서의 손절은 실전·페이퍼에서만 감시된다.
