import json as _json
import logging
from types import SimpleNamespace
from typing import Optional, Union

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core import settings, AuthHTTPException, BusinessHTTPException
from app.db import get_db, redis, User
from app.schemas import (
    AdminCodeSendRequest,
    AdminLoginRequest,
    EmailSendVerifyCodeRequest,
    EmailVerifyCodeRequest,
    FeedbackCreate,
    WxLoginRequest,
    AuthErrorResponse,
    ResponseModel,
    user as UserSchema,
)
from app.services import AuthService, FeedbackService

logger = logging.getLogger(__name__)

router = APIRouter()
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/swagger-login", auto_error=False)


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> UserSchema:
    """Read-Through gateway shield: Redis profile cache hit first, MySQL fallback only on miss.

    High-frequency dependency injected into every protected route.
    Eliminates mandatory per-request SELECT from user table under traffic spikes.
    """
    if not token:
        raise AuthHTTPException(
            code=settings.TOKEN_INVALID_CODE,
            msg="Token无效或已失效",
            status_code=401,
        )

    try:
        cached_user_id = await redis.get(f"token:{token}")
    except Exception as exc:
        logger.error(f"读取 Redis token 失败: {exc}")
        raise AuthHTTPException(
            code=settings.TOKEN_INVALID_CODE,
            msg="Token无效或已失效",
            status_code=401,
        )

    if not cached_user_id:
        raise AuthHTTPException(
            code=settings.TOKEN_INVALID_CODE,
            msg="Token无效或已失效",
            status_code=401,
        )

    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.TOKEN_ALGORITHM])
        sub = payload.get("sub")
        if not sub or str(sub) != str(cached_user_id):
            raise AuthHTTPException(
                code=settings.TOKEN_INVALID_CODE,
                msg="Token无效或已失效",
                status_code=401,
            )
        latest_token = await redis.get(f"user_token:{sub}")
        if latest_token is not None and str(latest_token) != str(token):
            raise AuthHTTPException(
                code=settings.TOKEN_INVALID_CODE,
                msg="Token无效或已失效",
                status_code=401,
            )
    except JWTError:
        raise AuthHTTPException(
            code=settings.TOKEN_INVALID_CODE,
            msg="Token无效或已失效",
            status_code=401,
        )

    uid = int(cached_user_id)
    cache_key = f"user:profile:cache:{uid}"

    # Step 1: Try Redis profile cache hit (0 DB I/O)
    try:
        cached_raw = await redis.get(cache_key)
        if cached_raw:
            data = _json.loads(cached_raw)
            return UserSchema.model_validate(SimpleNamespace(**data))
    except Exception:
        pass

    # Step 2: Cache miss - single MySQL SELECT
    user_res = await db.execute(
        select(User).where(
            and_(
                User.user_id == uid,
                User.is_deleted == False,
                User.is_active == True,
            )
        )
    )
    db_user = user_res.scalar_one_or_none()
    if not db_user:
        raise AuthHTTPException(
            code=settings.TOKEN_INVALID_CODE,
            msg="Token无效或用户不存在",
            status_code=401,
        )

    # Step 3: Backfill Redis cache with TTL
    try:
        import json as _json
        profile = {
            "user_id": db_user.user_id,
            "user_uuid": db_user.user_uuid.hex() if db_user.user_uuid else "",
            "user_name": db_user.user_name,
            "is_admin": db_user.is_admin,
            "is_verified": db_user.is_verified,
            "email": db_user.email,
            "phonenumber": db_user.phonenumber,
            "user_type": db_user.user_type.value if getattr(db_user.user_type, 'value', None) else str(db_user.user_type),
            "last_login_ip": db_user.last_login_ip,
            "identifier": db_user.user_name,
        }
        await redis.setex(cache_key, settings.USER_PROFILE_CACHE_TTL, _json.dumps(profile, ensure_ascii=False))
    except Exception:
        pass

    return UserSchema.model_validate(db_user)


