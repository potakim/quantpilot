# 07 · 운영

## 1. 환경

| 환경 | 용도 | 브로커 | 데이터 |
| --- | --- | --- | --- |
| `dev` (로컬 PC) | 개발·테스트 | PaperBroker, 합성·캐시 데이터 | 로컬 Docker(Timescale·Redis) 또는 SQLite 폴백 |
| `paper` (VPS) | 1~2단계 상시 페이퍼 | PaperBroker, KIS 모의, Alpaca paper | 실시간 |
| `live` (VPS, 같은 머신 다른 compose 프로젝트) | 3단계 소액 실전 | UpbitBroker(실), KIS(실) | 실시간 |

`paper`와 `live`는 같은 VPS에서 별도 compose 프로젝트(`-p qp-paper`, `-p qp-live`)로 띄운다. DB도 분리.

- **지금(1단계)**: `live`는 아직 없다. `deploy/compose.yml`은 `qp-paper` 하나이고 `QP_PAPER=true`를 못박는다. 엔진·scheduler는 `QP_PAPER=false`면 시작을 거부한다. API는 계좌를 읽는 요청(`/portfolio`·수동 주문·청산)에 503 `LIVE_ACCOUNT_MISSING`을 돌려준다(t43).
- **3단계 예정**: 실전 엔진은 `QP_PAPER=false`와 `QP_LIVE_CONFIRM=<날짜>` 두 값이 모두 있어야 시작한다. `QP_LIVE_CONFIRM`은 아직 읽는 곳이 없다.

## 2. VPS 사양과 배포

- Ubuntu 24.04, 2 vCPU / 4 GB / 40 GB SSD, **고정 IP**(업비트 허용 IP 등록). 리전은 서울(업비트·KIS 지연 최소).
- Docker Compose. 이미지는 GitHub Actions에서 빌드해 GHCR로 push, VPS에서 `docker compose pull && up -d`.
- 배포 순서: `api` → `scheduler` → `engine`. 엔진 재시작 규칙(ADR 0029):
  - 업비트는 재시작해도 포지션·손절선·오늘 처리 표시를 복원한다(ADR 0028). 그래서 열린 포지션이 있어도 알림만 남긴다.
  - 업비트 시간 청산 앞뒤(매일 KST 08:55~09:05)에는 재시작하지 않는다.
- 전략 설정 화면에서 바꾼 켜기/끄기·배분·파라미터·확신도 임계값은 엔진이 5초 안에 반영한다. 판단 모델·LLM 리뷰어는 저장 후 engine을 재시작해야 적용되고, 화면이 "재시작 후 적용"으로 알려 준다 (ADR 0032).
  - 권장 시각은 KST 09:10~10:00이다(청산 직후라 포지션이 가장 적다).
  - KRX 장중(평일 09:05~15:15) 차단과 KRX·미국 포지션 차단은 그 시장 엔진이 생기는 2단계부터 적용한다.
  - 막히면 `--force`가 필요하다.
- 롤백: 이전 이미지 태그로 `up -d`. DB 마이그레이션은 항상 하위 호환(컬럼 추가만, 삭제는 2배포 뒤).
- 구성 파일: `deploy/compose.yml`(프로젝트 `qp-paper`), `scripts/deploy.sh`, `scripts/backup.sh`. 결정 배경은 ADR 0019.

### 2.1 처음 설치 (VPS)

```bash
sudo mkdir -p /opt/quantpilot/backups && cd /opt/quantpilot
git clone https://github.com/potakim/quantpilot.git app
cp app/.env.example .env && chmod 600 .env       # 키·POSTGRES_PASSWORD 채우기
echo "$GHCR_TOKEN" | docker login ghcr.io -u potakim --password-stdin   # 비공개 패키지면
app/scripts/deploy.sh --dry-run                  # 실행할 명령만 확인
app/scripts/deploy.sh                            # pull → db·redis → migrate → api → scheduler → engine
curl -s 127.0.0.1:8000/api/v1/health             # ok·paper=true·engine_alive 확인
```

- api 헬스체크는 응답의 `ok`를 본다. `/api/v1/health`는 DB·redis가 실패해도 200에 `ok:false`·`error`로 답하므로, 헬스체크가 `ok`를 봐야 DB·redis 없이 `deploy.sh`가 다음 단계로 넘어가지 않는다(api 단계에서 멈춤 → `docker compose ... logs api`).

