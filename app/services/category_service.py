"""Category 服务：模板分类 CRUD 业务逻辑。"""

from typing import List

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import BusinessHTTPException, ResourceHTTPException, settings
from app.models import Category
from app.schemas import CategoryCreate, CategoryUpdate
from app.models.order import ItemType


class CategoryService:
    """模板分类服务层。"""

    @staticmethod
    async def list_categories(db: AsyncSession, item_type: str | None = None) -> List[Category]:
        stmt = select(Category).where(Category.is_deleted == False)
        if item_type:
            # 接受 POST/GOODS 字符串，统一为大写进行比较
            it = str(item_type).upper()
            if it in {"POST", "GOODS"}:
                stmt = stmt.where(Category.item_type == ItemType[it])
            else:
                # 非法类型返回空列表
                return []
        stmt = stmt.order_by(Category.create_time.desc())
        res = await db.execute(stmt)
        return list(res.scalars().all())

    @staticmethod
    async def get_category_by_id(db: AsyncSession, category_id: int) -> Category:
        stmt = select(Category).where(
            and_(
                Category.category_id == category_id,
                Category.is_deleted == False,
            )
        )
        res = await db.execute(stmt)
        category = res.scalar_one_or_none()
        if not category:
            raise ResourceHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="模板分类不存在",
            )
        return category

    @staticmethod
    async def create_category(db: AsyncSession, payload: CategoryCreate) -> Category:
        await CategoryService._ensure_name_unique(db, payload.name)

        category = Category(
            name=payload.name,
            icon=payload.icon,
            config_json=payload.config_json,
            item_type=ItemType[payload.item_type] if getattr(payload, "item_type", None) else ItemType.POST,
        )
        db.add(category)
        await db.flush()
        await db.refresh(category)
        return category

    @staticmethod
    async def update_category(
        db: AsyncSession,
        category_id: int,
        payload: CategoryUpdate,
    ) -> Category:
        category = await CategoryService.get_category_by_id(db, category_id)

        if payload.name is not None and payload.name != category.name:
            await CategoryService._ensure_name_unique(db, payload.name)
            category.name = payload.name

        if getattr(payload, "item_type", None) is not None:
            category.item_type = ItemType[payload.item_type]

        category.icon = payload.icon
        category.config_json = payload.config_json

        await db.flush()
        await db.refresh(category)
        return category

    @staticmethod
    async def delete_category(db: AsyncSession, category_id: int) -> None:
        category = await CategoryService.get_category_by_id(db, category_id)
        category.is_deleted = True
        await db.flush()

    @staticmethod
    async def _ensure_name_unique(db: AsyncSession, name: str) -> None:
        stmt = select(Category.category_id).where(
            and_(
                Category.name == name,
                Category.is_deleted == False,
            )
        )
        res = await db.execute(stmt)
        exists_id = res.scalar_one_or_none()
        if exists_id is not None:
            raise BusinessHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="模板分类名称已存在",
            )
