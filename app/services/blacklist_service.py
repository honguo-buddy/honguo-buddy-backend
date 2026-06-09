"""黑名单业务服务层。"""
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core import BusinessHTTPException, ResourceHTTPException, settings
from app.models import User, UserBlacklist
from app.services.attachment_service import AttachmentService


class BlacklistService:
    """黑名单 CRUD 服务。"""

    @staticmethod
    async def add_to_blacklist(db: AsyncSession, user_id: int, target_id: int) -> UserBlacklist:
        """拉黑目标用户。"""
        if user_id == target_id:
            raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg="不能拉黑自己")

        # 校验目标用户存在
        target = await db.get(User, target_id)
        if not target or target.is_deleted:
            raise ResourceHTTPException(code=settings.USER_GET_FAILED_CODE, msg="目标用户不存在")

        # 检查是否已拉黑
        existing = await db.execute(
            select(UserBlacklist).where(
                UserBlacklist.user_id == user_id,
                UserBlacklist.target_id == target_id,
            )
        )
        if existing.scalar_one_or_none():
            raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg="该用户已在黑名单中")

        entry = UserBlacklist(user_id=user_id, target_id=target_id)
        db.add(entry)
        await db.commit()
        await db.refresh(entry)
        return entry

    @staticmethod
    async def remove_from_blacklist(db: AsyncSession, user_id: int, target_id: int) -> None:
        """解除拉黑。"""
        result = await db.execute(
            select(UserBlacklist).where(
                UserBlacklist.user_id == user_id,
                UserBlacklist.target_id == target_id,
            )
        )
        entry = result.scalar_one_or_none()
        if not entry:
            raise ResourceHTTPException(code=settings.DATA_GET_FAILED_CODE, msg="该用户不在黑名单中")
        await db.delete(entry)
        await db.commit()

    @staticmethod
    async def list_blacklist(
        db: AsyncSession, user_id: int, page: int = 1, page_size: int = 20
    ) -> dict:
        """分页拉取黑名单列表，内联 target 用户的头像信息。"""
        # 总数
        total_result = await db.execute(
            select(func.count()).select_from(UserBlacklist).where(UserBlacklist.user_id == user_id)
        )
        total = int(total_result.scalar_one() or 0)

        offset = (page - 1) * page_size
        stmt = (
            select(UserBlacklist)
            .options(selectinload(UserBlacklist.target))
            .where(UserBlacklist.user_id == user_id)
            .order_by(UserBlacklist.create_time.desc())
            .offset(offset)
            .limit(page_size)
        )
        result = await db.execute(stmt)
        entries = result.scalars().all()

        # 批量获取头像 URL
        avatar_ids = []
        for entry in entries:
            if entry.target and getattr(entry.target, "avatar_id", None):
                avatar_ids.append(entry.target.avatar_id)
        avatar_url_map = {}
        if avatar_ids:
            avatar_url_map = await AttachmentService.get_urls_by_target(db, "USER", avatar_ids)

        items = []
        for entry in entries:
            target = entry.target
            target_avatar = None
            if target and getattr(target, "avatar_id", None):
                target_avatar = avatar_url_map.get(target.avatar_id, [None])[0] if avatar_url_map.get(target.avatar_id) else None
            items.append({
                "user_id": entry.user_id,
                "target_id": entry.target_id,
                "target_name": target.user_name if target else None,
                "target_avatar": target_avatar,
                "create_time": entry.create_time.isoformat() if entry.create_time else None,
            })

        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "list": items,
        }
    @staticmethod
    async def is_blocked(db: AsyncSession, blocker_id: int, current_user_id: int) -> bool:
        """检查 current_user_id 是否被 blocker_id 拉黑了。"""
        if blocker_id == current_user_id:
            return False
        result = await db.execute(
            select(UserBlacklist).where(
                UserBlacklist.user_id == blocker_id,
                UserBlacklist.target_id == current_user_id,
            )
        )
        return result.scalar_one_or_none() is not None

    @staticmethod
    async def get_blocker_ids(db: AsyncSession, current_user_id: int) -> list[int]:
        """获取所有拉黑了 current_user_id 的用户 ID 列表。"""
        result = await db.execute(
            select(UserBlacklist.user_id).where(
                UserBlacklist.target_id == current_user_id,
            )
        )
        return [row[0] for row in result.all()]

    @staticmethod
    async def get_blocked_target_ids(db: AsyncSession, current_user_id: int) -> list[int]:
        """获取 current_user_id 拉黑的所有目标用户 ID 列表。"""
        result = await db.execute(
            select(UserBlacklist.target_id).where(
                UserBlacklist.user_id == current_user_id,
            )
        )
        return [row[0] for row in result.all()]

