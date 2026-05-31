"""Goods API routes."""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import get_current_user, get_current_user_optional
from app.core import settings, ResourceHTTPException, AuthHTTPException
from app.db import get_db, get_redis
from app.schemas import (
    ResponseModel,
    GoodsCreate,
    GoodsRead,
    GoodsUpdate,
    GoodsDetailRead,
    GoodsListResponse,
    UserRead,
)
from app.services import GoodsService, MetricsService, SocialService

logger = logging.getLogger(__name__)

router = APIRouter()


def _build_goods_dict(goods) -> dict:
    """Build lightweight raw dict from ORM Goods object, no intermediate Pydantic overhead."""
    attachment_urls = [att.url for att in (goods.attachments or []) if not getattr(att, 'is_deleted', False)]
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
        "attachment_urls": attachment_urls,
    }

def _build_goods_urls(goods) -> list[str]:
    try:
        return [att.url for att in (goods.attachments or []) if not getattr(att, "is_deleted", False)]
    except Exception:
        return []


@router.post("/", response_model=ResponseModel[GoodsRead])
async def create_goods(
    obj_in: GoodsCreate,
    current_user: UserRead = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Publish a new goods item."""
    goods = await GoodsService.create_goods(db, current_user.user_id, obj_in)
    res = GoodsRead.model_validate(goods)
    res.attachment_urls = _build_goods_urls(goods)
    return ResponseModel(code=settings.SUCCESS_CODE, message=res)


@router.get("/", response_model=ResponseModel[GoodsListResponse])
async def list_goods(
    keyword: Optional[str] = Query(None, description="keyword search"),
    category_id: Optional[int] = Query(None, description="category ID"),
    status: Optional[str] = Query(None, description="status filter"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    redis_client = Depends(get_redis),
):
    """Marketplace lobby paginated query with counter hydration."""
    try:
        goods_items, total = await GoodsService.list_all_goods(
            db, keyword=keyword, category_id=category_id, status=status, page=page, page_size=page_size
        )

        raw_dicts = []
        goods_ids = []
        for g in goods_items:
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
    current_user: UserRead = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis_client = Depends(get_redis),
):
    """My published goods list."""
    try:
        goods_items, total = await GoodsService.list_goods_by_user(db, current_user.user_id, page, page_size)
        raw_dicts = []
        goods_ids = []
        for g in goods_items:
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

    # 已登录用户异步记录商品浏览历史足迹到 Redis ZSET
    if current_user:
        try:
            await SocialService.record_history(
                redis_client=redis_client,
                user_id=current_user.user_id,
                target_type="GOODS",
                target_id=goods_id,
            )
        except Exception:
            pass

    goods_detail = GoodsDetailRead.model_validate(goods)
    goods_detail.attachment_urls = _build_goods_urls(goods)
    goods_dict = goods_detail.model_dump()

    await MetricsService.hydrate_goods_with_metrics(db, redis_client, [goods_dict], [goods_id])

    return ResponseModel(code=settings.SUCCESS_CODE, message=GoodsDetailRead.model_validate(goods_dict))


@router.patch("/{goods_id}", response_model=ResponseModel[GoodsRead])
async def update_goods(
    goods_id: int,
    obj_in: GoodsUpdate,
    current_user: UserRead = Depends(get_current_user),
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
    return ResponseModel(code=settings.SUCCESS_CODE, message=res)


@router.delete("/{goods_id}", response_model=ResponseModel[dict])
async def delete_goods(
    goods_id: int,
    current_user: UserRead = Depends(get_current_user),
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