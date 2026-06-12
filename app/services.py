from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    AuditEvent,
    IdempotencyKey,
    Payment,
    RecordStatus,
    Refund,
    Student,
)

MONEY = Decimal("0.01")


class BusinessError(ValueError):
    pass


def money(value: str | Decimal | int) -> Decimal:
    result = Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP)
    if Decimal(str(value)) != result:
        raise BusinessError("金额最多保留两位小数")
    return result


def actual_received(receipt: Decimal, rebate: Decimal, transfer_cost: Decimal) -> Decimal:
    result = money(receipt) - money(rebate) - money(transfer_cost)
    if min(receipt, rebate, transfer_cost) < 0:
        raise BusinessError("金额不能为负数")
    if result <= 0:
        raise BusinessError("实际收款金额必须大于 0")
    return result


def mask_id_number(value: str) -> str:
    clean = value.strip().upper()
    if len(clean) <= 8:
        return "*" * len(clean)
    return f"{clean[:4]}{'*' * (len(clean) - 8)}{clean[-4:]}"


def snapshot(entity) -> dict:
    hidden = {"id_number"}
    return {
        column.name: (
            str(getattr(entity, column.name))
            if getattr(entity, column.name) is not None and column.name not in hidden
            else None
        )
        for column in entity.__table__.columns
        if column.name not in hidden
    }


def audit(
    db: Session,
    *,
    action: str,
    entity,
    before: dict | None = None,
    reason: str | None = None,
    actor_id: str | None = None,
) -> None:
    db.add(
        AuditEvent(
            actor_id=actor_id,
            action=action,
            entity_type=entity.__class__.__name__,
            entity_id=entity.id,
            before_snapshot=before,
            after_snapshot=snapshot(entity),
            reason=reason,
        )
    )


def create_student(
    db: Session,
    *,
    name: str,
    phone: str,
    id_number: str,
    address: str = "",
    training_period_text: str = "",
    actor_id: str | None = None,
) -> Student:
    normalized_id = id_number.strip().upper()
    if not normalized_id:
        raise BusinessError("身份证号不能为空")
    if db.scalar(select(Student).where(Student.id_number == normalized_id)):
        raise BusinessError("身份证号已存在")
    student = Student(
        name=name.strip(),
        phone=phone.strip(),
        id_number=normalized_id,
        address=address.strip(),
        training_period_text=training_period_text.strip(),
    )
    db.add(student)
    db.flush()
    audit(db, action="student.created", entity=student, actor_id=actor_id)
    return student


def create_payment_draft(
    db: Session,
    *,
    student: Student,
    receipt: str | Decimal,
    rebate: str | Decimal,
    transfer_cost: str | Decimal,
    training_period_text: str,
    address: str,
    payment_date: date,
    payment_method: str,
    note: str = "",
    actor_id: str | None = None,
) -> Payment:
    if student.status.value != "ACTIVE":
        raise BusinessError("停用学员不能新增缴费")
    receipt_value, rebate_value, transfer_value = money(receipt), money(rebate), money(transfer_cost)
    payment = Payment(
        student=student,
        receipt_amount=receipt_value,
        rebate_amount=rebate_value,
        transfer_cost_amount=transfer_value,
        actual_received_amount=actual_received(receipt_value, rebate_value, transfer_value),
        training_period_text=training_period_text.strip(),
        address=address.strip(),
        payment_date=payment_date,
        payment_method=payment_method.strip(),
        note=note.strip(),
    )
    db.add(payment)
    db.flush()
    audit(db, action="payment.draft_created", entity=payment, actor_id=actor_id)
    return payment


def confirm_payment(
    db: Session,
    payment: Payment,
    *,
    receipt: str | Decimal,
    rebate: str | Decimal,
    transfer_cost: str | Decimal,
    actor_id: str | None = None,
) -> Payment:
    if payment.status != RecordStatus.DRAFT:
        raise BusinessError("该缴费草稿已处理")
    entered = (money(receipt), money(rebate), money(transfer_cost))
    expected = (payment.receipt_amount, payment.rebate_amount, payment.transfer_cost_amount)
    if entered != expected:
        raise BusinessError("再次输入的金额与草稿不一致")
    before = snapshot(payment)
    payment.actual_received_amount = actual_received(*entered)
    payment.student_name_snapshot = payment.student.name
    payment.student_id_masked_snapshot = mask_id_number(payment.student.id_number)
    payment.training_period_snapshot = payment.training_period_text
    payment.status = RecordStatus.POSTED
    payment.confirmed_at = datetime.utcnow()
    payment.version += 1
    audit(db, action="payment.posted", entity=payment, before=before, actor_id=actor_id)
    return payment


