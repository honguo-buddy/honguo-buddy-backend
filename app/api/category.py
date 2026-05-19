"""Category（模板分类）路由。公开读取，管理员可进行模板 CRUD。"""

from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import get_current_user
from app.core import AuthHTTPException, settings
from app.db import get_db
from app.schemas import ResponseModel, CategoryCreate, CategoryRead, CategoryUpdate, UserRead
from app.services import CategoryService

router = APIRouter()


def _ensure_admin(current_user: UserRead) -> None:
    if not current_user.is_admin:
        raise AuthHTTPException(
            code=settings.INSUFFICIENT_AUTHORITY_CODE,
            msg="权限不足，仅管理员可操作",
        )


@router.post("/", response_model=ResponseModel[CategoryRead])
async def create_category_template(
    payload: CategoryCreate,
    current_user: UserRead = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """创建模板分类（管理员）。"""
    _ensure_admin(current_user)
    category = await CategoryService.create_category(db, payload)
    return ResponseModel(
        code=settings.SUCCESS_CODE,
        message=CategoryRead.model_validate(category),
    )


@router.get("/", response_model=ResponseModel[List[CategoryRead]])
async def list_category_templates(
    item_type: Optional[str] = Query(None, alias="type", description="业务类型: POST/GOODS"),
    db: AsyncSession = Depends(get_db),
):
    """获取模板分类列表。对外开放（用于前端选择模板），支持按 `type`(POST/GOODS) 过滤。"""
    categories = await CategoryService.list_categories(db, item_type)
    return ResponseModel(
        code=settings.SUCCESS_CODE,
        message=[CategoryRead.model_validate(c) for c in categories],
    )


@router.get("/{category_id}", response_model=ResponseModel[CategoryRead])
async def get_category_template(
    category_id: int,
    db: AsyncSession = Depends(get_db),
):
    """获取模板分类详情。对外开放，供前端展示单个模板定义。"""
    category = await CategoryService.get_category_by_id(db, category_id)
    return ResponseModel(
        code=settings.SUCCESS_CODE,
        message=CategoryRead.model_validate(category),
    )


@router.put("/{category_id}", response_model=ResponseModel[CategoryRead])
async def update_category_template(
    category_id: int,
    payload: CategoryUpdate,
    current_user: UserRead = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """更新模板分类（管理员）。"""
    _ensure_admin(current_user)
    category = await CategoryService.update_category(db, category_id, payload)
    return ResponseModel(
        code=settings.SUCCESS_CODE,
        message=CategoryRead.model_validate(category),
    )


@router.delete("/{category_id}", response_model=ResponseModel[dict])
async def delete_category_template(
    category_id: int,
    current_user: UserRead = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """删除模板分类（管理员，软删除）。"""
    _ensure_admin(current_user)
    await CategoryService.delete_category(db, category_id)
    return ResponseModel(
        code=settings.SUCCESS_CODE,
        message={"category_id": category_id, "deleted": True},
    )
