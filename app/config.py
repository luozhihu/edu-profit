import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    app_env: str = os.getenv("APP_ENV", "development")
    app_secret: str = os.getenv("APP_SECRET", "development-only-secret")
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./data/edu_profit.db")
    upload_dir: Path = Path(os.getenv("UPLOAD_DIR", "./uploads"))
    max_upload_bytes: int = int(os.getenv("MAX_UPLOAD_BYTES", "10485760"))


settings = Settings()
