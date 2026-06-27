"""订单路由接口。"""

from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import get_current_user, get_current_verified_user
from app.core import BusinessHTTPException, get_now_naive, settings
from app.db import get_db, get_redis
from app.models import AttachmentTargetType, Goods, ItemType, Order, OrderStatus, Post
from app.schemas import (
    OrderItemList,
    OrderList,
    OrderRead,
    OrderReviewCreateRequest,
    OrderReviewListResponse,
    OrderReviewRead,
    ResponseModel,
    UserRead,
)
from app.services import AttachmentService, OrderReviewService, OrderService, WeChatNotificationService

router = APIRouter()


def _order_to_read(order) -> OrderRead:
    return OrderRead.model_validate(OrderService._serialize_order(order))


async def _assert_item_owner(db: AsyncSession, current_user: UserRead, item_type: str, item_id: int) -> None:
    if current_user.is_admin:
        return

    item_type_upper = str(item_type).upper()
    if item_type_upper in {"POST", "POSTS"}:
        stmt = select(Post.publisher_id).where(Post.post_id == item_id, Post.is_deleted == False)
    elif item_type_upper == "GOODS":
        stmt = select(Goods.publisher_id).where(Goods.goods_id == item_id, Goods.is_deleted == False)
    else:
        raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg="不支持的 item_type")

    res = await db.execute(stmt)
    owner_id = res.scalar_one_or_none()
    if owner_id is None:
        raise BusinessHTTPException(code=settings.DATA_GET_FAILED_CODE, msg="关联项目不存在")
    if int(owner_id) != int(current_user.user_id):
        raise BusinessHTTPException(code=settings.INSUFFICIENT_AUTHORITY_CODE, msg="仅项目拥有者可查看关联订单")


@router.get("/me", response_model=ResponseModel[OrderList])
async def list_my_orders(
    current_user: UserRead = Depends(get_current_user),
    role: str = Query("all", description="buyer/seller/all"),
    status: Optional[str] = Query(None, description="订单状态筛选，支持 ACCEPTED 别名"),
    start_time: Optional[str] = Query(None, description="起始时间"),
    end_time: Optional[str] = Query(None, description="结束时间"),
    page: int = Query(1, ge=1, description="页码"),
    size: int = Query(20, ge=1, le=100, alias="size", description="每页数量"),
    db: AsyncSession = Depends(get_db),
):
    orders, total = await OrderService.list_orders(
        db=db,
        user_id=current_user.user_id,
        role=role,
        status=status,
        start_time=start_time,
        end_time=end_time,
        page=page,
        page_size=size,
    )
    raw_orders = [OrderService._serialize_order(order) for order in orders]
    # 批量从 Post.template_data 提取公告反哺到订单列表
    post_order_ids = [
        (i, raw["item_id"]) for i, raw in enumerate(raw_orders)
        if raw["item_type"] == "POST"
    ]
    if post_order_ids:
        post_ids = list({pid for _, pid in post_order_ids})
        bulletin_stmt = select(Post.post_id, Post.template_data).where(
            Post.post_id.in_(post_ids), Post.is_deleted == False
        )
        bulletin_res = await db.execute(bulletin_stmt)
        bulletin_map = {}
        for pid, td in bulletin_res.all():
            bulletin_map[int(pid)] = (td or {}).get("bulletin", "") if isinstance(td, dict) else ""
        for idx, pid in post_order_ids:
            raw_orders[idx]["bulletin"] = bulletin_map.get(int(pid), "")

    return ResponseModel(
        code=settings.SUCCESS_CODE,
        message=OrderList(
            total=total,
            page=page,
            page_size=size,
            list=[OrderRead.model_validate(d) for d in raw_orders],
        ),
    )


