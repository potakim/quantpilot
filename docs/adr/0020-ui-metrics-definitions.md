# 0020 · 화면 지표 정의: 오늘 손익·자산 곡선·오늘 일정·전략 손익·주문 수수료

상태: 승인 (2026-10-01)

## 맥락
P1-13(t15) 화면은 API가 아직 주지 않는 값을 "—"로 두었다. 대시보드의 오늘 손익·자산 곡선·오늘 일정, 전략 표의 이번 달 손익·MDD, 주문 패널의 수수료가 그렇다. 값은 이미 DB에 있다(`equity_snapshots`, `fills`, `candles`, scheduler 잡 표, CostModel 프리셋). 다만 "오늘"의 기준 시각, 곡선의 해상도, 전략별 자본 기준처럼 정하지 않으면 화면마다 다르게 계산될 정의가 남아 있다. 이 ADR은 그 정의를 고정한다. 전략·주문 경로·RiskRules·scheduler/realtime 배선은 바꾸지 않는다.

## 결정
1. **오늘 손익** (`GET /portfolio`의 `by_market.<m>.today_pnl`, `today_pnl_krw`). 기준값은 **시장 현지 자정 이후 첫 `equity_snapshots` 행**이다(업비트는 KST 00:00, 미국은 뉴욕 00:00). 자정 경계는 `core/clock.py`의 `to_local`로만 구한다. 손익 = 지금 평가액(`/portfolio`가 쓰는 같은 계좌) − 기준값, 비율 = 손익 ÷ 기준값. 오늘 스냅샷이 없으면 null이다. `today_pnl_krw`는 값이 있는 시장의 합이며, 미국은 환율이 있을 때만 원화로 바꿔 더한다. 하나도 없으면 null이다.
2. **자산 곡선** (`GET /portfolio/equity?market=&days=`). 출처는 `equity_snapshots`(1분)이다. days는 30·90·365만 받는다. 30일은 1시간, 90·365일은 하루(현지 자정 경계) 단위로 묶어 묶음마다 마지막 값 하나를 쓴다. 벤치마크는 같은 시작 자본으로 BTC를 들고만 있었을 때다: `v₀ × close_t ÷ close₀`. close는 업비트 KRW-BTC 1분봉을 각 점 시각에 맞춰 직전 종가로 붙인다. 업비트 외 시장이거나 첫 점 이전 종가가 없으면 null이다. 스냅샷이 없으면 오류가 아니라 `points: []`다.
3. **전략별 이번 달 손익·MDD** (`/strategies`의 `status.month_pnl`, `status.mdd_30d`). 전략 체결(섀도 원장 제외)을 처음부터 재생하고 1시간 종가로 평가한다(`judgment/ab.py`의 `equity_curve`·`book_stats`, `qp report ab`와 같은 방법). 자본 기준은 **allocation × 그 달 `month_start_equity.<market>`**, 월초 값이 없으면 allocation × 초기 자금(`initial_cash_krw`/`usd`)이다. `month_pnl`은 시장 현지 월초부터 지금까지의 수익률, `mdd_30d`는 최근 30일의 최대 낙폭(양수 비율)이다. 그 구간에 체결이 없거나, 체결 심볼의 봉이 하나도 없거나, allocation이 0이면 null이다. 시장마다 체결 조회 1회·심볼마다 봉 조회 1회로 끝낸다.
4. **오늘 일정** (`GET /schedule`). scheduler 잡 표(`scheduler/registry.py`의 `JOBS`)에서 `hour`가 정해진 cron 잡만 골라, 오늘(KST 00:00~24:00)에 걸리는 실행 시각을 계산한다. cron 해석은 직접 한다(APScheduler는 `infra` 의존성이라 api 기본 설치에 없다). KRX·미국 잡은 그 시장의 휴장일이면 뺀다. 조기 폐장용 변형 시각(12:55 ET)은 정규 시각과 겹쳐 보이므로 싣지 않는다. 매시·매분(interval) 잡은 일정에서 뺀다. 변동성 돌파 항목은 scheduler 잡 `publish_breakout_status`와 **같은 함수**(`scheduler/jobs/exits.py`의 `breakout_targets`)로 목표가를 읽기 전용 계산해 싣는다(가장 최근 09:00 기준). 1분봉이 없으면 "목표가 계산 불가"로 둔다. 이 엔드포인트는 이벤트를 발행하지 않고 scheduler·realtime 배선을 바꾸지 않는다. 화면은 이 REST 결과를 쓰고, WS `strategy.status`의 `next_action`이 오면 덧붙인다.
5. **주문 수수료** (`GET /quotes/{m}/{s}`의 `fee_rate`·`tax_rate_sell`). 백테스터·PaperBroker가 쓰는 `backtest/costs.py` 프리셋 값을 그대로 낸다(불변식 #4의 같은 CostModel). 화면은 "수수료 0.05% ₩금액"(= 요율 × 예상 주문 금액, 원화는 정수 원)으로 보인다. 수동 주문의 손절 행은 두지 않는다.
6. **검증 실패는 400 `INVALID_PARAM`이다.** `days`가 30·90·365가 아니면 03 §1 규칙대로 400을 낸다. 422는 `RISK_REJECTED` 전용으로 남긴다.

## 결과
- 화면의 "—"는 데이터가 없을 때만 남는다. 보이는 문자열은 `web/lib/metrics.ts`의 순수 함수가 만들고, null이면 "—"를 낸다.
- `equity_snapshots`는 지금 **업비트만** 쌓인다. scheduler `JobContext.markets` 기본값이 업비트 하나여서다. KRX·미국의 오늘 손익과 자산 곡선은 그 시장 스냅샷이 쌓이기 시작하면 별도 변경 없이 채워진다.
- 전략별 손익은 체결 재생 근사다. 같은 심볼을 여러 전략이 들고 있어도 체결의 `strategy`로 나누므로 계좌 평가액과 정확히 일치하지 않는다. 1시간 종가로 평가해 시간 내 낙폭은 놓칠 수 있다.
- 오늘 일정의 실행 시각은 잡 표에서 계산한 예정 시각이지 실제 실행 기록이 아니다. `done`은 "예정 시각이 지났다"는 뜻이다. 잡이 hook 미연결로 건너뛰어도 `done:true`로 보인다.
- 대시보드의 "규칙 미충족 n건"은 이번에 넣지 않는다. 진입 규칙 검사 결과를 남기는 기록자가 엔진에 없어서다. 엔진 쪽 기록자가 생긴 뒤 별도 카드로 한다. → ADR 0027에서 엔진 집계와 `GET /judgments`의 `rule_unmet`으로 넣었다.
