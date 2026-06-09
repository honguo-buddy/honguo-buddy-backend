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

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func, select

from app.api import get_current_user, get_current_user_optional
from app.core import AuthHTTPException, BusinessHTTPException, ResourceHTTPException, get_now_naive, settings
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
    PostBulletinUpdate,
    PostCreate,
    PostDetailRead,
    PostList,
    PostRead,
    PostUpdate,
    ResponseModel,
    UserRead,
)
from app.services import MetricsService, PostService, OrderService, SocialService, WeChatNotificationService
from app.models import Order, Post, PostStatus, User

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
        expire_time=post.expire_time.isoformat() if getattr(post, "expire_time", None) else None,
        attachment_urls=attachment_urls,
    )



def _build_post_dict(post, current_accepters: int, applicant_count: int = 0) -> dict:
    """Build lightweight raw dict from ORM Post object, no intermediate Pydantic overhead."""
    attachment_urls = [att.url for att in (post.attachments or []) if not att.is_deleted]
    publisher = post.user
    return {
        "post_id": post.post_id,
        "category_id": post.category_id,
        "title": post.title,
        "description": post.description,
        "price": float(post.price) if post.price else None,
        "direction": post.direction.value if post.direction else "SELL",
        "urgency": post.urgency.value if post.urgency else "NORMAL",
        "status": post.status.value if post.status else "OPEN",
        "template_data": post.template_data,
        "max_accepters": post.max_accepters,
        "publisher": UserRead.model_validate(publisher) if publisher else None,
        "publisher_id": post.publisher_id,
        "current_accepters": current_accepters,
        "applicant_count": applicant_count,
        "create_time": post.create_time.isoformat() if post.create_time else "",
        "expire_time": post.expire_time.isoformat() if getattr(post, "expire_time", None) else None,
        "attachment_urls": attachment_urls,
    }


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
    # 检查用户当前开启的帖子数是否已达上限
    cnt_result = await db.execute(
        select(func.count()).select_from(Post).where(
            Post.publisher_id == current_user.user_id,
            Post.status.in_([PostStatus.OPEN, PostStatus.IN_PROGRESS]),
            Post.is_deleted == False,
        )
    )
    open_count = cnt_result.scalar() or 0
    if open_count >= settings.MAX_OPEN_POSTS_PER_USER:
        raise BusinessHTTPException(code=settings.DATA_GET_FAILED_CODE, msg='当前发布的活跃帖子已达上限，请先结帖后再试')

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
            _post_direction=post.direction,
        )
        
        return ResponseModel(
            code=settings.SUCCESS_CODE,
            message=_build_post_read(post, current_accepters),
        )
    except BusinessHTTPException:
        raise
    except Exception as e:
        logger.error(f"发布帖子失败 user_id={current_user.user_id}: {e}", exc_info=True)
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
    template_segment_1: Optional[str] = Query(None, description="模板数据文本片段1，模糊匹配 template_data 全文"),
    template_segment_2: Optional[str] = Query(None, description="模板数据文本片段2，模糊匹配 template_data 全文"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    db: AsyncSession = Depends(get_db),
    redis_client = Depends(get_redis),
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
            template_segment_1=template_segment_1,
            template_segment_2=template_segment_2,
            page=page,
            page_size=page_size,
        )
        post_id_list = [post.post_id for post in posts]
        current_accepters_map = await OrderService.get_current_accepters_count_map(
            db,
            item_type="POST",
            item_ids=post_id_list,
            _direction_map={post.post_id: post.direction for post in posts},
        )
        applicant_count_map = await OrderService.get_pending_applicants_count_map(db, post_id_list)
        
        # Linear dict pipeline: ORM -> raw dict -> hydrate -> single validate
        raw_dicts = []
        post_ids = []
        for post in posts:
            # 懒检查：若已到期但定时任务未刷新，动态覆写状态为 SUSPENDED
            if getattr(post, "expire_time", None) is not None and getattr(post, "expire_time") <= get_now_naive() and post.status == PostStatus.OPEN:
                post.status = PostStatus.SUSPENDED
            pid = post.post_id
            post_ids.append(pid)
            raw_dicts.append(_build_post_dict(
                post,
                current_accepters_map.get(pid, 0),
                applicant_count_map.get(pid, 0),
            ))

        if raw_dicts:
            await MetricsService.hydrate_posts_with_metrics(db, redis_client, raw_dicts, post_ids)
            post_list = [PostRead.model_validate(d) for d in raw_dicts]
        else:
            post_list = []
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
        logger.error(f"获取任务列表失败: {e}", exc_info=True)
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
    redis_client = Depends(get_redis),
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
    my_post_ids = [post.post_id for post in posts]
    current_accepters_map = await OrderService.get_current_accepters_count_map(
        db,
        item_type="POST",
        item_ids=my_post_ids,
        _direction_map={post.post_id: post.direction for post in posts},
    )
    applicant_count_map = await OrderService.get_pending_applicants_count_map(db, my_post_ids)
    raw_dicts = []
    post_ids = []
    for post in posts:
        pid = post.post_id
        post_ids.append(pid)
        raw_dicts.append(_build_post_dict(
            post,
            current_accepters_map.get(pid, 0),
            applicant_count_map.get(pid, 0),
        ))

    if raw_dicts:
        await MetricsService.hydrate_posts_with_metrics(db, redis_client, raw_dicts, post_ids)
        post_list = [PostRead.model_validate(d) for d in raw_dicts]
    else:
        post_list = []
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
    redis_client = Depends(get_redis),
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
    public_post_ids = [post.post_id for post in posts]
    current_accepters_map = await OrderService.get_current_accepters_count_map(
        db,
        item_type="POST",
        item_ids=public_post_ids,
        _direction_map={post.post_id: post.direction for post in posts},
    )
    applicant_count_map = await OrderService.get_pending_applicants_count_map(db, public_post_ids)
    raw_dicts = []
    post_ids = []
    for post in posts:
        pid = post.post_id
        post_ids.append(pid)
        raw_dicts.append(_build_post_dict(
            post,
            current_accepters_map.get(pid, 0),
            applicant_count_map.get(pid, 0),
        ))

    if raw_dicts:
        await MetricsService.hydrate_posts_with_metrics(db, redis_client, raw_dicts, post_ids)
        post_list = [PostRead.model_validate(d) for d in raw_dicts]
    else:
        post_list = []
    return ResponseModel(
        code=settings.SUCCESS_CODE,
        message=PostList(total=total, page=page, page_size=size, list=post_list),
    )




