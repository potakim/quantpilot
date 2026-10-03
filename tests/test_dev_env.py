"""개발 환경 회귀 방지 — Windows(한국어 로캘 cp949)에서도 파일 읽기·쓰기가 깨지지 않게 (t23).

배포는 리눅스(UTF-8)라 CI로는 안 잡히는 종류라서 소스를 직접 검사한다.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_IO = re.compile(r"\.(read_text|write_text)\(")


def _calls_without_encoding(path: Path) -> list[str]:
    """한 문장(괄호 짝이 맞을 때까지) 안에 encoding=이 없는 read_text/write_text 호출."""
    src = path.read_text(encoding="utf-8")
    bad = []
    for m in _IO.finditer(src):
        depth, i = 1, m.end()
        while i < len(src) and depth:
            depth += {"(": 1, ")": -1}.get(src[i], 0)
            i += 1
        if "encoding=" not in src[m.end() : i]:
            line = src.count("\n", 0, m.start()) + 1
            bad.append(f"{path.relative_to(ROOT)}:{line}")
    return bad


def test_text_file_io_always_names_utf8():
    """read_text()/write_text()는 encoding을 명시한다 — 기본값은 OS 로캘(cp949)이다."""
    files = [*ROOT.glob("quantpilot/**/*.py"), *ROOT.glob("tests/*.py"), *ROOT.glob("scripts/*.py")]
    bad = [b for f in files for b in _calls_without_encoding(f)]
    assert not bad, bad


def test_subprocess_text_mode_names_utf8():
    """subprocess의 text=True도 OS 로캘로 디코딩한다 — 한글 출력이 cp949에서 깨지면 stdout이 None이 된다."""
    bad = []
    for f in [
        *ROOT.glob("quantpilot/**/*.py"),
        *ROOT.glob("tests/*.py"),
        *ROOT.glob("scripts/*.py"),
    ]:
        if f.name == Path(__file__).name:
            continue  # 이 검사 파일은 패턴 문자열을 담고 있다
        lines = f.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            near = lines[max(0, i - 3) : i + 4]
            if "text=True" in line and not any("encoding=" in x for x in near):
                bad.append(f"{f.relative_to(ROOT)}:{i + 1}")
    assert not bad, bad


def test_alembic_ini_is_ascii():
    """alembic은 alembic.ini를 OS 로캘 인코딩으로 읽는다 — 한글 주석이 있으면 Windows에서 깨진다."""
    (ROOT / "alembic.ini").read_bytes().decode("ascii")