- `POSTGRES_PASSWORD`는 DB URL에 그대로 들어가므로 URL에 안전한 문자로 만든다(예: `openssl rand -hex 24`). `QP_JWT_SECRET`은 32바이트 이상.
- compose가 `QP_PAPER=true`를 못박는다. env 파일 값으로 실전으로 바뀌지 않는다.
- db·redis는 호스트 포트가 없고 api는 `127.0.0.1:8000`에만 열린다. 밖에서 볼 때는 `ssh -L 8000:127.0.0.1:8000 vps` 또는 HTTPS 앞단(후속 카드).
- 업데이트: `app/scripts/deploy.sh --tag <커밋 40자 sha>`(기본 `latest`). CI는 main에 머지된 커밋만 이미지를 올리고 태그는 40자 sha와 `latest`다 — 짧은 sha는 pull이 실패한다. 머지 전 커밋은 `--build`. engine 재시작 전 가드가 위 규칙(ADR 0029)을 확인하고, 걸리면 api·scheduler까지만 갱신한 뒤 멈춘다. 그래도 진행하려면 `--force`. `--dry-run`도 docker가 있으면 `compose config`로 설정을 실제로 검사한다.
- 이미지를 VPS에서 직접 만들 때는 `--build`.
- 관문 G1 증거 기록(ADR 0030) — 첫 배포 후 한 번. 그 뒤로는 전략·파라미터를 바꿀 때마다 다시 실행한다. 데이터 캐시는 `appdata` 볼륨에 남는다:
  ```bash
  docker compose -f app/deploy/compose.yml --env-file .env run --rm api sh -c \
    "qp fetch yfinance SPY VEU AGG BIL --start 2005-01-01 && \
     qp fetch upbit KRW-BTC KRW-ETH KRW-SOL KRW-XRP KRW-ADA --count 3500 && \
     qp gate g1 --write"
  ```
  이후 `/reports/gates`와 대시보드의 G1이 실제 결과로 바뀐다. G2(보정·A/B)는 페이퍼 운영 데이터로 자동 계산된다(표본 20건 전에는 "판정 보류").
- 화면(web, P1-13)까지 띄우려면 `--web`. web은 `127.0.0.1:3000`에 열리고, 브라우저 WS는 `QP_WS_URL`(기본 `ws://127.0.0.1:8000/api/v1/ws`)로 api에 직접 붙으므로 SSH 터널은 두 포트 모두 연다: `ssh -L 3000:127.0.0.1:3000 -L 8000:127.0.0.1:8000 vps`. HTTPS 앞단을 둔 뒤에는 env 파일에 `QP_WS_URL=wss://<도메인>/api/v1/ws`.

### 2.2 백업·복원

- cron(KST 03:30): `30 3 * * * /opt/quantpilot/app/scripts/backup.sh --remote <rclone 대상> >> /opt/quantpilot/backup.log 2>&1` (VPS 시간대가 UTC면 `30 18 * * *`). `--remote`를 쓰려면 호스트에 rclone을 설치하고 `rclone config`로 대상을 먼저 만든다.
- 담는 것: `qp-db-<시각>.dump`(`pg_dump -Fc`), `qp-data-<시각>.tar.gz`(`data/`). **`data/keys.env`·`.env`는 담지 않는다** — 키는 따로 보관한다. `data/cache`도 뺀다.
- 30일 지난 백업은 스크립트가 지운다(`--keep-days`로 조정).
- 복원(하이퍼테이블 포함):

```bash
docker compose -p qp-paper -f app/deploy/compose.yml --env-file .env stop api scheduler engine
docker compose -p qp-paper -f app/deploy/compose.yml --env-file .env exec -T db \
  psql -U quantpilot -d quantpilot -c "select timescaledb_pre_restore();"
docker compose -p qp-paper -f app/deploy/compose.yml --env-file .env exec -T db \
  pg_restore -U quantpilot -d quantpilot --clean --if-exists < backups/qp-db-<시각>.dump
docker compose -p qp-paper -f app/deploy/compose.yml --env-file .env exec -T db \
  psql -U quantpilot -d quantpilot -c "select timescaledb_post_restore();"
# data/ (appdata 볼륨) — 멈춘 api 대신 일회용 컨테이너로 푼다
docker compose -p qp-paper -f app/deploy/compose.yml --env-file .env run --rm --no-deps -T api   tar -C /app/data -xzf - < backups/qp-data-<시각>.tar.gz
app/scripts/deploy.sh
```

- 백업에는 `data/keys.env`가 없다. 화면(설정 · API 키)에서 등록했던 키는 복원 뒤 다시 등록하거나 `.env`에 넣는다.
- `timescaledb_pre/post_restore`는 덤프를 만든 TimescaleDB와 같은 버전에서 돌려야 한다 — `QP_TIMESCALE_IMAGE`를 고정해 두는 이유.

