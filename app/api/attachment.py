"""附件上传与管理路由。遵循两阶段上传（先上传拿 id/url，后业务绑定）。"""

from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from starlette.formparsers import MultiPartException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import get_current_user
from app.core import BusinessHTTPException, settings
from app.db import get_db
from app.schemas import ResponseModel
from app.services import AttachmentService

router = APIRouter()
ATTACHMENT_UPLOAD_MAX_PART_SIZE = settings.ATTACHMENT_UPLOAD_MAX_PART_SIZE


@router.post("/upload", response_model=ResponseModel)
async def upload_attachment(
    request: Request,
    file: UploadFile = File(...),
    target_type: Optional[str] = Form(None),
    target_id: Optional[int] = Form(None),
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """上传图片附件，并在落盘前按目标类型压缩、缩放和统一转为 WebP。"""
    try:
        await request.form(max_part_size=ATTACHMENT_UPLOAD_MAX_PART_SIZE)
    except MultiPartException as exc:
        if "Part exceeded maximum size" in str(exc):
            max_mb = settings.ATTACHMENT_MAX_FILE_SIZE // (1024 * 1024)
            raise BusinessHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg=f"文件大小不能超过 {max_mb}MB",
            ) from exc
        raise BusinessHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg="上传表单格式不正确",
        ) from exc

    attachment = await AttachmentService.upload(file=file, target_type=target_type, target_id=target_id, current_user=current_user, db=db)

    return ResponseModel(
        code=settings.SUCCESS_CODE,
        message={"id": attachment.attachment_id, "url": AttachmentService.to_public_url(attachment.url)},
    )