def dashboard_totals(payments: list[Payment]) -> dict[str, Decimal]:
    posted = [payment for payment in payments if payment.status == RecordStatus.POSTED]
    drafts = [payment for payment in payments if payment.status == RecordStatus.DRAFT]
    income = sum((payment.receipt_amount for payment in posted), Decimal("0"))
    expense = sum(
        (payment.rebate_amount + payment.transfer_cost_amount for payment in posted),
        Decimal("0"),
    )
    payable = sum((payment.actual_received_amount for payment in drafts), Decimal("0"))
    balance = sum((payment.actual_received_amount for payment in posted), Decimal("0"))
    return {
        "income": income,
        "expense": expense,
        "payable": payable,
        "balance": balance,
    }


def refundable_amount(payment: Payment) -> Decimal:
    refunded = sum(
        (refund.amount for refund in payment.refunds if refund.status == RecordStatus.POSTED),
        Decimal("0"),
    )
    return payment.actual_received_amount - refunded


def create_refund_draft(
    db: Session,
    *,
    payment: Payment,
    amount: str | Decimal,
    refund_date: date,
    reason: str,
    actor_id: str | None = None,
) -> Refund:
    if payment.status != RecordStatus.POSTED:
        raise BusinessError("退款必须关联有效缴费")
    amount_value = money(amount)
    if amount_value <= 0:
        raise BusinessError("退款金额必须大于 0")
    refund = Refund(payment=payment, amount=amount_value, refund_date=refund_date, reason=reason.strip())
    db.add(refund)
    db.flush()
    audit(db, action="refund.draft_created", entity=refund, actor_id=actor_id)
    return refund


def confirm_refund(
    db: Session,
    refund: Refund,
    *,
    amount: str | Decimal,
    actor_id: str | None = None,
) -> Refund:
    if refund.status != RecordStatus.DRAFT:
        raise BusinessError("该退款草稿已处理")
    entered = money(amount)
    if entered != refund.amount:
        raise BusinessError("再次输入的退款金额与草稿不一致")
    payment = db.scalar(select(Payment).where(Payment.id == refund.payment_id).with_for_update())
    if payment is None or payment.status != RecordStatus.POSTED:
        raise BusinessError("关联缴费不是有效状态")
    if entered > refundable_amount(payment):
        raise BusinessError("退款金额超过剩余可退金额")
    before = snapshot(refund)
    refund.status = RecordStatus.POSTED
    refund.confirmed_at = datetime.utcnow()
    refund.version += 1
    audit(db, action="refund.posted", entity=refund, before=before, actor_id=actor_id)
    return refund


def void_refund(db: Session, refund: Refund, *, reason: str, actor_id: str | None = None) -> Refund:
    db.scalar(select(Payment).where(Payment.id == refund.payment_id).with_for_update())
    if refund.status != RecordStatus.POSTED:
        raise BusinessError("只有已入账退款可以作废")
    if not reason.strip():
        raise BusinessError("作废原因不能为空")
    before = snapshot(refund)
    refund.status = RecordStatus.VOIDED
    refund.void_reason = reason.strip()
    refund.voided_at = datetime.utcnow()
    refund.version += 1
    audit(db, action="refund.voided", entity=refund, before=before, reason=reason, actor_id=actor_id)
    return refund


def void_payment(
    db: Session, payment: Payment, *, reason: str, actor_id: str | None = None
) -> Payment:
    locked = db.scalar(select(Payment).where(Payment.id == payment.id).with_for_update())
    if locked is None or locked.status != RecordStatus.POSTED:
        raise BusinessError("只有已入账缴费可以作废")
    if any(refund.status == RecordStatus.POSTED for refund in locked.refunds):
        raise BusinessError("该缴费存在有效退款，请先作废关联退款")
    if not reason.strip():
        raise BusinessError("作废原因不能为空")
    before = snapshot(locked)
    locked.status = RecordStatus.VOIDED
    locked.void_reason = reason.strip()
    locked.voided_at = datetime.utcnow()
    locked.version += 1
    audit(db, action="payment.voided", entity=locked, before=before, reason=reason, actor_id=actor_id)
    return locked


def request_hash(payload: dict) -> str:
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def claim_idempotency(
    db: Session, *, actor_id: str, operation: str, key: str, payload: dict
) -> IdempotencyKey:
    existing = db.scalar(
        select(IdempotencyKey).where(
            IdempotencyKey.actor_id == actor_id,
            IdempotencyKey.operation == operation,
            IdempotencyKey.key == key,
        )
    )
    digest = request_hash(payload)
    if existing:
        if existing.request_hash != digest:
            raise BusinessError("同一幂等键不能用于不同请求")
        return existing
    record = IdempotencyKey(actor_id=actor_id, operation=operation, key=key, request_hash=digest)
    db.add(record)
    db.flush()
    return record