async def get_current_user_optional(
    token: Optional[str] = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> Optional[UserSchema]:
    """Read-Through optional gateway: same cache-first pattern as get_current_user."""
    if not token:
        return None

    try:
        cached_user_id = await redis.get(f"token:{token}")
        if not cached_user_id:
            return None

        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.TOKEN_ALGORITHM])
        if str(payload.get("sub")) != str(cached_user_id):
            return None
        latest_token = await redis.get(f"user_token:{payload.get('sub')}")
        if latest_token is not None and str(latest_token) != str(token):
            return None

        uid = int(cached_user_id)
        cache_key = f"user:profile:cache:{uid}"

        # Step 1: Try Redis profile cache hit
        try:
            cached_raw = await redis.get(cache_key)
            if cached_raw:
                data = _json.loads(cached_raw)
                return UserSchema.model_validate(SimpleNamespace(**data))
        except Exception:
            pass

        # Step 2: Cache miss - MySQL SELECT
        user_res = await db.execute(
            select(User).where(
                and_(
                    User.user_id == uid,
                    User.is_deleted == False,
                    User.is_active == True,
                )
            )
        )
        db_user = user_res.scalar_one_or_none()
        if not db_user:
            return None

        # Step 3: Backfill Redis cache
        try:
            import json as _json
            profile = {
                "user_id": db_user.user_id,
                "user_uuid": db_user.user_uuid.hex() if db_user.user_uuid else "",
                "user_name": db_user.user_name,
                "is_admin": db_user.is_admin,
                "is_verified": db_user.is_verified,
                "email": db_user.email,
                "phonenumber": db_user.phonenumber,
                "user_type": db_user.user_type.value if getattr(db_user.user_type, 'value', None) else str(db_user.user_type),
                "last_login_ip": db_user.last_login_ip,
                "identifier": db_user.user_name,
            }
            await redis.setex(cache_key, settings.USER_PROFILE_CACHE_TTL, _json.dumps(profile, ensure_ascii=False))
        except Exception:
            pass

        return UserSchema.model_validate(db_user)
    except Exception:
        return None


async def get_current_verified_user(
    current_user: UserSchema = Depends(get_current_user),
) -> UserSchema:
    """获取当前已认证用户：手机号绑定或校园认证二者满足其一。"""
    phone_number = current_user.phonenumber
    has_phone_number = phone_number is not None and str(phone_number).strip() != ""
    if has_phone_number or bool(current_user.is_verified):
        return current_user

    raise AuthHTTPException(
        code=settings.EMAIL_VERIFIED_NEEDED_CODE,
        msg="当前操作需要先完成手机号验证或校园认证",
        status_code=403,
    )


async def _extract_swagger_login_credentials(request: Request) -> tuple[str, str]:
    content_type = (request.headers.get("content-type") or "").lower()

    if "application/json" in content_type:
        payload = await request.json()
        wx_id = str(payload.get("wx_id") or payload.get("username") or "").strip()
        password = str(payload.get("password") or "")
        return wx_id, password

    if "application/x-www-form-urlencoded" in content_type or "multipart/form-data" in content_type:
        form_data = await request.form()
        wx_id = str(form_data.get("wx_id") or form_data.get("username") or "").strip()
        password = str(form_data.get("password") or "")
        return wx_id, password

    wx_id = str(request.query_params.get("wx_id") or request.query_params.get("username") or "").strip()
    password = str(request.query_params.get("password") or "")
    return wx_id, password


@router.post("/wxLogin", response_model=ResponseModel[Union[dict, AuthErrorResponse]])
async def wx_login_or_register(
    payload: WxLoginRequest,
    db: AsyncSession = Depends(get_db),
):
    """微信小程序登录：同一微信标识自动绑定同一账号，不存在则自动注册。"""
    message = await AuthService.wx_login_or_register(db, payload.code)
    logger.info(f"微信登录成功 user_id={message['userId']} new={message['isNewUser']}")

    return ResponseModel(
        code=settings.SUCCESS_CODE,
        message=message,
    )


