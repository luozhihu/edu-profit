from datetime import date
from decimal import Decimal

import pytest

from app.models import RecordStatus
from app.services import (
    BusinessError,
    actual_received,
    confirm_payment,
    confirm_refund,
    create_payment_draft,
    create_refund_draft,
    create_student,
    dashboard_totals,
    refundable_amount,
    void_payment,
    void_refund,
)


def student(db, id_number="110101199001010011", phone="13800000000"):
    return create_student(db, name="张三", phone=phone, id_number=id_number)


def posted_payment(db):
    payment = create_payment_draft(
        db, student=student(db), receipt="1000", rebate="100", transfer_cost="50",
        training_period_text="2026春季班", address="北京市朝阳区",
        payment_date=date.today(), payment_method="转账",
    )
    confirm_payment(db, payment, receipt="1000", rebate="100", transfer_cost="50")
    return payment


def test_actual_received_formula():
    assert actual_received(Decimal("1000"), Decimal("100"), Decimal("50")) == Decimal("850.00")


def test_actual_received_must_be_positive():
    with pytest.raises(BusinessError):
        actual_received(Decimal("100"), Decimal("100"), Decimal("0"))


def test_phone_can_repeat_but_id_number_cannot(db):
    student(db)
    student(db, id_number="110101199001010022")
    with pytest.raises(BusinessError, match="身份证号已存在"):
        student(db, phone="13900000000")


def test_payment_confirmation_must_match_draft(db):
    payment = create_payment_draft(
        db, student=student(db), receipt="1000", rebate="0", transfer_cost="0",
        training_period_text="2026春季班", address="北京市朝阳区",
        payment_date=date.today(), payment_method="现金",
    )
    with pytest.raises(BusinessError, match="不一致"):
        confirm_payment(db, payment, receipt="999", rebate="0", transfer_cost="0")
    assert payment.status == RecordStatus.DRAFT


def test_refund_cannot_exceed_actual_received(db):
    payment = posted_payment(db)
    refund = create_refund_draft(db, payment=payment, amount="900", refund_date=date.today(), reason="退费")
    with pytest.raises(BusinessError, match="超过"):
        confirm_refund(db, refund, amount="900")


def test_payment_with_posted_refund_cannot_be_voided(db):
    payment = posted_payment(db)
    refund = create_refund_draft(db, payment=payment, amount="100", refund_date=date.today(), reason="退费")
    confirm_refund(db, refund, amount="100")
    assert refundable_amount(payment) == Decimal("750.00")
    with pytest.raises(BusinessError, match="先作废关联退款"):
        void_payment(db, payment, reason="录错")
    void_refund(db, refund, reason="退款录错")
    void_payment(db, payment, reason="缴费录错")
    assert payment.status == RecordStatus.VOIDED


def test_dashboard_totals_use_payment_status_and_requested_formulas(db):
    posted = posted_payment(db)
    draft = create_payment_draft(
        db,
        student=posted.student,
        receipt="500",
        rebate="50",
        transfer_cost="25",
        training_period_text="2026夏季班",
        address="北京市海淀区",
        payment_date=date.today(),
        payment_method="转账",
    )

    totals = dashboard_totals([posted, draft])

    assert totals == {
        "income": Decimal("1000.00"),
        "expense": Decimal("150.00"),
        "payable": Decimal("425.00"),
        "balance": Decimal("850.00"),
    }


def test_training_period_and_address_belong_to_payment(db):
    payment = posted_payment(db)

    assert payment.student.training_period_text == ""
    assert payment.student.address == ""
    assert payment.training_period_text == "2026春季班"
    assert payment.address == "北京市朝阳区"
    assert payment.training_period_snapshot == "2026春季班"
