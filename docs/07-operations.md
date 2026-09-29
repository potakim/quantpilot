# 07 · 운영

## 1. 환경

| 환경 | 용도 | 브로커 | 데이터 |
| --- | --- | --- | --- |
| `dev` (로컬 PC) | 개발·테스트 | PaperBroker, 합성·캐시 데이터 | 로컬 Docker(Timescale·Redis) 또는 SQLite 폴백 |
| `paper` (VPS) | 1~2단계 상시 페이퍼 | PaperBroker, KIS 모의, Alpaca paper | 실시간 |
| `live` (VPS, 같은 머신 다른 compose 프로젝트) | 3단계 소액 실전 | UpbitBroker(실), KIS(실) | 실시간 |

`paper`와 `live`는 같은 VPS에서 별도 compose 프로젝트(`-p qp-paper`, `-p qp-live`)로 띄운다. DB도 분리. 실전 엔진은 `QP_PAPER=false`와 `QP_LIVE_CONFIRM=<날짜>` 두 값이 모두 있어야 시작한다.

## 2. VPS 사양과 배포

- Ubuntu 24.04, 2 vCPU / 4 GB / 40 GB SSD, **고정 IP**(업비트 허용 IP 등록). 리전은 서울(업비트·KIS 지연 최소).
- Docker Compose. 이미지는 GitHub Actions에서 빌드해 GHCR로 push, VPS에서 `docker compose pull && up -d`.
- 배포 순서: `api` → `scheduler` → `engine`. 엔진은 **포지션이 없는 시간대**(KST 09:05~15:15 사이는 KRX 보유 중일 수 있으니 피하고, 20:00~22:00 권장)에만 재시작. 배포 스크립트가 `positions` 비어 있는지 확인하고 아니면 `--force` 요구.
- 롤백: 이전 이미지 태그로 `up -d`. DB 마이그레이션은 항상 하위 호환(컬럼 추가만, 삭제는 2배포 뒤).

## 3. 환경변수

`.env.example` 참조. 운영 추가:

| 변수 | 설명 |
| --- | --- |
| `QP_ENV` | dev / paper / live |
| `QP_PAPER` | true면 모든 브로커가 페이퍼 |
| `QP_LIVE_CONFIRM` | live 시작 승인 날짜(YYYY-MM-DD). 7일 지나면 재승인 필요 |
| `QP_DATABASE_URL`, `QP_REDIS_URL` | 연결 |
| `QP_ADMIN_PASSWORD` | 화면 로그인 · 2차 확인 |
| `QP_JWT_SECRET` | 32바이트 이상 |
| `QP_TELEGRAM_BOT_TOKEN`, `QP_TELEGRAM_CHAT_ID` | 알림 |
| `QP_AI_BUDGET_USD_DAILY` | 기본 2 |
| `QP_UPBIT_*`, `QP_KIS_*`, `QP_ALPACA_*`, `QP_TYPESAFE_API_KEY`, `QP_ANTHROPIC_API_KEY`, `QP_GOOGLE_API_KEY` | 외부 키 |

키는 VPS의 `/opt/quantpilot/.env`(권한 600)에만. 화면의 "설정 > API 키"는 `.env`를 쓰지 않고 DB `settings`에 암호화(Fernet, 키는 `QP_SECRET_KEY`) 저장하며, 값은 절대 반환하지 않고 "등록됨/미등록"만 보여준다. 업비트 키는 **출금 권한 제외**, KIS는 모의·실전 앱키 분리.

## 4. 스케줄 (KST)

04 문서 §8 표 그대로. 여기에 운영 잡 추가:

| 잡 | 시각 | 동작 |
| --- | --- | --- |
| `db_backup` | 03:30 | `pg_dump` → 오브젝트 스토리지(30일 보관) |
| `log_rotate` | 03:40 | 14일 |
| `cost_report` | 매일 20:35 | AI 비용·거래 비용 일일 합계 알림 |
| `weekly_gate_report` | 월 08:35 | 관문 G1~G4 상태 알림 |

## 5. 모니터링

- 헬스: `/health`를 외부 업타임 모니터(1분)로. `engine_alive`가 false면 알림.
- 메트릭(Prometheus, 2단계): 틱 지연, 판단 모델 지연·타임아웃율, 주문 오류율, WS 재접속 수, AI 비용.
- 로그: JSON 라인, `docker logs` + 파일. 레벨: 주문·체결·리스크 이벤트는 INFO, 판단 결과는 INFO(요약)·DEBUG(state 전문).
- 대시보드 상단 상태 표시: 브로커 연결, 시세 지연(마지막 체결 후 초), 할트 여부, 오늘 AI 비용.

