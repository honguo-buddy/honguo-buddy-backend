"""Goods API routes."""
import logging
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func, select

from app.api import get_current_user_optional, get_current_verified_user
from app.core import BusinessHTTPException, settings, ResourceHTTPException, AuthHTTPException, get_now_naive
from app.db import get_db, get_redis
from app.models import Goods, GoodsStatus, ItemType, Order
from app.schemas import (
    ResponseModel,
    GoodsApplicationApplicantRead,
    GoodsApplicationItem,
    GoodsApplicationListResponse,
    GoodsAttachmentBriefRead,
    GoodsCreate,
    GoodsRead,
    GoodsUpdate,
    GoodsDetailRead,
    GoodsListResponse,
    UserRead,
)
from app.services import BlacklistService, GoodsService, MetricsService, OrderService, SocialService, WeChatNotificationService

logger = logging.getLogger(__name__)

router = APIRouter()


def _build_goods_attachments(goods) -> list[dict]:
    try:
        return [
            GoodsAttachmentBriefRead(id=att.attachment_id, url=att.url)
            for att in (goods.attachments or [])
            if not getattr(att, "is_deleted", False)
        ]
    except Exception as e:
        logger.warning("Failed to build goods attachments: %s", e, exc_info=True)
        return []


def _build_goods_dict(goods) -> dict:
    """Build lightweight raw dict from ORM Goods object, no intermediate Pydantic overhead."""
    attachment_urls = [att.url for att in (goods.attachments or []) if not getattr(att, 'is_deleted', False)]
    attachments = _build_goods_attachments(goods)
    publisher = goods.user
    return {
        "goods_id": goods.goods_id,
        "category_id": goods.category_id,
        "name": goods.name,
        "description": goods.description,
        "price": float(goods.price) if goods.price else None,
        "condition": goods.condition.value if goods.condition else None,
        "status": goods.status.value if goods.status else None,
        "template_data": goods.template_data,
        "publisher": UserRead.model_validate(publisher) if publisher else None,
        "publisher_id": goods.publisher_id,
        "create_time": goods.create_time.isoformat() if goods.create_time else "",
        "expire_time": goods.expire_time.isoformat() if getattr(goods, "expire_time", None) else None,
        "attachment_urls": attachment_urls,
        "attachments": attachments,
    }

def _build_goods_urls(goods) -> list[str]:
    try:
        return [att.url for att in (goods.attachments or []) if not getattr(att, "is_deleted", False)]
    except Exception as e:
        logger.warning("Failed to build goods attachment urls: %s", e, exc_info=True)
        return []


def _build_goods_application_applicant_read(applicant, completed_order_count: int) -> GoodsApplicationApplicantRead:
    base_data = UserRead.model_validate(applicant).model_dump()
    base_data["avatar"] = applicant.avatar_attachment.url if getattr(applicant, "avatar_attachment", None) else None
    base_data["completed_order_count"] = int(completed_order_count)
    return GoodsApplicationApplicantRead.model_validate(base_data)


@router.post("/", response_model=ResponseModel[GoodsRead])
async def create_goods(
    obj_in: GoodsCreate,
    current_user: UserRead = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_db),
):
    """Publish a new goods item."""
    open_count = await GoodsService.count_open_goods_by_user(db, current_user.user_id)
    if open_count >= int(getattr(settings, "MAX_OPEN_GOODS_PER_USER")):
        raise BusinessHTTPException(code=settings.DATA_GET_FAILED_CODE, msg="当前发布的活跃商品已达上限，请先下架后再试")

    goods = await GoodsService.create_goods(db, current_user.user_id, obj_in)
    res = GoodsRead.model_validate(goods)
    res.attachment_urls = _build_goods_urls(goods)
    res.attachments = _build_goods_attachments(goods)
    return ResponseModel(code=settings.SUCCESS_CODE, message=res)


