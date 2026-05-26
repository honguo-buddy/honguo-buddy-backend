"""Post 服务：任务发布、查询、接单等业务逻辑。

核心职责：
- 发布帖子：保存 Post 记录，绑定附件
- 查询列表：支持关键词、属性过滤，返回分页结果
- 获取详情：加载完整的 Post 及其关联数据

注意：
- price 单位为元（浮点），避免精度问题时应由 OrderService 转换为分（整数）
- template_data 存储 max_accepters、属性等 JSON 扩展字段
- 接单数计算由 OrderService 统一管理，本服务通过委托获取
"""

import logging
from typing import List, Optional, Tuple

from sqlalchemy import and_, select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core import BusinessHTTPException, ResourceHTTPException, parse_datetime_to_beijing_naive, settings
from app.models import Category, Direction, Post, PostStatus, UrgencyLevel, Order, ItemType, OrderStatus
from app.schemas.post import PostCreate, PostUpdate
from app.services.attachment_service import AttachmentService

logger = logging.getLogger(__name__)


class PostService:
    """任务（Post）业务服务层。"""

    @staticmethod
    async def _resolve_default_category_id(db: AsyncSession) -> int:
        """解析帖子默认分类 ID，避免依赖写死的魔数。"""
        stmt = (
            select(Category.category_id)
            .where(Category.is_deleted == False)
            .order_by(Category.category_id.asc())
        )
        res = await db.execute(stmt)
        default_category_id = res.scalar_one_or_none()
        if default_category_id is None:
            raise ResourceHTTPException(code=settings.DATA_GET_FAILED_CODE, msg="暂无可用分类")
        return int(default_category_id)

    @staticmethod
    async def _get_post_for_update(db: AsyncSession, post_id: int) -> Post:
        stmt = select(Post).where(Post.post_id == post_id, Post.is_deleted == False).with_for_update()
        res = await db.execute(stmt)
        post = res.scalars().first()
        if not post:
            raise ResourceHTTPException(code=settings.DATA_GET_FAILED_CODE, msg="帖子不存在或已删除")
        return post

    @staticmethod
    async def create_post(
        db: AsyncSession, 
        publisher_id: int, 
        post_create: PostCreate,
        attachment_ids: Optional[List[int]] = None,
    ) -> Post:
        """创建新帖子并绑定附件。
        
        Args:
            db: 数据库会话
            publisher_id: 发布者 ID
            post_create: 创建请求数据
            attachment_ids: 附件 ID 列表（可选）
            
        Returns:
            创建后的 Post 对象
        """
        # 构建 template_data，包含 max_accepters 和 template_filters（模板相关字段）
        template_data = post_create.template_filters.copy() if post_create.template_filters else {}
        template_data["max_accepters"] = post_create.max_accepters
        
        # 验证并转换 direction 和 urgency
        try:
            direction = Direction(post_create.direction)
        except ValueError:
            direction = Direction.SELL
        
        try:
            urgency = UrgencyLevel(post_create.urgency)
        except ValueError:
            urgency = UrgencyLevel.NORMAL

        category_id = post_create.category_id
        if category_id is None:
            category_id = await PostService._resolve_default_category_id(db)
        
        # 创建 Post 对象
        post = Post(
            publisher_id=publisher_id,
            title=post_create.title,
            description=post_create.description,
            price=post_create.price,
            direction=direction,
            urgency=urgency,
            template_data=template_data,
            category_id=category_id,
            status=PostStatus.OPEN,
        )
        db.add(post)
        await db.flush()
        await db.refresh(post)
        
        # 如果提供了附件 ID，绑定附件
        if attachment_ids:
            for attachment_id in attachment_ids:
                try:
                    await AttachmentService.bind_attachment_to_target(
                        db, 
                        attachment_id, 
                        target_type="POST",
                        target_id=post.post_id,
                    )
                except Exception as e:
                    logger.warning(f"绑定附件 {attachment_id} 到帖子 {post.post_id} 失败: {e}")
        
        await db.commit()
        await db.refresh(post)
        return post

    @staticmethod
    async def update_post(db: AsyncSession, post_id: int, payload: PostUpdate, operator_id: int, is_admin: bool = False) -> Post:
        """局部更新帖子，要求帖子仍处于可编辑状态。"""

        post = await PostService._get_post_for_update(db, post_id)
        if not is_admin and post.publisher_id != operator_id:
            raise BusinessHTTPException(code=settings.INSUFFICIENT_AUTHORITY_CODE, msg="只有帖子拥有者或管理员可以修改")

        if post.status != PostStatus.OPEN:
            raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg="当前状态下禁止修改委托信息")

        # 若存在待处理的接单（PENDING），则禁止帖子拥有者修改以避免竞态与不一致
        pending_stmt = select(func.count()).select_from(Order).where(
            Order.item_type == ItemType.POST,
            Order.item_id == post_id,
            Order.status == OrderStatus.PENDING,
            Order.is_deleted == False,
        )
        pending_res = await db.execute(pending_stmt)
        pending_cnt = int(pending_res.scalar_one() or 0)
        if pending_cnt > 0 and not is_admin:
            raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg="禁止修改委托信息")

        if payload.title is not None:
            post.title = payload.title
        if payload.description is not None:
            post.description = payload.description
        if payload.price is not None:
            post.price = payload.price
        if payload.direction is not None:
            try:
                post.direction = Direction[payload.direction.upper()]
            except KeyError as exc:
                raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg="direction 仅支持 SELL/BUY") from exc
        if payload.urgency is not None:
            try:
                post.urgency = UrgencyLevel[payload.urgency.upper()]
            except KeyError as exc:
                raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg="urgency 仅支持 NORMAL/URGENT/EMERGENCY") from exc
        if payload.category_id is not None:
            post.category_id = payload.category_id
        if payload.max_accepters is not None or payload.template_filters is not None:
            template_data = dict(post.template_data or {})
            if payload.template_filters:
                template_data.update(payload.template_filters)
            if payload.max_accepters is not None:
                template_data["max_accepters"] = payload.max_accepters
            post.template_data = template_data

        await db.flush()
        await db.refresh(post)
        await db.commit()
        return post

    @staticmethod
    async def soft_delete_post(db: AsyncSession, post_id: int, operator_id: int, is_admin: bool = False) -> Post:
        """软删除帖子，只允许拥有者或管理员操作。"""

        post = await PostService._get_post_for_update(db, post_id)
        if not is_admin and post.publisher_id != operator_id:
            raise BusinessHTTPException(code=settings.INSUFFICIENT_AUTHORITY_CODE, msg="只有帖子拥有者或管理员可以删除")
        post.is_deleted = True
        await db.flush()
        await db.refresh(post)
        await db.commit()
        return post

    @staticmethod
    async def list_posts_by_user(
        db: AsyncSession,
        user_id: int,
        page: int = 1,
        page_size: int = 20,
        category_id: Optional[int] = None,
        status: Optional[str] = None,
        public_only: bool = False,
    ) -> Tuple[List[Post], int]:
        """按发布者查询帖子列表。"""

        conditions = [Post.publisher_id == user_id, Post.is_deleted == False]
        if category_id is not None:
            conditions.append(Post.category_id == category_id)

        if status:
            status_values = [s.strip().upper() for s in status.split(",") if s.strip()]
            if public_only:
                allowed_statuses = {PostStatus.OPEN, PostStatus.IN_PROGRESS, PostStatus.CLOSED}
                filtered = []
                for item in status_values:
                    try:
                        status_enum = PostStatus[item]
                    except KeyError:
                        continue
                    if status_enum in allowed_statuses:
                        filtered.append(status_enum)
                if filtered:
                    conditions.append(Post.status.in_(filtered))
                else:
                    conditions.append(Post.status.in_(list(allowed_statuses)))
            else:
                parsed_statuses = []
                for item in status_values:
                    try:
                        parsed_statuses.append(PostStatus[item])
                    except KeyError:
                        continue
                if parsed_statuses:
                    conditions.append(Post.status.in_(parsed_statuses))

        count_stmt = select(func.count()).select_from(Post).where(and_(*conditions))
        count_res = await db.execute(count_stmt)
        total = int(count_res.scalar_one() or 0)

        stmt = (
            select(Post)
            .options(selectinload(Post.user), selectinload(Post.attachments), selectinload(Post.orders))
            .where(and_(*conditions))
            .order_by(Post.create_time.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        res = await db.execute(stmt)
        posts = res.scalars().unique().all()
        return posts, total

    @staticmethod
    async def list_public_posts_by_user(
        db: AsyncSession,
        user_id: int,
        page: int = 1,
        page_size: int = 20,
        category_id: Optional[int] = None,
        status: Optional[str] = None,
    ) -> Tuple[List[Post], int]:
        """公开查询指定用户的帖子，仅返回允许公开可见的状态。"""

        allowed_statuses = [PostStatus.OPEN, PostStatus.IN_PROGRESS, PostStatus.CLOSED]
        conditions = [
            Post.publisher_id == user_id,
            Post.is_deleted == False,
            Post.status.in_(allowed_statuses),
        ]
        if category_id is not None:
            conditions.append(Post.category_id == category_id)
        if status:
            requested = []
            for item in str(status).split(","):
                item = item.strip().upper()
                if not item:
                    continue
                try:
                    status_enum = PostStatus[item]
                except KeyError:
                    continue
                if status_enum in allowed_statuses:
                    requested.append(status_enum)
            if requested:
                conditions.append(Post.status.in_(requested))

        count_stmt = select(func.count()).select_from(Post).where(and_(*conditions))
        count_res = await db.execute(count_stmt)
        total = int(count_res.scalar_one() or 0)

        stmt = (
            select(Post)
            .options(selectinload(Post.user), selectinload(Post.attachments), selectinload(Post.orders))
            .where(and_(*conditions))
            .order_by(Post.create_time.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        res = await db.execute(stmt)
        posts = res.scalars().unique().all()
        return posts, total

    @staticmethod
    async def list_posts(
        db: AsyncSession,
        keyword: Optional[str] = None,
        category_id: Optional[int] = None,
        urgency: Optional[str] = None,
        direction: Optional[str] = None,
        price_min: Optional[float] = None,
        price_max: Optional[float] = None,
        create_time_start: Optional[str] = None,  # ISO 格式 YYYY-MM-DD HH:MM:SS
        create_time_end: Optional[str] = None,    # ISO 格式 YYYY-MM-DD HH:MM:SS
        status: Optional[str] = None,
        template_filters: Optional[dict] = None,  # 模板相关筛选字段（JSON）
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[Post], int]:
        """查询帖子列表，支持多条件过滤和分页。
        
        核心设计：
        - 直接字段：keyword、urgency、direction、price_min/max、create_time_range、status
        - 模板字段：template_filters（JSON，根据模板动态定义）
        
        Args:
            db: 数据库会话
            keyword: 关键词（模糊匹配 title 和 description）
            urgency: 紧急度（NORMAL, URGENT, EMERGENCY，逗号分隔支持多个）
            direction: 方向（SELL 或 BUY）
            price_min: 最小价格
            price_max: 最大价格
            create_time_start: 创建时间范围起始
            create_time_end: 创建时间范围结束
            status: 状态过滤（默认 OPEN）
            template_filters: 模板相关筛选（如 {"address": "中关村", "condition": "新"}）
            page: 当前页（从 1 开始）
            page_size: 每页数量
            
        Returns:
            (帖子列表, 总数) 元组
        """
        
        
        # 基础查询条件
        conditions = [Post.is_deleted == False]
        if category_id is not None:
            conditions.append(Post.category_id == category_id)
        
        # 状态过滤（默认 OPEN）
        if status:
            try:
                post_status = PostStatus(status)
                conditions.append(Post.status == post_status)
            except ValueError:
                conditions.append(Post.status == PostStatus.OPEN)
        else:
            conditions.append(Post.status == PostStatus.OPEN)
        
        # 关键词过滤：全局搜索（标题和描述）
        if keyword:
            keyword_pattern = f"%{keyword}%"
            conditions.append(
                or_(
                    Post.title.ilike(keyword_pattern),
                    Post.description.ilike(keyword_pattern),
                )
            )
        
        # 紧急度过滤（支持多个值）
        if urgency:
            urgency_list = [u.strip().upper() for u in urgency.split(",")]
            conditions.append(Post.urgency.in_(urgency_list))
        
        # 方向过滤
        if direction:
            try:
                direction_enum = Direction(direction)
                conditions.append(Post.direction == direction_enum)
            except ValueError:
                pass
        
        # 价格范围过滤
        if price_min is not None:
            conditions.append(Post.price >= price_min)
        if price_max is not None:
            conditions.append(Post.price <= price_max)
        
        # 时间范围过滤
        if create_time_start:
            try:
                start_dt = parse_datetime_to_beijing_naive(create_time_start)
                conditions.append(Post.create_time >= start_dt)
            except (ValueError, TypeError):
                logger.warning(f"无效的 create_time_start 格式: {create_time_start}")
        
        if create_time_end:
            try:
                end_dt = parse_datetime_to_beijing_naive(create_time_end)
                conditions.append(Post.create_time <= end_dt)
            except (ValueError, TypeError):
                logger.warning(f"无效的 create_time_end 格式: {create_time_end}")
        
        # 模板相关字段过滤（JSON）
        if template_filters and isinstance(template_filters, dict):
            for key, value in template_filters.items():
                if value is not None:
                    # 支持模糊匹配（如地址、条件等文本字段）
                    if isinstance(value, str):
                        conditions.append(Post.template_data[key].astext.ilike(f"%{value}%"))
                    else:
                        conditions.append(Post.template_data[key] == value)
        
        # 统计总数
        cnt_stmt = select(func.count()).select_from(Post).where(and_(*conditions))
        cnt_res = await db.execute(cnt_stmt)
        total = int(cnt_res.scalar_one() or 0)
        
        # 分页查询，使用 selectinload 防止 N+1
        offset = (page - 1) * page_size
        stmt = (
            select(Post)
            .options(
                selectinload(Post.user),
                selectinload(Post.attachments),
                selectinload(Post.orders),
            )
            .where(and_(*conditions))
            .order_by(Post.urgency.desc(), Post.create_time.desc())  # 优先按紧急度排序，然后按创建时间
            .offset(offset)
            .limit(page_size)
        )
        res = await db.execute(stmt)
        posts = res.scalars().unique().all()
        
        return posts, total

    @staticmethod
    async def get_post_detail(db: AsyncSession, post_id: int) -> Post:
        """获取帖子详情，包含发布者、附件、评论、订单关联。
        
        Args:
            db: 数据库会话
            post_id: 帖子 ID
            
        Returns:
            Post 对象（包含所有关联数据）
            
        Raises:
            ResourceHTTPException: 如果帖子不存在或已删除
        """
        stmt = (
            select(Post)
            .options(
                selectinload(Post.user),
                selectinload(Post.attachments),
                selectinload(Post.comments),
                selectinload(Post.orders),
            )
            .where(
                and_(
                    Post.post_id == post_id,
                    Post.is_deleted == False,
                )
            )
        )
        res = await db.execute(stmt)
        post = res.scalars().unique().first()
        
        if not post:
            raise ResourceHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="帖子不存在或已删除",
            )
        
        return post
