from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def new_id() -> str:
    return str(uuid4())


class RecordStatus(StrEnum):
    DRAFT = "DRAFT"
    POSTED = "POSTED"
    VOIDED = "VOIDED"
    ABANDONED = "ABANDONED"


class StudentStatus(StrEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class AttachmentStatus(StrEnum):
    UPLOADING = "UPLOADING"
    READY = "READY"
    FROZEN = "FROZEN"
    DELETE_PENDING = "DELETE_PENDING"
    FAILED = "FAILED"


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    username: Mapped[str] = mapped_column(String(80), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default="finance")
    is_active: Mapped[bool] = mapped_column(default=True)


class Student(Base):
    __tablename__ = "students"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120))
    phone: Mapped[str] = mapped_column(String(40))
    id_number: Mapped[str] = mapped_column(String(80), unique=True)
    address: Mapped[str] = mapped_column(Text, default="")
    training_period_text: Mapped[str] = mapped_column(String(160), default="")
    status: Mapped[StudentStatus] = mapped_column(
        Enum(StudentStatus), default=StudentStatus.ACTIVE
    )
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    payments: Mapped[list[Payment]] = relationship(back_populates="student")


class Payment(Base):
    __tablename__ = "payments"
    __table_args__ = (
        CheckConstraint("receipt_amount >= 0", name="payment_receipt_nonnegative"),
        CheckConstraint("rebate_amount >= 0", name="payment_rebate_nonnegative"),
        CheckConstraint("transfer_cost_amount >= 0", name="payment_transfer_nonnegative"),
        CheckConstraint("actual_received_amount > 0", name="payment_actual_positive"),
        CheckConstraint(
            "actual_received_amount = receipt_amount - rebate_amount - transfer_cost_amount",
            name="payment_actual_formula",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id"), index=True)
    receipt_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    rebate_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    transfer_cost_amount: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), default=Decimal("0")
    )
    actual_received_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    training_period_text: Mapped[str] = mapped_column(String(160), default="")
    address: Mapped[str] = mapped_column(Text, default="")
    payment_date: Mapped[date] = mapped_column(Date)
    payment_method: Mapped[str] = mapped_column(String(80))
    note: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[RecordStatus] = mapped_column(Enum(RecordStatus), default=RecordStatus.DRAFT)
    student_name_snapshot: Mapped[str | None] = mapped_column(String(120))
    student_id_masked_snapshot: Mapped[str | None] = mapped_column(String(80))
    training_period_snapshot: Mapped[str | None] = mapped_column(String(160))
    source_payment_id: Mapped[str | None] = mapped_column(ForeignKey("payments.id"))
    void_reason: Mapped[str | None] = mapped_column(Text)
    voided_at: Mapped[datetime | None] = mapped_column(DateTime)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    student: Mapped[Student] = relationship(back_populates="payments")
    refunds: Mapped[list[Refund]] = relationship(back_populates="payment")


class Refund(Base):
    __tablename__ = "refunds"
    __table_args__ = (CheckConstraint("amount > 0", name="refund_amount_positive"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    payment_id: Mapped[str] = mapped_column(ForeignKey("payments.id"), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    refund_date: Mapped[date] = mapped_column(Date)
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[RecordStatus] = mapped_column(Enum(RecordStatus), default=RecordStatus.DRAFT)
    source_refund_id: Mapped[str | None] = mapped_column(ForeignKey("refunds.id"))
    void_reason: Mapped[str | None] = mapped_column(Text)
    voided_at: Mapped[datetime | None] = mapped_column(DateTime)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    payment: Mapped[Payment] = relationship(back_populates="refunds")


class Attachment(Base):
    __tablename__ = "attachments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    storage_key: Mapped[str] = mapped_column(String(500), unique=True)
    original_name: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(120))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[AttachmentStatus] = mapped_column(
        Enum(AttachmentStatus), default=AttachmentStatus.UPLOADING
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class PaymentAttachment(Base):
    __tablename__ = "payment_attachments"
    payment_id: Mapped[str] = mapped_column(ForeignKey("payments.id"), primary_key=True)
    attachment_id: Mapped[str] = mapped_column(ForeignKey("attachments.id"), primary_key=True)


class RefundAttachment(Base):
    __tablename__ = "refund_attachments"
    refund_id: Mapped[str] = mapped_column(ForeignKey("refunds.id"), primary_key=True)
    attachment_id: Mapped[str] = mapped_column(ForeignKey("attachments.id"), primary_key=True)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    actor_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(80))
    entity_type: Mapped[str] = mapped_column(String(80))
    entity_id: Mapped[str] = mapped_column(String(36))
    before_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    after_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"
    __table_args__ = (
        UniqueConstraint("actor_id", "operation", "key", name="uq_idempotency_scope"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    actor_id: Mapped[str] = mapped_column(String(36))
    operation: Mapped[str] = mapped_column(String(80))
    key: Mapped[str] = mapped_column(String(120))
    request_hash: Mapped[str] = mapped_column(String(64))
    result_entity_type: Mapped[str | None] = mapped_column(String(80))
    result_entity_id: Mapped[str | None] = mapped_column(String(36))
    response_status: Mapped[int | None] = mapped_column(Integer)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime)
