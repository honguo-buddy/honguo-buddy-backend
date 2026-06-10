"""管理员动态配置路由。"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import get_current_user
from app.core import AuthHTTPException, DynamicConfigManager, settings
from app.db import get_db, get_redis
from app.schemas import ResponseModel, SysConfigRead, SysConfigUpdateRequest, UserRead
from app.services import ConfigService

router = APIRouter()


def _ensure_admin(current_user: UserRead) -> None:
    if not current_user.is_admin:
        raise AuthHTTPException(
            code=settings.INSUFFICIENT_AUTHORITY_CODE,
            msg="权限不足，仅管理员可操作",
        )


@router.get("/configs", response_model=ResponseModel[list[SysConfigRead]])
async def list_sys_configs(
    current_user: UserRead = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """管理员获取全部业务动态配置。"""
    _ensure_admin(current_user)
    config_rows = await ConfigService.list_configs(db)
    return ResponseModel(
        code=settings.SUCCESS_CODE,
        message=[SysConfigRead.model_validate(row) for row in config_rows],
    )


@router.get("/configs/{config_key}", response_model=ResponseModel[SysConfigRead])
async def get_sys_config_detail(
    config_key: str,
    current_user: UserRead = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """管理员获取单个业务动态配置详情。"""
    _ensure_admin(current_user)
    config_row = await ConfigService.get_config_by_key(db, config_key=config_key)
    return ResponseModel(
        code=settings.SUCCESS_CODE,
        message=SysConfigRead.model_validate(config_row),
    )


@router.patch("/configs/{config_key}", response_model=ResponseModel[SysConfigRead])
async def update_sys_config(
    config_key: str,
    payload: SysConfigUpdateRequest,
    current_user: UserRead = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis_client=Depends(get_redis),
):
    """管理员热更新业务配置，并广播刷新事件。"""
    _ensure_admin(current_user)
    config_row = await ConfigService.update_config_value(
        db,
        config_key=config_key,
        config_value=payload.config_value,
    )
    await DynamicConfigManager().load_all(db)
    await redis_client.publish("sys_config_refresh_channel", "refresh")
    return ResponseModel(
        code=settings.SUCCESS_CODE,
        message=SysConfigRead.model_validate(config_row),
    )
