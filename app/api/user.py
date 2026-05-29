"""用户 API 路由层。"""

from typing import Literal, Optional, Union

from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import get_current_user, get_current_user_optional
from app.core import settings, AuthHTTPException
from app.db import get_db, redis
from app.schemas import (
    AuthErrorResponse,
    FavoriteListResponse,
    HistoryDeletePayload,
    FavoriteRequest,
    FavoriteResponse,
    HistoryListResponse,
    ResponseModel,
    UserFollowListResponse,
    UserFollowToggleRequest,
    UserFollowToggleResponse,
    user as UserSchema,
    UserProfileResponse,
    UserPublicResponse,
    UserSelfUpdateRequest,
)
from app.services import ReputationService, SocialService, UserService
from app.models import User as UserModel

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


@router.post("/follow", response_model=ResponseModel[UserFollowToggleResponse])
async def toggle_follow(
    request: UserFollowToggleRequest,
    current_user: UserSchema = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """关注 / 取消关注用户。"""
    result = await SocialService.toggle_follow(db, current_user.user_id, request.following_id)
    return ResponseModel(code=settings.SUCCESS_CODE, message=UserFollowToggleResponse.model_validate(result))


@router.post("/favorite", response_model=ResponseModel[FavoriteResponse])
async def toggle_favorite(
    request: FavoriteRequest,
    current_user: UserSchema = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """收藏 / 取消收藏帖子或商品。"""
    result = await SocialService.toggle_favorite(db, current_user.user_id, request.target_type, request.target_id)
    return ResponseModel(code=settings.SUCCESS_CODE, message=FavoriteResponse.model_validate(result))


@router.get("/me/followings", response_model=ResponseModel[UserFollowListResponse])
async def list_my_followings(
    current_user: UserSchema = Depends(get_current_user),
    page: int = 1,
    page_size: int = 20,
    db: AsyncSession = Depends(get_db),
):
    """获取我的关注列表。"""
    offset = (page - 1) * page_size
    result = await SocialService.list_followings(db, current_user.user_id, offset, page_size)
    result["page"] = page
    result["page_size"] = page_size
    return ResponseModel(code=settings.SUCCESS_CODE, message=UserFollowListResponse.model_validate(result))


@router.get("/me/followers", response_model=ResponseModel[UserFollowListResponse])
async def list_my_followers(
    current_user: UserSchema = Depends(get_current_user),
    page: int = 1,
    page_size: int = 20,
    db: AsyncSession = Depends(get_db),
):
    """获取我的粉丝列表。"""
    offset = (page - 1) * page_size
    result = await SocialService.list_followers(db, current_user.user_id, offset, page_size)
    result["page"] = page
    result["page_size"] = page_size
    return ResponseModel(code=settings.SUCCESS_CODE, message=UserFollowListResponse.model_validate(result))


@router.get("/me/favorites", response_model=ResponseModel[FavoriteListResponse])
async def list_my_favorites(
    current_user: UserSchema = Depends(get_current_user),
    page: int = 1,
    page_size: int = 20,
    db: AsyncSession = Depends(get_db),
):
    """获取我的收藏列表。"""
    offset = (page - 1) * page_size
    result = await SocialService.list_favorites(db, current_user.user_id, offset, page_size)
    result["page"] = page
    result["page_size"] = page_size
    return ResponseModel(code=settings.SUCCESS_CODE, message=FavoriteListResponse.model_validate(result))


@router.get("/me/histories", response_model=ResponseModel[HistoryListResponse])
async def list_my_histories(
    current_user: UserSchema = Depends(get_current_user),
    page: int = 1,
    page_size: int = 20,
    db: AsyncSession = Depends(get_db),
):
    """获取我的历史墙（最近浏览记录）。"""
    offset = (page - 1) * page_size
    result = await SocialService.list_history(redis, db, current_user.user_id, offset, page_size)
    result["page"] = page
    result["page_size"] = page_size
    return ResponseModel(code=settings.SUCCESS_CODE, message=HistoryListResponse.model_validate(result))


@router.post("/me/histories/delete", response_model=ResponseModel[dict])
async def delete_my_histories(
    payload: HistoryDeletePayload,
    current_user: UserSchema = Depends(get_current_user),
    bg_tasks: BackgroundTasks = BackgroundTasks(),
    db: AsyncSession = Depends(get_db),
):
    """多维聚合清理历史足迹（支持单条、时间段、全量三种模式）。

    禁止使用 DELETE 带 Body，采用 POST 承载清理载荷以防网关裁剪。
    """
    result = await SocialService.delete_user_history(
        db=db,
        user_id=current_user.user_id,
        payload=payload,
        bg_tasks=bg_tasks,
        redis_client=redis,
    )
    return ResponseModel(code=settings.SUCCESS_CODE, message=result)


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


@router.get("/{user_id}/profile", response_model=ResponseModel[dict])
async def get_user_profile(
    user_id: int,
    current_user: Optional[UserSchema] = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    """获取用户主页声誉画像（含双角色星级与印象标签）。

    优先读取 Redis 缓存，击穿时回数据库重算。
    """
    user = await db.get(UserModel, user_id)
    if not user or user.is_deleted:
        raise AuthHTTPException(
            code=settings.USER_GET_FAILED_CODE,
            msg="用户不存在",
        )
    reputation = await ReputationService.get_user_reputation(redis, db, user_id)
    return ResponseModel(code=settings.SUCCESS_CODE, message=reputation)


@router.get("/{user_id}/reviews", response_model=ResponseModel[dict])
async def get_user_reviews(
    user_id: int,
    role: Literal["CARRIER", "CLIENT"] = "CARRIER",
    offset: int = 0,
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
):
    """延迟加载用户评价详情（支持 CARRIER/CLIENT 双 Tab 页签）。

    执行严格双向脱敏：评价发表人头像置 None，姓名打码。
    仅展示已通过双盲释放机制（is_visible=True）的评价。
    """
    user = await db.get(UserModel, user_id)
    if not user or user.is_deleted:
        raise AuthHTTPException(
            code=settings.USER_GET_FAILED_CODE,
            msg="用户不存在",
        )
    result = await ReputationService.get_user_reviews(db, user_id, role, offset, limit)
    return ResponseModel(code=settings.SUCCESS_CODE, message=result)


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


