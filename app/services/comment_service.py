"""评论服务：处理评论发布、删除、查询等业务逻辑。

核心职责：
- 发布评论/回复：创建新评论，验证父评论
- 软删除评论：标记删除，处理回复的内容清洗
- 查询根评论列表：游标分页，包含回复计数和预览
- 查询回复详情流：获取单条评论下的所有回复
"""

import logging
from typing import List, Optional, Tuple

from sqlalchemy import and_, select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core import AuthHTTPException, BusinessHTTPException, ResourceHTTPException, settings
from app.models import AttachmentTargetType, Comment, Goods, Post, User, TargetType
from app.services.attachment_service import AttachmentService
from app.services.metrics_service import MetricsService
from app.db import redis as app_redis
logger = logging.getLogger(__name__)


class CommentService:
    """评论业务服务层。"""

    @staticmethod
    async def create_comment(
        db: AsyncSession,
        user_id: int,
        target_type: str,
        target_id: int,
        content: str,
        parent_id: Optional[int] = None,
        attachment_ids: Optional[List[int]] = None,
    ) -> Comment:
        """创建新评论/回复。
        
        Args:
            db: 数据库会话
            user_id: 评论用户ID
            target_type: 目标类型 (POST, GOODS, ORDER)
            target_id: 目标ID
            content: 评论内容
            parent_id: 父评论ID（可选，为None表示根评论）
            
        Returns:
            创建的Comment对象
            
        Raises:
            BusinessHTTPException: 如果target_type无效
            ResourceHTTPException: 如果parent_id指向的评论不存在
        """
        # 验证 target_type
        try:
            TargetType(target_type)
        except ValueError:
            raise BusinessHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg=f"无效的目标类型: {target_type}",
            )

        # 验证目标实体是否存在（防御孤儿评论写入）
        if target_type == "POST":
            target_obj = await db.get(Post, target_id)
        elif target_type == "GOODS":
            target_obj = await db.get(Goods, target_id)
        else:
            target_obj = None

        if target_obj is None or getattr(target_obj, "is_deleted", False):
            raise ResourceHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="目标帖子或商品不存在或已被删除",
            )
        
        # 如果有parent_id，验证父评论是否存在且未被删除
        if parent_id is not None:
            stmt = select(Comment).where(
                and_(Comment.comment_id == parent_id, Comment.is_deleted == False)
            )
            result = await db.execute(stmt)
            parent_comment = result.scalars().first()
            if not parent_comment:
                raise ResourceHTTPException(
                    code=settings.DATA_GET_FAILED_CODE,
                    msg="父评论不存在或已被删除",
                )
        
        # 创建新评论
        new_comment = Comment(
            user_id=user_id,
            target_type=TargetType(target_type),
            target_id=target_id,
            parent_id=parent_id,
            content=content,
            is_deleted=False,
        )
        db.add(new_comment)
        await db.flush()

        if attachment_ids:
            await AttachmentService.bind_attachments_to_target(
                db=db,
                attachment_ids=attachment_ids,
                target_type=AttachmentTargetType.COMMENT.value,
                target_id=new_comment.comment_id,
                creator_id=user_id,
            )

        await db.commit()
        await db.refresh(new_comment, attribute_names=["user", "parent", "replies"])

        # 提取归一化的业务类型字符串（POST 或 GOODS）
        current_target_type = getattr(new_comment.target_type, 'value', new_comment.target_type)

        if current_target_type == "POST":
            try:
                await MetricsService.incr_post_comment(app_redis, new_comment.target_id, delta=1)
            except Exception:
                pass
                
        elif current_target_type == "GOODS":
            try:
                # 精准轰击商品专属的评论自增引擎，彻底打通集市计数闭环！
                await MetricsService.incr_goods_comment(app_redis, new_comment.target_id, delta=1)
            except Exception:
                pass
        
        return new_comment

    @staticmethod
    async def delete_comment(
        db: AsyncSession,
        comment_id: int,
        current_user_id: int,
        is_admin: bool,
    ) -> None:
        """软删除评论。
        
        Args:
            db: 数据库会话
            comment_id: 要删除的评论ID
            current_user_id: 当前用户ID
            is_admin: 当前用户是否为管理员
            
        Raises:
            ResourceHTTPException: 如果评论不存在
            AuthHTTPException: 如果没有权限删除
        """
        # 获取评论
        stmt = select(Comment).where(Comment.comment_id == comment_id)
        result = await db.execute(stmt)
        comment = result.scalars().first()
        
        if not comment:
            raise ResourceHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="评论不存在",
            )
        
        # 检查权限：只有所有者或管理员可以删除
        if comment.user_id != current_user_id and not is_admin:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权删除他人评论",
            )
        
        # 标记为删除，清洗内容
        comment.is_deleted = True
        comment.content = "该评论已由用户删除"

        if getattr(comment.target_type, 'value', comment.target_type) == "POST":
            try:
                await MetricsService.incr_post_comment(app_redis, comment.target_id, delta=-1)
            except Exception:
                pass
        
        # 如果有子回复，需要清洗它们的内容以保持树状结构完整
        # 但不删除子回复记录本身
        stmt_children = select(Comment).where(
            and_(Comment.parent_id == comment_id, Comment.is_deleted == False)
        )
        result_children = await db.execute(stmt_children)
        child_comments = result_children.scalars().all()
        
        for child in child_comments:
            child.content = "该评论已由用户删除"
        
        await db.commit()

    @staticmethod
    async def get_root_comments(
        db: AsyncSession,
        target_type: str,
        target_id: int,
        cursor: Optional[int] = None,
        size: int = 20,
    ) -> Tuple[List[Comment], Optional[int]]:
        """获取目标的根评论列表（游标分页）。
        
        Args:
            db: 数据库会话
            target_type: 目标类型
            target_id: 目标ID
            cursor: 游标（上一页最后一条评论的ID）
            size: 每页大小
            
        Returns:
            (评论列表, 下一页游标)
        """
        try:
            TargetType(target_type)
        except ValueError:
            raise BusinessHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg=f"无效的目标类型: {target_type}",
            )
        
        # 构建查询条件
        where_conditions = [
            Comment.target_type == TargetType(target_type),
            Comment.target_id == target_id,
            Comment.parent_id.is_(None),
            Comment.is_deleted == False,
        ]
        
        # 游标分页：获取 cursor 之前的评论（降序）
        if cursor is not None:
            where_conditions.append(Comment.comment_id < cursor)
        
        stmt = (
            select(Comment)
            .where(and_(*where_conditions))
            .options(
                selectinload(Comment.user).selectinload(User.avatar_attachment),
                selectinload(Comment.replies),
            )
            .order_by(Comment.comment_id.desc())
            .limit(size + 1)  # 多查1条用于判断是否有下一页
        )
        
        result = await db.execute(stmt)
        comments = result.scalars().all()
        
        next_cursor = None
        if len(comments) > size:
            # 有下一页，返回最后一条评论的ID作为下一页游标
            comments = comments[:size]
            next_cursor = comments[-1].comment_id
        
        return comments, next_cursor

    @staticmethod
    async def get_replies(
        db: AsyncSession,
        comment_id: int,
        cursor: Optional[int] = None,
        size: int = 20,
    ) -> Tuple[List[Comment], Optional[int]]:
        """获取单条根评论下的所有回复（按时间正序）。
        
        Args:
            db: 数据库会话
            comment_id: 根评论ID
            cursor: 游标（上一页最后一条回复的ID）
            size: 每页大小
            
        Returns:
            (回复列表, 下一页游标)
        """
        # 验证根评论存在
        stmt_parent = select(Comment).where(Comment.comment_id == comment_id)
        result_parent = await db.execute(stmt_parent)
        parent_comment = result_parent.scalars().first()
        
        if not parent_comment:
            raise ResourceHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="评论不存在",
            )
        
        # 构建查询条件
        where_conditions = [
            Comment.parent_id == comment_id,
            Comment.is_deleted == False,
        ]
        
        # 游标分页：获取 cursor 之后的评论（正序，按create_time或comment_id）
        if cursor is not None:
            where_conditions.append(Comment.comment_id > cursor)
        
        stmt = (
            select(Comment)
            .where(and_(*where_conditions))
            .options(selectinload(Comment.user).selectinload(User.avatar_attachment))
            .order_by(Comment.create_time.asc())
            .limit(size + 1)
        )
        
        result = await db.execute(stmt)
        replies = result.scalars().all()
        
        next_cursor = None
        if len(replies) > size:
            replies = replies[:size]
            next_cursor = replies[-1].comment_id
        
        return replies, next_cursor

    @staticmethod
    async def get_reply_count(db: AsyncSession, comment_id: int) -> int:
        """获取评论的回复总数。
        
        Args:
            db: 数据库会话
            comment_id: 评论ID
            
        Returns:
            回复总数
        """
        stmt = select(func.count(Comment.comment_id)).where(
            and_(Comment.parent_id == comment_id, Comment.is_deleted == False)
        )
        result = await db.execute(stmt)
        count = result.scalar() or 0
        return count

    @staticmethod
    async def get_reply_count_map(db: AsyncSession, comment_ids: List[int]) -> dict:
        """批量获取多个评论的回复计数。
        
        Args:
            db: 数据库会话
            comment_ids: 评论ID列表
            
        Returns:
            {comment_id: reply_count} 的字典
        """
        if not comment_ids:
            return {}
        
        stmt = (
            select(Comment.parent_id, func.count(Comment.comment_id).label("count"))
            .where(
                and_(
                    Comment.parent_id.in_(comment_ids),
                    Comment.is_deleted == False,
                )
            )
            .group_by(Comment.parent_id)
        )
        result = await db.execute(stmt)
        rows = result.all()
        
        count_map = {parent_id: count for parent_id, count in rows}
        # 对于没有回复的评论，补充0
        for comment_id in comment_ids:
            if comment_id not in count_map:
                count_map[comment_id] = 0
        
        return count_map

    @staticmethod
    async def get_preview_replies(
        db: AsyncSession,
        comment_id: int,
        limit: int = 3,
    ) -> List[Comment]:
        """获取评论的最新N条回复预览。
        
        Args:
            db: 数据库会话
            comment_id: 评论ID
            limit: 预览数量（默认3条）
            
        Returns:
            最新回复列表
        """
        stmt = (
            select(Comment)
            .where(
                and_(Comment.parent_id == comment_id, Comment.is_deleted == False)
            )
            .options(selectinload(Comment.user))
            .order_by(Comment.create_time.desc())
            .limit(limit)
        )
        result = await db.execute(stmt)
        replies = result.scalars().all()
        
        # 反序以保持时间正序
        return list(reversed(replies))

    @staticmethod
    async def get_comment_attachment_urls_map(db: AsyncSession, comment_ids: List[int]) -> dict[int, list[str]]:
        return await AttachmentService.get_urls_by_target(db, AttachmentTargetType.COMMENT.value, comment_ids)
