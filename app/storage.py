import hashlib
from pathlib import Path

from fastapi import UploadFile
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Attachment, AttachmentStatus
from app.services import BusinessError

ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "application/pdf"}


async def save_upload(db: Session, upload: UploadFile) -> Attachment:
    if upload.content_type not in ALLOWED_CONTENT_TYPES:
        raise BusinessError("仅支持 JPG、PNG 和 PDF 凭证")
    content = await upload.read(settings.max_upload_bytes + 1)
    if len(content) > settings.max_upload_bytes:
        raise BusinessError("附件超过大小限制")
    digest = hashlib.sha256(content).hexdigest()
    suffix = Path(upload.filename or "").suffix.lower()
    storage_key = f"{digest[:2]}/{digest}{suffix}"
    target = settings.upload_dir / storage_key
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    attachment = Attachment(
        storage_key=storage_key,
        original_name=upload.filename or "attachment",
        content_type=upload.content_type or "application/octet-stream",
        size_bytes=len(content),
        sha256=digest,
        status=AttachmentStatus.READY,
    )
    db.add(attachment)
    db.flush()
    return attachment