## 6. 알림 등급

| 등급 | 예 | 채널 |
| --- | --- | --- |
| info | 체결, 일일 리뷰, 비용 리포트 | 텔레그램 |
| warning | WS 재접속, 판단 모델 타임아웃 누적, 관문 미달 | 텔레그램 |
| critical | 청산 실패, 서킷브레이커, API 할트, 정합 불일치, 엔진 다운 | 텔레그램 5분 반복 + 이메일 |

## 7. 런북

### 7.1 엔진이 죽었다 (`engine_alive=false`)

1. `docker compose logs engine --tail 200`으로 원인 확인.
2. 보유 포지션 확인(`/positions`). 있으면 `scheduler`가 시간 청산 백업 모드로 처리하는지 확인(알림에 "backup exit armed").
3. `docker compose restart engine`. 재시작 시 `Reconciler`가 브로커와 대조. 불일치면 할트 상태로 뜬다 → 7.3.

### 7.2 서킷브레이커 발동 (`monthly_loss`)

- 자동: 신규 진입 중단, 청산은 정상. 다음 달 1일 자동 해제.
- 사람이 할 일: 원인 리뷰(일일 리뷰 + 판단 로그). **수동 해제 기능은 없다.** 정말 필요하면 코드 상수를 바꾸는 배포가 필요하고 그것이 의도다.

### 7.3 정합 불일치 (`reconcile_mismatch`)

1. 화면 "포지션 > 브로커 대조"에서 차이 확인(수동 거래, 부분 체결 누락, 상장폐지 등).
2. 원인이 시스템 밖(수동 거래)이면 "브로커 기준으로 맞추기" → DB 갱신 → 할트 해제.
3. 원인이 시스템 안(체결 누락)이면 `orders`·`fills` 수기 보정 후 버그 이슈.

### 7.4 KIS 토큰 갱신 실패

- 자동: 국내·미국 전략 당일 휴무 플래그, 보유분은 기존 토큰 만료 전 청산 시도.
- 사람: KIS 포털에서 앱키 상태 확인(1일 발급 한도, 앱 만료). 재발급 후 `settings/keys` 갱신 → `scheduler`가 다음 시도.

### 7.5 판단 모델 장애 (`judge_down`)

- 자동: 진입만 중단(hold), 청산 정상.
- 사람: TypeSafe 상태 확인. 장기화면 `judge.provider=laya`(파인튜닝판 있을 때만) 또는 그대로 대기. **판단 계층을 끄고 진입하는 옵션은 없다** — 게이팅 OFF는 페이퍼 A/B 섀도 원장에만 존재.

### 7.6 거래소 점검·장애

- 업비트 점검 공지는 `events.py`에 등록 → 그 시간 진입 금지. 예고 없는 장애는 WS 30초 무응답 → 진입 중단, REST 폴링.
- 보유 중 장애가 길어지면 알림만. 손절가 이탈은 복구 후 첫 체결가에 처리.

### 7.7 실전 전환 절차 (3단계)

1. `/reports/gates`에서 G1·G2 통과 확인. 화면 "실전 전환" 버튼 활성.
2. 업비트 실계좌 API 키(출금 권한 없음, 허용 IP 등록) 등록.
3. 자본 배정: 실전은 총 자본의 10% 이하로 `allocation` 설정. 나머지는 페이퍼 유지.
4. 버튼 → 비밀번호 재입력 → `QP_LIVE_CONFIRM`에 오늘 날짜 → `qp-live` compose 기동.
5. 첫 3거래일은 `max_weight`를 절반으로(설정), 매일 리뷰 확인.

## 8. 비용 예산 (월)

| 항목 | 예상 |
| --- | --- |
| VPS 2 vCPU/4 GB | 2~3만 원 |
| 판단 모델(Jev) | $1 미만 |
| LLM(Claude·Gemini) | $5~10 (일 $0.3) |
| 시세 데이터 | 0 (업비트·KIS 무료, Alpaca Basic 무료). 미국 1분봉 과거분 필요 시 Massive $29 |
| 합계 | 5만 원 이내 |
