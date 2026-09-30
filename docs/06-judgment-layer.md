# 06 · 판단 계층

## 1. 역할 경계

| 계층 | 하는 일 | 절대 하지 않는 일 |
| --- | --- | --- |
| 판단 모델 (Jev / Laya / Kev) | 원자 질문에 확률·확신도 즉답. 국면 분류, 뉴스 위험도, 신호 품질 | 계산(수량·가격·손절), 카운팅, 날짜 비교 ("Jev is not a calculator") |
| LLM 리뷰어 (Claude, Gemini) | 후보 신호에 approve/hold + 한 줄 이유. 뉴스·공시 맥락 해석. 사후 리뷰·질의응답 | 신호 생성, 파라미터 변경, 리스크 규칙 접근 |
| 코드 | 지표·신호·수량·게이팅 규칙·주문·원장·보정 지표 | — |

## 2. 원자 질문 세트 (v1)

`judgment/base.py::DEFAULT_QUESTIONS`. 한 호출에 6개를 묶어 보낸다.

| key | kind | 옵션 / 범위 | 코드가 쓰는 곳 |
| --- | --- | --- | --- |
| `regime` | choice | trend_up / range / trend_down | 로그·리뷰 (v1에서는 게이팅에 미사용, v2에서 range면 돌파 전략 절반) |
| `news_risk` | score | 0~1 | **hard block** > 0.5 |
| `liquidity_stress` | score | 0~1 | 로그. v2: > 0.7이면 시장가 → 지정가 |
| `event_ahead` | score | 0~1 | **hard block** > 0.5 |
| `already_priced` | score | 0~1 | 로그·리뷰 |
| `signal_quality` | score | 0~1 | 로그. v2: 확신도와 곱해 사이징 |

`confidence`: 프로바이더가 주는 값. TypeSafe는 질문별 confidence를 주므로 **최솟값**을 채택(보수적). 임계값 기본 0.5 / 0.9 (TypeSafe 가이드), 설정 `gate.hold_below`, `gate.full_above`로 0.3~0.7 / 0.7~0.98 범위에서 조정 가능.

score 질문은 TypeSafe 형식상 `criteria` 단계(0..N-1)의 기댓값으로 돌아오므로, 코드가 `score / (N-1)`로 0~1로 바꿔 위 표의 범위를 맞춘다(v1은 5단계, ADR 0012).

질문 문구는 `judgment/questions/v1.yaml`로 빼고 `prompt_hash`를 판단 로그에 남긴다(`JudgeResult.raw["prompt_hash"]` → `judgments.state["prompt_hash"]`, ADR 0014). 문구를 바꾸면 v2로 올리고 4주간 v1과 병행 기록해 Brier를 비교한 뒤 교체.

## 3. state 스키마

```
market: upbit KRW-ETH
strategy: vol_breakout
signal: breakout k=0.5, +0.6% above target
vol_pctl_20d: 78 · ma_score: 0.75 · volume_ratio: 2.1x · spread_bps: 2
dist_from_high_20d_pct: -3.1 · rsi2: 71
news_24h: "현물 ETF 순유입 2일 연속, 규제 이슈 없음" | "..."
events_24h: none
```

- 400토큰 이내. 숫자는 등급·백분위·소수 1자리. 원시 가격·수량은 넣지 않는다(모델이 계산하려 들지 않도록).
- 뉴스는 요약문만. 원문 링크·제목은 로그에만.
- 같은 state는 같은 답을 내야 한다(temperature 0 / 판단 모델은 결정적). 테스트: 같은 state 2회 호출 answers 동일.

## 4. 게이팅 규칙 (코드, `judgment/base.py::decide`)

```
blocks = [news_risk > 0.5, event_ahead > 0.5]
gate   = HOLD (conf < 0.5) | HALF (0.5 ≤ conf < 0.9) | FULL (conf ≥ 0.9)
llm_ok = all(approve)                       # 2/2 필요. 타임아웃 = hold
proceed = not blocks and gate != HOLD and llm_ok
size_multiplier = 0 | 0.5 | 1.0
```

전략별 적용:

| 전략 | 판단 모델 | LLM 합의 | 합의 실패 시 |
| --- | --- | --- | --- |
| vol_breakout (1분 반응) | 진입마다 | 08:10 사전 심사만 (코인별 하루 1회) | 당일 그 코인 제외 |
| orb (5분) | 진입마다 | 진입마다 | hold |
| gem, gtaa (월간) | 리밸런싱마다 | 리밸런싱마다 | **현 포지션 유지** (현금화 아님) |
| 모든 청산 | 없음 | 없음 | — |

## 5. LLM 리뷰 프롬프트 계약

시스템 프롬프트(요지, 전문은 `judgment/prompts/review_v1.md`):