@router.post("/{post_id}/bulletin", response_model=ResponseModel[dict])
async def update_post_bulletin(
    post_id: int,
    payload: PostBulletinUpdate,
    current_user: UserRead = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """更新帖子公告栏。bulletin 为 None 不修改，为空字符串清空公告。"""
    if payload.bulletin is None:
        post_stmt = select(Post.template_data).where(
            Post.post_id == post_id, Post.is_deleted == False
        )
        post_res = await db.execute(post_stmt)
        td = post_res.scalar_one_or_none()
        current = (td or {}).get("bulletin", "") if isinstance(td, dict) else ""
        return ResponseModel(
            code=settings.SUCCESS_CODE,
            message={"post_id": post_id, "bulletin": current},
        )

    post_stmt = (
        select(Post)
        .where(Post.post_id == post_id, Post.is_deleted == False)
        .with_for_update()
    )
    post_res = await db.execute(post_stmt)
    post = post_res.scalars().first()
    if not post:
        raise ResourceHTTPException(code=settings.DATA_GET_FAILED_CODE, msg="帖子不存在")
    if post.publisher_id != current_user.user_id:
        raise BusinessHTTPException(code=settings.INSUFFICIENT_AUTHORITY_CODE, msg="仅发帖人可更新公告")
    post.template_data = dict(post.template_data or {})
    post.template_data["bulletin"] = payload.bulletin
    await db.flush()
    await db.commit()
    return ResponseModel(
        code=settings.SUCCESS_CODE,
        message={"post_id": post_id, "bulletin": payload.bulletin},
    )


@router.get("/{post_id}/bulletin", response_model=ResponseModel[dict])
async def get_post_bulletin(
    post_id: int,
    db: AsyncSession = Depends(get_db),
):
    """获取帖子公告栏内容。"""
    post_stmt = select(Post.template_data).where(Post.post_id == post_id, Post.is_deleted == False)
    post_res = await db.execute(post_stmt)
    td = post_res.scalar_one_or_none()
    bulletin = (td or {}).get("bulletin", "") if isinstance(td, dict) else ""
    return ResponseModel(code=settings.SUCCESS_CODE, message={"post_id": post_id, "bulletin": bulletin})

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
    current_accepters = await OrderService.get_current_accepters_count(db, item_type="POST", item_id=post.post_id, _post_direction=post.direction)
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
    redis_client = Depends(get_redis),
):
    """
    获取任务详情。评论列表请使用独立接口 GET /comments/{target_type}/{target_id} 分页拉取。
    """
    try:
        post = await PostService.get_post_detail(db, post_id)
        # 懒检查：若已到期但定时任务未刷新，动态覆写状态为 SUSPENDED
        if getattr(post, "expire_time", None) is not None and getattr(post, "expire_time") <= get_now_naive() and post.status == PostStatus.OPEN:
            post.status = PostStatus.SUSPENDED
        # 帖子存在性验证通过后再自增浏览计数，防止对不存在/已删除帖子虚增指标
        await MetricsService.incr_post_view(redis_client, post_id)
        # 通过 OrderService 统一获取接单数
        current_accepters = await OrderService.get_current_accepters_count(
            db,
            item_type="POST",
            item_id=post_id,
            _post_direction=post.direction,
        )
        applicant_count = (await OrderService.get_pending_applicants_count_map(db, [post_id])).get(post_id, 0)
        attachment_urls = [att.url for att in (post.attachments or []) if not att.is_deleted]

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
            applicant_count=applicant_count,
            create_time=post.create_time.isoformat() if post.create_time else "",
            expire_time=post.expire_time.isoformat() if getattr(post, "expire_time", None) else None,
            status=post.status.value if post.status else None,
            attachment_urls=attachment_urls,
        )

        if current_user:
            await SocialService.record_history(
                redis_client=redis_client,
                user_id=current_user.user_id,
                target_type="POST",
                target_id=post_id,
            )

        # 灌入计数器到详情卡片
        post_detail_dict = post_detail.model_dump()
        await MetricsService.hydrate_posts_with_metrics(db, redis_client, [post_detail_dict], [post_id])
        hydrated_detail = PostDetailRead.model_validate(post_detail_dict)

        return ResponseModel(
            code=settings.SUCCESS_CODE,
            message=hydrated_detail,
        )
    except Exception as e:
        logger.error(f"获取任务详情失败 post_id={post_id}: {e}", exc_info=True)
        raise


