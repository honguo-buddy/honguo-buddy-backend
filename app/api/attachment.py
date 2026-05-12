"""附件上传与管理路由。遵循两阶段上传（先上传拿 id/url，后业务绑定）。"""

from typing import Optional

from fastapi import APIRouter, Depends, UploadFile, File, Form
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import get_current_user
from app.core import settings
from app.db import get_db
from app.schemas import ResponseModel
from app.services import AttachmentService

router = APIRouter()


@router.post("/upload", response_model=ResponseModel)
async def upload_attachment(
    file: UploadFile = File(...),
    target_type: Optional[str] = Form(None),
    target_id: Optional[int] = Form(None),
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    attachment = await AttachmentService.upload(file=file, target_type=target_type, target_id=target_id, current_user=current_user, db=db)

    return ResponseModel(
        code=settings.SUCCESS_CODE,
        message={"id": attachment.attachment_id, "url": AttachmentService.to_public_url(attachment.url)},
    )
