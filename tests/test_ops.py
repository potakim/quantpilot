"""P1-14 운영: compose 정적 검증, 배포 스크립트 dry-run, 재시작 가드, 백업 스크립트(SQLite)."""

from __future__ import annotations

import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tarfile
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from quantpilot.core.models import Market
from quantpilot.ops.deploy_guard import evaluate, in_blackout

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "deploy" / "compose.yml"
APP_SERVICES = ("migrate", "api", "scheduler", "engine")
BASH = shutil.which("bash")
needs_bash = pytest.mark.skipif(BASH is None, reason="bash 필요")
posix_only = pytest.mark.skipif(
    os.name == "nt", reason="Git Bash tar가 C: 경로를 원격 호스트로 해석한다 (배포는 리눅스)"
)


@pytest.fixture(scope="module")
def compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


# ── compose 정적 검증 (docker 없는 환경의 `docker compose config` 대용) ──


def test_compose_services(compose):
    assert set(compose["services"]) == {"db", "redis", "migrate", *APP_SERVICES[1:], "web"}
    assert compose["services"]["web"]["profiles"] == ["web"]


@pytest.mark.invariant
def test_compose_forces_paper(compose):
    for name in APP_SERVICES:
        env = compose["services"][name]["environment"]
        assert env["QP_PAPER"] == "true", name
        assert env["QP_ENV"] == "paper", name


@pytest.mark.invariant
def test_compose_has_no_secret_values(compose):
    """키·시크릿은 env_file로만 들어온다. compose에 값이 적혀 있으면 안 된다 (불변식 #10)."""
    secret = re.compile(r"(KEY|SECRET|TOKEN|PASSWORD)", re.IGNORECASE)
    for name, svc in compose["services"].items():
        for k, v in (svc.get("environment") or {}).items():
            if secret.search(k):
                assert str(v).startswith("${"), f"{name}.{k}에 값이 박혀 있음"
    text = COMPOSE.read_text(encoding="utf-8")
    assert "${POSTGRES_PASSWORD:?" in text  # 기본 비밀번호로 뜨지 않는다
    for name in APP_SERVICES:
        assert compose["services"][name]["env_file"] == ["${QP_ENV_FILE:-/opt/quantpilot/.env}"]


def test_compose_ports_minimal(compose):
    """db·redis는 호스트에 열지 않고, api·web은 127.0.0.1에만 묶는다."""
    for name in ("db", "redis", "migrate", "scheduler", "engine"):
        assert "ports" not in compose["services"][name], name
    for name in ("api", "web"):
        for p in compose["services"][name]["ports"]:
            assert p.startswith("127.0.0.1:"), p


def test_compose_start_order(compose):
    s = compose["services"]
    assert s["migrate"]["command"] == ["alembic", "upgrade", "head"]
    assert s["migrate"]["depends_on"]["db"]["condition"] == "service_healthy"
    for name in ("api", "scheduler", "engine"):
        dep = s[name]["depends_on"]
        assert dep["migrate"]["condition"] == "service_completed_successfully"
        assert dep["redis"]["condition"] == "service_healthy"
    assert s["engine"]["command"] == ["python", "-m", "quantpilot.engine.main"]
    assert s["scheduler"]["command"] == ["python", "-m", "quantpilot.scheduler.main"]
    assert "/api/v1/health" in " ".join(s["api"]["healthcheck"]["test"])


def test_compose_env_refs_are_documented():
    """compose가 읽는 ${VAR} 중 env 파일에서 와야 하는 것은 .env.example에 있다."""
    refs = set(re.findall(r"\$\{([A-Z_]+)", COMPOSE.read_text(encoding="utf-8")))
    # 배포 스크립트·셸이 넘기는 값(선택)은 제외
    optional = {
        "QP_IMAGE",
        "QP_TAG",
        "QP_ENV_FILE",
        "QP_TIMESCALE_IMAGE",
        "QP_API_PORT",
        "QP_WEB_IMAGE",
        "QP_WEB_PORT",
        "QP_WS_URL",
    }
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    for var in refs - optional:
        assert re.search(rf"^{var}=", example, re.MULTILINE), var


