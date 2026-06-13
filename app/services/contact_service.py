"""联系方式业务服务层。"""
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import BusinessHTTPException, ResourceHTTPException, settings
from app.models import UserContact


class ContactService:
    """联系方式 CRUD 服务。"""

    @staticmethod
    async def list_contacts(db: AsyncSession, user_id: int) -> list[UserContact]:
        """拉取用户所有联系方式。"""
        stmt = (
            select(UserContact)
            .where(UserContact.user_id == user_id)
            .order_by(UserContact.contact_id.asc())
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def upsert_contact(
        db: AsyncSession, user_id: int, contact_type: str, contact_value: str, is_public: bool
    ) -> UserContact:
        """追加或覆盖某种联系方式。

        同一类型只能有一条记录，存在则更新值，不存在则新增。
        """
        stmt = select(UserContact).where(
            UserContact.user_id == user_id,
            UserContact.contact_type == contact_type,
        )
        result = await db.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing:
            existing.contact_value = contact_value
            existing.is_public = is_public
            await db.commit()
            await db.refresh(existing)
            return existing

        contact = UserContact(
            user_id=user_id,
            contact_type=contact_type,
            contact_value=contact_value,
            is_public=is_public,
        )
        db.add(contact)
        await db.commit()
        await db.refresh(contact)
        return contact

    @staticmethod
    async def delete_contact(db: AsyncSession, user_id: int, contact_id: int) -> None:
        """定点删除联系方式。仅允许删除本人条目。"""
        stmt = select(UserContact).where(
            UserContact.contact_id == contact_id,
            UserContact.user_id == user_id,
        )
        result = await db.execute(stmt)
        contact = result.scalar_one_or_none()
        if not contact:
            raise ResourceHTTPException(code=settings.DATA_GET_FAILED_CODE, msg="联系方式不存在或无权操作")
        await db.delete(contact)
        await db.commit()