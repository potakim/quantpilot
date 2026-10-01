# 0019 · paper 배포 구성: compose 서비스·마이그레이션 단계·배포 가드·백업 범위·이미지 빌드

상태: 승인 (2026-10-01)

## 맥락
P1-14는 07 문서의 운영 절차를 실제 파일로 옮기는 카드다. CI(P1-14a)는 t3에서 끝났고, 이 카드는 나머지(compose·배포 스크립트·백업)를 맡는다. 0단계의 루트 `docker-compose.yml`은 api 하나만 띄웠고, db·redis 포트를 모든 인터페이스에 열었으며, 기본 비밀번호로도 떴다. P1-09~P1-12에서 engine·scheduler가 별도 프로세스가 되었고(ADR 0013), api와 engine이 redis 큐로 이어졌다(ADR 0017). 07 §2는 배포 순서(api → scheduler → engine), 포지션이 있으면 `--force` 요구, GHCR 이미지를 정했지만 마이그레이션 시점·백업 범위·포트 노출은 정하지 않았다. 개발 환경에는 docker가 없다.

## 결정
1. **`deploy/compose.yml` 하나로 paper를 띄운다** (프로젝트 이름 `qp-paper`). 서비스는 db(TimescaleDB)·redis·migrate·api·scheduler·engine, 그리고 `web` 프로필(`scripts/deploy.sh --web`)이다. 백엔드 넷은 같은 이미지를 쓰고 명령만 다르다. web은 `web/Dockerfile`(node 22, 비루트)로 따로 만들고, API 주소는 실행 시 `QP_API_URL=http://api:8000`(서버)·`QP_WS_URL`(브라우저 WS, 기본 `ws://127.0.0.1:8000/api/v1/ws`)으로만 받는다(ADR 0018).
2. **`QP_PAPER=true`·`QP_ENV=paper`는 compose가 못박는다.** env 파일 값보다 compose `environment`가 우선하므로, env 파일에 false가 들어가도 paper로 뜬다. live는 07 §7.7 절차대로 별도 compose 프로젝트로 만든다(3단계, 이 카드 범위 밖).
3. **키는 env 파일(`/opt/quantpilot/.env`, 권한 600)로만 들어온다** (불변식 #10). 이미지는 `.dockerignore`로 `.env`·`data/`·`.moai/`를 빼고, 비루트 사용자(uid 10001)로 돈다. `POSTGRES_PASSWORD`가 없으면 compose가 시작을 거부한다(기본 비밀번호 없음).
4. **포트 노출은 최소로 한다** (운영자 승인). db·redis는 호스트 포트를 열지 않는다. api(8000)와 web(3000)은 `127.0.0.1`에만 묶는다. 외부 공개는 HTTPS 앞단(Caddy·nginx)을 둔 뒤 별도 카드에서 한다. 그 전까지 외부 업타임 모니터(07 §5)는 붙일 수 없고, 접속은 SSH 터널로 한다.
5. **마이그레이션은 일회성 `migrate` 서비스가 맡는다** (`alembic upgrade head`). api·scheduler·engine은 `migrate`가 성공한 뒤에만 뜬다. TimescaleDB 확장·하이퍼테이블은 0001 마이그레이션이 만든다(ADR 0008).
6. **배포 가드는 파이썬으로 둔다** (`quantpilot/ops/deploy_guard.py`). `scripts/deploy.sh`가 api 이미지로 한 번 실행해 `positions` 표의 열린 포지션과 KRX 장중(평일 KST 09:05~15:15)을 확인한다. 막히면 api·scheduler까지만 갱신하고 engine은 그대로 두며, `--force`로만 넘어간다. 출력에는 DB URL을 남기지 않는다.
7. **백업(`scripts/backup.sh`)은 DB 덤프와 `data/`를 담는다.** Postgres는 `pg_dump -Fc`, SQLite는 온라인 백업 API를 쓴다. `data/keys.env`·`.env`(키), `data/cache`(다시 받을 수 있는 시세), SQLite 원본(덤프로 대신)은 뺀다. 30일이 지난 파일은 지우고, `--remote`를 주면 rclone으로 복사한다. 실행은 VPS cron(03:30 KST)이 맡는다. scheduler의 `db_backup` 잡으로 두지 않는 이유는 scheduler 컨테이너에 `pg_dump`와 docker 소켓이 없고, 그것을 넣으면 권한이 커지기 때문이다.
8. **이미지는 CI가 만든다** (운영자 승인). python·web 잡이 모두 통과하면 PR에서는 빌드만 하고, main push에서는 `ghcr.io/<owner>/quantpilot:<sha>`·`quantpilot-web:<sha>`(+`latest`)로 올린다. web 잡의 Node는 20(2026-04 지원 종료)에서 이미지와 같은 22 LTS로 올린다. 이 job만 `packages: write` 권한을 가진다. 러너는 VPS와 같은 `ubuntu-24.04`로 고정하고(운영자 승인), 액션은 Node 24 버전(checkout v7, setup-uv v10.2.0(주 버전 태그가 없어 전체 버전으로 고정), docker/* 최신)으로 올린다.
9. **scheduler의 APScheduler 잡 저장소용 동기 드라이버 `psycopg[binary]`를 `infra` 의존성에 넣는다.** 없으면 `make_scheduler`가 메모리 저장소로 폴백한다(ADR 0013 결과의 미검증 항목).

## 결과
- VPS에서는 `scripts/deploy.sh` 한 번으로 이미지 pull → db·redis → migrate → api → scheduler → (가드) → engine 순서가 지켜진다. 롤백은 `--tag <이전 sha>`로 다시 실행한다.
- 개발 PC에는 docker가 없어서 이 카드에서는 compose를 정적으로만 검증했다(`tests/test_ops.py`). `up -d`·하이퍼테이블 생성·`/health` 확인은 VPS에서 처음 한다.
- TimescaleDB 이미지는 `latest-pg16`을 기본으로 두고 `QP_TIMESCALE_IMAGE`로 고정 태그를 줄 수 있게 했다. 처음 띄운 뒤 쓰인 버전으로 고정하는 것을 권장한다.
- `pg_dump`로 뜬 하이퍼테이블을 복원하려면 `timescaledb_pre_restore()`·`timescaledb_post_restore()`로 감싸야 한다(07 §2.2).
- 루트 `docker-compose.yml`은 개발용으로 남기고 포트만 `127.0.0.1`로 묶었다.
