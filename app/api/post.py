"""Post（任务）路由接口。

职责：
- 解析请求参数和用户认证
- 调用 PostService 完成业务逻辑
- 调用 OrderService 获取相关订单数据
- 返回统一的 ResponseModel 格式

注意：接单数由 OrderService 统一计算（DRY 原则）
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.api import get_current_user, get_current_user_optional
from app.core import AuthHTTPException, BusinessHTTPException, ResourceHTTPException, settings
from app.db import get_db, get_redis, redis
from app.schemas import (
    FavoriteRequest,
    FavoriteResponse,
    PostApplicationApplicantRead,
    PostApplicationItem,
    PostApplicationListResponse,
    PostBatchAcceptErrorItem,
    PostBatchAcceptRequest,
    PostBatchAcceptResponse,
    PostBatchAcceptResultItem,
    PostCreate,
    PostDetailRead,
    PostList,
    PostRead,
    PostUpdate,
    ResponseModel,
    UserRead,
)
from app.services import MetricsService, PostService, OrderService, SocialService
from app.models import Comment, Post, TargetType

logger = logging.getLogger(__name__)

router = APIRouter()


def _build_post_read(post, current_accepters: int) -> PostRead:
    attachment_urls = [att.url for att in (post.attachments or []) if not att.is_deleted]
    return PostRead(
        post_id=post.post_id,
        category_id=post.category_id,
        title=post.title,
        description=post.description,
        price=float(post.price) if post.price else None,
        direction=post.direction.value if post.direction else "SELL",
        urgency=post.urgency.value if post.urgency else "NORMAL",
        status=post.status.value if post.status else "OPEN",
        template_data=post.template_data,
        max_accepters=post.max_accepters,
        publisher=UserRead.model_validate(post.user) if post.user else None,
        publisher_id=post.publisher_id,
        current_accepters=current_accepters,
        create_time=post.create_time.isoformat() if post.create_time else "",
        attachment_urls=attachment_urls,
    )


def _build_application_applicant_read(applicant, completed_order_count: int) -> PostApplicationApplicantRead:
    base_data = UserRead.model_validate(applicant).model_dump()
    base_data["avatar"] = applicant.avatar_attachment.url if getattr(applicant, "avatar_attachment", None) else None
    base_data["completed_order_count"] = int(completed_order_count)
    return PostApplicationApplicantRead.model_validate(base_data)


@router.post("/", response_model=ResponseModel[PostRead])
async def publish_post(
    post_create: PostCreate,
    current_user: UserRead = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    发布悬赏帖
    """
    try:
        # 调用服务创建帖子
        post = await PostService.create_post(
            db,
            publisher_id=current_user.user_id,
            post_create=post_create,
            attachment_ids=post_create.attachment_ids,
        )
        current_accepters = await OrderService.get_current_accepters_count(
            db,
            item_type="POST",
            item_id=post.post_id,
        )
        
        return ResponseModel(
            code=settings.SUCCESS_CODE,
            message=_build_post_read(post, current_accepters),
        )
    except Exception as e:
        logger.error(f"发布帖子失败 user_id={current_user.user_id}: {e}")
        raise BusinessHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg="发布帖子失败，请稍后重试",
        )


