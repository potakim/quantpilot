"""03 §2.1 시스템: /health, /auth/login, /settings, /settings/keys.

불변식 #6: RiskRules는 읽기 전용(`locked: true`)으로만 보여 주고, PATCH는 허용 키만 받는다.
불변식 #10: 키 값은 파일에 쓰기만 하고 응답·로그에 싣지 않는다. 비밀번호·JWT 시크릿도 마찬가지.
"""

from __future__ import annotations

import logging
import os
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from quantpilot import __version__
from quantpilot.api.auth import require_user
from quantpilot.api.deps import Deps, DepsDep
from quantpilot.api.errors import ApiError
from quantpilot.core.models import Market
from quantpilot.engine.link import SettingsEngineLink
from quantpilot.execution.risk import RiskRules
from quantpilot.realtime import keys as hk

log = logging.getLogger(__name__)

public = APIRouter()
router = APIRouter(dependencies=[Depends(require_user)])

HEARTBEAT_STALE = timedelta(seconds=90)
JUDGE_PROVIDERS = ("stub", "typesafe", "laya")
LLM_MODELS = ("claude", "gemini", "stub")
SECRET_WORDS = ("key", "secret", "token", "password")
# POST /settings/keys로 받을 수 있는 이름 = config.Settings의 외부 키 필드 (QP_<NAME>)
KEY_NAMES = frozenset(
    {
        "upbit_access_key",
        "upbit_secret_key",
        "kis_app_key",
        "kis_app_secret",
        "kis_account",
        "alpaca_key",
        "alpaca_secret",
        "typesafe_api_key",
        "anthropic_api_key",
        "google_api_key",
        "dart_api_key",
        "telegram_bot_token",
        "telegram_chat_id",
    }
)


def _is_secret_name(name: str) -> bool:
    low = name.lower()
    return any(w in low for w in SECRET_WORDS)


def link_of(deps: Deps) -> SettingsEngineLink:
    """settings 우편함 EngineLink (하트비트·할트)."""
    from quantpilot.db.repo import SqlConfigRepo

    return SettingsEngineLink(SqlConfigRepo(deps.sessions), utcnow=deps.utcnow)


async def halted_map(deps: Deps) -> dict[str, str | None]:
    """시장별 할트 사유 (없으면 None)."""
    link = link_of(deps)
    return {m.value: await link.halt_reason(m) for m in Market}


@public.get("/health")
async def health(
    deps: DepsDep,
) -> dict[str, Any]:
    """헬스 체크 (인증 없음). DB·허브가 죽어 있어도 200으로 상태를 알린다."""
    out: dict[str, Any] = {
        "ok": True,
        "version": __version__,
        "paper": bool(deps.settings.paper),
        "env": deps.settings.env,
        "engine_alive": False,
        "ws_connected": {"upbit": False, "kis": False},
        "halted": {m.value: None for m in Market},
    }
    try:
        out["ws_connected"] = {
            "upbit": bool(await deps.hub.get(hk.feed(Market.UPBIT))),
            "kis": bool(await deps.hub.get(hk.feed(Market.KRX))),
        }
        link = link_of(deps)
        now = deps.utcnow()
        beats = [await link.last_beat(m) for m in Market]
        out["engine_alive"] = any(b is not None and now - b < HEARTBEAT_STALE for b in beats)
        out["halted"] = await halted_map(deps)
    except Exception as e:  # noqa: BLE001 — 헬스는 실패를 상태로 돌려준다
        out["ok"] = False
        out["error"] = type(e).__name__
    return out


class LoginRequest(BaseModel):
    password: str


@public.post("/auth/login")
async def login(
    deps: DepsDep,
    req: LoginRequest,
) -> dict[str, Any]:
    """비밀번호 → `{token, expires_at}`."""
    token, exp = deps.auth.login(req.password)
    return {"token": token, "expires_at": exp.isoformat()}


async def all_settings(deps: Deps) -> dict[str, Any]:
    """settings 테이블 전체 (비밀 이름은 가린다)."""
    from sqlalchemy import select

    from quantpilot.db.models import SettingRow

    async with deps.sessions() as s:
        rows = (await s.scalars(select(SettingRow).order_by(SettingRow.key))).all()
    return {r.key: ("***" if _is_secret_name(r.key) else r.value) for r in rows}


@router.get("/settings")
async def get_settings(
    deps: DepsDep,
) -> dict[str, Any]:
    """settings + RiskRules(읽기 전용)."""
    return {
        "settings": await all_settings(deps),
        "risk_rules": {**asdict(RiskRules()), "locked": True},
        "paper": bool(deps.settings.paper),
    }