@pytest.mark.invariant
def test_image_excludes_keys():
    ignore = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    for entry in (".env", "data/", ".moai/"):
        assert entry in ignore, entry
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert not re.search(r"^\s*(ENV|ARG)\s+QP_", dockerfile, re.MULTILINE)
    assert re.search(r"^USER\s+qp", dockerfile, re.MULTILINE)


# ── 엔진 재시작 가드 (docs/07 §2) ──


def _utc(y, mo, d, h, mi) -> datetime:
    return datetime(y, mo, d, h, mi, tzinfo=UTC)


def test_blackout_window():
    # 2026-09-30(수) KST 09:05 = UTC 00:05
    assert not in_blackout(_utc(2026, 9, 30, 0, 4))
    assert in_blackout(_utc(2026, 9, 30, 0, 5))
    assert in_blackout(_utc(2026, 9, 30, 6, 14))
    assert not in_blackout(_utc(2026, 9, 30, 6, 15))
    # 토요일 장중 시각은 막지 않는다
    assert not in_blackout(_utc(2026, 10, 3, 3, 0))


def test_evaluate_blocks_on_positions_and_hours():
    """ADR 0029: 복원되는 업비트 포지션은 알림, 그 밖의 시장 포지션은 막음, 위험 시간대는 실시간 엔진 시장만."""
    evening = _utc(2026, 9, 30, 11, 30)  # KST 20:30
    assert evaluate(evening, {"upbit": 0, "krx": 0, "us": 0}).ok
    held = evaluate(evening, {"upbit": 2, "krx": 0, "us": 0})
    assert held.ok and any("upbit=2" in n for n in held.notes)  # 재시작 뒤 복원 (ADR 0028)
    krx_held = evaluate(evening, {"upbit": 0, "krx": 1})
    assert not krx_held.ok and "krx=1" in krx_held.reasons[0]
    noon = _utc(2026, 9, 30, 3, 0)  # KST 12:00 평일
    assert evaluate(noon, {"upbit": 0}).ok  # KRX 엔진이 없으면 장중 차단 없음
    with_krx = evaluate(noon, {"upbit": 0}, live=frozenset({Market.UPBIT, Market.KRX}))
    assert not with_krx.ok and "KRX" in with_krx.reasons[0]
    exit_window = evaluate(_utc(2026, 10, 3, 0, 0), {"upbit": 0})  # 토요일 KST 09:00
    assert not exit_window.ok and "시간 청산" in exit_window.reasons[0]
    assert evaluate(_utc(2026, 10, 3, 0, 5), {"upbit": 0}).ok  # 09:05부터 허용
    assert evaluate(_utc(2026, 9, 30, 16, 0), {}).notes  # 권장 시각 밖 안내


async def test_count_open_positions(tmp_path):
    pytest.importorskip("aiosqlite")
    from quantpilot.core.models import Market, Position
    from quantpilot.db.models import Base
    from quantpilot.db.repo import SqlPositionRepo
    from quantpilot.db.session import make_sessions
    from quantpilot.ops.deploy_guard import count_open_positions

    url = f"sqlite+aiosqlite:///{tmp_path / 'qp.db'}"
    sessions = make_sessions(url)
    async with sessions.kw["bind"].begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await SqlPositionRepo(sessions).upsert(
        Position("KRW-BTC", qty=0.01, avg_price=1e8, strategy="vol_breakout", market=Market.UPBIT)
    )
    await sessions.kw["bind"].dispose()
    counts = await count_open_positions(url)
    assert counts == {"upbit": 1, "krx": 0, "us": 0}


def test_guard_cli_hides_db_url(capsys):
    from quantpilot.ops.deploy_guard import main

    code = main(["--db-url", "postgresql+asyncpg://quantpilot:s3cret-pw@nohost.invalid:1/x"])
    out = capsys.readouterr().out
    assert code == 4 and "BLOCK" in out
    assert "s3cret-pw" not in out


# ── 배포 스크립트 dry-run ──


def _env_file(tmp_path: Path) -> Path:
    env = tmp_path / "qp.env"
    env.write_text("POSTGRES_PASSWORD=x\nQP_JWT_SECRET=do-not-print\n", encoding="utf-8")
    env.chmod(0o600)
    return env


