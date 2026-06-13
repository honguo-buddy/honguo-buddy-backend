import asyncio
import io
import time
from pathlib import Path
from typing import Optional

import aiofiles
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import case, select, text, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import BusinessHTTPException, ResourceHTTPException, settings
from app.models import Attachment, AttachmentTargetType, User


class AttachmentService:
    _MAX_FILE_SIZE = settings.ATTACHMENT_MAX_FILE_SIZE
    _AVATAR_SIZE = (200, 200)
    _POST_MAX_WIDTH = 1080
    _DEFAULT_MAX_WIDTH = 800
    _sort_order_ready = False
    _sort_order_lock: asyncio.Lock | None = None

    @staticmethod
    def _is_missing_sort_order_error(exc: Exception) -> bool:
        """识别 attachment.sort_order 缺列错误。"""
        if not isinstance(exc, OperationalError):
            return False
        original = getattr(exc, "orig", None)
        message = str(original or exc)
        original_args = getattr(original, "args", ()) or ()
        return "Unknown column 'sort_order'" in message or any(
            isinstance(item, str) and "Unknown column 'sort_order'" in item
            for item in original_args
        )

    @staticmethod
    async def ensure_sort_order_column_with_retry_boundary(db: AsyncSession) -> None:
        """在运行时缺列场景下补齐 attachment.sort_order，并清理当前事务状态。"""
        if hasattr(db, "rollback"):
            await db.rollback()
        await AttachmentService.ensure_sort_order_column(db)
        if hasattr(db, "commit"):
            await db.commit()

    @staticmethod
    async def upload(file, target_type: Optional[str], target_id: Optional[int], current_user, db: AsyncSession) -> Attachment:
        """保存上传文件并创建附件记录。"""
        content = await file.read()
        if len(content) > AttachmentService._MAX_FILE_SIZE:
            max_mb = settings.ATTACHMENT_MAX_FILE_SIZE // (1024 * 1024)
            raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg=f"文件大小不能超过 {max_mb}MB")

        normalized_target_type = AttachmentService._normalize_target_type(target_type)
        folder = AttachmentService._resolve_folder(normalized_target_type)
        loop = asyncio.get_running_loop()
        processed_content = await loop.run_in_executor(
            None,
            AttachmentService._sync_compress_image,
            content,
            normalized_target_type,
        )

        base_dir = Path("app/static")
        dest_dir = base_dir / folder
        dest_dir.mkdir(parents=True, exist_ok=True)

        timestamp = time.time_ns()
        safe_name = f"{normalized_target_type.lower()}_{current_user.user_id}_{timestamp}.webp"
        rel_path = f"/static/{folder}/{safe_name}"
        abs_path = Path("app") / rel_path.lstrip("/")

        async with aiofiles.open(abs_path, "wb") as f:
            await f.write(processed_content)

        try:
            enum_type = AttachmentTargetType[normalized_target_type]
        except KeyError:
            enum_type = AttachmentTargetType.USER

        resolved_target_id = target_id
        if target_type is not None and enum_type == AttachmentTargetType.USER and resolved_target_id is None:
            resolved_target_id = current_user.user_id

        attachment = Attachment(
            target_type=enum_type,
            target_id=resolved_target_id,
            url=rel_path,
            creator_id=current_user.user_id,
            sort_order=0,
        )
        db.add(attachment)
        try:
            await db.flush()
        except OperationalError as exc:
            if not AttachmentService._is_missing_sort_order_error(exc):
                raise
            await AttachmentService.ensure_sort_order_column_with_retry_boundary(db)
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
    def _normalize_target_type(target_type: Optional[str]) -> str:
        normalized_target_type = (target_type or "USER").upper()
        return normalized_target_type if normalized_target_type in AttachmentTargetType.__members__ else "USER"

    @staticmethod
    def _resolve_folder(normalized_target_type: str) -> str:
        folder_map = {
            "USER": "avatar",
            "POST": "post",
            "GOODS": "goods",
            "COMMENT": "comment",
            "CHAT": "chat",
            "ORDERREVIEW": "order_review",
        }
        return folder_map.get(normalized_target_type, "avatar")

    @staticmethod
    def _sync_compress_image(content: bytes, normalized_target_type: str) -> bytes:
        try:
            with Image.open(io.BytesIO(content)) as source_image:
                source_image.load()
                processed_image, quality = AttachmentService._transform_image(source_image, normalized_target_type)
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg="不支持的文件类型") from exc

        try:
            output = io.BytesIO()
            processed_image.save(output, format="WEBP", quality=quality, method=6)
            return output.getvalue()
        finally:
            processed_image.close()

    @staticmethod
    def _transform_image(source_image: Image.Image, normalized_target_type: str) -> tuple[Image.Image, int]:
        working_image = AttachmentService._normalize_image_mode(source_image)

        if normalized_target_type == "USER":
            avatar_image = ImageOps.fit(
                working_image,
                AttachmentService._AVATAR_SIZE,
                method=Image.Resampling.LANCZOS,
            )
            working_image.close()
            return avatar_image, 80

        if normalized_target_type in {"POST", "GOODS"}:
            return AttachmentService._resize_to_max_width(working_image, AttachmentService._POST_MAX_WIDTH), 75

        return AttachmentService._resize_to_max_width(working_image, AttachmentService._DEFAULT_MAX_WIDTH), 70

    @staticmethod
    def _resize_to_max_width(image: Image.Image, max_width: int) -> Image.Image:
        if image.width <= max_width:
            return image

        resized_height = max(1, int(image.height * max_width / image.width))
        resized_image = image.resize((max_width, resized_height), Image.Resampling.LANCZOS)
        image.close()
        return resized_image

    @staticmethod
    def _normalize_image_mode(image: Image.Image) -> Image.Image:
        if image.mode in {"RGBA", "LA"}:
            return image.convert("RGBA")
        if image.mode == "P":
            if "transparency" in image.info:
                return image.convert("RGBA")
            return image.convert("RGB")
        if image.mode != "RGB":
            return image.convert("RGB")
        return image.copy()

    @staticmethod
    async def bind_attachments_to_target(
        db: AsyncSession,
        attachment_ids: list[int],
        target_type: str,
        target_id: int,
        creator_id: int,
    ) -> None:
        if not attachment_ids:
            return

        normalized_target_type = target_type.upper()
        try:
            AttachmentTargetType[normalized_target_type]
        except KeyError as exc:
            raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg=f"无效的附件目标类型: {target_type}") from exc

        stmt = select(Attachment.attachment_id).where(
            Attachment.attachment_id.in_(attachment_ids),
            Attachment.creator_id == creator_id,
            Attachment.is_deleted == False,
        )
        result = await db.execute(stmt)
        allowed_ids = {row[0] for row in result.all()}
        if allowed_ids != set(attachment_ids):
            raise ResourceHTTPException(code=settings.DATA_GET_FAILED_CODE, msg="附件不存在或无权绑定")

        await db.execute(
            update(Attachment)
            .where(Attachment.attachment_id.in_(attachment_ids))
            .values(
                target_type=AttachmentTargetType[normalized_target_type],
                target_id=target_id,
                sort_order=case(
                    {attachment_id: order_index for order_index, attachment_id in enumerate(attachment_ids)},
                    value=Attachment.attachment_id,
                    else_=len(attachment_ids),
                ),
            )
            .execution_options(synchronize_session=False)
        )

    @staticmethod
    async def unbind_attachments_from_target(
        db: AsyncSession,
        target_type: str,
        target_id: int,
    ) -> None:
        if target_id is None:
            return

        normalized_target_type = target_type.upper()
        try:
            enum_type = AttachmentTargetType[normalized_target_type]
        except KeyError as exc:
            raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg=f"无效的附件目标类型: {target_type}") from exc

        await db.execute(
            update(Attachment)
            .where(
                Attachment.target_type == enum_type,
                Attachment.target_id == target_id,
                Attachment.is_deleted == False,
            )
            .values(target_id=None, sort_order=0)
            .execution_options(synchronize_session=False)
        )

    @staticmethod
    async def get_urls_by_target(db: AsyncSession, target_type: str, target_ids: list[int]) -> dict[int, list[str]]:
        if not target_ids:
            return {}

        try:
            enum_type = AttachmentTargetType[target_type.upper()]
        except KeyError as exc:
            raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg=f"无效的附件目标类型: {target_type}") from exc

        result = await db.execute(
            select(Attachment.target_id, Attachment.url).where(
                Attachment.target_type == enum_type,
                Attachment.target_id.in_(target_ids),
                Attachment.is_deleted == False,
            ).order_by(Attachment.sort_order.asc(), Attachment.attachment_id.asc())
        )
        url_map: dict[int, list[str]] = {target_id: [] for target_id in target_ids}
        for target_id, url in result.all():
            if target_id is None:
                continue
            url_map.setdefault(int(target_id), []).append(AttachmentService.to_public_url(url) or url)
        return url_map

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

    @staticmethod
    async def ensure_sort_order_column(db: AsyncSession) -> None:
        """幂等补齐 attachment.sort_order 物理列，兼容非迁移启动场景。"""
        if AttachmentService._sort_order_ready:
            return

        if AttachmentService._sort_order_lock is None:
            AttachmentService._sort_order_lock = asyncio.Lock()

        async with AttachmentService._sort_order_lock:
            if AttachmentService._sort_order_ready:
                return

            db_name_result = await db.execute(text("SELECT DATABASE()"))
            db_name = db_name_result.scalar_one_or_none()
            if not db_name:
                return

            column_exists_result = await db.execute(
                text(
                    """
                    SELECT 1
                    FROM information_schema.COLUMNS
                    WHERE TABLE_SCHEMA = :schema_name
                      AND TABLE_NAME = 'attachment'
                      AND COLUMN_NAME = 'sort_order'
                    LIMIT 1
                    """
                ),
                {"schema_name": db_name},
            )
            column_exists = column_exists_result.scalar_one_or_none()
            if not column_exists:
                await db.execute(
                    text(
                        """
                        ALTER TABLE attachment
                        ADD COLUMN sort_order BIGINT NOT NULL DEFAULT 0 COMMENT '同一目标下的附件排序序号，数值越小越靠前'
                        """
                    )
                )

            index_exists_result = await db.execute(
                text(
                    """
                    SELECT 1
                    FROM information_schema.STATISTICS
                    WHERE TABLE_SCHEMA = :schema_name
                      AND TABLE_NAME = 'attachment'
                      AND INDEX_NAME = 'idx_attachment_target_sort_order'
                    LIMIT 1
                    """
                ),
                {"schema_name": db_name},
            )
            index_exists = index_exists_result.scalar_one_or_none()
            if not index_exists:
                await db.execute(
                    text(
                        """
                        CREATE INDEX idx_attachment_target_sort_order
                        ON attachment (target_type, target_id, sort_order)
                        """
                    )
                )

            AttachmentService._sort_order_ready = True
    @staticmethod
    async def hydrate_owners_avatar(db: AsyncSession, entity_list: list) -> None:
        """统一头像灌水中心：批量收集 avatar_id → IN 查询 Attachment 表 → 内存注入 user.avatar。

        适用于 Post、Goods 等所有持有 user 关系的实体列表，
        单次 DB 往返消灭 N+1 问题。
        """
        if not entity_list:
            return

        avatar_ids: list[int] = []
        for entity in entity_list:
            if entity is None:
                continue
            owner = getattr(entity, "user", None) or getattr(entity, "publisher", None)
            if owner and getattr(owner, "avatar_id", None):
                avatar_ids.append(owner.avatar_id)

        if not avatar_ids:
            return

        attachments_result = await db.execute(
            select(Attachment).where(Attachment.attachment_id.in_(avatar_ids))
        )
        avatar_url_map = {att.attachment_id: att.url for att in attachments_result.scalars().all()}

        for entity in entity_list:
            if entity is None:
                continue
            owner = getattr(entity, "user", None) or getattr(entity, "publisher", None)
            if owner:
                avatar_id = getattr(owner, "avatar_id", None)
                owner.avatar = avatar_url_map.get(avatar_id) if avatar_id else None

