"""订单路由接口。"""

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import get_current_user
from app.core import BusinessHTTPException, settings
from app.db import get_db
from app.models import Goods, Post
from app.schemas import OrderItemList, OrderList, OrderRead, ResponseModel, UserRead
from app.services import OrderService

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


@router.post("/{order_id}/complete", response_model=ResponseModel[OrderRead])
async def complete_order(
    order_id: int,
    current_user: UserRead = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    order = await OrderService.complete_order(db, order_id, current_user.user_id)
    return ResponseModel(code=settings.SUCCESS_CODE, message=_order_to_read(order))


@router.post("/{order_id}/cancel", response_model=ResponseModel[OrderRead])
async def cancel_order(
    order_id: int,
    current_user: UserRead = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    order = await OrderService.cancel_order(db, order_id, current_user.user_id)
    return ResponseModel(code=settings.SUCCESS_CODE, message=_order_to_read(order))
