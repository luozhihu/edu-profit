import hashlib
from datetime import date
from decimal import Decimal

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.db import get_db
from app.models import (
    Attachment,
    AttachmentStatus,
    AuditEvent,
    Payment,
    PaymentAttachment,
    RecordStatus,
    Refund,
    RefundAttachment,
    Student,
    User,
)
from app.services import (
    BusinessError,
    confirm_payment,
    confirm_refund,
    create_payment_draft,
    create_refund_draft,
    create_student,
    dashboard_totals,
    mask_id_number,
    refundable_amount,
    void_payment,
    void_refund,
)
from app.storage import save_upload

app = FastAPI(title="培训学员财务管理")
app.add_middleware(SessionMiddleware, secret_key=settings.app_secret, same_site="lax")
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")
templates.env.filters["mask_id"] = mask_id_number


def user_for(request: Request, db: Session) -> User:
    user = db.get(User, request.session.get("user_id")) if request.session.get("user_id") else None
    if not user or not user.is_active:
        raise HTTPException(401, "请先登录")
    return user


def page(request: Request, name: str, **context):
    return templates.TemplateResponse(request=request, name=name, context={"request": request, **context})


def go(url: str):
    return RedirectResponse(url, status_code=303)


def fail(url: str, message: str):
    return go(f"{url}?error={message}")


@app.exception_handler(401)
def unauthorized(_request, _exc):
    return go("/login")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return page(request, "login.html", error=request.query_params.get("error"))


@app.post("/login")
def login(request: Request, username: str = Form(), password: str = Form(), db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.username == username))
    if not user or user.password_hash != hashlib.sha256(password.encode()).hexdigest():
        return fail("/login", "用户名或密码错误")
    request.session.clear()
    request.session["user_id"] = user.id
    return go("/")


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return go("/login")


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    user = user_for(request, db)
    totals = dashboard_totals(list(db.scalars(select(Payment)).all()))
    return page(request, "dashboard.html", user=user, **totals)


@app.get("/students", response_class=HTMLResponse)
def student_list(request: Request, q: str = "", db: Session = Depends(get_db)):
    user = user_for(request, db)
    stmt = select(Student).order_by(Student.created_at.desc())
    if q:
        term = f"%{q}%"
        stmt = stmt.where(or_(Student.name.like(term), Student.phone.like(term)))
    return page(request, "students.html", user=user, students=db.scalars(stmt).all(), q=q, error=request.query_params.get("error"))


@app.post("/students")
def student_add(
    request: Request,
    name: str = Form(),
    phone: str = Form(),
    id_number: str = Form(),
    db: Session = Depends(get_db),
):
    user = user_for(request, db)
    try:
        student = create_student(db, name=name, phone=phone, id_number=id_number, actor_id=user.id)
        db.commit()
        return go(f"/students/{student.id}")
    except BusinessError as exc:
        db.rollback()
        return fail("/students", str(exc))


def student_or_404(db: Session, student_id: str) -> Student:
    student = db.scalar(select(Student).where(Student.id == student_id).options(selectinload(Student.payments).selectinload(Payment.refunds)))
    if not student:
        raise HTTPException(404, "学员不存在")
    return student


def payment_or_404(db: Session, payment_id: str) -> Payment:
    payment = db.scalar(select(Payment).where(Payment.id == payment_id).options(selectinload(Payment.student), selectinload(Payment.refunds)))
    if not payment:
        raise HTTPException(404, "缴费不存在")
    return payment


def refund_or_404(db: Session, refund_id: str) -> Refund:
    refund = db.scalar(select(Refund).where(Refund.id == refund_id).options(selectinload(Refund.payment).selectinload(Payment.student), selectinload(Refund.payment).selectinload(Payment.refunds)))
    if not refund:
        raise HTTPException(404, "退款不存在")
    return refund


def payment_attachments(db: Session, payment_id: str):
    return db.scalars(
        select(Attachment)
        .join(PaymentAttachment)
        .where(PaymentAttachment.payment_id == payment_id)
    ).all()


def refund_attachments(db: Session, refund_id: str):
    return db.scalars(
        select(Attachment)
        .join(RefundAttachment)
        .where(RefundAttachment.refund_id == refund_id)
    ).all()


