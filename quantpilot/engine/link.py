"""engine ↔ scheduler 연결 — DB `settings`를 우편함으로 쓰는 EngineLink (04 §8, 01 §2).

engine과 scheduler는 다른 프로세스다. Redis 허브(P1-12) 전까지 둘은 settings 키로만 대화한다.

- `engine.heartbeat.<market>` = 마지막 하트비트(UTC ISO). engine이 몇 초마다 쓴다.
- `engine.cmd.<market>.time_exit.<strategy>` = {"id", "ts"}. scheduler가 시간 청산을 요청한다.
- `engine.ack.<market>.time_exit.<strategy>` = 처리한 명령 id. 명령 키와 다르면 대기 중.
- `engine.halt.<market>` = {"reason", "ts", ...}. Reconciler가 걸고, 사람 조작만 지운다(ADR 0015).
  엔진은 하트비트 때마다 읽어서 RiskManager.halted_reason에 반영한다.
- `engine.prescreen.<market>` = {"day", "blocked": {symbol: 사유}, "ts"}. 08:10 사전 심사 결과
  (ADR 0004·0022). scheduler만 쓰고, 엔진은 하트비트 때마다 읽어 판단 파이프라인에 넘긴다.

전략마다 키가 따로라서 한 키를 두 프로세스가 동시에 고치지 않는다(명령은 scheduler만, ack는 처리한 쪽만).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from quantpilot.core.models import Market
from quantpilot.core.repos import ConfigRepo


def _utcnow() -> datetime:
    return datetime.now(UTC)


class SettingsEngineLink:
    """core.ports.EngineLink 구현 (settings 키-값)."""

    def __init__(self, config: ConfigRepo, *, utcnow: Callable[[], datetime] = _utcnow) -> None:
        self.config = config
        self.utcnow = utcnow

    @staticmethod
    def _key(kind: str, market: Market, strategy: str = "") -> str:
        m = Market(market).value
        return (
            f"engine.heartbeat.{m}"
            if kind == "heartbeat"
            else f"engine.{kind}.{m}.time_exit.{strategy}"
        )

    async def beat(self, market: Market) -> None:
        """엔진이 살아 있음을 기록한다."""
        await self.config.set_setting(self._key("heartbeat", market), self.utcnow().isoformat())

    async def last_beat(self, market: Market) -> datetime | None:
        """마지막 하트비트 시각(UTC). 없으면 None."""
        raw = await self.config.get_setting(self._key("heartbeat", market))
        return None if raw is None else datetime.fromisoformat(raw)

    async def request_time_exit(self, market: Market, strategy: str) -> str:
        """시간 청산 명령을 넣고 명령 id를 돌려준다. 이전 미처리 명령은 이 명령으로 대체된다."""
        cmd_id = uuid4().hex[:12]
        value = {"id": cmd_id, "ts": self.utcnow().isoformat()}
        await self.config.set_setting(self._key("cmd", market, strategy), value)
        return cmd_id

    async def pending_time_exit(self, market: Market, strategy: str) -> str | None:
        """아직 처리되지 않은 명령 id. 없으면 None."""
        cmd = await self.config.get_setting(self._key("cmd", market, strategy))
        if not cmd:
            return None
        acked = await self.config.get_setting(self._key("ack", market, strategy))
        return None if acked == cmd["id"] else cmd["id"]

    async def ack_time_exit(self, market: Market, strategy: str, cmd_id: str) -> None:
        """명령을 처리했다고 기록한다."""
        await self.config.set_setting(self._key("ack", market, strategy), cmd_id)

    @staticmethod
    def _halt_key(market: Market) -> str:
        return f"engine.halt.{Market(market).value}"

    async def halt(self, market: Market, reason: str, detail: dict[str, Any] | None = None) -> None:
        """엔진에 할트를 건다. 이미 걸려 있으면 처음 기록을 둔다."""
        if await self.halt_reason(market):
            return
        value = {"reason": reason, "ts": self.utcnow().isoformat(), **(detail or {})}
        await self.config.set_setting(self._halt_key(market), value)

    async def halt_reason(self, market: Market) -> str | None:
        """걸려 있는 할트 사유. 없으면 None."""
        raw = await self.config.get_setting(self._halt_key(market))
        return raw.get("reason") if raw else None

    async def clear_halt(self, market: Market) -> None:
        """할트를 푼다 (사람 조작 경로에서만)."""
        await self.config.set_setting(self._halt_key(market), None)

    @staticmethod
    def _prescreen_key(market: Market) -> str:
        return f"engine.prescreen.{Market(market).value}"

    async def set_prescreen(self, market: Market, day: str, blocked: dict[str, str]) -> None:
        """그 거래일(day, 현지 YYYY-MM-DD)의 사전 심사 제외 목록 {symbol: 사유}을 쓴다."""
        value = {"day": day, "blocked": dict(blocked), "ts": self.utcnow().isoformat()}
        await self.config.set_setting(self._prescreen_key(market), value)

    async def prescreen(self, market: Market) -> tuple[str, dict[str, str]] | None:
        """마지막 사전 심사 (day, {symbol: 사유}). 없으면 None."""
        raw = await self.config.get_setting(self._prescreen_key(market))
        if not raw:
            return None
        return str(raw.get("day", "")), dict(raw.get("blocked") or {})

    @staticmethod
    def _rules_key(market: Market) -> str:
        return f"engine.rules.{Market(market).value}"

    async def merge_rules(
        self, market: Market, days: dict[str, dict[str, dict[str, set[str]]]], keep: int = 35
    ) -> None:
        """거래일별 규칙 평가 기록을 저장된 값과 합집합으로 합쳐 쓴다 (ADR 0027). 최근 keep일만 남긴다."""
        stored = await self.rules(market)
        for day, by_strategy in days.items():
            for name, rec in by_strategy.items():
                cur = stored.setdefault(day, {}).setdefault(name, {})
                for k in ("evaluated", "signaled"):
                    cur[k] = sorted(set(cur.get(k, [])) | set(rec.get(k, ())))
        kept = {d: stored[d] for d in sorted(stored)[-keep:]}
        await self.config.set_setting(self._rules_key(market), {"days": kept})

    async def rules(self, market: Market) -> dict[str, dict[str, dict[str, list[str]]]]:
        """저장된 거래일별 규칙 평가 기록 {day: {strategy: {evaluated, signaled}}}. 없으면 빈 dict."""
        raw = await self.config.get_setting(self._rules_key(market))
        return dict((raw or {}).get("days") or {})
