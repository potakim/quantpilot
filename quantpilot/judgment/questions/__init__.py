"""판단 모델 원자 질문 문구 (06 §2). 버전별 YAML을 읽어 TypeSafe 요청 형식과 prompt_hash를 만든다.

키·종류·옵션의 기준은 `judgment/base.py::DEFAULT_QUESTIONS`(코드 상수)이고, 이 모듈은 모델에 보낼 문구만 담당한다.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

try:  # PyYAML은 어댑터 쪽 의존성 (코어는 pandas·numpy만)
    import yaml
except ImportError:  # pragma: no cover - 설치 환경 문제
    yaml = None

_DIR = Path(__file__).parent


@dataclass(frozen=True)
class QuestionSet:
    """버전 하나의 질문 문구 묶음. spec은 키 → {type, instructions, criteria}."""

    version: str
    spec: dict[str, dict[str, Any]]

    @property
    def prompt_hash(self) -> str:
        """문구 전체의 sha256 앞 16자리. 판단 로그에 남겨 문구 버전을 추적한다."""
        blob = json.dumps(self.spec, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()[:16]

    def levels(self, key: str) -> int:
        """score 질문의 단계 수 (criteria 길이)."""
        return len(self.spec[key]["criteria"])


@cache
def load_questions(version: str = "v1") -> QuestionSet:
    """judgment/questions/<version>.yaml을 읽는다. 결과는 캐시된다."""
    if yaml is None:
        raise ImportError("질문 YAML을 읽으려면 PyYAML이 필요하다: uv pip install pyyaml")
    data = yaml.safe_load((_DIR / f"{version}.yaml").read_text(encoding="utf-8"))
    if data.get("version") != version:
        raise ValueError(f"{version}.yaml의 version 필드가 {data.get('version')!r}")
    spec = data["questions"]
    for key, q in spec.items():
        if q["type"] == "choice" and not isinstance(q["criteria"], dict):
            raise ValueError(f"{key}: choice criteria는 옵션 → 설명 dict여야 한다")
        if q["type"] == "score" and (not isinstance(q["criteria"], list) or len(q["criteria"]) < 2):
            raise ValueError(f"{key}: score criteria는 2단계 이상의 list여야 한다")
    return QuestionSet(version, spec)
