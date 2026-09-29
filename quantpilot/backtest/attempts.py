"""파라미터 시도 횟수 카운터 — 과최적화 경고 (Bailey · López de Prado).

전략별로 '서로 다른 파라미터 조합'을 몇 번 시도했는지 JSON에 기록한다.
같은 조합의 재실행은 세지 않는다. 7회를 넘기면 결과에 경고를 붙인다.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

WARN_AFTER = 7


class AttemptTracker:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._data: dict = json.loads(self.path.read_text()) if self.path.exists() else {}

    @staticmethod
    def _key(params: dict) -> str:
        return hashlib.sha1(json.dumps(params, sort_keys=True, default=str).encode()).hexdigest()[:12]

    def record(self, strategy: str, params: dict, universe: tuple[str, ...]) -> dict:
        entry = self._data.setdefault(strategy, {"attempts": {}, "first": None})
        key = self._key({"p": params, "u": sorted(universe)})
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if key not in entry["attempts"]:
            entry["attempts"][key] = {"params": params, "universe": list(universe), "at": now}
        entry["first"] = entry["first"] or now
        self.path.write_text(json.dumps(self._data, indent=2, ensure_ascii=False, default=str))
        n = len(entry["attempts"])
        return {"strategy": strategy, "distinct_attempts": n, "warn_after": WARN_AFTER,
                "overfit_warning": n > WARN_AFTER}

    def count(self, strategy: str) -> int:
        return len(self._data.get(strategy, {}).get("attempts", {}))
