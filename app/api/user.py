"""用户 API 路由层。"""

from typing import Optional, Union

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import get_current_user, get_current_user_optional
from app.core import settings, AuthHTTPException
from app.db import get_db
from app.schemas import (
    AuthErrorResponse,
    ResponseModel,
    user as UserSchema,
    UserProfileResponse,
    UserPublicResponse,
    UserSelfUpdateRequest,
)
from app.services import UserService

router = APIRouter()


@router.get("/me", response_model=ResponseModel[UserProfileResponse])
async def get_me(
    current_user: UserSchema = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取本人详细资料（含敏感字段如手机号、邮箱）。"""
    user_data = await UserService.get_user_with_avatar_url(current_user.user_id, db)
    return ResponseModel(
        code=settings.SUCCESS_CODE,
        message=UserProfileResponse.model_validate(user_data),
    )


@router.patch("/me", response_model=ResponseModel[UserProfileResponse])
async def update_me(
    update_req: UserSelfUpdateRequest,
    current_user: UserSchema = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """修改本人资料（局部更新）。
    
    仅允许修改以下字段：
    - user_name: 用户名
    - avatar_id: 用户头像附件ID
    - sex: 性别
    
    以下字段无法修改：
    - user_id、user_uuid（固定标识）
    - email、phonenumber（需专门认证接口）
    - wechat_openid、user_type（注册时确定）
    """
    updated_user = await UserService.update_user_profile(
        user_id=current_user.user_id,
        user_name=update_req.user_name,
        avatar_id=update_req.avatar_id,
        sex=update_req.sex,
        db=db,
    )
    # 返回时从 service 获取带 avatar URL 的 payload，确保 avatar 字段有值
    user_data = await UserService.get_user_with_avatar_url(current_user.user_id, db)
    return ResponseModel(
        code=settings.SUCCESS_CODE,
        message=UserProfileResponse.model_validate(user_data),
    )


@router.delete("/me", response_model=ResponseModel[dict])
async def delete_me(
    current_user: UserSchema = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """注销本人账号（逻辑删除）。
    
    账号逻辑删除后无法登录，数据保留在数据库。
    """
    await UserService.delete_user(current_user.user_id, db)
    return ResponseModel(
        code=settings.SUCCESS_CODE,
        message={"message": "账号已注销"},
    )


@router.get("/info", response_model=ResponseModel[Union[dict, AuthErrorResponse]])
async def get_profile(current_user: UserSchema = Depends(get_current_user)):
    """获取当前登录用户基础信息（向后兼容/弃用）。"""
    return ResponseModel(
        code=settings.SUCCESS_CODE,
        message={
            "userUuid": current_user.user_uuid,
            "userName": current_user.user_name,
            "isAdmin": current_user.is_admin,
            "isVerified": current_user.is_verified,
            "userType": current_user.user_type,
        },
    )


@router.get("/{user_id}", response_model=ResponseModel[Union[UserPublicResponse, UserProfileResponse]])
async def get_user_public(
    user_id: int,
    current_user: Optional[UserSchema] = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    """获取用户资料。

    未登录用户或普通登录用户只能查看未软删除用户的脱敏信息。
    管理员在已登录状态下可查看软删除用户，并返回与 /me 一致的敏感字段。
    """

    if current_user and current_user.is_admin:
        user_data = await UserService.get_user_with_avatar_url_admin(user_id, db)
        return ResponseModel(
            code=settings.SUCCESS_CODE,
            message=UserProfileResponse.model_validate(user_data),
        )

    user_data = await UserService.get_user_public_with_avatar_url(user_id, db)
    return ResponseModel(
        code=settings.SUCCESS_CODE,
        message=UserPublicResponse.model_validate(user_data),
    )


@router.put("/{user_id}", response_model=ResponseModel[UserProfileResponse])
async def update_user_admin(
    user_id: int,
    update_req: UserSelfUpdateRequest,
    current_user: UserSchema = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """[管理员] 修改用户信息。
    
    需要管理员权限。强制替换用户信息。
    """
    # 检查是否有管理员权限
    if not current_user.is_admin:
        raise AuthHTTPException(
            code=settings.INSUFFICIENT_AUTHORITY_CODE,
            msg="权限不足，仅管理员可操作",
        )

    updated_user = await UserService.update_user_by_admin(
        user_id=user_id,
        user_name=update_req.user_name,
        avatar_id=update_req.avatar_id,
        sex=update_req.sex,
        db=db,
    )
    # 管理员更新后也返回带 avatar URL 的 payload
    user_data = await UserService.get_user_with_avatar_url(user_id, db)
    return ResponseModel(
        code=settings.SUCCESS_CODE,
        message=UserProfileResponse.model_validate(user_data),
    )


@router.delete("/{user_id}", response_model=ResponseModel[dict])
async def delete_user_admin(
    user_id: int,
    current_user: UserSchema = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """[管理员] 禁用/删除用户。
    
    需要管理员权限。仅限管理员或系统任务调用。逻辑删除用户。
    """
    # 检查是否有管理员权限
    if not current_user.is_admin:
        raise AuthHTTPException(
            code=settings.INSUFFICIENT_AUTHORITY_CODE,
            msg="权限不足，仅管理员可操作",
        )

    # 防止管理员删除自己
    if user_id == current_user.user_id:
        raise AuthHTTPException(
            code=settings.INSUFFICIENT_AUTHORITY_CODE,
            msg="无法删除自己的账号",
        )

    await UserService.admin_delete_user(user_id, db)
    return ResponseModel(
        code=settings.SUCCESS_CODE,
        message={"message": f"用户 {user_id} 已被禁用/删除"},
    )