@app.get("/students/{student_id}", response_class=HTMLResponse)
def student_detail(request: Request, student_id: str, db: Session = Depends(get_db)):
    user = user_for(request, db)
    student = student_or_404(db, student_id)
    posted_payments = [p for p in student.payments if p.status == RecordStatus.POSTED]
    posted_refunds = [r for p in student.payments for r in p.refunds if r.status == RecordStatus.POSTED]
    actual_total = sum((p.actual_received_amount for p in posted_payments), Decimal("0"))
    refund_total = sum((r.amount for r in posted_refunds), Decimal("0"))
    entity_ids = [student.id] + [p.id for p in student.payments] + [r.id for p in student.payments for r in p.refunds]
    audits = db.scalars(select(AuditEvent).where(AuditEvent.entity_id.in_(entity_ids)).order_by(AuditEvent.created_at.desc()).limit(20)).all()
    return page(request, "student_detail.html", user=user, student=student, actual_total=actual_total, refund_total=refund_total, audits=audits, error=request.query_params.get("error"), today=date.today().isoformat())


@app.post("/students/{student_id}/payments")
def payment_add(
    request: Request,
    student_id: str,
    receipt_amount: str = Form(),
    rebate_amount: str = Form("0"),
    transfer_cost_amount: str = Form("0"),
    training_period_text: str = Form(),
    address: str = Form(),
    payment_date: date = Form(),
    payment_method: str = Form(),
    note: str = Form(""),
    db: Session = Depends(get_db),
):
    user, student = user_for(request, db), student_or_404(db, student_id)
    try:
        payment = create_payment_draft(
            db,
            student=student,
            receipt=receipt_amount,
            rebate=rebate_amount,
            transfer_cost=transfer_cost_amount,
            training_period_text=training_period_text,
            address=address,
            payment_date=payment_date,
            payment_method=payment_method,
            note=note,
            actor_id=user.id,
        )
        db.commit()
        return go(f"/payments/{payment.id}/confirm")
    except BusinessError as exc:
        db.rollback()
        return fail(f"/students/{student_id}", str(exc))


@app.get("/payments/{payment_id}/confirm", response_class=HTMLResponse)
def payment_confirm_page(request: Request, payment_id: str, db: Session = Depends(get_db)):
    return page(
        request,
        "payment_confirm.html",
        user=user_for(request, db),
        payment=payment_or_404(db, payment_id),
        attachments=payment_attachments(db, payment_id),
        error=request.query_params.get("error"),
    )


@app.get("/payments/{payment_id}", response_class=HTMLResponse)
def payment_detail_page(request: Request, payment_id: str, db: Session = Depends(get_db)):
    payment = payment_or_404(db, payment_id)
    return page(
        request,
        "payment_detail.html",
        user=user_for(request, db),
        payment=payment,
        attachments=payment_attachments(db, payment_id),
    )


@app.post("/payments/{payment_id}/attachments")
async def payment_attachment_add(
    request: Request,
    payment_id: str,
    files: list[UploadFile] = File(),
    db: Session = Depends(get_db),
):
    user_for(request, db)
    payment = payment_or_404(db, payment_id)
    if payment.status != RecordStatus.DRAFT:
        return fail(f"/students/{payment.student_id}", "正式记录不能新增附件")
    try:
        for upload in files:
            attachment = await save_upload(db, upload)
            db.add(PaymentAttachment(payment_id=payment.id, attachment_id=attachment.id))
        db.commit()
        return go(f"/payments/{payment.id}/confirm")
    except BusinessError as exc:
        db.rollback()
        return fail(f"/payments/{payment.id}/confirm", str(exc))


@app.post("/payments/{payment_id}/confirm")
def payment_confirm_action(request: Request, payment_id: str, receipt_amount: str = Form(), rebate_amount: str = Form(), transfer_cost_amount: str = Form(), db: Session = Depends(get_db)):
    user, payment = user_for(request, db), payment_or_404(db, payment_id)
    try:
        confirm_payment(db, payment, receipt=receipt_amount, rebate=rebate_amount, transfer_cost=transfer_cost_amount, actor_id=user.id)
        for attachment in payment_attachments(db, payment.id):
            attachment.status = AttachmentStatus.FROZEN
        db.commit()
        return go(f"/students/{payment.student_id}")
    except BusinessError as exc:
        db.rollback()
        return fail(f"/payments/{payment_id}/confirm", str(exc))