@router.get("/by-item", response_model=ResponseModel[OrderItemList])
async def list_orders_by_item(
    item_id: int = Query(..., description="项目ID"),
    item_type: str = Query(..., description="项目类型：POSTS/GOODS"),
    current_user: UserRead = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_db),
):
    await _assert_item_owner(db, current_user, item_type, item_id)
    orders = await OrderService.list_orders_by_item(db, item_type, item_id)
    raw_orders = [OrderService._serialize_order(order) for order in orders]
    # 批量从 Post.template_data 提取公告反哺到订单列表
    post_order_ids = [
        (i, raw['item_id']) for i, raw in enumerate(raw_orders)
        if raw['item_type'] == 'POST'
    ]
    if post_order_ids:
        post_ids = list({pid for _, pid in post_order_ids})
        bulletin_stmt = select(Post.post_id, Post.template_data).where(
            Post.post_id.in_(post_ids), Post.is_deleted == False
        )
        bulletin_res = await db.execute(bulletin_stmt)
        bulletin_map = {}
        for pid, td in bulletin_res.all():
            bulletin_map[int(pid)] = (td or {}).get('bulletin', '') if isinstance(td, dict) else ''
        for idx, pid in post_order_ids:
            raw_orders[idx]['bulletin'] = bulletin_map.get(int(pid), '')

    return ResponseModel(
        code=settings.SUCCESS_CODE,
        message=OrderItemList(
            item_id=item_id,
            item_type=str(item_type).upper(),
            list=[OrderRead.model_validate(d) for d in raw_orders],
        ),
    )


@router.get("/{order_id}", response_model=ResponseModel[OrderRead])
async def get_order_detail(
    order_id: int,
    current_user: UserRead = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    order = await OrderService.get_order_detail(db, order_id, current_user.user_id)
    raw = OrderService._serialize_order(order)
    # 从 Post.template_data 提取公告反哺到订单详情
    if str(order.item_type.value if hasattr(order.item_type, "value") else order.item_type).upper() == "POST":
        bulletin_stmt = select(Post.template_data).where(Post.post_id == order.item_id, Post.is_deleted == False)
        bulletin_res = await db.execute(bulletin_stmt)
        td = bulletin_res.scalar_one_or_none()
        raw["bulletin"] = (td or {}).get("bulletin", "") if isinstance(td, dict) else ""
    return ResponseModel(code=settings.SUCCESS_CODE, message=OrderRead.model_validate(raw))


@router.post("/{order_id}/approve", response_model=ResponseModel[OrderRead])
async def approve_order(
    order_id: int,
    background_tasks: BackgroundTasks,
    current_user: UserRead = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_db),
    redis_client = Depends(get_redis),
):
    """同意录用申请，异步通知买家。"""
    order = await OrderService.approve_order(db, order_id, current_user.user_id)

    # 钩子 B：异步通知买家已被录用
    if order.item_type == ItemType.POST:
        post_res = await db.execute(select(Post).where(Post.post_id == order.item_id, Post.is_deleted == False))
        post = post_res.scalar_one_or_none()
        if post:
            background_tasks.add_task(
                WeChatNotificationService.notify_approved,
                db, redis_client, order, post.title or "",
            )

    return ResponseModel(code=settings.SUCCESS_CODE, message=_order_to_read(order))


@router.post("/{order_id}/reject", response_model=ResponseModel[OrderRead])
async def reject_order(
    order_id: int,
    background_tasks: BackgroundTasks,
    current_user: UserRead = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_db),
    redis_client = Depends(get_redis),
):
    """拒绝申请，异步通知买家。"""
    order = await OrderService.reject_order(db, order_id, current_user.user_id)

    # 钩子 C：异步通知买家已被拒绝
    if order.item_type == ItemType.POST:
        post_res = await db.execute(select(Post).where(Post.post_id == order.item_id, Post.is_deleted == False))
        post = post_res.scalar_one_or_none()
        if post:
            background_tasks.add_task(
                WeChatNotificationService.notify_rejected,
                db, redis_client, order, post.title or "",
            )

    return ResponseModel(code=settings.SUCCESS_CODE, message=_order_to_read(order))