@needs_bash
def test_deploy_dry_run_order(tmp_path):
    env = _env_file(tmp_path)
    r = subprocess.run(
        [
            BASH,
            str(ROOT / "scripts/deploy.sh"),
            "--dry-run",
            "--env-file",
            str(env),
            "--tag",
            "abc",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    cmds = [ln for ln in r.stdout.splitlines() if ln.startswith("+ ")]
    order = ["pull", "up -d --wait db redis", "run --rm migrate", "--wait api", "scheduler"]
    order += ["deploy_guard", "--no-deps engine", " ps"]
    pos = [next(i for i, c in enumerate(cmds) if key in c) for key in order]
    assert pos == sorted(pos), r.stdout
    assert all("deploy/compose.yml" in c for c in cmds)
    assert "do-not-print" not in r.stdout + r.stderr


@needs_bash
def test_deploy_dry_run_with_web(tmp_path):
    env = _env_file(tmp_path)
    r = subprocess.run(
        [BASH, str(ROOT / "scripts/deploy.sh"), "--dry-run", "--web", "--env-file", str(env)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    cmds = [ln for ln in r.stdout.splitlines() if ln.startswith("+ ")]
    assert all("--profile web" in c for c in cmds)
    api = next(i for i, c in enumerate(cmds) if "--wait api" in c)
    web = next(i for i, c in enumerate(cmds) if c.endswith("--no-deps web"))
    sched = next(i for i, c in enumerate(cmds) if c.endswith("scheduler"))
    assert api < web < sched


def test_web_image_takes_api_url_at_runtime(compose):
    """web 이미지에는 API 주소·키가 없고, compose가 실행 시 넣는다 (ADR 0018·0019)."""
    env = compose["services"]["web"]["environment"]
    assert env["QP_API_URL"] == "http://api:8000"
    assert env["QP_WS_URL"].startswith("${QP_WS_URL:-ws://127.0.0.1:")
    dockerfile = (ROOT / "web" / "Dockerfile").read_text(encoding="utf-8")
    assert not re.search(r"^\s*(ENV|ARG)\s+(QP_|NEXT_PUBLIC_)", dockerfile, re.MULTILINE)
    assert re.search(r"^USER\s+node", dockerfile, re.MULTILINE)
    assert ".env*" in (ROOT / "web" / ".dockerignore").read_text(encoding="utf-8").splitlines()


@needs_bash
def test_deploy_requires_env_file(tmp_path):
    r = subprocess.run(
        [BASH, str(ROOT / "scripts/deploy.sh"), "--dry-run", "--env-file", str(tmp_path / "no")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert r.returncode == 2


# ── 백업 스크립트 (SQLite 경로) ──


@needs_bash
@posix_only
def test_backup_sqlite_excludes_keys(tmp_path):
    data = tmp_path / "data"
    (data / "cache").mkdir(parents=True)
    db = data / "quantpilot.db"
    with sqlite3.connect(db) as c:
        c.execute("create table positions (symbol text)")
        c.execute("insert into positions values ('KRW-BTC')")
    (data / "keys.env").write_text("QP_UPBIT_SECRET_KEY=nope\n", encoding="utf-8")
    (data / "cache" / "big.parquet").write_bytes(b"x" * 10)
    (data / "backtest_attempts.json").write_text("{}", encoding="utf-8")
    out = tmp_path / "backups"
    stale = out / "qp-db-20000101T000000Z.sqlite"
    out.mkdir()
    stale.write_bytes(b"old")
    os.utime(stale, (0, 0))

    r = subprocess.run(
        [BASH, str(ROOT / "scripts/backup.sh"), "--sqlite", str(db), "--data-dir", str(data)]
        + ["--out", str(out), "--keep-days", "30"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHON": sys.executable},
        check=False,
    )
    assert r.returncode == 0, r.stderr
    dbs = list(out.glob("qp-db-*.sqlite"))
    tars = list(out.glob("qp-data-*.tar.gz"))
    assert len(dbs) == 1 and len(tars) == 1 and not stale.exists()
    with sqlite3.connect(dbs[0]) as c:
        assert c.execute("select symbol from positions").fetchall() == [("KRW-BTC",)]
    with tarfile.open(tars[0]) as t:
        names = {n.removeprefix("./") for n in t.getnames()}
    assert "backtest_attempts.json" in names
    assert not {"keys.env", "quantpilot.db", "cache"} & names
    assert not any(n.startswith("cache/") for n in names)
