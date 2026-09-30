"""TypeSafe Jev 어댑터 (04 §4, 06 §2·§7, ADR 0012).

`POST {base_url}/v1/systemone` 한 번에 원자 질문 6개를 보내고 확률·확신도만 받는다.
- 응답 계약: answers[key] = {type, confidence, probabilities, choice | score, legend}, usage = {input_tokens, output_tokens}
- choice → 옵션별 확률 dict. score → 0..N-1 단계 기댓값을 N-1로 나눠 0~1로 바꾼다.
- `JudgeResult.confidence`는 질문별 confidence의 최솟값 (보수적).
- 총 소요 3초 초과 → `JudgeTimeout`, HTTP 오류·계약 위반 → `JudgeError`. 파이프라인이 hold로 처리한다.
- API 키는 `QP_TYPESAFE_API_KEY`(설정 `typesafe_api_key`)로만 받고 로그·repr·raw·예외 메시지에 넣지 않는다 (불변식 #10).
Kev(TypeSafe 호환 로컬 서버)는 `base_url`만 바꿔 쓴다.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from quantpilot.core.errors import JudgeError, JudgeTimeout
from quantpilot.core.models import JudgeResult
from quantpilot.judgment.base import DEFAULT_QUESTIONS, JudgeProvider, Question, State
from quantpilot.judgment.pricing import cost_usd, price_for
from quantpilot.judgment.questions import QuestionSet, load_questions

try:
    import httpx
except ImportError:  # pragma: no cover - httpx는 기본 의존성이지만 어댑터 규칙대로 감싼다
    httpx = None

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.typesafe.ai"
_PROB_TOL = 0.01  # 확률 합이 1에서 이만큼 벗어나면 계약 위반


class TypeSafeJudge(JudgeProvider):
    """TypeSafe System One(Jev) 판단 모델. 확률·확신도만 묻는다 — 수량·가격·손절은 묻지 않는다 (불변식 #7)."""

    name = "typesafe"

    def __init__(
        self,
        api_key: str,
        model: str = "jev-latest",
        timeout: float = 3.0,
        *,
        base_url: str = DEFAULT_BASE_URL,
        questions_version: str = "v1",
        transport: Any = None,
    ) -> None:
        if httpx is None:
            raise ImportError("TypeSafeJudge에는 httpx가 필요하다")
        if not api_key:
            raise ValueError("TypeSafe API 키가 비어 있다 (환경변수 QP_TYPESAFE_API_KEY)")
        price_for(model)  # 단가표에 없는 모델이면 호출마다가 아니라 시작할 때 실패한다
        self._api_key = api_key
        self.model = model
        self.timeout = timeout
        self.base_url = base_url.rstrip("/")
        self.questions: QuestionSet = load_questions(questions_version)
        self._transport = transport  # 테스트용 httpx.MockTransport / AsyncBaseTransport
        self.consecutive_timeouts = 0  # 연속 10회면 judge_down (P1-08 파이프라인이 읽는다)

    @classmethod
    def from_settings(cls, settings: Any, **kwargs: Any) -> TypeSafeJudge:
        """설정(`QP_TYPESAFE_API_KEY`)에서 키를 읽어 만든다."""
        return cls(settings.typesafe_api_key, **kwargs)

    def __repr__(self) -> str:
        return f"TypeSafeJudge(model={self.model!r}, base_url={self.base_url!r})"

    @property
    def url(self) -> str:
        """요청 엔드포인트."""
        return f"{self.base_url}/v1/systemone"

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}

    def payload(self, state: State, questions: tuple[Question, ...] = DEFAULT_QUESTIONS) -> dict:
        """요청 본문. state는 render() 문자열, 질문 문구는 YAML에서 가져온다."""
        spec = self.questions.spec
        missing = [q.key for q in questions if q.key not in spec]
        if missing:
            raise ValueError(f"질문 문구 {self.questions.version}에 없는 키: {missing}")
        for q in questions:
            if spec[q.key]["type"] != q.kind:
                raise ValueError(f"{q.key}: 코드 종류 {q.kind} ≠ 문구 종류 {spec[q.key]['type']}")
        return {
            "state": state.render(),
            "model": self.model,
            "questions": {q.key: spec[q.key] for q in questions},
        }

    def parse(
        self, body: Any, questions: tuple[Question, ...], latency_ms: float = 0.0
    ) -> JudgeResult:
        """응답 본문 → JudgeResult. 계약에 어긋나면 JudgeError."""
        try:
            got = body["answers"]
            answers: dict = {}
            confs: list[float] = []
            for q in questions:
                a = got[q.key]
                if a["type"] != q.kind:
                    raise JudgeError(f"{q.key}: 응답 종류 {a['type']!r} ≠ {q.kind!r}")
                conf = float(a["confidence"])
                if not 0.0 <= conf <= 1.0:
                    raise JudgeError(f"{q.key}: confidence {conf} 범위 밖")
                confs.append(conf)
                if q.kind == "choice":
                    probs = {k: float(v) for k, v in a["probabilities"].items()}
                    if set(probs) != set(q.options):
                        raise JudgeError(f"{q.key}: 옵션 {sorted(probs)} ≠ {sorted(q.options)}")
                    if abs(sum(probs.values()) - 1.0) > _PROB_TOL:
                        raise JudgeError(f"{q.key}: 확률 합 {sum(probs.values()):.3f}")
                    answers[q.key] = probs
                else:
                    top = self.questions.levels(q.key) - 1
                    score = float(a["score"])
                    if not 0.0 <= score <= top:
                        raise JudgeError(f"{q.key}: score {score} 범위(0~{top}) 밖")
                    answers[q.key] = round(score / top, 4)
            usage = body.get("usage") or {}
            resp_model = str(body.get("model") or self.model)
            tokens = int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))
            try:
                cost = cost_usd(resp_model, *tokens)
            except ValueError:  # 응답 모델명이 표에 없으면 요청 모델(시작 시 검증됨) 단가로
                cost = cost_usd(self.model, *tokens)
        except (KeyError, TypeError, ValueError, AttributeError) as e:
            raise JudgeError(f"응답 계약 위반: {type(e).__name__}: {e}") from None
        if not confs:
            raise JudgeError("질문이 없다")
        return JudgeResult(
            answers=answers,
            confidence=min(confs),
            latency_ms=latency_ms,
            model=f"typesafe:{resp_model}",
            cost_usd=cost,
            raw={
                "answers": got,
                "usage": usage,
                "prompt_hash": self.questions.prompt_hash,
                "questions_version": self.questions.version,
            },
        )

    def _finish(
        self, resp: Any, questions: tuple[Question, ...], t0: float, state: State
    ) -> JudgeResult:
        latency_ms = (time.perf_counter() - t0) * 1000
        if latency_ms > self.timeout * 1000:  # 늦게 온 답은 쓰지 않는다
            raise self._timeout(state, latency_ms)
        if resp.status_code != 200:
            self._log_error(state, f"HTTP {resp.status_code}")
            raise JudgeError(f"TypeSafe HTTP {resp.status_code}")
        try:
            body = resp.json()
        except ValueError:
            raise JudgeError("TypeSafe 응답이 JSON이 아니다") from None
        result = self.parse(body, questions, latency_ms)
        self.consecutive_timeouts = 0
        return result

    def _timeout(self, state: State, latency_ms: float) -> JudgeTimeout:
        self.consecutive_timeouts += 1
        log.warning(
            "typesafe timeout",
            extra={
                "symbol": state.symbol,
                "strategy": state.strategy,
                "provider": self.name,
                "latency_ms": round(latency_ms),
                "consecutive_timeouts": self.consecutive_timeouts,
            },
        )
        return JudgeTimeout(f"TypeSafe {self.timeout:g}초 초과")

    def _log_error(self, state: State, error: str) -> None:
        log.warning(
            "typesafe error",
            extra={
                "symbol": state.symbol,
                "strategy": state.strategy,
                "provider": self.name,
                "error": error,
            },
        )

    def judge(
        self, state: State, questions: tuple[Question, ...] = DEFAULT_QUESTIONS
    ) -> JudgeResult:
        """동기 판단 (CLI·API용). 틱 루프에서는 ajudge를 쓴다."""
        payload = self.payload(state, questions)
        t0 = time.perf_counter()
        try:
            with httpx.Client(transport=self._transport, timeout=self.timeout) as client:
                resp = client.post(self.url, json=payload, headers=self._headers())
        except httpx.TimeoutException:
            raise self._timeout(state, (time.perf_counter() - t0) * 1000) from None
        except httpx.HTTPError as e:
            self._log_error(state, type(e).__name__)
            raise JudgeError(f"TypeSafe 연결 실패: {type(e).__name__}") from None
        return self._finish(resp, questions, t0, state)

    async def ajudge(
        self, state: State, questions: tuple[Question, ...] = DEFAULT_QUESTIONS
    ) -> JudgeResult:
        """비동기 판단. 연결·응답까지 총 timeout초를 넘기면 JudgeTimeout."""
        payload = self.payload(state, questions)
        t0 = time.perf_counter()
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=self.timeout) as client:
                resp = await asyncio.wait_for(
                    client.post(self.url, json=payload, headers=self._headers()), self.timeout
                )
        except (TimeoutError, httpx.TimeoutException):
            raise self._timeout(state, (time.perf_counter() - t0) * 1000) from None
        except httpx.HTTPError as e:
            self._log_error(state, type(e).__name__)
            raise JudgeError(f"TypeSafe 연결 실패: {type(e).__name__}") from None
        return self._finish(resp, questions, t0, state)