> 당신은 규칙 기반 퀀트 전략의 진입 후보를 검토하는 리스크 검토자다. 매매 신호를 만들지 말고, 아래 후보를 **막아야 할 이유**가 있는지만 판단하라. 근거는 제공된 state·뉴스 요약·판단 모델 답변에 한정한다. 출력은 JSON `{"approve": bool, "reason": "<80자 이내 한국어>"}` 뿐이다.

- 입력: `state.render()`, `judge.answers`, `judge.confidence`, 전략 규칙 한 줄.
- 출력 파싱 실패·호출 오류·거부(refusal)·30초 타임아웃 → 그 모델 hold. 응답 길이 제한 200토큰. Gemini는 temperature 0. Claude Sonnet 5는 샘플링 인자를 받지 않아 JSON 스키마 구조화 출력 + thinking disabled로 고정한다 (ADR 0014).
- 리뷰 프롬프트의 `prompt_hash`는 `llm_verdicts.prompt_hash`에, 판단 모델 질문 문구의 `prompt_hash`는 `judgments.state`의 `prompt_hash` 키에 남긴다 (ADR 0014).
- 두 모델에 같은 프롬프트. 모델별 프롬프트 차이를 두지 않는다(합의의 의미).
- 사후 리뷰(`daily_review`)는 별도 프롬프트: 오늘 원장·판단 로그를 받아 "무엇이 맞았고 무엇이 틀렸는지, 규칙 위반은 없었는지" 500자. 파라미터 변경 제안은 하되 **적용은 사람이**.
- 질의응답(`/judgments/{id}/ask`): 해당 판단의 state·answers·verdicts·체결만 컨텍스트로. 다른 종목·계좌 정보는 넣지 않는다.

## 6. 보정 지표와 A/B

### 6.1 보정

- 정답 정의: 진입 후보의 **24시간 후 방향**(진입가 대비 close 상승 = 1). 판단 모델의 `signal_quality` 또는 `confidence`를 예측 확률 p로.
- Brier = mean((p − y)²). 기준선 0.25(동전). G2 조건 < 0.25.
- ECE = Σ |avg_conf − hit_rate| × n_bucket / N, 10구간.
- 화면: 확신도 구간별(0.5~0.7, 0.7~0.9, 0.9+) 적중률 막대. 구간별 적중률이 단조 증가하지 않으면 "보정 불량" 배지.
- `realized_ret_24h`는 판단 24시간 후 `scheduler.fill_realized_24h`가 채운다. hold된 신호도 채운다(그때 안 산 것이 맞았는지 보기 위해).

### 6.2 게이팅 ON/OFF A/B (관문 G2)

- 같은 신호를 두 원장에 기록: ON(실제 게이팅 적용, 페이퍼 체결), OFF(게이팅 없이 전량 페이퍼 체결, `orders`·`fills.shadow=true`). 배선은 `TickRunner(shadow=...)` (ADR 0016).
- OFF 원장은 `PaperBroker` 인스턴스를 하나 더 두고 같은 시세로 체결. 리스크 규칙은 동일 적용.
- 비교: 4주 수익률, MDD, 거래 수, 비용. G2 = ON의 MDD < OFF의 MDD. 수익률은 참고(ON이 낮아도 통과 — 목표는 방어).
- 표본(실현 수익률이 채워진 판단)이 20건 미만이면 "판정 보류". 수익률·MDD는 원장별 체결을 재생한 자산 곡선으로 잰다 (ADR 0016).

## 7. 프로바이더 교체와 폴백

| 상황 | 동작 |
| --- | --- |
| TypeSafe 3초 타임아웃 | 그 신호 hold. 연속 10회 → `risk_events(judge_down)` + 알림 (횟수는 `TypeSafeJudge.consecutive_timeouts`) |
| TypeSafe HTTP 오류·응답 계약 위반 | 그 신호 hold (`JudgeError`, 확신도 0으로 기록. ADR 0012) |
| TypeSafe 장기 장애 | 설정 `judge.provider=laya`로 전환 (Laya는 파인튜닝판만 허용; zero-shot은 다수클래스 기준선 미만이라 사용 금지) |
| LLM 한쪽 장애 | 2/2 요구 유지 → 사실상 진입 중단. 설정 `llm.require_all=false`로 임시 완화 가능하되 risk_events에 기록 |
| 비용 상한 | `settings.ai_budget_usd_daily`(기본 $2) 초과 시 LLM 합의 중단(= hold), 판단 모델은 계속 |

## 8. 로컬 모델 원장으로 Laya 파인튜닝 (2단계 이후)

`judgments` 테이블의 (state, answers, realized_ret_24h)가 학습 데이터다. 1,000건 이상 쌓이면 Laya typed-decisions 체크포인트를 파인튜닝해 Jev와 Brier를 비교하고, 동등하면 비용·지연 이점으로 교체를 검토한다.