@router.get("/", response_model=ResponseModel[GoodsListResponse])
async def list_goods(
    keyword: Optional[str] = Query(None, description="keyword search"),
    category_id: Optional[int] = Query(None, description="category ID"),
    status: Optional[str] = Query(None, description="status filter"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: Optional[UserRead] = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
    redis_client = Depends(get_redis),
):
    """Marketplace lobby paginated query with counter hydration."""
    try:
        # 黑名单过滤：获取拉黑了当前用户的用户 ID 列表
        blocker_ids = []
        blocked_target_ids = []
        if current_user:
            blocker_ids = await BlacklistService.get_blocker_ids(db, current_user.user_id)
            blocked_target_ids = await BlacklistService.get_blocked_target_ids(db, current_user.user_id)
        exclude_ids = list(set(blocker_ids + blocked_target_ids))
        goods_items, total = await GoodsService.list_all_goods(
            db, keyword=keyword, category_id=category_id, status=status, page=page, page_size=page_size,
            exclude_publisher_ids=exclude_ids if exclude_ids else None,
        )

        raw_dicts = []
        goods_ids = []
        for g in goods_items:
            # 懒检查：若已到期但定时任务未刷新，动态覆写状态为 OFF_SHELF
            if getattr(g, "expire_time", None) is not None and getattr(g, "expire_time") <= get_now_naive() and g.status == GoodsStatus.ON_SALE:
                g.status = GoodsStatus.OFF_SHELF
            gid = g.goods_id
            goods_ids.append(gid)
            raw_dicts.append(_build_goods_dict(g))

        if raw_dicts:
            await MetricsService.hydrate_goods_with_metrics(db, redis_client, raw_dicts, goods_ids)
            goods_list = [GoodsRead.model_validate(d) for d in raw_dicts]
        else:
            goods_list = []

        return ResponseModel(
            code=settings.SUCCESS_CODE,
            message=GoodsListResponse(total=total, page=page, page_size=page_size, list=goods_list),
        )
    except Exception:
        logger.exception("Failed to list goods")
        raise ResourceHTTPException(code=settings.DATA_GET_FAILED_CODE, msg="Failed to list goods")


@router.get("/me", response_model=ResponseModel[GoodsListResponse])
async def list_my_goods(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1),
    current_user: UserRead = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_db),
    redis_client = Depends(get_redis),
):
    """My published goods list."""
    try:
        goods_items, total = await GoodsService.list_goods_by_user(db, current_user.user_id, page, page_size)
        raw_dicts = []
        goods_ids = []
        for g in goods_items:
            # 懒检查：若已到期但定时任务未刷新，动态覆写状态为 OFF_SHELF
            if getattr(g, "expire_time", None) is not None and getattr(g, "expire_time") <= get_now_naive() and g.status == GoodsStatus.ON_SALE:
                g.status = GoodsStatus.OFF_SHELF
            gid = g.goods_id
            goods_ids.append(gid)
            raw_dicts.append(_build_goods_dict(g))

        if raw_dicts:
            await MetricsService.hydrate_goods_with_metrics(db, redis_client, raw_dicts, goods_ids)
            goods_list = [GoodsRead.model_validate(d) for d in raw_dicts]
        else:
            goods_list = []

        return ResponseModel(
            code=settings.SUCCESS_CODE,
            message=GoodsListResponse(total=total, page=page, page_size=page_size, list=goods_list),
        )
    except ResourceHTTPException:
        raise
    except Exception:
        logger.exception("Failed to list my goods")
        raise ResourceHTTPException(code=settings.DATA_GET_FAILED_CODE, msg="Failed to list goods")


@router.get("/{goods_id}", response_model=ResponseModel[GoodsDetailRead])




async def get_goods_detail(
    goods_id: int,
    db: AsyncSession = Depends(get_db),
    redis_client = Depends(get_redis),
    current_user = Depends(get_current_user_optional),
):
    """Goods detail page with view counter increment, hydration, and history footprint."""
    await MetricsService.incr_goods_view(redis_client, goods_id)

    goods = await GoodsService.get_goods_by_id(db, goods_id)
    if not goods:
        raise ResourceHTTPException(code=settings.USER_GET_FAILED_CODE, msg="Goods not found or deleted")
    # 懒检查：若已到期但定时任务未刷新，动态覆写状态为 OFF_SHELF
    if getattr(goods, "expire_time", None) is not None and getattr(goods, "expire_time") <= get_now_naive() and goods.status == GoodsStatus.ON_SALE:
        goods.status = GoodsStatus.OFF_SHELF

    # 已登录用户异步记录商品浏览历史足迹到 Redis ZSET
    if current_user:
        try:
            await SocialService.record_history(
                redis_client=redis_client,
                user_id=current_user.user_id,
                target_type="GOODS",
                target_id=goods_id,
            )
        except Exception as e:
            logger.warning("Failed to record goods history footprint goods_id=%d: %s", goods_id, e, exc_info=True)

    goods_detail = GoodsDetailRead.model_validate(goods)
    goods_detail.attachment_urls = _build_goods_urls(goods)
    goods_detail.attachments = _build_goods_attachments(goods)
    goods_dict = goods_detail.model_dump()

    await MetricsService.hydrate_goods_with_metrics(db, redis_client, [goods_dict], [goods_id])

    return ResponseModel(code=settings.SUCCESS_CODE, message=GoodsDetailRead.model_validate(goods_dict))


