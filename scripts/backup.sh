#!/usr/bin/env bash
# QuantPilot 백업 (docs/07 §4 db_backup, ADR 0019)
#
#   scripts/backup.sh [--out DIR] [--keep-days N] [--env-file PATH] [--remote RCLONE_DEST]
#   scripts/backup.sh --sqlite data/quantpilot.db --data-dir data [--out DIR]    # 개발 환경
#
# 담는 것: DB 덤프(Postgres는 pg_dump -Fc, SQLite는 온라인 백업) + data/ 디렉터리
# 빼는 것: data/keys.env·.env(키, 불변식 #10), data/cache(다시 받을 수 있는 시세), SQLite 원본(덤프로 대신)
# 보관: --keep-days(기본 30)일이 지난 qp-* 파일은 지운다. --remote가 있으면 rclone으로 복사.
# VPS cron 예: 30 3 * * * /opt/quantpilot/app/scripts/backup.sh >> /opt/quantpilot/backup.log 2>&1
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${QP_ENV_FILE:-/opt/quantpilot/.env}"
OUT="/opt/quantpilot/backups"
KEEP_DAYS=30
SQLITE=""
DATA_DIR=""
REMOTE=""
PY="${PYTHON:-python3}"

usage() {
  sed -n '2,10p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --out) OUT="$2"; shift 2 ;;
    --keep-days) KEEP_DAYS="$2"; shift 2 ;;
    --env-file) ENV_FILE="$2"; shift 2 ;;
    --sqlite) SQLITE="$2"; shift 2 ;;
    --data-dir) DATA_DIR="$2"; shift 2 ;;
    --remote) REMOTE="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "알 수 없는 옵션: $1" >&2; usage >&2; exit 2 ;;
  esac
done

umask 077
mkdir -p "$OUT"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
EXCLUDES=(--exclude=./keys.env --exclude=./.env --exclude=./cache --exclude='./*.db' --exclude='./*.db-*')

if [[ -n "$SQLITE" ]]; then
  # 개발 환경: SQLite 온라인 백업 (쓰는 중이어도 일관된 사본)
  [[ -f "$SQLITE" ]] || { echo "SQLite 파일 없음: $SQLITE" >&2; exit 2; }
  "$PY" - "$SQLITE" "$OUT/qp-db-$STAMP.sqlite" <<'EOF'
import sqlite3
import sys

src, dst = sqlite3.connect(sys.argv[1]), sqlite3.connect(sys.argv[2])
with dst:
    src.backup(dst)
src.close()
dst.close()
EOF
  if [[ -n "$DATA_DIR" ]]; then
    tar -C "$DATA_DIR" "${EXCLUDES[@]}" -czf "$OUT/qp-data-$STAMP.tar.gz" .
  fi
else
  # VPS: compose 프로젝트의 db·api 컨테이너에서 받는다
  # compose.yml의 env_file(${QP_ENV_FILE})도 --env-file과 같은 파일을 보게 한다 (ADR 0029)
  export QP_ENV_FILE="$ENV_FILE"
  COMPOSE=(docker compose -f "$ROOT/deploy/compose.yml" --env-file "$ENV_FILE")
  "${COMPOSE[@]}" exec -T db pg_dump -U quantpilot -d quantpilot -Fc > "$OUT/qp-db-$STAMP.dump"
  "${COMPOSE[@]}" exec -T api tar -C /app/data "${EXCLUDES[@]}" -czf - . \
    > "$OUT/qp-data-$STAMP.tar.gz"
fi

# 빈 덤프는 실패로 본다
for f in "$OUT"/qp-*-"$STAMP".*; do
  [[ -s "$f" ]] || { echo "빈 백업 파일: $f" >&2; exit 1; }
done

find "$OUT" -maxdepth 1 -type f -name 'qp-*' -mtime +"$KEEP_DAYS" -delete

if [[ -n "$REMOTE" ]]; then
  rclone copy "$OUT" "$REMOTE" --include "qp-*-$STAMP.*"
fi

ls -1 "$OUT" | grep -- "-$STAMP\." | sed 's/^/backup: /'
