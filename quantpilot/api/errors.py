"""API 공통 오류 형식 (03 §1): `{"error": {"code", "message", "detail"}}`."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger(__name__)

_DEFAULT_CODES = {
    400: "INVALID_PARAM",
    401: "UNAUTHORIZED",
    403: "CONFIRMATION_REQUIRED",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    422: "RISK_REJECTED",
    503: "NOT_READY",
}


class ApiError(Exception):
    """03 §1 표의 오류 1건. status·code·message·detail을 그대로 응답에 싣는다."""

    def __init__(
        self, status: int, code: str, message: str, detail: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.status, self.code, self.message, self.detail = status, code, message, detail or {}


def body(code: str, message: str, detail: dict[str, Any] | None = None) -> dict[str, Any]:
    """오류 응답 본문."""
    return {"error": {"code": code, "message": message, "detail": detail or {}}}


def install(app: FastAPI) -> None:
    """앱에 오류 핸들러를 건다. 검증 실패는 400 INVALID_PARAM (03 §1)."""

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, e: ApiError) -> JSONResponse:
        return JSONResponse(body(e.code, e.message, e.detail), status_code=e.status)

    @app.exception_handler(RequestValidationError)
    async def _invalid(_: Request, e: RequestValidationError) -> JSONResponse:
        errs = [{"loc": list(x.get("loc", ())), "msg": str(x.get("msg", ""))} for x in e.errors()]
        return JSONResponse(body("INVALID_PARAM", "요청 형식 오류", {"errors": errs}), 400)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, e: StarletteHTTPException) -> JSONResponse:
        code = _DEFAULT_CODES.get(e.status_code, "ERROR")
        return JSONResponse(body(code, str(e.detail)), status_code=e.status_code)