@router.post("/{goods_id}/buy", response_model=ResponseModel[dict])
async def buy_goods(
    goods_id: int,
    background_tasks: BackgroundTasks,
    current_user: UserRead = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_db),
    redis_client = Depends(get_redis),
):
    """兼容接口：将当前用户已有的商品申请推进为 ONGOING，并异步通知卖家。"""
    order = await OrderService.promote_goods_order_to_ongoing(
        db=db,
        goods_id=goods_id,
        initiator_id=current_user.user_id,
    )

    # 获取商品名称用于通知
    g_stmt = select(Goods.name, Goods.publisher_id).where(Goods.goods_id == goods_id)
    g_res = await db.execute(g_stmt)
    g_row = g_res.first()
    goods_name = g_row.name if g_row else ""
    goods_publisher = g_row.publisher_id if g_row else 0
    if goods_publisher and (goods_publisher != current_user.user_id):
        background_tasks.add_task(
            WeChatNotificationService.notify_goods_purchased,
            db, redis_client, order, goods_name or "", current_user.user_name or "匿名用户",
        )

    return ResponseModel(code=settings.SUCCESS_CODE, message={
        "order_id": order.order_id,
        "goods_id": goods_id,
        "status": order.status.value,
    })


@router.post("/{goods_id}/accept", response_model=ResponseModel[dict])
async def accept_goods(
    goods_id: int,
    background_tasks: BackgroundTasks,
    current_user: UserRead = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_db),
    redis_client = Depends(get_redis),
):
    """商品申请：创建 PENDING 订单，仅用于建立联系，不直接锁单。"""
    order = await OrderService.create_order(
        db=db,
        item_type="GOODS",
        item_id=goods_id,
        initiator_id=current_user.user_id,
        redis_client=redis_client,
    )

    g_stmt = select(Goods.name, Goods.publisher_id).where(Goods.goods_id == goods_id)
    g_res = await db.execute(g_stmt)
    g_row = g_res.first()
    goods_name = g_row.name if g_row else ""
    goods_publisher = g_row.publisher_id if g_row else 0
    if goods_publisher and goods_publisher != current_user.user_id:
        background_tasks.add_task(
            WeChatNotificationService.notify_goods_purchased,
            db, redis_client, order, goods_name or "", current_user.user_name or "匿名用户",
        )

    return ResponseModel(
        code=settings.SUCCESS_CODE,
        message={
            "order_id": order.order_id,
            "goods_id": goods_id,
            "status": order.status.value,
            "message": "申请已提交，快去和卖家私信沟通吧",
        },
    )


@router.get("/{goods_id}/applications", response_model=ResponseModel[GoodsApplicationListResponse])
async def list_goods_applications(
    goods_id: int,
    current_user: UserRead = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_db),
):
    """查看指定商品的申请列表，仅商品发布者可访问。"""

    stmt = select(Goods.publisher_id).where(Goods.goods_id == goods_id, Goods.is_deleted == False)
    res = await db.execute(stmt)
    publisher_id = res.scalar_one_or_none()
    if publisher_id is None:
        raise ResourceHTTPException(code=settings.DATA_GET_FAILED_CODE, msg="商品不存在")
    if int(current_user.user_id) != int(publisher_id):
        raise AuthHTTPException(code=settings.INSUFFICIENT_AUTHORITY_CODE, msg="仅商品拥有者可查看申请列表")

    applications_data = await OrderService.list_goods_applications(db, goods_id)
    await OrderService.mark_goods_applications_seen_by_seller(db, goods_id)
    applications = []
    for row in applications_data:
        order = row["order"]
        applicant = row["applicant"]
        applications.append(
            GoodsApplicationItem(
                application_id=order.order_id,
                goods_id=order.item_id,
                applicant=_build_goods_application_applicant_read(applicant, row["completed_order_count"]),
                note=row["note"],
                status=order.status.value if getattr(order.status, "value", None) else str(order.status),
                created_at=order.create_time.isoformat() if order.create_time else "",
            )
        )

    return ResponseModel(
        code=settings.SUCCESS_CODE,
        message=GoodsApplicationListResponse(applications=applications),
    )


