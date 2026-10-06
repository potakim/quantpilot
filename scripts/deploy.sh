#!/usr/bin/env bash
# QuantPilot paper 배포 (docs/07 §2, ADR 0019)
#
#   scripts/deploy.sh [--tag TAG] [--web] [--build] [--force] [--dry-run] [--env-file PATH]
#
# 순서: 이미지 pull(또는 build) → db·redis → migrate(alembic upgrade head) → api → (web) → scheduler → engine
# engine은 재시작 가드(ADR 0029)가 막지 않을 때만 재시작한다 — 1단계는 업비트 시간 청산 앞뒤(KST 08:55~09:05)만
# 막고, 업비트 포지션은 재시작 뒤 복원된다(ADR 0028). 걸리면 api·scheduler까지만 갱신하고 멈춘다 — 진행하려면 --force.
# api 헬스체크는 응답의 ok를 본다 — DB·redis가 안 붙으면 api 단계에서 멈춘다.
# 태그는 CI가 붙인 40자 커밋 sha 또는 latest. 롤백: scripts/deploy.sh --tag <이전 커밋 40자 sha>
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${QP_ENV_FILE:-/opt/quantpilot/.env}"
TAG="${QP_TAG:-latest}"
BUILD=0
WEB=0
FORCE=0
DRY_RUN=0

usage() {
  sed -n '2,10p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --tag) TAG="$2"; shift 2 ;;
    --env-file) ENV_FILE="$2"; shift 2 ;;
    --build) BUILD=1; shift ;;
    --web) WEB=1; shift ;;
    --force) FORCE=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "알 수 없는 옵션: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ ! -f "$ENV_FILE" ]]; then
  echo "env 파일 없음: $ENV_FILE (.env.example을 복사해 채운다)" >&2
  exit 2
fi
perm="$(stat -c '%a' "$ENV_FILE" 2>/dev/null || echo '?')"
if [[ "$perm" != "600" && "$perm" != "400" ]]; then
  echo "경고: $ENV_FILE 권한이 $perm — chmod 600 권장 (docs/07 §3)" >&2
fi

export QP_TAG="$TAG" QP_ENV_FILE="$ENV_FILE"
COMPOSE=(docker compose -f "$ROOT/deploy/compose.yml" --env-file "$ENV_FILE")
if [[ "$WEB" -eq 1 ]]; then
  COMPOSE+=(--profile web)
fi

run() {
  echo "+ $*"
  if [[ "$DRY_RUN" -eq 0 ]]; then
    "$@"
  fi
}

step() {
  echo "==> $*"
}

# dry-run도 compose 설정(필수 변수·파일 경로)은 실제로 검사한다 (ADR 0029). docker가 없으면 건너뛴다
if [[ "$DRY_RUN" -eq 1 ]]; then
  step "compose 설정 검사"
  if command -v docker >/dev/null 2>&1; then
    "${COMPOSE[@]}" config -q
    echo "compose 설정 OK"
  else
    echo "docker 없음 — 설정 검사 생략"
  fi
fi

step "이미지 준비 (tag=$TAG)"
if [[ "$BUILD" -eq 1 ]]; then
  run "${COMPOSE[@]}" build
else
  run "${COMPOSE[@]}" pull
fi

step "db·redis"
run "${COMPOSE[@]}" up -d --wait db redis

step "migrate (alembic upgrade head)"
run "${COMPOSE[@]}" run --rm migrate

step "api"
run "${COMPOSE[@]}" up -d --no-deps --wait api

if [[ "$WEB" -eq 1 ]]; then
  step "web"
  run "${COMPOSE[@]}" up -d --no-deps web
fi

step "scheduler"
run "${COMPOSE[@]}" up -d --no-deps scheduler

step "engine 재시작 전 확인 (포지션·시간대)"
if [[ "$DRY_RUN" -eq 1 ]]; then
  run "${COMPOSE[@]}" run --rm --no-deps api python -m quantpilot.ops.deploy_guard
elif ! "${COMPOSE[@]}" run --rm --no-deps api python -m quantpilot.ops.deploy_guard; then
  if [[ "$FORCE" -eq 1 ]]; then
    echo "--force: 확인 실패를 무시하고 engine을 재시작한다" >&2
  else
    echo "engine은 재시작하지 않았다 (api·scheduler는 갱신됨). 확인 후 --force로 다시 실행" >&2
    exit 3
  fi
fi

step "engine"
run "${COMPOSE[@]}" up -d --no-deps engine

step "완료"
run "${COMPOSE[@]}" ps
