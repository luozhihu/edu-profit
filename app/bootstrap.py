import hashlib
import os
from pathlib import Path

from sqlalchemy import inspect, text

from app.config import settings
from app.db import Base, SessionLocal, engine
from app.models import User


def password_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def ensure_legacy_columns() -> None:
    columns = {column["name"] for column in inspect(engine).get_columns("payments")}
    with engine.begin() as connection:
        if "training_period_text" not in columns:
            connection.execute(
                text("ALTER TABLE payments ADD COLUMN training_period_text VARCHAR(160) DEFAULT ''")
            )
        if "address" not in columns:
            connection.execute(text("ALTER TABLE payments ADD COLUMN address TEXT DEFAULT ''"))


def initial_users(
    app_env: str,
    admin_username: str = "admin",
    admin_password: str = "",
) -> list[tuple[str, str, str]]:
    if app_env == "production":
        if not admin_password:
            raise RuntimeError("BOOTSTRAP_ADMIN_PASSWORD is required in production")
        return [(admin_username.strip(), "admin", admin_password)]
    return [
        ("finance", "finance", "finance-dev"),
        ("admin", "admin", "admin-dev"),
    ]


def main() -> None:
    Path("./data").mkdir(exist_ok=True)
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(engine)
    ensure_legacy_columns()
    users = initial_users(
        settings.app_env,
        os.getenv("BOOTSTRAP_ADMIN_USERNAME", "admin"),
        os.getenv("BOOTSTRAP_ADMIN_PASSWORD", ""),
    )
    with SessionLocal.begin() as db:
        for username, role, password in users:
            if not db.query(User).filter_by(username=username).first():
                db.add(User(username=username, role=role, password_hash=password_hash(password)))
        if settings.app_env == "production":
            username = users[0][0]
            print(f"Bootstrap complete. Production administrator: {username}")
        else:
            print("Bootstrap complete. Development login: finance / finance-dev")


if __name__ == "__main__":
    main()