@router.post(
    "/swagger-login",
    summary="Swagger UI 登录",
    tags=["Auth"],
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "required": ["wx_id", "password"],
                        "properties": {
                            "wx_id": {"type": "string", "description": "微信唯一标识"},
                            "password": {"type": "string", "description": "Debug 通用密码"},
                        },
                    }
                }
            },
        }
    },
)
async def swagger_login(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    供 Swagger Authorize 使用的登录接口。
    当 DEBUG=True 时：
    - 输入 DEBUG_MASTER_PASSWORD 可作为万能密码登录
    - 或启用 DEBUG_SKIP_PASSWORD_CHECK 直接跳过密码校验
    """
    wx_id, password = await _extract_swagger_login_credentials(request)
    result = await AuthService.swagger_login(
        db=db,
        wx_id=wx_id,
        password=password,
        login_ip=request.client.host if request.client else None,
    )
    # 登录后清除旧缓存，下次 GET /me 返回最新 last_login 信息
    try:
        uid = result.get("userId")
        if uid:
            await redis.delete(f"user:profile:cache:{uid}")
            await redis.delete(f"user:profile:me:{uid}")
            await redis.delete(f"user:profile:public:{uid}")
    except Exception as e:
        logger.warning("登录后缓存刷新失败 uid=%s: %s", uid, e, exc_info=True)
    return result


@router.post("/email/send-verify-code", response_model=ResponseModel[Union[dict, AuthErrorResponse]])
async def send_email_verify_code(
    payload: EmailSendVerifyCodeRequest,
    current_user: UserSchema = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    message = await AuthService.send_email_verify_code(
        db=db,
        current_user_id=current_user.user_id,
        email=str(payload.email),
    )
    return ResponseModel(code=settings.SUCCESS_CODE, message=message)


@router.post("/email/verify-code", response_model=ResponseModel[Union[dict, AuthErrorResponse]])
async def verify_email_code(
    payload: EmailVerifyCodeRequest,
    current_user: UserSchema = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    message = await AuthService.verify_email_code(
        db=db,
        current_user_id=current_user.user_id,
        email=str(payload.email),
        code=payload.code,
    )
    # 邮箱绑定成功后清除 Redis 缓存，确保 GET /me 返回最新 email
    try:
        uid = current_user.user_id
        await redis.delete(f"user:profile:cache:{uid}")
        await redis.delete(f"user:profile:me:{uid}")
        await redis.delete(f"user:profile:public:{uid}")
    except Exception as e:
        logger.warning("邮箱绑定后缓存刷新失败 uid=%d: %s", uid, e, exc_info=True)
    return ResponseModel(code=settings.SUCCESS_CODE, message=message)


@router.post("/logout", response_model=ResponseModel[Union[str, AuthErrorResponse]])
async def logout(current_user: UserSchema = Depends(get_current_user)):
    """登出并清理当前用户 token。"""
    try:
        token = await redis.get(f"user_token:{current_user.user_id}")
        if token:
            await redis.delete(f"token:{token}")
            await redis.delete(f"user_token:{current_user.user_id}")
        return ResponseModel(code=settings.SUCCESS_CODE, message="登出成功")
    except Exception as exc:
        logger.error(f"登出失败 user_id={current_user.user_id} err={exc}")
        raise BusinessHTTPException(
            code=settings.DATA_GET_FAILED_CODE,
            msg="登出失败，请稍后重试",
            status_code=500,
        )

@router.post("/admin/send-code", response_model=ResponseModel[Union[dict, AuthErrorResponse]])
async def send_admin_login_code(
    payload: AdminCodeSendRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """管理端发送邮箱验证码（免Token鉴权开放端点）。

    安全设计：若邮箱不存在或非管理员，统一返回模糊错误提示，阻断管理员邮箱枚举攻击。
    """
    try:
        message = await AuthService.send_admin_login_code(
            db=db,
            redis_client=redis,
            email=str(payload.email),
            background_tasks=background_tasks,
        )
        return ResponseModel(code=settings.SUCCESS_CODE, message=message)
    except Exception as e:
        logger.warning(f"管理员验证码发送失败 email={payload.email}: {e}")
        raise


@router.post("/admin/login", response_model=ResponseModel[Union[dict, AuthErrorResponse]])
async def admin_login(
    payload: AdminLoginRequest,
    db: AsyncSession = Depends(get_db),
):
    """管理端邮箱验证码登入（免Token鉴权开放端点）。

    校验通过后签发高权限 JWT Token，返回结构对齐 wxLogin 规范。
    """
    message = await AuthService.verify_admin_login_code(
        db=db,
        redis_client=redis,
        email=str(payload.email),
        code=payload.code,
    )
    logger.info(f"管理员登录成功 user_id={message['userId']}")
    return ResponseModel(code=settings.SUCCESS_CODE, message=message)

@router.post("/feedback", response_model=ResponseModel)
async def submit_feedback(
    payload: FeedbackCreate,
    current_user: Optional[UserSchema] = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    """提交意见反馈（支持匿名）。

    已登录用户自动关联 user_id，未登录用户以匿名方式提交。
    feedback_type 可选值：BUG / FEATURE / OTHER。
    """
    user_id = current_user.user_id if current_user else None
    await FeedbackService.create_feedback(
        db=db,
        content=payload.content,
        feedback_type=payload.feedback_type,
        contact_info=payload.contact_info,
        user_id=user_id,
    )
    return ResponseModel(code=settings.SUCCESS_CODE, message={"detail": "感谢您的反馈，我们会尽快处理"})