- 개발 PC(SQLite): `scripts/backup.sh --sqlite data/quantpilot.db --data-dir data --out backups`.

## 3. 환경변수

`.env.example` 참조. 운영 추가:

| 변수 | 설명 |
| --- | --- |
| `QP_ENV` | dev / paper / live. 표시용이다 — `/health`의 `env`로만 나가고 동작은 바꾸지 않는다 |
| `QP_PAPER` | true면 모든 브로커가 페이퍼. false면 엔진·scheduler는 시작을 거부하고 계좌 API는 503(1단계, 위 §1) |
| `QP_LIVE_CONFIRM` | (3단계 예정, 아직 읽는 곳 없음) live 시작 승인 날짜(YYYY-MM-DD). 7일 지나면 재승인 필요 |
| `QP_DATABASE_URL`, `QP_REDIS_URL` | 연결 |
| `QP_ADMIN_PASSWORD` | 화면 로그인 · 2차 확인 |
| `QP_JWT_SECRET` | 32바이트 이상 |
| `QP_TELEGRAM_BOT_TOKEN`, `QP_TELEGRAM_CHAT_ID` | 알림 |
| `QP_AI_BUDGET_USD_DAILY` | 기본 2 |
| `QP_LOG_FORMAT` | `text`(기본) / `json`. 배포 compose는 `json`을 못박는다(§5) |
| `QP_NEWS_FILE` | 뉴스 피드·키워드 YAML. 비우면 패키지 기본값 `quantpilot/data/news_sources.yaml` (ADR 0021) |
| `QP_EVENTS_FILE` | 이벤트 캘린더 YAML(FOMC·CPI·금통위·업비트 점검). paper compose는 저장소의 `deploy/events.yaml`을 `/app/config/events.yaml`로 꽂고 이 값을 고정한다 — 일정 갱신은 그 파일을 PR로 고친다. 로컬 기본 `data/events.yaml`, 없으면 빈 캘린더 |
| `QP_UPBIT_*`, `QP_KIS_*`, `QP_ALPACA_*`, `QP_TYPESAFE_API_KEY`, `QP_ANTHROPIC_API_KEY`, `QP_GOOGLE_API_KEY`, `QP_DART_API_KEY` | 외부 키. Gemini 키가 없으면 뉴스 요약은 제목 절단, Claude 키가 없으면 일일 리뷰는 통계만 (ADR 0021) |

키는 VPS의 `/opt/quantpilot/.env`(권한 600)에 둔다. 화면의 "설정 > API 키"(`POST /settings/keys`, 비밀번호 재확인)는 `.env`를 건드리지 않고 `data/keys.env`(권한 600, `QP_<이름>=값`)에 쓴다. 설정은 `.env` → `data/keys.env` 순으로 읽히므로 저장한 키는 api·scheduler·engine을 재시작해야 적용된다(ADR 0017 §4). 값은 절대 반환하지 않고 "등록됨/미등록"만 보여준다. 이 파일은 암호화하지 않으므로 백업에서 빼고(§2.2) 권한으로 지킨다. 업비트 키는 **출금 권한 제외**, KIS는 모의·실전 앱키 분리.

## 4. 스케줄 (KST)

04 문서 §8 표 그대로. 여기에 운영 잡 추가:

| 잡 | 시각 | 동작 |
| --- | --- | --- |
| `db_backup` | 03:30 | VPS cron이 `scripts/backup.sh` 실행: `pg_dump` + `data/`(키 제외) → 오브젝트 스토리지(30일 보관). scheduler 잡이 아니다 (§2.2, ADR 0019) |
| 로그 보관 | 상시 | Docker `json-file` 드라이버가 컨테이너마다 20 MB × 5개로 돌려 쓴다(`deploy/compose.yml`의 `logging`). 날짜 기준 보관(14일) 잡은 없다 |
| `cost_report` | 매일 20:35 | (미구현 — 2단계 예정) AI 비용·거래 비용 일일 합계 알림. 지금은 대시보드 AI 판단 카드와 전략 설정의 "이번 달 AI 비용"(`/costs/ai`)으로 본다 |
| `weekly_gate_report` | 월 08:35 | (미구현 — 2단계 예정) 관문 G1~G4 상태 알림. 지금은 화면(대시보드·전략 카드)과 `GET /reports/gates`로 본다 |