@router.post("/{post_id}/accept", response_model=ResponseModel)
async def accept_post(
    post_id: int,
    background_tasks: BackgroundTasks,
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
        
        # 先加载帖子获取 direction/max_accepters，再查询更新后的接单数
        post = await PostService.get_post_detail(db, post_id)
        max_accepters = post.max_accepters
        current_accepters = await OrderService.get_current_accepters_count(
            db,
            item_type="POST",
            item_id=post_id,
            _post_direction=post.direction,
        )

        # 查询当前排队申请人数
        pending_counts = await OrderService.get_pending_applicants_count_map(db, [post_id])
        applicant_count = pending_counts.get(post_id, 0)

        # Polymorphic response: BUY vs SELL direction
        if post.direction and str(post.direction.value).upper() == "BUY":
            accept_msg = "接单申请递交成功，等待发帖人审批"
        else:
            accept_msg = "已成功加入沟通池，火速去和帖主私信聊聊吧"

        # 钩子 A：异步通知发帖人收到新申请
        post_title = post.title or ""
        applicant_name = current_user.user_name or "匿名用户"
        background_tasks.add_task(
            WeChatNotificationService.notify_new_application,
            db, redis_client, order, post_title, applicant_name,
        )

        return ResponseModel(
            code=settings.SUCCESS_CODE,
            message={
                "order_id": order.order_id,
                "post_id": post_id,
                "current_accepters": current_accepters,
                "max_accepters": max_accepters,
                "applicant_count": applicant_count,
                "accepted": False,
                "status": order.status.value,
                "message": accept_msg,
            },
        )
    except Exception as e:
        logger.error(f"接单失败 post_id={post_id} user_id={current_user.user_id}: {e}", exc_info=True)
        raise


@router.post("/{post_id}/suspend", response_model=ResponseModel)
async def suspend_post(
    post_id: int,
    current_user: UserRead = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """暂停招募：将帖子状态从 OPEN 变更为 SUSPENDED。"""
    try:
        post = await PostService.get_post_detail(db, post_id)
        if post.publisher_id != current_user.user_id:
            raise BusinessHTTPException(code=settings.INSUFFICIENT_AUTHORITY_CODE, msg="仅帖子发布者可操作")
        if post.status != PostStatus.OPEN:
            raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg="仅 OPEN 状态可暂停招募")
        post.status = PostStatus.SUSPENDED
        await db.commit()
        return ResponseModel(code=settings.SUCCESS_CODE, message={"post_id": post_id, "status": PostStatus.SUSPENDED.value})
    except BusinessHTTPException:
        raise
    except Exception as e:
        logger.error(f"暂停招募失败 post_id={post_id}: {e}", exc_info=True)
        raise


@router.post("/{post_id}/resume", response_model=ResponseModel)
async def resume_post(
    post_id: int,
    current_user: UserRead = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """恢复招募：将帖子状态从 SUSPENDED 还原为 OPEN。"""
    try:
        post = await PostService.get_post_detail(db, post_id)
        if post.publisher_id != current_user.user_id:
            raise BusinessHTTPException(code=settings.INSUFFICIENT_AUTHORITY_CODE, msg="仅帖子发布者可操作")
        if post.status != PostStatus.SUSPENDED:
            raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg="仅 SUSPENDED 状态可恢复招募")
        post.status = PostStatus.OPEN
        await db.commit()
        return ResponseModel(code=settings.SUCCESS_CODE, message={"post_id": post_id, "status": PostStatus.OPEN.value})
    except BusinessHTTPException:
        raise
    except Exception as e:
        logger.error(f"恢复招募失败 post_id={post_id}: {e}", exc_info=True)
        raise

@router.get("/{post_id}/contact", response_model=ResponseModel)
async def get_post_contact(
    post_id: int,
    current_user: UserRead = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取帖子发布者的联系方式（需鉴权：楼主本人或已申请者）。"""
    post_obj = await db.get(Post, post_id)
    if not post_obj or post_obj.is_deleted:
        raise ResourceHTTPException(code=settings.DATA_GET_FAILED_CODE, msg="帖子不存在")
    
    # 楼主直接放行
    if post_obj.publisher_id == current_user.user_id:
        return ResponseModel(code=settings.SUCCESS_CODE, message=post_obj.contact or {})
    
    # 检查是否存在订单记录（需先申请该帖子）
    order_result = await db.execute(
        select(func.count()).select_from(Order).where(
            Order.item_type == "POST",
            Order.item_id == post_id,
            Order.initiator_id == current_user.user_id,
            Order.is_deleted == False,
        )
    )
    if (order_result.scalar() or 0) > 0:
        return ResponseModel(code=settings.SUCCESS_CODE, message=post_obj.contact or {})
    
    raise BusinessHTTPException(code=settings.DATA_GET_FAILED_CODE, msg="您需要先申请该委托，才能查看车主的联系方式")
