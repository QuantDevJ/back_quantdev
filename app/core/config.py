from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_ENV_FILES = tuple(str(p) for p in (_PROJECT_ROOT / ".env",) if p.is_file())


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_FILES or str(_PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: Optional[str] = None
    db_user: str = "postgres"
    db_password: str = "postgres"
    db_host: str = "localhost"
    db_port: int = 5432
    db_name: str = "quantly"
    jwt_secret_key: str = "replace-with-strong-secret"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7
    reset_token_expire_minutes: int = 15
    email_encryption_key: Optional[str] = None
    # Base64-encoded 32-byte key; alias for sensitive storage (e.g. Plaid). Env: DATA_ENCRYPTION_KEY
    data_encryption_key: Optional[str] = None
    app_debug: bool = True

    plaid_client_id: Optional[str] = None
    plaid_secret: Optional[str] = None
    plaid_env: str = "sandbox"
    backend_base_url: Optional[str] = None

    def get_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return (
            f"postgresql+psycopg://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )


settings = Settings()
