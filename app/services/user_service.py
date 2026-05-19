"""用户服务层 - 处理用户相关业务逻辑。"""

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core import AuthHTTPException, ResourceHTTPException, settings
from app.models import Attachment, AttachmentTargetType, User


class UserService:
    """用户业务逻辑服务。"""

    @staticmethod
    async def get_user_by_id(user_id: int, db: AsyncSession) -> User:
        """获取用户详细信息。"""
        stmt = (
            select(User)
            .options(selectinload(User.avatar_attachment))
            .where((User.user_id == user_id) & (User.is_deleted == False))
        )
        result = await db.execute(stmt)
        user = result.scalars().first()

        if not user:
            raise ResourceHTTPException(
                message="用户不存在或已被删除",
                error_code=103,
            )

        return user

    @staticmethod
    async def update_user_profile(
        user_id: int,
        user_name: str | None = None,
        avatar_id: int | None = None,
        sex: str | None = None,
        db: AsyncSession | None = None,
    ) -> User:
        """更新用户个人资料（本人修改）。"""
        update_data = {}
        if user_name is not None:
            update_data["user_name"] = user_name
        if avatar_id is not None:
            # 先校验附件归属（必须为当前用户自己上传的 USER 类型附件），但不立即写入
            await UserService._validate_avatar_attachment_owned_by_user(user_id, avatar_id, db)
            update_data["avatar_id"] = avatar_id
        if sex is not None:
            update_data["sex"] = sex

        if update_data:
            # 使用 ORM 对象方式更新，确保在同一会话内可见性
            user_obj = await db.get(User, user_id)
            if not user_obj:
                raise ResourceHTTPException(message="用户不存在", error_code=103)
            for k, v in update_data.items():
                setattr(user_obj, k, v)
            await db.flush()
            await db.commit()
            # 刷新对象以确保在后续查询中能看到最新关联（例如 avatar_attachment）
            try:
                await db.refresh(user_obj)
                await db.refresh(user_obj, attribute_names=["avatar_attachment"])
            except Exception:
                # 若刷新不支持 attribute_names 或出错，忽略刷新，后续查询仍会尝试加载
                try:
                    await db.refresh(user_obj)
                except Exception:
                    pass

        return await UserService.get_user_by_id(user_id, db)

    @staticmethod
    async def delete_user(user_id: int, db: AsyncSession) -> None:
        """逻辑删除用户。"""
        stmt = (
            update(User)
            .where(User.user_id == user_id)
            .values(is_deleted=True)
            .execution_options(synchronize_session=False)
        )
        await db.execute(stmt)
        await db.commit()

    @staticmethod
    async def get_user_by_user_id_admin(user_id: int, db: AsyncSession) -> User:
        """获取用户信息(管理员可查看所有用户，包括已删除)"""
        stmt = (
            select(User)
            .options(selectinload(User.avatar_attachment))
            .where(User.user_id == user_id)
        )
        result = await db.execute(stmt)
        user = result.scalars().first()

        if not user:
            raise ResourceHTTPException(
                message="用户不存在",
                error_code=103,
            )

        return user

    @staticmethod
    async def update_user_by_admin(
        user_id: int,
        user_name: str | None = None,
        avatar_id: int | None = None,
        sex: str | None = None,
        is_admin: bool | None = None,
        is_verified: bool | None = None,
        is_active: bool | None = None,
        db: AsyncSession | None = None,
    ) -> User:
        """管理员修改用户信息。"""
        update_data = {}
        if user_name is not None:
            update_data["user_name"] = user_name
        if avatar_id is not None:
            # 管理员可以为任意用户设置 avatar（跳过上传者归属检查）
            # 直接在 update_data 中设置 avatar_id，先校验附件存在且为 USER 类型
            attachment_stmt = select(Attachment).where(
                Attachment.attachment_id == avatar_id,
                Attachment.target_type == AttachmentTargetType.USER,
                Attachment.is_deleted == False,
            )
            attachment_result = await db.execute(attachment_stmt)
            attachment_obj = attachment_result.scalars().first()
            if not attachment_obj:
                raise ResourceHTTPException(message="头像附件不存在或类型不对", error_code=103)
            update_data["avatar_id"] = avatar_id
        if sex is not None:
            update_data["sex"] = sex
        if is_admin is not None:
            update_data["is_admin"] = is_admin
        if is_verified is not None:
            update_data["is_verified"] = is_verified
        if is_active is not None:
            update_data["is_active"] = is_active

        if update_data:
            # 使用 ORM 对象更新以保证同一会话可见性
            user_obj = await db.get(User, user_id)
            if not user_obj:
                raise ResourceHTTPException(message="用户不存在", error_code=103)
            for k, v in update_data.items():
                setattr(user_obj, k, v)
            await db.flush()
            await db.commit()
            try:
                await db.refresh(user_obj)
                await db.refresh(user_obj, attribute_names=["avatar_attachment"])
            except Exception:
                try:
                    await db.refresh(user_obj)
                except Exception:
                    pass

        return await UserService.get_user_by_user_id_admin(user_id, db)

    @staticmethod
    async def admin_delete_user(user_id: int, db: AsyncSession) -> None:
        """管理员禁用/删除用户（逻辑删除）。"""
        await UserService.delete_user(user_id, db)

    @staticmethod
    def _build_user_payload(user: User, *, public: bool = False) -> dict:
        avatar_url = None
        if user.avatar_attachment and not user.avatar_attachment.is_deleted:
            avatar_url = user.avatar_attachment.url

        payload = {
            "user_id": user.user_id,
            "user_uuid": user.user_uuid,
            "user_name": user.user_name,
            "avatar": avatar_url,
            "sex": user.sex.value if hasattr(user.sex, "value") else user.sex,
            "user_type": user.user_type.value if hasattr(user.user_type, "value") else user.user_type,
            "credit_score": user.credit_score,
            "is_verified": user.is_verified,
        }

        if not public:
            payload.update(
                {
                    "email": user.email,
                    "phonenumber": user.phonenumber,
                    "is_active": user.is_active,
                    "is_admin": user.is_admin,
                    "last_login_ip": user.last_login_ip,
                    "last_login_time": user.last_login_time,
                    "wechat_unionid": user.wechat_unionid,
                }
            )

        return payload

    @staticmethod
    async def get_user_with_avatar_url(user_id: int, db: AsyncSession) -> dict:
        """获取用户信息，并返回头像附件 URL。"""
        user = await UserService.get_user_by_id(user_id, db)
        return UserService._build_user_payload(user)

    @staticmethod
    async def get_user_public_with_avatar_url(user_id: int, db: AsyncSession) -> dict:
        """获取用户公开信息（脱敏），并返回头像附件 URL。"""
        user = await UserService.get_user_by_id(user_id, db)
        return UserService._build_user_payload(user, public=True)

    @staticmethod
    async def get_user_with_avatar_url_admin(user_id: int, db: AsyncSession) -> dict:
        """[管理员]获取用户公开信息，并返回头像附件 URL。"""
        user = await UserService.get_user_by_user_id_admin(user_id, db)
        return UserService._build_user_payload(user)
    
    @staticmethod
    async def set_user_avatar_by_attachment(
        user_id: int,
        attachment_id: int,
        db: AsyncSession,
        allow_force: bool = False,
    ) -> None:
        """将用户头像直接关联到指定附件。"""
        attachment_stmt = select(Attachment).where(
            Attachment.attachment_id == attachment_id,
            Attachment.target_type == AttachmentTargetType.USER,
            Attachment.is_deleted == False,
        )
        attachment_result = await db.execute(attachment_stmt)
        attachment = attachment_result.scalars().first()
        if not attachment:
            raise ResourceHTTPException(
                message="头像附件不存在",
                error_code=103,
            )

        if attachment.target_type != AttachmentTargetType.USER:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="只能使用头像类型的附件",
            )
        if not allow_force and attachment.creator_id != user_id:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="只能使用自己上传的头像图片",
            )

        stmt = (
            update(User)
            .where(User.user_id == user_id)
            .values(avatar_id=attachment_id)
            .execution_options(synchronize_session=False)
        )
        await db.execute(stmt)
        await db.commit()

    @staticmethod
    async def _validate_avatar_attachment_owned_by_user(user_id: int, avatar_id: int, db: AsyncSession) -> None:
        """校验 avatar_id 指向的附件存在且属于指定用户（creator_id == user_id）且类型为 USER。"""
        attachment_stmt = select(Attachment).where(
            Attachment.attachment_id == avatar_id,
            Attachment.is_deleted == False,
        )
        attachment_result = await db.execute(attachment_stmt)
        attachment = attachment_result.scalars().first()
        if not attachment:
            raise ResourceHTTPException(
                message="头像附件不存在",
                error_code=103,
            )

        if attachment.target_type != AttachmentTargetType.USER:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="只能使用头像类型的附件",
            )

        if attachment.creator_id != user_id:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="只能使用自己上传的头像图片",
            )
