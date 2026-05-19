import logging
from typing import Optional, Union

from fastapi import APIRouter, Depends, Request
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import settings, AuthHTTPException, BusinessHTTPException
from app.db import get_db, redis, User
from app.schemas import (
    EmailSendVerifyCodeRequest,
    EmailVerifyCodeRequest,
    WxLoginRequest,
    AuthErrorResponse,
    ResponseModel,
    user as UserSchema,
)
from app.services import AuthService

logger = logging.getLogger(__name__)

router = APIRouter()
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/swagger-login", auto_error=False)


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> UserSchema:
    """基于 Bearer Token 获取当前用户。"""
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
    except JWTError:
        raise AuthHTTPException(
            code=settings.TOKEN_INVALID_CODE,
            msg="Token无效或已失效",
            status_code=401,
        )

    user_res = await db.execute(
        select(User).where(
            and_(
                User.user_id == int(cached_user_id),
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

    return UserSchema.model_validate(db_user)


async def get_current_user_optional(
    token: Optional[str] = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> Optional[UserSchema]:
    if not token:
        return None

    try:
        cached_user_id = await redis.get(f"token:{token}")
        if not cached_user_id:
            return None

        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.TOKEN_ALGORITHM])
        if str(payload.get("sub")) != str(cached_user_id):
            return None

        user_res = await db.execute(
            select(User).where(
                and_(
                    User.user_id == int(cached_user_id),
                    User.is_deleted == False,
                    User.is_active == True,
                )
            )
        )
        db_user = user_res.scalar_one_or_none()
        return UserSchema.model_validate(db_user) if db_user else None
    except Exception:
        return None


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
    return await AuthService.swagger_login(
        db=db,
        wx_id=wx_id,
        password=password,
        login_ip=request.client.host if request.client else None,
    )


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
