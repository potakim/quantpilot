"""단일 사용자 JWT 인증 (03 §1, 07 §2, ADR 0017 §3).

- 표준 라이브러리만 쓴 HS256. 헤더의 alg가 HS256이 아니면(`none` 포함) 거부한다.
- 비밀번호·시크릿은 `QP_ADMIN_PASSWORD`·`QP_JWT_SECRET`에서만 온다. 응답·로그에 싣지 않는다 (불변식 #10).
- 시크릿이 32바이트 미만이거나 비밀번호가 비어 있으면 로그인 자체를 막는다 (503 NOT_READY).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import Request

from quantpilot.api.errors import ApiError

log = logging.getLogger(__name__)

ALG = "HS256"
MIN_SECRET_BYTES = 32


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _sign(msg: bytes, secret: str) -> str:
    return _b64e(hmac.new(secret.encode(), msg, hashlib.sha256).digest())


def encode(payload: dict[str, Any], secret: str) -> str:
    """payload를 HS256 JWT로 서명한다."""
    head = _b64e(json.dumps({"alg": ALG, "typ": "JWT"}, separators=(",", ":")).encode())
    body = _b64e(json.dumps(payload, separators=(",", ":")).encode())
    return f"{head}.{body}.{_sign(f'{head}.{body}'.encode(), secret)}"


class TokenError(ValueError):
    """토큰 형식·서명·만료 오류. 메시지에 토큰·시크릿을 담지 않는다."""


def decode(token: str, secret: str, *, now: datetime | None = None) -> dict[str, Any]:
    """서명·alg·만료를 검증하고 payload를 돌려준다. 실패하면 TokenError."""
    parts = token.split(".")
    if len(parts) != 3:
        raise TokenError("malformed")
    head_s, body_s, sig = parts
    try:
        head = json.loads(_b64d(head_s))
        payload = json.loads(_b64d(body_s))
    except (ValueError, TypeError) as e:
        raise TokenError("malformed") from e
    if not isinstance(head, dict) or head.get("alg") != ALG:
        raise TokenError("bad alg")
    if not hmac.compare_digest(sig, _sign(f"{head_s}.{body_s}".encode(), secret)):
        raise TokenError("bad signature")
    exp = payload.get("exp") if isinstance(payload, dict) else None
    if not isinstance(exp, int | float):
        raise TokenError("no exp")
    if (now or datetime.now(UTC)).timestamp() >= exp:
        raise TokenError("expired")
    return payload


class Auth:
    """로그인·토큰 발급·검증. 설정 값을 보관만 하고 밖으로 내보내지 않는다."""

    def __init__(
        self,
        secret: str,
        password: str,
        *,
        ttl: timedelta = timedelta(hours=12),
        utcnow: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._secret = secret
        self._password = password
        self.ttl = ttl
        self.utcnow = utcnow

    def __repr__(self) -> str:
        return "Auth(<redacted>)"

    @property
    def ready(self) -> bool:
        """시크릿·비밀번호가 설정되어 있는가."""
        return len(self._secret.encode()) >= MIN_SECRET_BYTES and bool(self._password)

    def _require_ready(self) -> None:
        if not self.ready:
            raise ApiError(
                503, "NOT_READY", "QP_JWT_SECRET(32바이트 이상)·QP_ADMIN_PASSWORD 설정 필요"
            )

    def check_password(self, password: str) -> bool:
        """비밀번호 비교 (상수 시간)."""
        self._require_ready()
        return hmac.compare_digest(password.encode(), self._password.encode())

    def login(self, password: str) -> tuple[str, datetime]:
        """비밀번호가 맞으면 (토큰, 만료시각UTC). 틀리면 401."""
        if not self.check_password(password):
            log.warning("login failed")
            raise ApiError(401, "UNAUTHORIZED", "비밀번호가 틀렸다")
        now = self.utcnow()
        exp = now + self.ttl
        token = encode(
            {"sub": "admin", "iat": int(now.timestamp()), "exp": int(exp.timestamp())},
            self._secret,
        )
        log.info("login ok")
        return token, exp

    def verify(self, token: str) -> dict[str, Any]:
        """토큰을 검증해 payload를 돌려준다. 실패하면 401."""
        self._require_ready()
        try:
            return decode(token, self._secret, now=self.utcnow())
        except TokenError as e:
            raise ApiError(401, "UNAUTHORIZED", f"토큰 오류: {e}") from None


def require_user(request: Request) -> dict[str, Any]:
    """FastAPI 의존성: `Authorization: Bearer <JWT>` 검증."""
    auth: Auth = request.app.state.deps.auth
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise ApiError(401, "UNAUTHORIZED", "토큰 없음")
    return auth.verify(token.strip())