@router.post("/{goods_id}/delist", response_model=ResponseModel[dict])
async def delist_goods(
    goods_id: int,
    current_user: UserRead = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_db),
):
    """卖家下架商品：ON_SALE → OFF_SHELF。"""
    goods = await GoodsService.delist_goods(db, goods_id, current_user.user_id)
    return ResponseModel(code=settings.SUCCESS_CODE, message={
        "goods_id": goods_id,
        "status": goods.status.value,
    })


@router.post("/{goods_id}/relist", response_model=ResponseModel[dict])
async def relist_goods(
    goods_id: int,
    current_user: UserRead = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_db),
):
    """卖家重新上架商品：OFF_SHELF → ON_SALE。"""
    goods = await GoodsService.relist_goods(db, goods_id, current_user.user_id)
    return ResponseModel(code=settings.SUCCESS_CODE, message={
        "goods_id": goods_id,
        "status": goods.status.value,
    })

@router.patch("/{goods_id}", response_model=ResponseModel[GoodsRead])
async def update_goods(
    goods_id: int,
    obj_in: GoodsUpdate,
    current_user: UserRead = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_db),
):
    """Update / delist / relist my goods."""
    goods = await GoodsService.get_goods_by_id(db, goods_id)
    if not goods:
        raise ResourceHTTPException(code=settings.USER_GET_FAILED_CODE, msg="Goods not found")
    if goods.publisher_id != current_user.user_id:
        raise AuthHTTPException(code=settings.INSUFFICIENT_AUTHORITY_CODE, msg="Not authorized to modify")

    updated = await GoodsService.update_goods(db, goods, obj_in)
    res = GoodsRead.model_validate(updated)
    res.attachment_urls = _build_goods_urls(updated)
    res.attachments = _build_goods_attachments(updated)
    return ResponseModel(code=settings.SUCCESS_CODE, message=res)


@router.delete("/{goods_id}", response_model=ResponseModel[dict])
async def delete_goods(
    goods_id: int,
    current_user: UserRead = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_db),
):
    """Soft-delete a goods item."""
    goods = await GoodsService.get_goods_by_id(db, goods_id)
    if not goods:
        raise ResourceHTTPException(code=settings.USER_GET_FAILED_CODE, msg="Goods not found")
    if goods.publisher_id != current_user.user_id:
        raise AuthHTTPException(code=settings.INSUFFICIENT_AUTHORITY_CODE, msg="Not authorized to delete")

    await GoodsService.soft_delete_goods(db, goods)
    return ResponseModel(code=settings.SUCCESS_CODE, message={"goods_id": goods_id, "deleted": True})
@router.get("/{goods_id}/contact", response_model=ResponseModel)
async def get_goods_contact(
    goods_id: int,
    current_user: UserRead = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_db),
):
    """获取商品发布者的联系方式（需鉴权：卖家本人或任意已申请者）。"""
    goods_obj = await db.get(Goods, goods_id)
    if not goods_obj or goods_obj.is_deleted:
        raise ResourceHTTPException(code=settings.DATA_GET_FAILED_CODE, msg="商品不存在")

    # 卖家直接放行
    if goods_obj.publisher_id == current_user.user_id:
        return ResponseModel(code=settings.SUCCESS_CODE, message=goods_obj.contact or {})

    # 检查是否存在订单记录
    order_result = await db.execute(
        select(func.count()).select_from(Order).where(
            Order.item_type == ItemType.GOODS,
            Order.item_id == goods_id,
            Order.initiator_id == current_user.user_id,
            Order.is_deleted == False,
        )
    )
    if (order_result.scalar() or 0) > 0:
        return ResponseModel(code=settings.SUCCESS_CODE, message=goods_obj.contact or {})

    raise BusinessHTTPException(code=settings.DATA_GET_FAILED_CODE, msg="您需要先申请该商品，才能查看贴主的联系方式")