scheduler 잡은 `quantpilot/scheduler/registry.py`의 `JOBS`가 기준이다. 그중 `krx_close_orders`·`us_orb_entry_window`·`kis_token_refresh`·`upbit_prescreen`·`morning_brief`는 본문이 아직 연결되지 않은 자리(`_hook`)다.

## 5. 모니터링

- 헬스: `/api/v1/health`의 `ok`·`engine_alive`가 false면 알림. api는 `127.0.0.1`에만 열려 있어 외부 업타임 모니터는 바로 못 본다 — HTTPS 앞단을 두기 전에는 VPS 안 cron(1분)으로 `curl -s 127.0.0.1:8000/api/v1/health`를 확인한다.
- 메트릭(Prometheus, 2단계): 틱 지연, 판단 모델 지연·타임아웃율, 주문 오류율, WS 재접속 수, AI 비용.
- 로그: `docker logs`(json-file 드라이버, 위 §4). 엔진·scheduler·API 모두 `quantpilot/logsetup.py`를 쓴다(t47).
  - 형식은 `QP_LOG_FORMAT`으로 고른다. 배포 compose는 `json`, 로컬 기본은 `text`다.
    - `json`: 한 줄에 JSON 하나 — `{"ts": UTC ISO, "level", "logger", "msg", …extra 필드, "exc": 예외}`. 예: `docker compose logs engine --no-log-prefix | jq 'select(.symbol=="KRW-BTC")'`.
    - `text`: `시각 레벨 로거: 메시지 key=value …`.
  - 두 형식 모두 `extra={...}` 구조화 필드를 싣는다. 필드 이름에 key·secret·token·password가 들어가면 값을 `***`로 가린다. 알림 중복 키는 그래서 `alert` 필드로 남긴다.
  - API(uvicorn)는 `json`일 때 시작하면서 uvicorn 로그도 같은 형식으로 돌린다.
  - httpx·httpcore 로거는 URL에 키가 실릴 수 있어 WARNING으로 올려 둔다(불변식 #10).
  - 레벨: 주문·체결·리스크 이벤트는 INFO.
- 화면 상단 상태 표시:
  - 헤더는 연결 상태 한 줄(API 끊김 / 엔진 응답 없음 / 실시간 재연결 중 / 시세 수신 없음 / "업비트 연결됨")과 페이퍼·실전 배지를 보인다.
  - 할트는 대시보드 할트 배너에, 오늘 AI 비용은 대시보드 AI 판단 카드에 보인다.
  - 시세 지연(마지막 체결 후 초)은 아직 보이지 않는다.

## 6. 알림 등급

| 등급 | 예 | 채널 |
| --- | --- | --- |
| info | 체결, 일일 리뷰, 비용 리포트 | 텔레그램 |
| warning | WS 재접속, 판단 모델 타임아웃 누적, 관문 미달 | 텔레그램 |
| critical | 청산 실패, 서킷브레이커, API 할트, 정합 불일치, 엔진 다운 | 텔레그램 5분 반복(scheduler `alert_repeat`). 이메일은 미구현(SMTP 변수를 정한 뒤 추가) |

## 7. 런북

### 7.1 엔진이 죽었다 (`engine_alive=false`)

1. `docker compose logs engine --tail 200`으로 원인 확인.
2. 보유 포지션 확인(`/positions`). 있으면 `scheduler`가 시간 청산 백업 모드로 처리하는지 확인(알림에 "backup exit armed").
3. `docker compose restart engine`. 재시작 시 `Reconciler`가 브로커와 대조. 불일치면 할트 상태로 뜬다 → 7.3.

### 7.2 서킷브레이커 발동 (`monthly_loss`)

- 자동: 신규 진입 중단, 청산은 정상. 다음 달 1일 자동 해제.
- 사람이 할 일: 원인 리뷰(일일 리뷰 + 판단 로그). **수동 해제 기능은 없다.** 정말 필요하면 코드 상수를 바꾸는 배포가 필요하고 그것이 의도다.

### 7.3 정합 불일치 (`reconcile_mismatch`)

1. 대시보드 할트 배너와 텔레그램 알림에서 차이 확인(수동 거래, 부분 체결 누락, 상장폐지 등). 별도의 "브로커 대조" 화면은 없다.
2. 원인이 시스템 밖(수동 거래)이면 할트 배너의 "브로커 기준으로 맞추기"(`POST /reconcile/{market}/accept-broker`) → DB 갱신 → 할트 해제.
3. 원인이 시스템 안(체결 누락)이면 `orders`·`fills` 수기 보정 후 버그 이슈.

### 7.4 KIS 토큰 갱신 실패 (2단계)

KIS 어댑터와 `kis_token_refresh` 본문은 2단계에서 연결한다(지금은 `_hook` 자리). 아래는 그때의 동작이다.

- 자동: 국내·미국 전략 당일 휴무 플래그, 보유분은 기존 토큰 만료 전 청산 시도.
- 사람: KIS 포털에서 앱키 상태 확인(1일 발급 한도, 앱 만료). 재발급 후 `settings/keys` 갱신 → `scheduler`가 다음 시도.

### 7.5 판단 모델 장애 (`judge_down`)

- 자동: 진입만 중단(hold), 청산 정상.
- 사람: TypeSafe 상태 확인. 장기화되어도 지금은 그대로 대기한다 — `laya`는 아직 연결되지 않아 설정에서 고를 수 없다(ADR 0032, 파인튜닝판과 함께 2단계). **판단 계층을 끄고 진입하는 옵션은 없다** — 게이팅 OFF는 페이퍼 A/B 섀도 원장에만 존재.

### 7.6 거래소 점검·장애

- 업비트 점검 공지는 이벤트 캘린더(`QP_EVENTS_FILE`, paper는 `deploy/events.yaml`)에 등록 → 그 시간 진입 금지.
- 예고 없는 장애:
  - **재연결**: WS가 30초 동안 응답이 없으면 다시 연결한다(`data/upbit_ws.py`의 `STALE_AFTER`). 대기 1·2·5·5·5초로 5번 실패하면 스트림이 `DataStale`을 던지고 엔진 프로세스가 끝난다. compose의 `restart: unless-stopped`가 다시 띄우며, 그동안 화면은 "엔진 응답 없음", 알림은 엔진 다운이다.
  - **자동 진입**: 체결이 없으면 분봉이 생기지 않으므로(집계기는 진행 중인 봉만 마감한다) 전략이 평가되지 않아 진입하지 않는다.
  - **수동 매수**: 엔진은 그 종목 마지막 체결이 30초보다 오래됐거나 재시작 뒤 아직 체결을 못 받았으면 `stale_price`로 거부한다. 매도·청산은 그대로 처리한다(불변식 #6, t46).
  - **헤더**: 체결이 60초 없으면 허브 `feed` 키가 만료되어 헤더가 "시세 수신 없음"으로 바뀐다(ADR 0029).
  - **미구현**: REST 폴링 대체. `UpbitStream.ensure_fresh`는 쓰이지 않는다(위 경로가 대신한다).
- 보유 중 장애가 길어지면 알림만. 손절가 이탈은 복구 후 첫 체결가에 처리.

### 7.7 실전 전환 절차 (3단계)

아직 실행할 수 없는 3단계 절차다. 지금 있는 것은 다음 세 가지뿐이다.

- 관문 API `GET /reports/gates`. 화면에서는 대시보드와 전략 카드가 G1·G2를 보여 준다.
- 전략별 전환 API `POST /strategies/{name}/go-live`. 관문 미통과면 409, 통과하면 비밀번호를 다시 확인한 뒤 DB의 전략 `paper`만 바꾼다.
- ORB의 잠긴 "실전 전환 (잠김)" 버튼.

일반 전략의 실전 전환 버튼, `QP_LIVE_CONFIRM` 검사, `qp-live` compose, 실계좌 연결은 3단계에서 만든다.

1. 관문 G1·G2 통과 확인. 화면 "실전 전환" 버튼 활성.
2. 업비트 실계좌 API 키(출금 권한 없음, 허용 IP 등록) 등록.
3. 자본 배정: 실전은 총 자본의 10% 이하로 `allocation` 설정. 나머지는 페이퍼 유지.
4. 버튼 → 비밀번호 재입력 → `QP_LIVE_CONFIRM`에 오늘 날짜 → `qp-live` compose 기동.
5. 첫 3거래일은 전략 설정 화면에서 `max_weight`를 직접 절반으로 내리고(자동 아님), 매일 리뷰 확인.

## 8. 비용 예산 (월)

| 항목 | 예상 |
| --- | --- |
| VPS 2 vCPU/4 GB | 2~3만 원 |
| 판단 모델(Jev) | $1 미만 |
| LLM(Claude·Gemini) | $5~10 (일 $0.3) |
| 시세 데이터 | 0 (업비트·KIS 무료, Alpaca Basic 무료). 미국 1분봉 과거분 필요 시 Massive $29 |
| 합계 | 5만 원 이내 |
