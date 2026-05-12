import os
import time
from pathlib import Path
from typing import Optional

import aiofiles
from fastapi import HTTPException
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Attachment, AttachmentTargetType, User


class AttachmentService:
    @staticmethod
    async def upload(file, target_type: Optional[str], target_id: Optional[int], current_user, db: AsyncSession) -> Attachment:
        """保存上传文件并创建附件记录。"""
        allowed_ext = {".jpg", ".jpeg", ".png"}
        filename = getattr(file, "filename", None) or "file"
        ext = os.path.splitext(filename)[1].lower()
        if ext not in allowed_ext:
            raise HTTPException(status_code=400, detail="仅支持 jpg/jpeg/png 格式")

        content = await file.read()
        if len(content) > 5 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="文件大小不能超过 5MB")

        normalized_target_type = (target_type or "USER").upper()
        folder_map = {
            "USER": "avatar",
            "POST": "post",
            "GOODS": "goods",
        }
        folder = folder_map.get(normalized_target_type, "avatar")

        base_dir = Path("app/static")
        dest_dir = base_dir / folder
        dest_dir.mkdir(parents=True, exist_ok=True)

        timestamp = time.time_ns()
        safe_name = f"{normalized_target_type.lower()}_{current_user.user_id}_{timestamp}{ext}"
        rel_path = f"/static/{folder}/{safe_name}"
        abs_path = Path("app") / rel_path.lstrip("/")

        async with aiofiles.open(abs_path, "wb") as f:
            await f.write(content)

        try:
            enum_type = AttachmentTargetType[normalized_target_type]
        except Exception:
            enum_type = AttachmentTargetType.USER

        resolved_target_id = target_id
        if enum_type == AttachmentTargetType.USER and resolved_target_id is None:
            resolved_target_id = current_user.user_id

        attachment = Attachment(
            target_type=enum_type,
            target_id=resolved_target_id,
            url=rel_path,
            creator_id=current_user.user_id,
        )
        db.add(attachment)
        await db.flush()

        if enum_type == AttachmentTargetType.USER and resolved_target_id is not None:
            await db.execute(
                update(User)
                .where(User.user_id == resolved_target_id)
                .values(avatar_id=attachment.attachment_id)
                .execution_options(synchronize_session=False)
            )

        await db.commit()
        return attachment

    @staticmethod
    def to_public_url(rel_url: Optional[str]) -> Optional[str]:
        if not rel_url:
            return None
        return rel_url if rel_url.startswith("/") else f"/{rel_url}"

    @staticmethod
    async def get_attachment_url_by_id(attachment_id: Optional[int], db: AsyncSession) -> Optional[str]:
        if attachment_id is None:
            return None
        result = await db.execute(
            Attachment.__table__.select().where(
                Attachment.attachment_id == attachment_id,
                Attachment.is_deleted == False,
            )
        )
        row = result.mappings().first()
        if not row:
            return None
        return row["url"]
