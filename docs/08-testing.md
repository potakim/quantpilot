# 08 · 테스트 전략

## 1. 피라미드

| 층 | 대상 | 데이터 | 실행 |
| --- | --- | --- | --- |
| 단위 | 전략 규칙, 비용 모델, 지표, 리스크 규칙, 게이팅, 파서 | 합성(`data/synthetic.py`), `seed_of(symbol)` 고정 seed | `pytest` 기본, < 30초 |
| 불변식 | CLAUDE.md의 10개 불변식 각각에 테스트 1개 이상 | 합성 | `pytest -m invariant` (기본 포함) |
| 통합 | TickRunner + PaperBroker + StubJudge + 인메모리 repo로 하루치 재생 | 캐시된 실데이터 스냅샷(`tests/fixtures/*.parquet`, 커밋) | `pytest -m integration` |
| 계약 | 브로커·판단 모델 어댑터가 실제 API 응답 샘플을 올바르게 파싱 | `tests/fixtures/api/*.json` (실응답 저장본) | 기본 포함 |
| 네트워크 | 실제 업비트·KIS 모의·TypeSafe 호출 | 실 API | `pytest -m network` (수동, CI 제외) |
| 관문 | G1~G4 스크립트 | 실데이터 | 수동 |

## 2. 불변식 테스트 목록 (1단계에서 채운다)

| # | 불변식 | 테스트 |
| --- | --- | --- |
| 1 | 전략은 Target만 | `test_strategy_returns_only_targets` — REGISTRY 전체 on_bar 반환 타입 검사 (기존 `test_every_registered_strategy_runs` 확장) |
| 2 | 같은 on_bar | `test_backtest_and_tickrunner_same_fills` — 같은 데이터로 Backtester와 TickRunner+PaperBroker 체결 동일 |
| 3 | 룩어헤드 | `test_<strategy>_no_lookahead_on_entry_day` — 현재 봉 close 변조 시 Target 불변 (vol_breakout 완료, orb 추가) |
| 4 | 비용 0 금지 | `test_zero_cost_requires_explicit_flag` (완료) |
| 5 | 홀드아웃 | `test_holdout_cuts_last_12_months` (완료) + `test_unlock_holdout_once_per_strategy` (API) |
| 6 | RiskRules 불변 | `test_risk_rules_frozen` — dataclass frozen + API PATCH `/settings`에 risk 키 거부 |
| 7 | JudgeResult에 수량 없음 | `test_judge_result_has_no_sizing_fields` — answers 키가 질문 세트 키의 부분집합 |
| 8 | 하드블록·합의 우선 | `test_hard_block_overrides_confidence`, `test_llm_consensus_requires_all` (완료) |
| 9 | 주문은 risk.check 경유 | `test_executor_calls_risk_before_broker` — mock 순서 검사 |
| 10 | 키 유출 없음 | `test_no_secret_in_logs_or_responses` — 로그 캡처·API 응답에 키 패턴 grep |
| — | AI → execution import 금지 | `test_no_ai_to_execution_import` — `judgment/`·`features/` 소스에서 `execution` import grep |

## 3. 전략 테스트 규칙

- 각 전략에 **규칙이 발동하는 최소 시나리오**를 손으로 만든 5~30봉 fixture로 검증한다(예: 변동성 돌파 — 고가가 목표가에 정확히 닿는 봉, 안 닿는 봉, 이평 스코어 0인 봉).
- 파라미터 경계값(min·max)에서 예외 없이 실행.
- 월간 전략: 월말 봉에서만 Target을 내고 그 외 봉에서는 빈 리스트.
- 청산 우선: 같은 봉 exit+entry 순서.

## 4. 백테스터 검증

- 매수보유 1종목: 총수익 = 가격 변화 − 비용 (완료).
- 알려진 수치 재현: 20일 이평 교차 전략을 SPY 일봉 fixture로 돌려 vectorbt 결과와 ±0.1% 이내 (1단계, vectorbt 탐색기 도입 시).
- 슬리피지 민감도: 슬리피지 0→0.1% 증가 시 ORB 수익률이 단조 감소.
- 독립 기준 구현 대조: `backtest/reference.py`(전략·엔진 코드 미사용, 05 규칙만)의 변동성 돌파를 엔진과 같은 데이터로 돌려 진입 횟수 일치·자산 곡선 상대오차 1e-9(합성, 상장 시점이 다른 심볼 포함, `tests/test_reference.py`, ADR 0026), 실데이터는 G1에서 CAGR·MDD ±10% (ADR 0024).

## 5. 페이퍼 A/B 설계 (관문 G2)

- 기간 4주, 대상 vol_breakout 5코인.
- 그룹: ON(게이팅), OFF(섀도). 같은 신호·같은 시세·같은 리스크 규칙.
- 지표: MDD(주), 수익률, 거래 수, 판단 모델 Brier·ECE, hold된 신호의 사후 수익률(“안 산 게 맞았나”).
- 판정: n ≥ 20이고 MDD_ON < MDD_OFF이고 Brier < 0.25 → 통과. 아니면 4주 연장 또는 질문 세트 v2.
- 리포트: `qp report ab --weeks 4` → 마크다운 + 화면 `/judgments/ab`.

## 6. 실계좌 전 체크리스트 (관문 G3·G4 보조)

- [ ] 모의에서 주문 거부·부분 체결·취소 시나리오 각 1회 이상 발생하고 정상 처리됨
- [ ] 토큰 만료·WS 끊김을 강제로 일으켜 복구 확인
- [ ] 엔진 강제 종료 후 재시작 → Reconciler 통과
- [ ] 서킷브레이커를 인위적으로(월초 equity 조작) 발동시켜 진입 차단·청산 허용 확인
- [ ] 알림 3등급 모두 수신 확인
- [ ] 백업에서 DB 복원 리허설 1회

## 7. CI

GitHub Actions: `ruff` → `pytest -m "not network"` → 이미지 빌드. PR은 CI 통과 + 문서 변경 시 `docs/` diff 포함 여부 확인(설계 변경 PR인데 문서가 없으면 라벨 `needs-adr`).
