import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.bootstrap import password_hash
from app.db import Base, get_db
from app.main import app
from app.models import Payment, Student, User


def test_production_bootstrap_requires_configured_admin_password():
    from app.bootstrap import initial_users

    with pytest.raises(RuntimeError, match="BOOTSTRAP_ADMIN_PASSWORD"):
        initial_users("production")


def test_production_bootstrap_only_creates_configured_admin():
    from app.bootstrap import initial_users

    assert initial_users("production", "owner", "strong-password") == [
        ("owner", "admin", "strong-password")
    ]


def test_dashboard_layout_uses_full_width_responsive_grid():
    css = Path("app/static/app.css").read_text()
    summary_rule = css.split(".dashboard-summary {", 1)[1].split("}", 1)[0]
    metrics_rule = css.split(".dashboard-metrics {", 1)[1].split("}", 1)[0]

    assert "max-width" not in summary_rule
    assert "grid-template-columns: repeat(4, minmax(0, 1fr));" in metrics_rule


def test_login_and_student_creation():
    with tempfile.TemporaryDirectory() as tmp:
        engine = create_engine(f"sqlite:///{Path(tmp) / 'test.db'}", connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine, expire_on_commit=False)
        with Session.begin() as db:
            db.add(User(username="finance", role="finance", password_hash=password_hash("finance-dev")))

        def override():
            with Session() as db:
                yield db

        app.dependency_overrides[get_db] = override
        client = TestClient(app)
        response = client.post("/login", data={"username": "finance", "password": "finance-dev"})
        assert response.status_code == 200
        assert 'class="dashboard-summary"' in response.text
        assert response.text.count('class="dashboard-metric ') == 4
        assert 'class="metric-strip"' not in response.text

        response = client.post(
            "/students",
            data={"name": "李四", "phone": "13800000000", "id_number": "110101199001010099"},
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert "李四" in response.text
        assert "培训期数" in response.text
        assert "地址" in response.text

        with Session() as db:
            student = db.scalar(select(Student).where(Student.name == "李四"))

        missing_fields = client.post(
            f"/students/{student.id}/payments",
            data={
                "receipt_amount": "1000",
                "rebate_amount": "100",
                "transfer_cost_amount": "50",
                "payment_date": "2026-06-12",
                "payment_method": "转账",
            },
        )
        assert missing_fields.status_code == 422

        complete = client.post(
            f"/students/{student.id}/payments",
            data={
                "receipt_amount": "1000",
                "rebate_amount": "100",
                "transfer_cost_amount": "50",
                "training_period_text": "2026第1期",
                "address": "上海市浦东新区",
                "payment_date": "2026-06-12",
                "payment_method": "转账",
            },
            follow_redirects=False,
        )
        assert complete.status_code == 303

        with Session() as db:
            payment = db.scalar(select(Payment).where(Payment.student_id == student.id))
            assert payment.training_period_text == "2026第1期"
            assert payment.address == "上海市浦东新区"

        confirmation = client.get(f"/payments/{payment.id}/confirm")
        assert confirmation.status_code == 200
        assert "草稿金额" in confirmation.text
        assert "¥1000.00" in confirmation.text
        assert "¥100.00" in confirmation.text
        assert "¥50.00" in confirmation.text
        assert "¥850.00" in confirmation.text
        assert 'name="receipt_amount" type="number" min="0" step="0.01" required autofocus' in confirmation.text

        detail = client.get(f"/payments/{payment.id}")
        assert detail.status_code == 200
        assert "缴费详情" in detail.text
        assert "¥1000.00" in detail.text
        assert "¥100.00" in detail.text
        assert "¥50.00" in detail.text
        assert "¥850.00" in detail.text
        assert f'href="/payments/{payment.id}"' in client.get(f"/students/{student.id}").text

        app.dependency_overrides.clear()