@router.get("/", response_model=ResponseModel[PostList])
async def list_posts(
    keyword: Optional[str] = Query(None, description="全局关键词（搜索标题和描述）"),
    category_id: Optional[int] = Query(None, description="模板/分类ID"),
    urgency: Optional[str] = Query(None, description="紧急度，支持多个逗号分隔（NORMAL, URGENT, EMERGENCY）"),
    direction: Optional[str] = Query(None, description="方向（SELL 或 BUY）"),
    price_min: Optional[float] = Query(None, ge=0, description="最小价格"),
    price_max: Optional[float] = Query(None, ge=0, description="最大价格"),
    create_time_start: Optional[str] = Query(None, description="创建时间起始（ISO 格式：YYYY-MM-DD HH:MM:SS）"),
    create_time_end: Optional[str] = Query(None, description="创建时间结束（ISO 格式：YYYY-MM-DD HH:MM:SS）"),
    status: Optional[str] = Query(None, description="状态（OPEN, IN_PROGRESS, CLOSED, CANCELLED）"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    db: AsyncSession = Depends(get_db),
):
    """
    获取任务列表，支持多条件过滤。
    
    **筛选策略**：
    - 直接字段：keyword（全局）、urgency、direction、price_min/max、create_time_range、status
    - 模板字段：已移至前端选择模板后动态传递（后续扩展）
    """
    try:
        # 调用服务层查询
        posts, total = await PostService.list_posts(
            db,
            keyword=keyword,
            category_id=category_id,
            urgency=urgency,
            direction=direction,
            price_min=price_min,
            price_max=price_max,
            create_time_start=create_time_start,
            create_time_end=create_time_end,
            status=status,
            template_filters=None,  # 暂时为 None，后续可扩展
            page=page,
            page_size=page_size,
        )
        current_accepters_map = await OrderService.get_current_accepters_count_map(
            db,
            item_type="POST",
            item_ids=[post.post_id for post in posts],
        )
        
        # 对每个帖子补充接单数和附件 URL
        post_list = []
        for post in posts:
            current_accepters = current_accepters_map.get(post.post_id, 0)
            post_list.append(_build_post_read(post, current_accepters))
        
        return ResponseModel(
            code=settings.SUCCESS_CODE,
            message=PostList(
                total=total,
                page=page,
                page_size=page_size,
                list=post_list,
            ),
        )
    except Exception as e:
        logger.error(f"获取任务列表失败: {e}")
        raise BusinessHTTPException(
            code=settings.DATA_GET_FAILED_CODE,
            msg="获取任务列表失败",
        )


@router.get("/me", response_model=ResponseModel[PostList])
async def list_my_posts(
    current_user: UserRead = Depends(get_current_user),
    category_id: Optional[int] = Query(None, description="分类ID"),
    status: Optional[str] = Query(None, description="状态筛选"),
    page: int = Query(1, ge=1, description="页码"),
    size: int = Query(20, ge=1, le=100, alias="size", description="每页数量"),
    db: AsyncSession = Depends(get_db),
):
    """获取当前用户发布的帖子。"""

    posts, total = await PostService.list_posts_by_user(
        db=db,
        user_id=current_user.user_id,
        page=page,
        page_size=size,
        category_id=category_id,
        status=status,
        public_only=False,
    )
    current_accepters_map = await OrderService.get_current_accepters_count_map(
        db,
        item_type="POST",
        item_ids=[post.post_id for post in posts],
    )
    post_list = []
    for post in posts:
        current_accepters = current_accepters_map.get(post.post_id, 0)
        post_list.append(_build_post_read(post, current_accepters))

    return ResponseModel(
        code=settings.SUCCESS_CODE,
        message=PostList(total=total, page=page, page_size=size, list=post_list),
    )


@router.get("/user/{user_id}", response_model=ResponseModel[PostList])
async def list_public_user_posts(
    user_id: int,
    category_id: Optional[int] = Query(None, description="分类ID"),
    status: Optional[str] = Query(None, description="状态筛选"),
    page: int = Query(1, ge=1, description="页码"),
    size: int = Query(20, ge=1, le=100, alias="size", description="每页数量"),
    db: AsyncSession = Depends(get_db),
):
    """公开查询指定用户发布的帖子。"""

    posts, total = await PostService.list_public_posts_by_user(
        db=db,
        user_id=user_id,
        page=page,
        page_size=size,
        category_id=category_id,
        status=status,
    )
    current_accepters_map = await OrderService.get_current_accepters_count_map(
        db,
        item_type="POST",
        item_ids=[post.post_id for post in posts],
    )
    post_list = []
    for post in posts:
        current_accepters = current_accepters_map.get(post.post_id, 0)
        post_list.append(_build_post_read(post, current_accepters))

    return ResponseModel(
        code=settings.SUCCESS_CODE,
        message=PostList(total=total, page=page, page_size=size, list=post_list),
    )


@router.post("/batch-accept", response_model=ResponseModel[PostBatchAcceptResponse])
async def batch_accept_posts(
    payload: PostBatchAcceptRequest,
    current_user: UserRead = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis_client = Depends(get_redis),
):
    """批量接单入口：适用于顺路聚合场景，允许单次申请多个 BUY 帖子。"""

    batch_result = await OrderService.batch_accept_posts(
        db=db,
        initiator_id=current_user.user_id,
        post_ids=payload.post_ids,
        redis_client=redis_client,
    )
    return ResponseModel(
        code=settings.SUCCESS_CODE,
        message=PostBatchAcceptResponse(
            results=[PostBatchAcceptResultItem.model_validate(item) for item in batch_result["results"]],
            errors=[PostBatchAcceptErrorItem.model_validate(item) for item in batch_result["errors"]],
        ),
    )


@router.get("/{post_id}/applications", response_model=ResponseModel[PostApplicationListResponse])
async def list_post_applications(
    post_id: int,
    current_user: UserRead = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """查看指定帖子的接单申请列表，仅帖子发布者可访问。"""

    stmt = select(Post.publisher_id).where(Post.post_id == post_id, Post.is_deleted == False)
    res = await db.execute(stmt)
    publisher_id = res.scalar_one_or_none()
    if publisher_id is None:
        raise ResourceHTTPException(code=settings.DATA_GET_FAILED_CODE, msg="帖子不存在")
    if int(current_user.user_id) != int(publisher_id):
        raise AuthHTTPException(code=settings.INSUFFICIENT_AUTHORITY_CODE, msg="仅帖子拥有者可查看申请列表")

    applications_data = await OrderService.list_post_applications(db, post_id)
    applications = []
    for row in applications_data:
        order = row["order"]
        applicant = row["applicant"]
        applications.append(
            PostApplicationItem(
                application_id=order.order_id,
                post_id=order.item_id,
                applicant=_build_application_applicant_read(applicant, row["completed_order_count"]),
                note=row["note"],
                status=order.status.value if getattr(order.status, "value", None) else str(order.status),
                created_at=order.create_time.isoformat() if order.create_time else "",
            )
        )

    return ResponseModel(
        code=settings.SUCCESS_CODE,
        message=PostApplicationListResponse(applications=applications),
    )


@router.patch("/{post_id}", response_model=ResponseModel[PostRead])
async def update_post(
    post_id: int,
    payload: PostUpdate,
    current_user: UserRead = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """局部更新帖子。"""

    post = await PostService.update_post(
        db=db,
        post_id=post_id,
        payload=payload,
        operator_id=current_user.user_id,
        is_admin=bool(current_user.is_admin),
    )
    current_accepters = await OrderService.get_current_accepters_count(db, item_type="POST", item_id=post.post_id)
    return ResponseModel(code=settings.SUCCESS_CODE, message=_build_post_read(post, current_accepters))


@router.delete("/{post_id}", response_model=ResponseModel[dict])
async def delete_post(
    post_id: int,
    current_user: UserRead = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """软删除帖子。"""

    post = await PostService.soft_delete_post(
        db=db,
        post_id=post_id,
        operator_id=current_user.user_id,
        is_admin=bool(current_user.is_admin),
    )
    return ResponseModel(code=settings.SUCCESS_CODE, message={"post_id": post.post_id, "deleted": True})


@router.get("/{post_id}", response_model=ResponseModel[PostDetailRead])
async def get_post_detail(
    post_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[UserRead] = Depends(get_current_user_optional),
    comments_limit: int = Query(5, ge=0, le=100, description="返回的评论条数，0 表示不返回（建议使用独立分页接口）"),
):
    """
    获取任务详情（仅返回前 N 条评论以避免内存压力）。
    建议：更大数据量场景下使用独立的评论分页接口或在前端逐页加载。
    """
    try:
        post = await PostService.get_post_detail(db, post_id)
        # 通过 OrderService 统一获取接单数
        current_accepters = await OrderService.get_current_accepters_count(
            db,
            item_type="POST",
            item_id=post_id,
        )
        attachment_urls = [att.url for att in (post.attachments or []) if not att.is_deleted]

        # 构建评论列表（仅查询前 N 条热评，按时间倒序）
        comments = []
        if comments_limit > 0:
            comments_stmt = (
                select(Comment)
                .where(
                    Comment.target_type == TargetType.POST,
                    Comment.target_id == post_id,
                    Comment.is_deleted == False,
                )
                .options(selectinload(Comment.user))
                .order_by(Comment.create_time.desc())
                .limit(comments_limit)
            )
            res = await db.execute(comments_stmt)
            comment_rows = res.scalars().all()
            for comment in comment_rows:
                user = comment.user
                avatar = None
                if user and getattr(user, "avatar_attachment", None):
                    avatar = user.avatar_attachment.url
                comments.append({
                    "id": comment.comment_id,
                    "username": user.user_name if user else "匿名",
                    "avatar": avatar,
                    "content": comment.content,
                    "time": comment.create_time.isoformat() if comment.create_time else "",
                })

        # 发布者脱敏处理（使用 UserRead）
        publisher_public = UserRead.model_validate(post.user) if post.user else None

        post_detail = PostDetailRead(
            post_id=post.post_id,
            category_id=post.category_id,
            title=post.title,
            description=post.description,
            price=float(post.price) if post.price else None,
            direction=post.direction.value if post.direction else "SELL",
            urgency=post.urgency.value if post.urgency else "NORMAL",
            template_data=post.template_data,
            max_accepters=post.max_accepters,
            publisher=publisher_public,
            publisher_id=post.publisher_id,
            current_accepters=current_accepters,
            create_time=post.create_time.isoformat() if post.create_time else "",
            status=post.status.value if post.status else None,
            attachment_urls=attachment_urls,
            comments=comments,
        )

        if current_user:
            await SocialService.record_history(
                redis_client=redis,
                user_id=current_user.user_id,
                target_type="POST",
                target_id=post_id,
            )

        # 注意：可在此处添加 Redis 缓存层（key: post_detail:{post_id}:{comments_limit}），
        # 当 Post 变更（更新/新接单/新增评论）时应触发缓存失效。

        return ResponseModel(
            code=settings.SUCCESS_CODE,
            message=post_detail,
        )
    except Exception as e:
        logger.error(f"获取任务详情失败 post_id={post_id}: {e}")
        raise


@router.post("/{post_id}/accept", response_model=ResponseModel)
async def accept_post(
    post_id: int,
    current_user: UserRead = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis_client = Depends(get_redis),
):
    """
    接单（创建订单）
    """
    try:
        # 调用 OrderService 创建订单
        order = await OrderService.create_order(
            db,
            item_type="POST",
            item_id=post_id,
            initiator_id=current_user.user_id,
            redis_client=redis_client,
        )
        
        # 查询更新后的接单数
        current_accepters = await OrderService.get_current_accepters_count(
            db,
            item_type="POST",
            item_id=post_id,
        )
        post = await PostService.get_post_detail(db, post_id)
        max_accepters = post.max_accepters
        
        return ResponseModel(
            code=settings.SUCCESS_CODE,
            message={
                "order_id": order.order_id,
                "post_id": post_id,
                "current_accepters": current_accepters,
                "max_accepters": max_accepters,
                "accepted": True,
                "status": order.status.value,
            },
        )
    except Exception as e:
        logger.error(f"接单失败 post_id={post_id} user_id={current_user.user_id}: {e}")
        raise