@router.post("/posts/{post_id}/start", response_model=ResponseModel[dict])
async def start_post_fulfillment(
    post_id: int,
    background_tasks: BackgroundTasks,
    current_user: UserRead = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_db),
    redis_client = Depends(get_redis),
):
    """SELL direction: publisher batch-starts fulfillment, auto-rejects remaining PENDING applicants."""
    try:
        washed_count = await OrderService.start_collective_fulfillment(db, post_id, current_user.user_id)

        # 钩子 F：批量加载 openid 后扇出通知所有 ONGOING 买家
        post_res = await db.execute(select(Post).where(Post.post_id == post_id, Post.is_deleted == False))
        post = post_res.scalar_one_or_none()
        if post:
            ongoing_stmt = select(Order.buyer_id).where(
                Order.item_type == ItemType.POST,
                Order.item_id == post_id,
                Order.is_deleted == False,
                Order.status == OrderStatus.ONGOING,
            )
            ongoing_res = await db.execute(ongoing_stmt)
            buyer_ids = [row[0] for row in ongoing_res.all()]
            if buyer_ids:
                background_tasks.add_task(
                    WeChatNotificationService.notify_batch_start_collective,
                    db, redis_client, buyer_ids, post.title or "",
                )

        return ResponseModel(
            code=settings.SUCCESS_CODE,
            message={"washed_rejected_count": washed_count},
        )
    except Exception:
        await db.rollback()
        raise


@router.post("/reviews", response_model=ResponseModel[OrderReviewRead])
async def create_order_review(
    payload: OrderReviewCreateRequest,
    current_user: UserRead = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_db),
    redis_client = Depends(get_redis),
):
    review = await OrderReviewService.create_review(
        db,
        current_user_id=current_user.user_id,
        order_id=payload.order_id,
        reviewee_id=payload.reviewee_id,
        review_type=payload.review_type,
        parent_id=payload.parent_id,
        rating=payload.rating,
        content=payload.content,
        is_anonymous=payload.is_anonymous,
        attachment_ids=payload.attachment_ids,
        redis_client=redis_client,
    )

    review_urls = await AttachmentService.get_urls_by_target(
        db=db,
        target_type=AttachmentTargetType.ORDERREVIEW.value,
        target_ids=[review.review_id],
    )
    review_data = OrderReviewRead.model_validate(review).model_dump()
    review_data["attachment_urls"] = review_urls.get(review.review_id, [])
    return ResponseModel(code=settings.SUCCESS_CODE, message=OrderReviewRead.model_validate(review_data))


@router.get("/{order_id}/reviews", response_model=ResponseModel[OrderReviewListResponse])
async def list_order_reviews(
    order_id: int,
    current_user: UserRead = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    reviews = await OrderReviewService.list_reviews_for_order(
        db,
        order_id=order_id,
        current_user_id=current_user.user_id,
        is_admin=bool(current_user.is_admin),
    )
    review_ids = [review.review_id for review in reviews]
    attachment_urls_map = await AttachmentService.get_urls_by_target(
        db=db,
        target_type=AttachmentTargetType.ORDERREVIEW.value,
        target_ids=review_ids,
    )
    # Linear dict pipeline: ORM attrs -> raw dict -> single validate
    raw_items = []
    for review in reviews:
        raw_items.append({
            "review_id": review.review_id,
            "order_id": review.order_id,
            "reviewer_id": review.reviewer_id,
            "reviewee_id": review.reviewee_id,
            "review_type": review.review_type.value if getattr(review.review_type, 'value', None) else str(review.review_type),
            "parent_id": review.parent_id,
            "rating": review.rating,
            "content": review.content,
            "is_anonymous": review.is_anonymous,
            "is_visible": review.is_visible,
            "create_time": review.create_time,
            "attachment_urls": attachment_urls_map.get(review.review_id, []),
        })
    return ResponseModel(
        code=settings.SUCCESS_CODE,
        message=OrderReviewListResponse(
            items=[OrderReviewRead.model_validate(d) for d in raw_items]
        ),
    )


@router.post("/{order_id}/submit-delivery", response_model=ResponseModel[OrderRead])
async def submit_delivery(
    order_id: int,
    background_tasks: BackgroundTasks,
    current_user: UserRead = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_db),
    redis_client = Depends(get_redis),
):
    """提交已交付，异步通知买家验收。"""
    order = await OrderService.submit_delivery(db, order_id, current_user.user_id, redis_client=redis_client)

    # 钩子 D：异步通知买家服务已送达
    if order.item_type == ItemType.POST:
        post_res = await db.execute(select(Post).where(Post.post_id == order.item_id, Post.is_deleted == False))
        post = post_res.scalar_one_or_none()
        if post:
            background_tasks.add_task(
                WeChatNotificationService.notify_delivery,
                db, redis_client, order, post.title or "",
            )
    elif order.item_type == ItemType.GOODS:
        goods_res = await db.execute(select(Goods.title).where(Goods.goods_id == order.item_id, Goods.is_deleted == False))
        goods_title = goods_res.scalar_one_or_none()
        if goods_title:
            background_tasks.add_task(
                WeChatNotificationService.notify_goods_delivered,
                db, redis_client, order, goods_title,
            )

    return ResponseModel(code=settings.SUCCESS_CODE, message=_order_to_read(order))


