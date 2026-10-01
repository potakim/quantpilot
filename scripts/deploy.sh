#!/usr/bin/env bash
# QuantPilot paper 배포 (docs/07 §2, ADR 0019)
#
#   scripts/deploy.sh [--tag TAG] [--web] [--build] [--force] [--dry-run] [--env-file PATH]
#
# 순서: 이미지 pull(또는 build) → db·redis → migrate(alembic upgrade head) → api → (web) → scheduler → engine
# engine은 열린 포지션이 없고 KRX 장중(KST 09:05~15:15)이 아닐 때만 재시작한다.
# 걸리면 api·scheduler까지만 갱신하고 멈춘다 — 그래도 진행하려면 --force.
# 롤백은 이전 태그로 다시 실행: scripts/deploy.sh --tag <이전 sha>
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${QP_ENV_FILE:-/opt/quantpilot/.env}"
TAG="${QP_TAG:-latest}"
BUILD=0
WEB=0
FORCE=0
DRY_RUN=0

usage() {
  sed -n '2,9p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
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