@app.post("/payments/{payment_id}/refunds")
def refund_add(request: Request, payment_id: str, amount: str = Form(), refund_date: date = Form(), reason: str = Form(), db: Session = Depends(get_db)):
    user, payment = user_for(request, db), payment_or_404(db, payment_id)
    try:
        refund = create_refund_draft(db, payment=payment, amount=amount, refund_date=refund_date, reason=reason, actor_id=user.id)
        db.commit()
        return go(f"/refunds/{refund.id}/confirm")
    except BusinessError as exc:
        db.rollback()
        return fail(f"/students/{payment.student_id}", str(exc))


@app.get("/refunds/{refund_id}/confirm", response_class=HTMLResponse)
def refund_confirm_page(request: Request, refund_id: str, db: Session = Depends(get_db)):
    refund = refund_or_404(db, refund_id)
    return page(
        request,
        "refund_confirm.html",
        user=user_for(request, db),
        refund=refund,
        remaining=refundable_amount(refund.payment),
        attachments=refund_attachments(db, refund_id),
        error=request.query_params.get("error"),
    )


@app.post("/refunds/{refund_id}/attachments")
async def refund_attachment_add(
    request: Request,
    refund_id: str,
    files: list[UploadFile] = File(),
    db: Session = Depends(get_db),
):
    user_for(request, db)
    refund = refund_or_404(db, refund_id)
    if refund.status != RecordStatus.DRAFT:
        return fail(f"/students/{refund.payment.student_id}", "正式记录不能新增附件")
    try:
        for upload in files:
            attachment = await save_upload(db, upload)
            db.add(RefundAttachment(refund_id=refund.id, attachment_id=attachment.id))
        db.commit()
        return go(f"/refunds/{refund.id}/confirm")
    except BusinessError as exc:
        db.rollback()
        return fail(f"/refunds/{refund.id}/confirm", str(exc))


@app.post("/refunds/{refund_id}/confirm")
def refund_confirm_action(request: Request, refund_id: str, amount: str = Form(), db: Session = Depends(get_db)):
    user, refund = user_for(request, db), refund_or_404(db, refund_id)
    try:
        confirm_refund(db, refund, amount=amount, actor_id=user.id)
        for attachment in refund_attachments(db, refund.id):
            attachment.status = AttachmentStatus.FROZEN
        db.commit()
        return go(f"/students/{refund.payment.student_id}")
    except BusinessError as exc:
        db.rollback()
        return fail(f"/refunds/{refund_id}/confirm", str(exc))


@app.post("/payments/{payment_id}/void")
def payment_void(request: Request, payment_id: str, reason: str = Form(), db: Session = Depends(get_db)):
    user, payment = user_for(request, db), payment_or_404(db, payment_id)
    try:
        void_payment(db, payment, reason=reason, actor_id=user.id)
        db.commit()
    except BusinessError as exc:
        db.rollback()
        return fail(f"/students/{payment.student_id}", str(exc))
    return go(f"/students/{payment.student_id}")


@app.post("/refunds/{refund_id}/void")
def refund_void(request: Request, refund_id: str, reason: str = Form(), db: Session = Depends(get_db)):
    user, refund = user_for(request, db), refund_or_404(db, refund_id)
    try:
        void_refund(db, refund, reason=reason, actor_id=user.id)
        db.commit()
    except BusinessError as exc:
        db.rollback()
        return fail(f"/students/{refund.payment.student_id}", str(exc))
    return go(f"/students/{refund.payment.student_id}")


@app.get("/attachments/{attachment_id}")
def attachment_download(request: Request, attachment_id: str, db: Session = Depends(get_db)):
    user_for(request, db)
    attachment = db.get(Attachment, attachment_id)
    if not attachment:
        raise HTTPException(404, "附件不存在")
    path = settings.upload_dir / attachment.storage_key
    if not path.exists():
        raise HTTPException(404, "附件文件缺失")
    return FileResponse(path, media_type=attachment.content_type, filename=attachment.original_name)