@router.post("/{order_id}/accept-delivery", response_model=ResponseModel[OrderRead])
async def accept_delivery(
    order_id: int,
    current_user: UserRead = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_db),
):
    """买家确认验收完成订单。GOODS 订单完成后自动将商品标记为已售出。"""
    order = await OrderService.accept_delivery(db, order_id, current_user.user_id)


    return ResponseModel(code=settings.SUCCESS_CODE, message=_order_to_read(order))


@router.post("/{order_id}/complete", response_model=ResponseModel[OrderRead])
async def complete_order(
    order_id: int,
    current_user: UserRead = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_db),
):
    """兼容/弃用接口：管理员手动完结走强制完成，普通买家确认仍走验收流程。"""

    if current_user.is_admin:
        order = await OrderService.force_complete_order_by_admin(db, order_id, current_user.user_id)
    else:
        order = await OrderService.accept_delivery(db, order_id, current_user.user_id)


    return ResponseModel(code=settings.SUCCESS_CODE, message=_order_to_read(order))


@router.post("/{order_id}/cancel", response_model=ResponseModel[OrderRead])
async def cancel_order(
    order_id: int,
    background_tasks: BackgroundTasks,
    current_user: UserRead = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis_client = Depends(get_redis),
):
    """取消订单：settings.LIGHTNING_CANCEL_LIMIT_SECONDS//60分钟内为闪电退单，超时无限制放行。"""

    limit_minutes = settings.LIGHTNING_CANCEL_LIMIT_SECONDS // 60
    limit_count = settings.LIGHTNING_CANCEL_DAILY_LIMIT

    order = await OrderService.cancel_order(db, order_id, current_user.user_id, redis_client=redis_client)
    result = OrderService._serialize_order(order)
    if order.item_type == ItemType.POST:
        result["curr_accepters"] = await OrderService.get_current_accepters_count(
            db,
            item_type="POST",
            item_id=order.item_id,
        )

    # 计算闪电退单剩余次数与提示信息
    result["rest_cancel_times"] = None
    result["cancel_message"] = None
    if redis_client is not None and order.create_time is not None:
        now = get_now_naive()
        duration_seconds = (now - order.create_time).total_seconds()
        today_str = now.strftime("%Y%m%d")
        lightning_key = f"order:cancel:limit:{current_user.user_id}:{today_str}"
        current_raw = await redis_client.get(lightning_key)
        used = int(current_raw) if current_raw else 0
        if duration_seconds <= settings.LIGHTNING_CANCEL_LIMIT_SECONDS:
            remaining = max(0, limit_count - used)
            result["rest_cancel_times"] = remaining
            result["cancel_message"] = f"闪电退单！今日剩余 {remaining} 次提前主动取消次数"
        else:
            result["rest_cancel_times"] = limit_count
            result["cancel_message"] = "无闪电退单限制，可直接取消"

    # 钩子 E：异步通知被动取消的对端
    if order.item_type == ItemType.POST:
        target_user_id = order.seller_id if current_user.user_id == order.buyer_id else order.buyer_id
        post_res = await db.execute(select(Post).where(Post.post_id == order.item_id, Post.is_deleted == False))
        post = post_res.scalar_one_or_none()
        if post:
            background_tasks.add_task(
                WeChatNotificationService.notify_cancelled,
                db, redis_client, order, post.title or "", target_user_id,
            )
    elif order.item_type == ItemType.GOODS:
        target_user_id = order.seller_id if current_user.user_id == order.buyer_id else order.buyer_id
        goods_res = await db.execute(select(Goods.title).where(Goods.goods_id == order.item_id, Goods.is_deleted == False))
        goods_title = goods_res.scalar_one_or_none()
        if goods_title:
            background_tasks.add_task(
                WeChatNotificationService.notify_goods_cancelled,
                db, redis_client, order, goods_title, target_user_id,
            )

    return ResponseModel(code=settings.SUCCESS_CODE, message=OrderRead.model_validate(result))
