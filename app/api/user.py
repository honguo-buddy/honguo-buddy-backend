"""用户 API 路由层。"""

import json as _json
import logging
from types import SimpleNamespace
from typing import Literal, Optional, Union

logger = logging.getLogger(__name__)

from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import get_current_user, get_current_user_optional
from app.core import settings, AuthHTTPException, BusinessHTTPException, ResourceHTTPException
from app.db import get_db, get_redis, redis
from app.schemas import (
    AuthErrorResponse,
    BlacklistCreate,
    BlacklistItem,
    BlacklistListResponse,
    ContactCreate,
    ContactRead,
    ContactListResponse,
    FavoriteListResponse,
    HistoryDeletePayload,
    FavoriteRequest,
    FavoriteResponse,
    HistoryListResponse,
    PhoneSendCodeRequest,
    PhoneBindRequest,
    ResponseModel,
    UserFollowListResponse,
    UserOpenQuotaResponse,
    UserFollowToggleRequest,
    UserFollowToggleResponse,
    UserUnreadCountsResponse,
    user as UserSchema,
    UserProfileResponse,
    UserPublicResponse,
    UserSelfUpdateRequest,
)
from app.services import AttachmentService, BlacklistService, ChatService, ContactService, GoodsService, MetricsService, OrderService, PostService, ReputationService, SMSService, SocialService, UserService
from app.models import Direction, User as UserModel

# ---------------------------------------------------------------------------
# 共享工具：用户 profile 缓存刷新
# ---------------------------------------------------------------------------
async def _refresh_user_profile_cache(uid: int, db: AsyncSession) -> None:
    """删除旧缓存并用最新 DB 数据重建 user:profile:me:{uid}。"""
    try:
        await redis.delete(f"user:profile:cache:{uid}")
        await redis.delete(f"user:profile:me:{uid}")
        await redis.delete(f"user:profile:public:{uid}")
    except Exception as e:
        logger.warning("缓存删除失败 uid=%d: %s", uid, e, exc_info=True)

    try:
        user_data = await UserService.get_user_with_avatar_url(uid, db)
        profile_dict = {
            "user_id": user_data["user_id"],
            "user_uuid": user_data["user_uuid"] if isinstance(user_data.get("user_uuid"), str) else "",
            "user_name": user_data.get("user_name"),
            "is_admin": user_data.get("is_admin", False),
            "is_verified": user_data.get("is_verified", False),
            "email": user_data.get("email"),
            "phonenumber": user_data.get("phonenumber"),
            "last_login_ip": user_data.get("last_login_ip"),
            "last_login_time": user_data["last_login_time"].isoformat() if hasattr(user_data.get("last_login_time"), "isoformat") else user_data.get("last_login_time"),
            "user_type": user_data.get("user_type"),
            "avatar": user_data.get("avatar"),
            "sex": user_data.get("sex"),
            "bio": user_data.get("bio"),
            "credit_score": user_data.get("credit_score", 0),
            "is_active": user_data.get("is_active", True),
            "wechat_unionid": user_data.get("wechat_unionid"),
        }
        await redis.setex(
            f"user:profile:me:{uid}",
            settings.USER_PROFILE_CACHE_TTL,
            _json.dumps(profile_dict, ensure_ascii=False, default=str),
        )
    except Exception as e:
        logger.warning("缓存重建失败 uid=%d: %s", uid, e, exc_info=True)


def _build_public_profile_cache_payload(user_data: dict, user_id: int) -> dict:
    """构造公开资料缓存载荷，并写入版本号用于淘汰历史脏缓存。"""
    return {
        "_cache_version": settings.USER_PUBLIC_PROFILE_CACHE_VERSION,
        "user_id": user_data.get("user_id", user_id),
        "user_uuid": str(user_data.get("user_uuid", "")),
        "user_name": user_data.get("user_name"),
        "avatar": user_data.get("avatar"),
        "sex": user_data.get("sex"),
        "bio": user_data.get("bio"),
        "credit_score": int(user_data.get("credit_score", 0)),
        "is_verified": bool(user_data.get("is_verified", False)),
        "user_type": user_data.get("user_type"),
    }


