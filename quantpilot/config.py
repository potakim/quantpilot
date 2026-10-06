"""설정. 거래소 API 키는 환경변수(.env)로만 주입하고 출금 권한은 부여하지 않는다."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # data/keys.env는 POST /settings/keys가 쓰는 파일 — 재시작 뒤 환경변수처럼 읽힌다 (ADR 0017 §4)
    # 빈 환경변수(.env의 `QP_X=` 줄)는 "설정 안 함"으로 본다 — 화면에서 등록한 키를 덮지 않게 (ADR 0029)
    model_config = SettingsConfigDict(
        env_file=(".env", "data/keys.env"),
        env_prefix="QP_",
        extra="ignore",
        env_ignore_empty=True,
    )

    env: str = "dev"
    log_format: Literal["text", "json"] = "text"  # 배포 compose는 json (07 §5, logsetup.py)
    data_dir: Path = Path("data")
    initial_cash_krw: float = 10_000_000
    initial_cash_usd: float = 10_000
    paper: bool = True  # False로 바꾸는 것은 관문 통과 후 별도 플로우에서만

    # 저장소 (02 문서). 비어 있으면 data_dir 아래 SQLite 파일을 쓴다.
    database_url: str = ""

    # 실시간 허브 (02 §2). 비어 있으면 프로세스 내 MemoryHub (ADR 0017)
    redis_url: str = ""

    # API 인증 (03 §1, 07 §2). 값은 응답·로그에 절대 나오지 않는다 (불변식 #10)
    admin_password: str = ""
    jwt_secret: str = ""  # 32바이트 이상
    jwt_ttl_hours: int = 12
    keys_file: Path = Path("data/keys.env")

    # 판단 계층
    judge_provider: str = "stub"  # stub | typesafe | laya
    llm_providers: list[str] = Field(default_factory=lambda: ["stub", "stub"])
    gate_hold_below: float = 0.5
    gate_full_above: float = 0.9
    ai_budget_usd_daily: float = 2.0  # 넘으면 LLM 합의 중단(= hold), 판단 모델은 계속 (06 §7)

    # 뉴스·이벤트 (ADR 0021). news_file이 비면 패키지 기본값 quantpilot/data/news_sources.yaml
    news_file: Path | None = None
    events_file: Path = Path("data/events.yaml")  # 수동 캘린더(FOMC·점검 등), 없으면 빈 캘린더

    # 리스크 규칙은 설정에 없다 — ADR 0003: execution/risk.py::RiskRules 코드 상수.

    # 백테스트
    holdout_months: int = 12

    # 외부 키 (선택)
    upbit_access_key: str = ""
    upbit_secret_key: str = ""
    kis_app_key: str = ""
    kis_app_secret: str = ""
    kis_account: str = ""
    alpaca_key: str = ""
    alpaca_secret: str = ""
    typesafe_api_key: str = ""
    anthropic_api_key: str = ""
    google_api_key: str = ""  # Gemini 요약기·리뷰어 (judgment/google.py)
    dart_api_key: str = ""  # OpenDART 공시 목록 (data/news.py, P1-06)
    # 알림 (07 §6, notify/telegram.py). 비어 있으면 로그로만
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def db_url(self) -> str:
        return self.database_url or f"sqlite+aiosqlite:///{self.data_dir / 'quantpilot.db'}"

    @property
    def attempts_file(self) -> Path:
        return self.data_dir / "backtest_attempts.json"


settings = Settings()
