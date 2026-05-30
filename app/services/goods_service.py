"""Goods CRUD service layer."""
import logging
from typing import Optional, List, Tuple

from sqlalchemy import select, func, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.goods import Goods, GoodsStatus, GoodsCondition
from app.models.attachment import Attachment
from app.schemas.goods import GoodsCreate, GoodsUpdate

logger = logging.getLogger(__name__)


class GoodsService:
    @staticmethod
    async def create_goods(db: AsyncSession, publisher_id: int, obj_in: GoodsCreate) -> Goods:
        """Publish a new goods item."""
        goods = Goods(
            publisher_id=publisher_id,
            category_id=obj_in.category_id,
            name=obj_in.name,
            description=obj_in.description,
            price=obj_in.price,
            condition=GoodsCondition(obj_in.condition) if isinstance(obj_in.condition, str) else obj_in.condition,
            template_data=obj_in.template_data or {},
            status=GoodsStatus.ON_SALE,
        )
        db.add(goods)
        await db.flush()

        if obj_in.attachment_ids:
            stmt = (
                update(Attachment)
                .where(
                    Attachment.attachment_id.in_(obj_in.attachment_ids),
                    Attachment.creator_id == publisher_id,
                )
                .values(target_type="GOODS", target_id=goods.goods_id)
            )
            await db.execute(stmt)

        await db.commit()
        return goods

    @staticmethod
    async def get_goods_by_id(db: AsyncSession, goods_id: int) -> Optional[Goods]:
        """Get single non-deleted goods by ID."""
        stmt = (
            select(Goods)
            .where(Goods.goods_id == goods_id, Goods.is_deleted == False)
            .options(selectinload(Goods.user), selectinload(Goods.attachments))
        )
        res = await db.execute(stmt)
        return res.scalar_one_or_none()

    @staticmethod
    async def list_all_goods(
        db: AsyncSession,
        keyword: Optional[str] = None,
        category_id: Optional[int] = None,
        status: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[Goods], int]:
        """Marketplace lobby paginated query with filters."""
        stmt = select(Goods).where(Goods.is_deleted == False)
        if status:
            stmt = stmt.where(Goods.status == GoodsStatus(status))
        if category_id:
            stmt = stmt.where(Goods.category_id == category_id)
        if keyword:
            stmt = stmt.where(Goods.name.like(f"%{keyword}%"))

        count_stmt = select(func.count()).select_from(stmt.subquery())
        count_res = await db.execute(count_stmt)
        total = int(count_res.scalar_one() or 0)

        stmt = (
            stmt.options(selectinload(Goods.user), selectinload(Goods.attachments))
            .order_by(Goods.create_time.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        res = await db.execute(stmt)
        return res.scalars().all(), total

    @staticmethod
    async def list_goods_by_user(
        db: AsyncSession, user_id: int, page: int = 1, page_size: int = 20
    ) -> Tuple[List[Goods], int]:
        """Get non-deleted goods published by a user."""
        stmt = select(Goods).where(Goods.publisher_id == user_id, Goods.is_deleted == False)
        count_stmt = select(func.count()).select_from(stmt.subquery())
        count_res = await db.execute(count_stmt)
        total = int(count_res.scalar_one() or 0)

        stmt = (
            stmt.options(selectinload(Goods.user), selectinload(Goods.attachments))
            .order_by(Goods.create_time.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        res = await db.execute(stmt)
        return res.scalars().all(), total

    @staticmethod
    async def update_goods(db: AsyncSession, goods: Goods, obj_in: GoodsUpdate) -> Goods:
        """Partial update goods fields."""
        update_data = obj_in.model_dump(exclude_unset=True)
        for field in update_data:
            if field == "status" and update_data[field]:
                goods.status = GoodsStatus(update_data[field])
            elif field == "condition" and update_data[field]:
                goods.condition = GoodsCondition(update_data[field])
            elif field == "attachment_ids":
                pass
            else:
                setattr(goods, field, update_data[field])

        if obj_in.attachment_ids is not None:
            await db.execute(
                update(Attachment)
                .where(Attachment.target_type == "GOODS", Attachment.target_id == goods.goods_id)
                .values(target_id=None, target_type=None)
            )
            if obj_in.attachment_ids:
                await db.execute(
                    update(Attachment)
                    .where(Attachment.attachment_id.in_(obj_in.attachment_ids))
                    .values(target_type="GOODS", target_id=goods.goods_id)
                )

        await db.commit()
        return goods

    @staticmethod
    async def soft_delete_goods(db: AsyncSession, goods: Goods) -> None:
        """Soft-delete a goods item."""
        goods.is_deleted = True
        await db.commit()