def _extract_valid_public_profile_cache_payload(data: object) -> dict | None:
    """校验公开资料缓存版本，旧版本缓存返回 None 以触发删除和重建。"""
    if not isinstance(data, dict):
        return None
    if data.get("_cache_version") != settings.USER_PUBLIC_PROFILE_CACHE_VERSION:
        return None
    payload = dict(data)
    payload.pop("_cache_version", None)
    return payload


router = APIRouter()


async def _build_open_quota_response(
    *,
    limit: int,
    used: int,
) -> UserOpenQuotaResponse:
    remaining = limit - used
    if remaining < 0:
        remaining = 0
    return UserOpenQuotaResponse(limit=limit, used=used, remaining=remaining)


@router.get("/me", response_model=ResponseModel[UserProfileResponse])
async def get_me(
    current_user: UserSchema = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取本人详细资料（含敏感字段如手机号、邮箱）。Read-Through: Redis cache first, DB fallback."""
    cache_key = f"user:profile:me:{current_user.user_id}"
    try:
        cached = await redis.get(cache_key)
        if cached:
            data = _json.loads(cached)
            return ResponseModel(
                code=settings.SUCCESS_CODE,
                message=UserProfileResponse.model_validate(SimpleNamespace(**data)),
            )
    except Exception as e:
        logger.warning("Swallowed exception in user: %s", e, exc_info=True)

    user_data = await UserService.get_user_with_avatar_url(current_user.user_id, db)

    try:
        profile_dict = {
            "user_id": user_data["user_id"],
            "user_uuid": user_data["user_uuid"] if isinstance(user_data.get("user_uuid"), str) else "",
            "user_name": user_data.get("user_name"),
            "is_admin": user_data.get("is_admin", False),
            "is_verified": user_data.get("is_verified", False),
            "email": user_data.get("email"),
            "phonenumber": user_data.get("phonenumber"),
            "last_login_ip": user_data.get("last_login_ip"),
            "last_login_time": user_data["last_login_time"].isoformat() if hasattr(user_data.get("last_login_time"), "isoformat") else user_data.get("last_login_time"),
            "user_type": user_data.get("user_type"),
            "avatar": user_data.get("avatar"),
            "sex": user_data.get("sex"),
            "bio": user_data.get("bio"),
            "credit_score": user_data.get("credit_score", 0),
            "is_active": user_data.get("is_active", True),
            "wechat_unionid": user_data.get("wechat_unionid"),
        }
        await redis.setex(cache_key, settings.USER_PROFILE_CACHE_TTL, _json.dumps(profile_dict, ensure_ascii=False, default=str))
    except Exception as e:
        logger.warning("Swallowed exception in user: %s", e, exc_info=True)

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
    - bio: 个人简介
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
        bio=update_req.bio,
        db=db,
    )
    # 清除旧缓存并用最新 DB 数据强制重建
    await _refresh_user_profile_cache(current_user.user_id, db)

    # Fetch fresh profile with avatar URL for response
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
    uid = current_user.user_id
    await UserService.delete_user(uid, db)
    # 清除 Redis 缓存，注销后 profile 不再可用
    try:
        await redis.delete(f"user:profile:cache:{uid}")
        await redis.delete(f"user:profile:me:{uid}")
        await redis.delete(f"user:profile:public:{uid}")
    except Exception as e:
        logger.warning("缓存删除失败 uid=%d: %s", uid, e, exc_info=True)
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
    redis_client = Depends(get_redis),
):
    """获取我的收藏列表。"""
    offset = (page - 1) * page_size
    result = await SocialService.list_favorites(db, current_user.user_id, offset, page_size)
    result["page"] = page
    result["page_size"] = page_size

    # 批量灌水：为收藏列表中的 POST + GOODS 卡片双端注入计数器
    fav_items = result.get("list", [])
    post_items = [it for it in fav_items if it.get("target_type") == "POST"]
    goods_items = [it for it in fav_items if it.get("target_type") == "GOODS"]
    if post_items:
        await MetricsService.hydrate_posts_with_metrics(db, redis_client, post_items, [it["target_id"] for it in post_items], id_key="target_id")
    if goods_items:
        await MetricsService.hydrate_goods_with_metrics(db, redis_client, goods_items, [it["target_id"] for it in goods_items])

    return ResponseModel(code=settings.SUCCESS_CODE, message=FavoriteListResponse.model_validate(result))


@router.get("/me/histories", response_model=ResponseModel[HistoryListResponse])
async def list_my_histories(
    current_user: UserSchema = Depends(get_current_user),
    page: int = 1,
    page_size: int = 20,
    db: AsyncSession = Depends(get_db),
    redis_client = Depends(get_redis),
):
    """获取我的历史墙（最近浏览记录）。"""
    offset = (page - 1) * page_size
    result = await SocialService.list_history(redis_client, db, current_user.user_id, offset, page_size)
    result["page"] = page
    result["page_size"] = page_size

    # 批量灌水：为历史足迹中的 POST + GOODS 卡片双端注入计数器
    hist_items = result.get("list", [])
    post_items = [it for it in hist_items if it.get("target_type") == "POST"]
    goods_items = [it for it in hist_items if it.get("target_type") == "GOODS"]
    if post_items:
        await MetricsService.hydrate_posts_with_metrics(db, redis_client, post_items, [it["target_id"] for it in post_items], id_key="target_id")
    if goods_items:
        await MetricsService.hydrate_goods_with_metrics(db, redis_client, goods_items, [it["target_id"] for it in goods_items])

    return ResponseModel(code=settings.SUCCESS_CODE, message=HistoryListResponse.model_validate(result))


@router.get("/me/unread-counts", response_model=ResponseModel[UserUnreadCountsResponse])
async def get_my_unread_counts(
    current_user: UserSchema = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取当前用户的全局未读数聚合（私信未读 + 系统新申请未读）。"""
    chat_unread_count = await ChatService.get_total_unread_count(db, current_user.user_id)
    system_unread_count = await OrderService.get_system_pending_unread_count(db, current_user.user_id)
    total_unread_count = chat_unread_count + system_unread_count

    return ResponseModel(
        code=settings.SUCCESS_CODE,
        message=UserUnreadCountsResponse(
            chat_unread_count=chat_unread_count,
            system_unread_count=system_unread_count,
            total_unread_count=total_unread_count,
        ),
    )


@router.get("/me/open-quota/buy-posts", response_model=ResponseModel[UserOpenQuotaResponse])
async def get_my_buy_post_open_quota(
    current_user: UserSchema = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取当前登录用户委托帖子可开启剩余额度。"""
    limit = int(getattr(settings, "MAX_OPEN_BUY_POSTS_PER_USER"))
    used = await PostService.count_open_posts_by_direction(db, current_user.user_id, Direction.BUY)
    return ResponseModel(
        code=settings.SUCCESS_CODE,
        message=await _build_open_quota_response(limit=limit, used=used),
    )


@router.get("/me/open-quota/sell-posts", response_model=ResponseModel[UserOpenQuotaResponse])
async def get_my_sell_post_open_quota(
    current_user: UserSchema = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取当前登录用户服务帖子可开启剩余额度。"""
    limit = int(getattr(settings, "MAX_OPEN_SELL_POSTS_PER_USER"))
    used = await PostService.count_open_posts_by_direction(db, current_user.user_id, Direction.SELL)
    return ResponseModel(
        code=settings.SUCCESS_CODE,
        message=await _build_open_quota_response(limit=limit, used=used),
    )


@router.get("/me/open-quota/goods", response_model=ResponseModel[UserOpenQuotaResponse])
async def get_my_goods_open_quota(
    current_user: UserSchema = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取当前登录用户商品可开启剩余额度。"""
    limit = int(getattr(settings, "MAX_OPEN_GOODS_PER_USER"))
    used = await GoodsService.count_open_goods_by_user(db, current_user.user_id)
    return ResponseModel(
        code=settings.SUCCESS_CODE,
        message=await _build_open_quota_response(limit=limit, used=used),
    )


@router.post("/me/histories/delete", response_model=ResponseModel[dict])
async def delete_my_histories(
    payload: HistoryDeletePayload,
    bg_tasks: BackgroundTasks,
    current_user: UserSchema = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis_client=Depends(get_redis),
):
    """多维聚合清理历史足迹（支持单条、时间段、全量三种模式）。

    禁止使用 DELETE 带 Body，采用 POST 承载清理载荷以防网关裁剪。
    """
    result = await SocialService.delete_user_history(
        db=db,
        user_id=current_user.user_id,
        payload=payload,
        bg_tasks=bg_tasks,
        redis_client=redis_client,
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
    # 黑名单拦截
    if current_user:
        if await BlacklistService.is_blocked(db, user_id, current_user.user_id):
            raise BusinessHTTPException(code=102, msg="由于对方的隐私设置，无法访问该主页")
    reputation = await ReputationService.get_user_reputation(redis, db, user_id)
    return ResponseModel(code=settings.SUCCESS_CODE, message=reputation)


@router.get("/{user_id}/reviews", response_model=ResponseModel[dict])
async def get_user_reviews(
    user_id: int,
    role: Literal["CARRIER", "CLIENT"] = "CARRIER",
    offset: int = 0,
    limit: int = 20,
    current_user: Optional[UserSchema] = Depends(get_current_user_optional),
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
    # 黑名单拦截
    if current_user:
        if await BlacklistService.is_blocked(db, user_id, current_user.user_id):
            raise BusinessHTTPException(code=102, msg="由于对方的隐私设置，无法访问该主页")
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

    # 黑名单拦截：若被访用户拉黑了当前访客，禁止查看
    if current_user:
        if await BlacklistService.is_blocked(db, user_id, current_user.user_id):
            raise BusinessHTTPException(code=102, msg="由于对方的隐私设置，无法访问该主页")

    # Read-Through: Redis user:profile:public:{user_id} cache first
    cache_key = f"user:profile:public:{user_id}"
    try:
        cached = await redis.get(cache_key)
        if cached:
            data = _json.loads(cached)
            valid_payload = _extract_valid_public_profile_cache_payload(data)
            if valid_payload is None:
                await redis.delete(cache_key)
            else:
                return ResponseModel(
                    code=settings.SUCCESS_CODE,
                    message=UserPublicResponse.model_validate(SimpleNamespace(**valid_payload)),
                )
    except Exception as e:
        logger.warning("Swallowed exception in user: %s", e, exc_info=True)

    # Cache miss: query DB then backfill Redis
    user_data = await UserService.get_user_public_with_avatar_url(user_id, db)
    try:
        public_dict = _build_public_profile_cache_payload(user_data, user_id)
        await redis.setex(cache_key, settings.USER_PROFILE_CACHE_TTL,
                          _json.dumps(public_dict, ensure_ascii=False, default=str))
    except Exception as e:
        logger.warning("Swallowed exception in user: %s", e, exc_info=True)
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
    # 刷新 Redis 缓存，管理员更新后目标用户 GET /me 立即生效
    await _refresh_user_profile_cache(user_id, db)
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
    # 清除 Redis 缓存
    try:
        await redis.delete(f"user:profile:cache:{user_id}")
        await redis.delete(f"user:profile:me:{user_id}")
        await redis.delete(f"user:profile:public:{user_id}")
    except Exception as e:
        logger.warning("缓存删除失败 uid=%d: %s", user_id, e, exc_info=True)
    return ResponseModel(
        code=settings.SUCCESS_CODE,
        message={"message": f"用户 {user_id} 已被禁用/删除"},
    )

# ── 手机号绑定 ──────────────────────────────────────────────

@router.post("/me/phone/send-code", response_model=ResponseModel)
async def send_phone_bind_code(
    payload: PhoneSendCodeRequest,
    current_user: UserSchema = Depends(get_current_user),
):
    """发送手机号绑定验证码。

    调用阿里云 SMS 服务向指定手机号发送6位数字验证码，内置60秒防刷节流。
    验证码有效期5分钟，最多尝试3次。
    """
    if SMSService is None:
        raise BusinessHTTPException(code=settings.DATA_GET_FAILED_CODE, msg="短信服务未初始化")
    result = await SMSService.send_code(payload.phone)
    return ResponseModel(code=settings.SUCCESS_CODE, message=result)


@router.post("/me/phone/bind", response_model=ResponseModel)
async def bind_phone(
    payload: PhoneBindRequest,
    current_user: UserSchema = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """校验验证码并绑定手机号到当前用户。

    先调用 SMSService.verify_code 核销验证码，验证通过后将 phone 写入
    current_user.phonenumber 并 commit 落库。
    """
    if SMSService is None:
        raise BusinessHTTPException(code=settings.DATA_GET_FAILED_CODE, msg="短信服务未初始化")
    await SMSService.verify_code(payload.phone, payload.code)

    # 写入 phonenumber
    user_obj = await db.get(UserModel, current_user.user_id)
    if not user_obj:
        raise ResourceHTTPException(code=settings.USER_GET_FAILED_CODE, msg="用户不存在")
    user_obj.phonenumber = payload.phone
    await db.commit()

    # 刷新 Redis 缓存，确保 GET /me 返回最新手机号
    await _refresh_user_profile_cache(current_user.user_id, db)

    return ResponseModel(code=settings.SUCCESS_CODE, message={"detail": "手机号绑定成功", "phone": payload.phone})

# ── 联系方式管理 ──────────────────────────────────────────────

@router.get("/me/contacts", response_model=ResponseModel[ContactListResponse])
async def list_my_contacts(
    current_user: UserSchema = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """拉取当前用户配置的所有联系方式。"""
    contacts = await ContactService.list_contacts(db, current_user.user_id)
    return ResponseModel(
        code=settings.SUCCESS_CODE,
        message=ContactListResponse(list=[ContactRead.model_validate(c) for c in contacts]),
    )


@router.post("/me/contacts", response_model=ResponseModel[ContactRead])
async def upsert_my_contact(
    payload: ContactCreate,
    current_user: UserSchema = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """新增或覆盖某种联系方式。同一类型（PHONE/WECHAT/QQ）只能有一条记录。"""
    contact = await ContactService.upsert_contact(
        db, current_user.user_id, payload.contact_type, payload.contact_value, payload.is_public
    )
    return ResponseModel(code=settings.SUCCESS_CODE, message=ContactRead.model_validate(contact))


@router.delete("/me/contacts/{contact_id}", response_model=ResponseModel)
async def delete_my_contact(
    contact_id: int,
    current_user: UserSchema = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """定点删除某个联系方式渠道。"""
    await ContactService.delete_contact(db, current_user.user_id, contact_id)
    return ResponseModel(code=settings.SUCCESS_CODE, message={"detail": "联系方式已删除"})


# ── 黑名单管理 ──────────────────────────────────────────────

@router.post("/me/blacklist", response_model=ResponseModel)
async def add_blacklist(
    payload: BlacklistCreate,
    current_user: UserSchema = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """拉黑目标用户。"""
    entry = await BlacklistService.add_to_blacklist(db, current_user.user_id, payload.target_id)
    return ResponseModel(code=settings.SUCCESS_CODE, message={"detail": "已拉黑", "target_id": payload.target_id})


@router.delete("/me/blacklist/{target_id}", response_model=ResponseModel)
async def remove_blacklist(
    target_id: int,
    current_user: UserSchema = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """解除拉黑。"""
    await BlacklistService.remove_from_blacklist(db, current_user.user_id, target_id)
    return ResponseModel(code=settings.SUCCESS_CODE, message={"detail": "已解除拉黑", "target_id": target_id})


@router.get("/me/blacklist", response_model=ResponseModel[BlacklistListResponse])
async def list_my_blacklist(
    page: int = 1,
    page_size: int = 20,
    current_user: UserSchema = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """分页拉取当前用户的黑名单列表。"""
    result = await BlacklistService.list_blacklist(db, current_user.user_id, page, page_size)
    return ResponseModel(code=settings.SUCCESS_CODE, message=BlacklistListResponse(**result))
