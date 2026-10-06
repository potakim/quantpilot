"""로그 구조화 (07 §5): JSON 한 줄·텍스트 둘 다 extra 필드를 싣고, 비밀 이름 필드는 가린다 (불변식 #10)."""

from __future__ import annotations

import json
import logging

import pytest

from quantpilot.logsetup import MASK, UVICORN, JsonFormatter, TextFormatter, setup_logging


def _record(msg: str = "주문 체결", exc: bool = False, **extra) -> logging.LogRecord:
    exc_info = None
    if exc:
        try:
            raise ValueError("boom")
        except ValueError:
            import sys

            exc_info = sys.exc_info()
    rec = logging.LogRecord("quantpilot.engine", logging.INFO, __file__, 1, msg, None, exc_info)
    for k, v in extra.items():
        setattr(rec, k, v)
    return rec


def test_json_line_has_fields_extra_and_masks_secrets():
    rec = _record(
        symbol="KRW-BTC", strategy="vol_breakout", qty=0.01, api_key="sk-REAL", token="T1"
    )
    line = JsonFormatter().format(rec)
    assert "\n" not in line  # 한 줄
    out = json.loads(line)
    assert (
        out["level"] == "INFO"
        and out["logger"] == "quantpilot.engine"
        and out["msg"] == "주문 체결"
    )
    assert out["ts"].endswith("+00:00")  # UTC
    assert (out["symbol"], out["strategy"], out["qty"]) == ("KRW-BTC", "vol_breakout", 0.01)
    assert out["api_key"] == MASK and out["token"] == MASK
    assert "sk-REAL" not in line and "T1" not in line


def test_json_line_carries_exception_and_odd_values():
    out = json.loads(JsonFormatter().format(_record(exc=True, when=object())))
    assert "ValueError: boom" in out["exc"]
    assert isinstance(out["when"], str)  # JSON으로 못 바꾸는 값은 문자열로


def test_text_line_appends_extra_and_masks_secrets():
    line = TextFormatter().format(_record(symbol="KRW-ETH", password="pw-123"))
    assert "주문 체결 symbol=KRW-ETH" in line
    assert f"password={MASK}" in line and "pw-123" not in line


def test_setup_logging_replaces_only_its_own_handler():
    """다시 불러도 핸들러가 겹치지 않고, 다른 핸들러(pytest 캡처 등)는 그대로 둔다."""
    root = logging.getLogger()
    before = list(root.handlers)
    level = root.level
    try:
        setup_logging(fmt="json")
        setup_logging(fmt="json")
        ours = [h for h in root.handlers if getattr(h, "_quantpilot", False)]
        assert len(ours) == 1 and isinstance(ours[0].formatter, JsonFormatter)
        assert all(h in root.handlers for h in before)
        setup_logging(fmt="text")
        ours = [h for h in root.handlers if getattr(h, "_quantpilot", False)]
        assert len(ours) == 1 and isinstance(ours[0].formatter, TextFormatter)
    finally:
        root.handlers = before
        root.setLevel(level)


def test_setup_logging_routes_uvicorn_to_root():
    saved = {n: (logging.getLogger(n).handlers[:], logging.getLogger(n).propagate) for n in UVICORN}
    root = logging.getLogger()
    before, level = list(root.handlers), root.level
    try:
        logging.getLogger("uvicorn.access").addHandler(logging.NullHandler())
        logging.getLogger("uvicorn.access").propagate = False
        setup_logging(fmt="json", uvicorn=True)
        for n in UVICORN:
            assert logging.getLogger(n).handlers == [] and logging.getLogger(n).propagate
    finally:
        for n, (hs, prop) in saved.items():
            logging.getLogger(n).handlers = hs
            logging.getLogger(n).propagate = prop
        root.handlers = before
        root.setLevel(level)


def test_log_format_setting(monkeypatch):
    from pydantic import ValidationError

    from quantpilot.config import Settings

    assert Settings(_env_file=None).log_format == "text"
    monkeypatch.setenv("QP_LOG_FORMAT", "json")
    assert Settings(_env_file=None).log_format == "json"
    monkeypatch.setenv("QP_LOG_FORMAT", "xml")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_api_switches_to_json_only_when_configured(tmp_path):
    """API는 log_format=json일 때만 시작하면서 JSON 핸들러를 단다(테스트 기본 text는 그대로)."""
    from api_helpers import make_settings
    from fastapi.testclient import TestClient

    from quantpilot.api.app import create_app

    root = logging.getLogger()
    before, level = list(root.handlers), root.level
    saved = {n: (logging.getLogger(n).handlers[:], logging.getLogger(n).propagate) for n in UVICORN}
    try:
        with TestClient(create_app(settings=make_settings(tmp_path))):
            assert not [h for h in root.handlers if getattr(h, "_quantpilot", False)]
        with TestClient(create_app(settings=make_settings(tmp_path, log_format="json"))):
            ours = [h for h in root.handlers if getattr(h, "_quantpilot", False)]
            assert len(ours) == 1 and isinstance(ours[0].formatter, JsonFormatter)
    finally:
        for n, (hs, prop) in saved.items():
            logging.getLogger(n).handlers = hs
            logging.getLogger(n).propagate = prop
        root.handlers = before
        root.setLevel(level)
