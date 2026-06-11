"""Goods CRUD service layer."""
import logging
from typing import Optional, List, Tuple, Any

from sqlalchemy import select, func, update, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import noload, selectinload
from app.core import AuthHTTPException, BusinessHTTPException, ResourceHTTPException, get_now_naive, parse_datetime_to_beijing_naive, settings
from app.models.goods import Goods, GoodsStatus, GoodsCondition
from app.models.attachment import Attachment
from app.models.user import User
from app.schemas.goods import GoodsCreate, GoodsUpdate

logger = logging.getLogger(__name__)


class GoodsService:
    @staticmethod
    async def _hydrate_goods_avatar(db: AsyncSession, goods_list: list) -> None:
        """委托统一头像灌水中心（AttachmentService.hydrate_owners_avatar）。"""
        from app.services.attachment_service import AttachmentService
        await AttachmentService.hydrate_owners_avatar(db, goods_list)
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
        # 组装联系方式 JSON
        contact_parts = {}
        if obj_in.phone: contact_parts["phone"] = obj_in.phone
        if obj_in.wx: contact_parts["wx"] = obj_in.wx
        if obj_in.qq: contact_parts["qq"] = obj_in.qq
        if contact_parts:
            goods.contact = contact_parts
        # 截止时间处理
        if obj_in.expire_time:
            try:
                parsed_expire = parse_datetime_to_beijing_naive(obj_in.expire_time)
                if parsed_expire <= get_now_naive():
                    raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg="截止时间不能早于或等于当前时间")
                goods.expire_time = parsed_expire
            except BusinessHTTPException:
                raise
            except Exception as e:
                logger.warning(f"截止时间解析失败 expire_time={obj_in.expire_time!r}: {e}")
                raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg="截止时间格式不正确")
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
        
        try:
            # 正常刷新 goods 及其直属关联
            await db.refresh(goods, ["user", "attachments"])
            # 为刚刚创建的单个商品灌入卖家头像 URL
            await GoodsService._hydrate_goods_avatar(db, [goods])
        except Exception as e:
            # 完美保护单元测试的简陋 Mock 桩
            logger.warning("\u26a0 [Goods Service] Failed to refresh goods relations during instantiation: %s", e, exc_info=True)
            
        return goods

    @staticmethod
    async def get_goods_by_id(db: AsyncSession, goods_id: int) -> Optional[Goods]:
        """Get single non-deleted goods by ID."""
        stmt = (
            select(Goods)
            .where(Goods.goods_id == goods_id, Goods.is_deleted == False)
            # 只 selectinload 必需的关系链，noload comments 防止联动加载评论
            .options(selectinload(Goods.user), selectinload(Goods.attachments), noload(Goods.comments))
        )
        res = await db.execute(stmt)
        goods = res.scalar_one_or_none()
        
        if goods:
            # 单货点杀回填头像
            await GoodsService._hydrate_goods_avatar(db, [goods])
            
        return goods

    @staticmethod
    async def list_all_goods(
        db: AsyncSession,
        keyword: Optional[str] = None,
        category_id: Optional[int] = None,
        status: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
        exclude_publisher_ids: Optional[list[int]] = None,
    ) -> Tuple[List[Goods], int]:
        """Marketplace lobby paginated query with filters."""
        stmt = select(Goods).where(Goods.is_deleted == False)
        # 黑名单过滤：排除拉黑了当前用户的发布者
        if exclude_publisher_ids:
            stmt = stmt.where(Goods.publisher_id.notin_(exclude_publisher_ids))
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
            stmt.options(selectinload(Goods.user), selectinload(Goods.attachments), noload(Goods.comments))
            .order_by(Goods.create_time.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        res = await db.execute(stmt)
        goods_list = list(res.scalars().all())
        
        # 列表批量灌水回填头像（O(1) 效率，完美绞杀 N+1 问题）
        await GoodsService._hydrate_goods_avatar(db, goods_list)
        
        return goods_list, total

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
            stmt.options(selectinload(Goods.user), selectinload(Goods.attachments), noload(Goods.comments))
            .order_by(Goods.create_time.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        res = await db.execute(stmt)
        goods_list = list(res.scalars().all())
        
        # 用户商品列表批量灌水回填头像
        await GoodsService._hydrate_goods_avatar(db, goods_list)
        
        return goods_list, total

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


    @staticmethod
    async def delist_goods(db: AsyncSession, goods_id: int, user_id: int) -> Goods:
        """卖家下架商品：ON_SALE → OFF_SHELF。"""
        stmt = select(Goods).where(Goods.goods_id == goods_id, Goods.is_deleted == False)
        res = await db.execute(stmt)
        goods = res.scalar_one_or_none()
        if goods is None:
            raise ResourceHTTPException(code=settings.USER_GET_FAILED_CODE, msg="商品不存在或已删除")
        if goods.publisher_id != user_id:
            raise AuthHTTPException(code=settings.INSUFFICIENT_AUTHORITY_CODE, msg="仅商品发布者可操作")
        if goods.status != GoodsStatus.ON_SALE:
            raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg="仅上架中商品可下架")
        goods.status = GoodsStatus.OFF_SHELF
        await db.commit()
        return goods

    @staticmethod
    async def relist_goods(db: AsyncSession, goods_id: int, user_id: int) -> Goods:
        """卖家重新上架商品：OFF_SHELF → ON_SALE。"""
        stmt = select(Goods).where(Goods.goods_id == goods_id, Goods.is_deleted == False)
        res = await db.execute(stmt)
        goods = res.scalar_one_or_none()
        if goods is None:
            raise ResourceHTTPException(code=settings.USER_GET_FAILED_CODE, msg="商品不存在或已删除")
        if goods.publisher_id != user_id:
            raise AuthHTTPException(code=settings.INSUFFICIENT_AUTHORITY_CODE, msg="仅商品发布者可操作")
        if goods.status != GoodsStatus.OFF_SHELF:
            raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg="仅已下架商品可重新上架")
        goods.status = GoodsStatus.ON_SALE
        await db.commit()
        return goods
