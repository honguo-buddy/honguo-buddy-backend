"""Category 服务：模板分类 CRUD 业务逻辑。"""

from typing import List, Any

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
        # 创建分类时，必须将当前请求的 payload.item_type 顺手喂进唯一性校验中
        await CategoryService._ensure_name_unique(db, name=payload.name, item_type=payload.item_type)

        category = Category(
            name=payload.name,
            icon=payload.icon,
            config_json=payload.config_json,
            item_type=ItemType[payload.item_type] if getattr(payload, "item_type", None) else ItemType.POST,
        )
        db.add(category)
        await db.flush()
        await db.commit()
        await db.refresh(category)
        return category

    @staticmethod
    async def update_category(
        db: AsyncSession,
        category_id: int,
        payload: CategoryUpdate,
    ) -> Category:
        # 1. 捞出老分类对象
        category = await CategoryService.get_category_by_id(db, category_id)

        # 智能识别更新意图: 如果改了名字或改了业务类型，都需要重新做同域唯一性校验
        # 确定最终的目标名字与目标类型（前端传了用新值，前端没传沿用老值）
        target_name = payload.name if payload.name is not None else category.name
        
        # 兼容处理数据库里的 Enum 对象转为字符串名字
        current_type_str = category.item_type.name if hasattr(category.item_type, "name") else str(category.item_type)
        target_type = payload.item_type if getattr(payload, "item_type", None) is not None else current_type_str

        name_changed = payload.name is not None and payload.name != category.name
        type_changed = getattr(payload, "item_type", None) is not None and payload.item_type != current_type_str

        if name_changed or type_changed:
            # 喂入 3 参数完全体，守护联合防线
            await CategoryService._ensure_name_unique(db, name=target_name, item_type=target_type)

        # 2. 正式执行字段更新写入
        if payload.name is not None:
            category.name = payload.name
        if getattr(payload, "item_type", None) is not None:
            category.item_type = ItemType[payload.item_type]

        category.icon = payload.icon
        category.config_json = payload.config_json

        await db.flush()
        await db.commit()
        await db.refresh(category)
        return category

    @staticmethod
    async def delete_category(db: AsyncSession, category_id: int) -> None:
        category = await CategoryService.get_category_by_id(db, category_id)
        category.is_deleted = True
        await db.flush()
        await db.commit()

    @staticmethod
    async def _ensure_name_unique(db: AsyncSession, name: str, item_type: Any) -> None:
        """检查同业务类型（POST/GOODS）下，分类名称是否唯一。"""
        # 多模态防腐转换: 无论调用者传过来的是纯 String 还是 ItemType 枚举实例，
        # 一律强行对齐转换为标准的 ItemType 枚举类，粉碎 SQLAlchemy 数据库条件比对失败的隐患。
        if isinstance(item_type, str):
            target_enum = ItemType[item_type.upper()]
        elif hasattr(item_type, "name"):
            target_enum = ItemType[item_type.name]
        else:
            target_enum = item_type

        stmt = select(Category.category_id).where(
            and_(
                Category.name == name,
                Category.item_type == target_enum,  # 联合业务隔离对账
                Category.is_deleted == False,
            )
        )
        res = await db.execute(stmt)
        exists_id = res.scalar_one_or_none()
        
        if exists_id is not None:
            raise BusinessHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg=f"该业务类型下已存在名为 '{name}' 的模板分类",
            )