"""订单路由接口。"""

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import get_current_user
from app.core import BusinessHTTPException, settings
from app.db import get_db, get_redis
from app.models import Goods, Post
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
from app.services import OrderReviewService, OrderService

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
    return ResponseModel(
        code=settings.SUCCESS_CODE,
        message=OrderList(
            total=total,
            page=page,
            page_size=size,
            list=[_order_to_read(order) for order in orders],
        ),
    )


@router.get("/by-item", response_model=ResponseModel[OrderItemList])
async def list_orders_by_item(
    item_id: int = Query(..., description="项目ID"),
    item_type: str = Query(..., description="项目类型：POSTS/GOODS"),
    current_user: UserRead = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _assert_item_owner(db, current_user, item_type, item_id)
    orders = await OrderService.list_orders_by_item(db, item_type, item_id)
    return ResponseModel(
        code=settings.SUCCESS_CODE,
        message=OrderItemList(
            item_id=item_id,
            item_type=str(item_type).upper(),
            list=[_order_to_read(order) for order in orders],
        ),
    )


@router.get("/{order_id}", response_model=ResponseModel[OrderRead])
async def get_order_detail(
    order_id: int,
    current_user: UserRead = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    order = await OrderService.get_order_detail(db, order_id, current_user.user_id)
    return ResponseModel(code=settings.SUCCESS_CODE, message=_order_to_read(order))


@router.post("/{order_id}/approve", response_model=ResponseModel[OrderRead])
async def approve_order(
    order_id: int,
    current_user: UserRead = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    order = await OrderService.approve_order(db, order_id, current_user.user_id)
    return ResponseModel(code=settings.SUCCESS_CODE, message=_order_to_read(order))


@router.post("/{order_id}/reject", response_model=ResponseModel[OrderRead])
async def reject_order(
    order_id: int,
    current_user: UserRead = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    order = await OrderService.reject_order(db, order_id, current_user.user_id)
    return ResponseModel(code=settings.SUCCESS_CODE, message=_order_to_read(order))


@router.post("/reviews", response_model=ResponseModel[OrderReviewRead])
async def create_order_review(
    payload: OrderReviewCreateRequest,
    current_user: UserRead = Depends(get_current_user),
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
        redis_client=redis_client,
    )
    return ResponseModel(code=settings.SUCCESS_CODE, message=OrderReviewRead.model_validate(review))


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
    return ResponseModel(
        code=settings.SUCCESS_CODE,
        message=OrderReviewListResponse(items=[OrderReviewRead.model_validate(item) for item in reviews]),
    )


@router.post("/{order_id}/submit-delivery", response_model=ResponseModel[OrderRead])
async def submit_delivery(
    order_id: int,
    current_user: UserRead = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis_client = Depends(get_redis),
):
    order = await OrderService.submit_delivery(db, order_id, current_user.user_id, redis_client=redis_client)
    return ResponseModel(code=settings.SUCCESS_CODE, message=_order_to_read(order))


@router.post("/{order_id}/accept-delivery", response_model=ResponseModel[OrderRead])
async def accept_delivery(
    order_id: int,
    current_user: UserRead = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    order = await OrderService.accept_delivery(db, order_id, current_user.user_id)
    return ResponseModel(code=settings.SUCCESS_CODE, message=_order_to_read(order))


@router.post("/{order_id}/complete", response_model=ResponseModel[OrderRead])
async def complete_order(
    order_id: int,
    current_user: UserRead = Depends(get_current_user),
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
    current_user: UserRead = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    order = await OrderService.cancel_order(db, order_id, current_user.user_id)
    return ResponseModel(code=settings.SUCCESS_CODE, message=_order_to_read(order))