def _float_in(key: str, v: Any, lo: float, hi: float) -> float:
    if isinstance(v, bool) or not isinstance(v, int | float) or not lo <= float(v) <= hi:
        raise ApiError(400, "INVALID_PARAM", f"{key}는 {lo}~{hi} 사이 숫자", {"key": key})
    return float(v)


def validate_setting(key: str, value: Any) -> Any:
    """PATCH /settings 허용 키 검사. 그 밖의 키(risk.* 포함)는 400 (불변식 #6)."""
    if key == "gate.hold_below":
        return _float_in(key, value, 0.3, 0.7)
    if key == "gate.full_above":
        return _float_in(key, value, 0.7, 0.98)
    if key == "judge.provider":
        if value not in JUDGE_PROVIDERS:
            raise ApiError(400, "INVALID_PARAM", f"judge.provider는 {JUDGE_PROVIDERS} 중 하나")
        return value
    if key == "llm.models":
        ok = isinstance(value, list) and len(value) == 2 and all(v in LLM_MODELS for v in value)
        if not ok:  # 2모델 합의 (불변식 #8)
            raise ApiError(400, "INVALID_PARAM", f"llm.models는 {LLM_MODELS} 중 2개 목록")
        return value
    if key == "news.enabled":
        if not isinstance(value, bool):
            raise ApiError(400, "INVALID_PARAM", "news.enabled는 true/false")
        return value
    if key.startswith("notify.") and len(key) > 7 and not _is_secret_name(key):
        if not isinstance(value, str | int | float | bool | list) and value is not None:
            raise ApiError(400, "INVALID_PARAM", f"{key} 값 형식")
        return value
    raise ApiError(400, "INVALID_PARAM", f"바꿀 수 없는 설정 키: {key}", {"key": key})


@router.patch("/settings")
async def patch_settings(
    deps: DepsDep,
    patch: dict[str, Any],
) -> dict[str, Any]:
    """허용 키만 쓴다. 하나라도 틀리면 아무것도 쓰지 않는다."""
    from quantpilot.db.repo import SqlConfigRepo

    if not patch:
        raise ApiError(400, "INVALID_PARAM", "바꿀 키가 없다")
    clean = {k: validate_setting(k, v) for k, v in patch.items()}
    config = SqlConfigRepo(deps.sessions)
    hold = clean.get("gate.hold_below", await config.get_setting("gate.hold_below"))
    full = clean.get("gate.full_above", await config.get_setting("gate.full_above"))
    hold = deps.settings.gate_hold_below if hold is None else hold
    full = deps.settings.gate_full_above if full is None else full
    if hold >= full:
        raise ApiError(400, "INVALID_PARAM", "gate.hold_below < gate.full_above 이어야 한다")
    for k, v in clean.items():
        await config.set_setting(k, v)
    log.info("settings patched", extra={"keys": sorted(clean)})
    return {"updated": sorted(clean)}


class KeysRequest(BaseModel):
    keys: dict[str, str]
    confirm_password: str | None = None


def write_keys_file(path: Path, values: dict[str, str]) -> None:
    """QP_<NAME>=값 줄을 쓰거나 바꾼다. 파일 권한 0600."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text().splitlines() if path.exists() else []
    names = {f"QP_{k.upper()}" for k in values}
    kept = [ln for ln in lines if ln.split("=", 1)[0].strip() not in names]
    kept += [f"QP_{k.upper()}={v}" for k, v in sorted(values.items())]
    path.write_text("\n".join(kept) + "\n")
    os.chmod(path, 0o600)


@router.post("/settings/keys")
async def set_keys(
    deps: DepsDep,
    req: KeysRequest,
) -> dict[str, Any]:
    """거래소·AI 키 등록. 2차 확인 필요, 값은 저장만 하고 돌려주지 않는다 (재시작 후 QP_*로 적용)."""
    if not req.confirm_password or not deps.auth.check_password(req.confirm_password):
        raise ApiError(403, "CONFIRMATION_REQUIRED", "키 변경은 confirm_password가 필요하다")
    unknown = sorted(set(req.keys) - KEY_NAMES)
    if unknown or not req.keys:
        raise ApiError(400, "INVALID_PARAM", "알 수 없는 키 이름", {"unknown": unknown})
    write_keys_file(Path(deps.settings.keys_file), req.keys)
    names = sorted(req.keys)
    log.info("keys stored", extra={"names": names})
    return {"stored": names, "restart_required": True, "at": deps.utcnow().isoformat()}
