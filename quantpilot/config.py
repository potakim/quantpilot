"""설정. 거래소 API 키는 환경변수(.env)로만 주입하고 출금 권한은 부여하지 않는다."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="QP_", extra="ignore")

    env: str = "dev"
    data_dir: Path = Path("data")
    initial_cash_krw: float = 10_000_000
    initial_cash_usd: float = 10_000
    paper: bool = True  # False로 바꾸는 것은 관문 통과 후 별도 플로우에서만

    # 저장소 (02 문서). 비어 있으면 data_dir 아래 SQLite 파일을 쓴다.
    database_url: str = ""

    # 판단 계층
    judge_provider: str = "stub"  # stub | typesafe | laya
    llm_providers: list[str] = Field(default_factory=lambda: ["stub", "stub"])
    gate_hold_below: float = 0.5
    gate_full_above: float = 0.9

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
    google_api_key: str = ""

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
