import asyncio
import json
import random
import re
import uuid
from typing import Any

import httpx
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import (
    settings,
    AuthHTTPException,
    BusinessHTTPException,
    ResourceHTTPException,
    acquire_redis_lock,
    create_access_token,
    get_now,
    release_redis_lock,
    set_if_absent,
    send_email,
)
from app.db import redis, User, UserType, CreditLog


class AuthService:
    EMAIL_VERIFY_TTL_SECONDS: int = 300
    EMAIL_VERIFY_MAX_ATTEMPTS: int = 3
    EMAIL_VERIFY_RATE_LIMIT_SECONDS: int = 60

    @staticmethod
    def _is_campus_email(email: str) -> bool:
        domain = (email or "").strip().lower().split("@", 1)[-1]
        campus_suffixes = ("bjtu.edu.cn",)
        return any(domain.endswith(suffix) for suffix in campus_suffixes)

    @staticmethod
    async def _persist_user_token(user_id: int, token: str) -> None:
        ttl_seconds = settings.TOKEN_EXPIRE_TIME * 60
        lock_key = f"lock:user_token:{user_id}"
        lock_token = await acquire_redis_lock(redis, lock_key, ttl_seconds=3, wait_timeout_seconds=1.0)
        if lock_token is None:
            raise BusinessHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="登录状态写入繁忙，请稍后重试",
                status_code=503,
            )
        try:
            old_token = await redis.get(f"user_token:{user_id}")
            if old_token and old_token != token:
                await redis.delete(f"token:{old_token}")

            await redis.set(f"token:{token}", str(user_id), ex=ttl_seconds)
            await redis.set(f"user_token:{user_id}", token, ex=ttl_seconds)

            latest_token = await redis.get(f"user_token:{user_id}")
            if latest_token and latest_token != token:
                await redis.delete(f"token:{token}")
        finally:
            await release_redis_lock(redis, lock_key, lock_token)

    @staticmethod
    async def _issue_token_for_user(user: User) -> str:
        token = create_access_token(
            {
                "sub": str(user.user_id),
                "user_name": user.user_name,
                "user_type": user.user_type.value if user.user_type else UserType.USER.value,
            }
        )
        await AuthService._persist_user_token(user.user_id, token)
        return token

    @staticmethod
    def _normalize_user_name(raw_name: str) -> str:
        return (raw_name or "").strip()

    @staticmethod
    async def _scan_redis_keys(redis_client, pattern: str) -> list[str]:
        scan_iter = getattr(redis_client, "scan_iter", None)
        if callable(scan_iter):
            keys: list[str] = []
            async for key in scan_iter(match=pattern, count=200):
                if isinstance(key, (bytes, bytearray)):
                    keys.append(key.decode("utf-8"))
                else:
                    keys.append(str(key))
            return keys

        keys_method = getattr(redis_client, "keys", None)
        if callable(keys_method):
            raw_keys = await keys_method(pattern)
            result: list[str] = []
            for key in raw_keys:
                if isinstance(key, (bytes, bytearray)):
                    result.append(key.decode("utf-8"))
                else:
                    result.append(str(key))
            return result

        raw_data = getattr(redis_client, "_data", None)
        if isinstance(raw_data, dict):
            prefix = pattern[:-1] if pattern.endswith("*") else pattern
            return [str(key) for key in raw_data.keys() if str(key).startswith(prefix)]

        raise BusinessHTTPException(
            code=settings.DATA_GET_FAILED_CODE,
            msg="Redis 客户端不支持键扫描，无法执行旧 token 清理",
            status_code=500,
        )

    @staticmethod
    async def cleanup_stale_token_keys(redis_client=redis) -> dict[str, int]:
        token_keys = await AuthService._scan_redis_keys(redis_client, "token:*")
        summary = {
            "scanned": 0,
            "deleted": 0,
            "kept": 0,
            "skipped_locked": 0,
            "missing_mapping": 0,
        }

        for token_key in token_keys:
            summary["scanned"] += 1
            token = token_key[len("token:"):]
            user_id = await redis_client.get(token_key)
            if user_id is None:
                summary["missing_mapping"] += 1
                continue

            normalized_user_id = str(user_id)
            lock_key = f"lock:user_token:{normalized_user_id}"
            lock_token = await acquire_redis_lock(
                redis_client,
                lock_key,
                ttl_seconds=3,
                wait_timeout_seconds=0.1,
            )
            if lock_token is None:
                summary["skipped_locked"] += 1
                continue

            try:
                latest_token = await redis_client.get(f"user_token:{normalized_user_id}")
                if latest_token is not None and str(latest_token) == token:
                    summary["kept"] += 1
                    continue

                await redis_client.delete(token_key)
                summary["deleted"] += 1
            finally:
                await release_redis_lock(redis_client, lock_key, lock_token)

        return summary

    @staticmethod
    async def _gen_unique_user_uuid(db: AsyncSession) -> bytes:
        for _ in range(32):
            candidate = uuid.uuid4().bytes
            existing = await db.execute(select(User).where(User.user_uuid == candidate))
            if not existing.scalar_one_or_none():
                return candidate
        raise BusinessHTTPException(
            code=settings.DATA_GET_FAILED_CODE,
            msg="用户UUID生成失败，请稍后重试",
            status_code=500,
        )

    @staticmethod
    async def _gen_unique_user_name(db: AsyncSession) -> str:
        for _ in range(100):
            digits = random.randint(10000, 9999999)
            candidate = f"用户{digits}"
            existing = await db.execute(select(User).where(User.user_name == candidate))
            if not existing.scalar_one_or_none():
                return candidate
        return f"用户{uuid.uuid4().int % 10000000:07d}"

    @staticmethod
    async def _create_user(
        db: AsyncSession,
        *,
        user_name: str,
        wechat_openid: str | None,
        is_verified: bool,
    ) -> User:
        db_user = User(
            user_uuid=await AuthService._gen_unique_user_uuid(db),
            user_name=user_name,
            sex="未知",
            email=None,
            phonenumber=None,
            user_type=UserType.USER,
            credit_score=settings.USER_INITIAL_CREDIT_SCORE,
            is_active=True,
            is_deleted=False,
            is_admin=False,
            is_verified=is_verified,
            wechat_openid=wechat_openid,
            wechat_session_key=None,
            wechat_unionid=None,
            wechat_bind_time=None,
        )
        db.add(db_user)
        await db.flush()
        await db.refresh(db_user)
        
        # 初始化用户信用记录
        credit_log = CreditLog(
            user_id=db_user.user_id,
            change_amount=settings.USER_INITIAL_CREDIT_SCORE,
            reason="用户注册时初始化",
        )
        db.add(credit_log)
        await db.flush()
        
        return db_user

    @staticmethod
    async def wx_login_or_register(db: AsyncSession, code: str) -> dict[str, Any]:
        if not settings.WX_APP_ID or not settings.WX_APP_SECRET:
            raise BusinessHTTPException(
                code=settings.LOGIN_FAILED_CODE,
                msg="微信登录配置未完成",
                status_code=500,
            )

        if not code or not code.strip():
            raise BusinessHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="微信登录 code 不能为空",
                status_code=400,
            )

        params = {
            "appid": settings.WX_APP_ID,
            "secret": settings.WX_APP_SECRET,
            "js_code": code.strip(),
            "grant_type": "authorization_code",
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(settings.WX_CODE_TO_SESSION_URL, params=params)
            response.raise_for_status()
            payload = response.json()

        openid = payload.get("openid")
        if not openid:
            err_msg = payload.get("errmsg") or "微信登录失败"
            raise BusinessHTTPException(
                code=settings.LOGIN_FAILED_CODE,
                msg=err_msg,
                status_code=400,
            )

        result = await db.execute(
            select(User).where(and_(User.wechat_openid == openid, User.is_deleted == False))
        )
        db_user = result.scalar_one_or_none()
        is_new_user = False
        if not db_user:
            is_new_user = True
            suggested_name = await AuthService._gen_unique_user_name(db)
            db_user = await AuthService._create_user(
                db,
                user_name=suggested_name,
                wechat_openid=openid,
                is_verified=False,
            )

        if db_user.is_deleted or not db_user.is_active:
            raise AuthHTTPException(
                code=settings.LOGIN_FAILED_CODE,
                msg="账号不可用，请联系管理员",
                status_code=403,
            )

        token = await AuthService._issue_token_for_user(db_user)
        db_user.last_login_time = int(get_now().timestamp())
        await db.commit()

        return {
            "token": token,
            "tokenType": "bearer",
            "expiresIn": settings.TOKEN_EXPIRE_TIME * 60,
            "isNewUser": is_new_user,
            "userId": db_user.user_id,
            "username": db_user.user_name,
        }

    @staticmethod
    async def swagger_login(db: AsyncSession, wx_id: str, password: str, login_ip: str | None) -> dict[str, str]:
        normalized_wx_id = (wx_id or "").strip()
        if not normalized_wx_id:
            raise BusinessHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="wx_id 不能为空",
                status_code=400,
            )

        user_res = await db.execute(
            select(User).where(and_(User.wechat_openid == normalized_wx_id, User.is_deleted == False))
        )
        db_user = user_res.scalar_one_or_none()
        if not db_user:
            raise AuthHTTPException(
                code=settings.LOGIN_FAILED_CODE,
                msg="微信标识不存在",
                status_code=401,
            )

        is_debug_master_login = settings.DEBUG and password == settings.DEBUG_MASTER_PASSWORD
        is_debug_skip_password = settings.DEBUG and settings.DEBUG_SKIP_PASSWORD_CHECK
        if not (is_debug_master_login or is_debug_skip_password):
            raise AuthHTTPException(
                code=settings.LOGIN_FAILED_CODE,
                msg="当前环境仅支持 Debug 通用密码登录",
                status_code=401,
            )

        if not db_user.is_active:
            raise AuthHTTPException(
                code=settings.LOGIN_FAILED_CODE,
                msg="账号不可用",
                status_code=403,
            )

        token = await AuthService._issue_token_for_user(db_user)
        db_user.last_login_ip = login_ip
        db_user.last_login_time = int(get_now().timestamp())
        await db.commit()
        return {"access_token": token, "token_type": "bearer"}

    @staticmethod
    async def send_email_verify_code(db: AsyncSession, current_user_id: int, email: str) -> dict[str, str]:
        email_regex = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
        if not re.match(email_regex, email):
            raise BusinessHTTPException(
                code=settings.UPDATEPROFILE_FAILED_CODE,
                msg="邮箱格式不合法",
                status_code=400,
            )

        email_check = await db.execute(
            select(User).where(
                and_(
                    User.email == email,
                    User.user_id != current_user_id,
                    User.is_deleted == False,
                )
            )
        )
        if email_check.scalar_one_or_none():
            raise BusinessHTTPException(
                code=settings.UPDATEPROFILE_FAILED_CODE,
                msg="该邮箱已被其他用户使用",
                status_code=400,
            )

        rate_key = f"email_verify_rate:{email}"
        rate_acquired = await set_if_absent(
            redis,
            rate_key,
            "1",
            ex=AuthService.EMAIL_VERIFY_RATE_LIMIT_SECONDS,
        )
        if not rate_acquired:
            raise BusinessHTTPException(
                code=settings.UPDATEPROFILE_FAILED_CODE,
                msg="发送过于频繁，请稍后再试",
                status_code=429,
            )

        code = "".join(str(random.randint(0, 9)) for _ in range(6))
        code_data = {
            "code": code,
            "timestamp": get_now().timestamp(),
            "attempts": 0,
            "user_id": current_user_id,
        }
        await redis.set(
            f"email_verify_code:{email}",
            json.dumps(code_data, ensure_ascii=False),
            ex=AuthService.EMAIL_VERIFY_TTL_SECONDS,
        )

        subject = "邮箱验证码"
        body = (
            "<html><body style='font-family:Arial,sans-serif;'>"
            "<p>您好，</p>"
            "<p>您的邮箱验证码为：</p>"
            f"<h2 style='color:#007bff;letter-spacing:5px;'>{code}</h2>"
            "<p>该验证码有效期为5分钟，请勿泄露给他人。</p>"
            "</body></html>"
        )
        if not await asyncio.to_thread(send_email, email, subject, body):
            await redis.delete(f"email_verify_code:{email}")
            raise BusinessHTTPException(
                code=settings.UPDATEPROFILE_FAILED_CODE,
                msg="邮件发送失败，请稍后重试",
                status_code=500,
            )

        local, domain = email.split("@", 1)
        return {
            "detail": "验证码已发送到你的邮箱，请在5分钟内验证",
            "email_masked": f"{local[:1]}***@{domain}",
        }

    @staticmethod
    async def verify_email_code(db: AsyncSession, current_user_id: int, email: str, code: str) -> dict[str, str]:
        code_key = f"email_verify_code:{email}"
        lock_key = f"lock:{code_key}"
        lock_token = await acquire_redis_lock(redis, lock_key, ttl_seconds=3, wait_timeout_seconds=1.0)
        if lock_token is None:
            raise BusinessHTTPException(
                code=settings.UPDATEPROFILE_FAILED_CODE,
                msg="验证码校验繁忙，请稍后重试",
                status_code=429,
            )

        try:
            raw = await redis.get(code_key)
            if not raw:
                raise BusinessHTTPException(
                    code=settings.UPDATEPROFILE_FAILED_CODE,
                    msg="验证码错误或已过期",
                    status_code=400,
                )

            try:
                code_data = json.loads(raw)
            except Exception:
                await redis.delete(code_key)
                raise BusinessHTTPException(
                    code=settings.UPDATEPROFILE_FAILED_CODE,
                    msg="验证码状态异常，请重新获取",
                    status_code=400,
                )

            if int(code_data.get("user_id", -1)) != current_user_id:
                raise BusinessHTTPException(
                    code=settings.UPDATEPROFILE_FAILED_CODE,
                    msg="验证码与当前用户不匹配",
                    status_code=403,
                )

            attempts = int(code_data.get("attempts", 0))
            if attempts >= AuthService.EMAIL_VERIFY_MAX_ATTEMPTS:
                await redis.delete(code_key)
                raise BusinessHTTPException(
                    code=settings.UPDATEPROFILE_FAILED_CODE,
                    msg="尝试次数过多，请重新获取验证码",
                    status_code=400,
                )

            if get_now().timestamp() - float(code_data.get("timestamp", 0)) > AuthService.EMAIL_VERIFY_TTL_SECONDS:
                await redis.delete(code_key)
                raise BusinessHTTPException(
                    code=settings.UPDATEPROFILE_FAILED_CODE,
                    msg="验证码已过期，请重新获取",
                    status_code=400,
                )

            if str(code) != str(code_data.get("code")):
                code_data["attempts"] = attempts + 1
                await redis.set(
                    code_key,
                    json.dumps(code_data, ensure_ascii=False),
                    ex=AuthService.EMAIL_VERIFY_TTL_SECONDS,
                )
                left = max(0, AuthService.EMAIL_VERIFY_MAX_ATTEMPTS - code_data["attempts"])
                raise BusinessHTTPException(
                    code=settings.UPDATEPROFILE_FAILED_CODE,
                    msg=f"验证码错误，还剩{left}次机会",
                    status_code=400,
                )

            user_res = await db.execute(select(User).where(and_(User.user_id == current_user_id, User.is_deleted == False)))
            user = user_res.scalar_one_or_none()
            if not user:
                raise ResourceHTTPException(
                    code=settings.USER_GET_FAILED_CODE,
                    msg="用户不存在",
                    status_code=404,
                )

            user.email = email
            user.is_verified = AuthService._is_campus_email(email)
            await db.commit()
            await db.refresh(user)

            await redis.delete(code_key)
            await redis.delete(f"email_verify_rate:{email}")
            return {"detail": "邮箱绑定成功", "email": email}
        finally:
            await release_redis_lock(redis, lock_key, lock_token)

    @staticmethod
    async def send_admin_login_code(
        db: AsyncSession,
        redis_client,
        email: str,
        background_tasks,
    ) -> dict[str, str]:
        """向管理员邮箱发送6位数字登录验证码。

        安全策略：
        - 仅对 is_admin=True 且未删除的活跃用户发送
        - Redis 限频锁 60 秒，防止暴力刷码
        - 验证码 5 分钟有效期
        - 通过 BackgroundTasks 异步投递邮件，不阻塞主线程
        """
        # 校验管理员身份
        user_result = await db.execute(
            select(User).where(
                and_(
                    User.email == email,
                    User.is_deleted == False,
                    User.is_active == True,
                )
            )
        )
        user = user_result.scalar_one_or_none()
        if not user or not user.is_admin:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="认证失败，非系统授权管理员",
                status_code=403,
            )

        # 限频锁检查
        lock_key = f"admin:login:lock:{email}"
        rate_acquired = await set_if_absent(
            redis_client,
            lock_key,
            "1",
            ex=60,
        )
        if not rate_acquired:
            raise BusinessHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="验证码请求过于频繁，请60秒后再试",
                status_code=429,
            )

        # 生成6位数字验证码
        code = "".join(str(random.randint(0, 9)) for _ in range(6))
        code_key = f"admin:login:code:{email}"

        # 原子性写入 Redis：验证码 + 限频锁
        await redis_client.set(code_key, code, ex=300)  # 5分钟

        # 异步投递邮件
        subject = "管理端登录验证码"
        body = (
            "<html><body style='font-family:Arial,sans-serif;'>"
            "<p>您好，管理员：</p>"
            "<p>您的管理端登录验证码为：</p>"
            f"<h2 style='color:#007bff;letter-spacing:5px;'>{code}</h2>"
            "<p>该验证码有效期为5分钟，请勿泄露给他人。</p>"
            "</body></html>"
        )
        background_tasks.add_task(send_email, email, subject, body)

        local, domain = email.split("@", 1)
        return {
            "detail": "验证码已发送到管理员邮箱，请在5分钟内完成登录",
            "email_masked": f"{local[:1]}***@{domain}",
        }

    @staticmethod
    async def verify_admin_login_code(
        db: AsyncSession,
        redis_client,
        email: str,
        code: str,
    ) -> dict[str, Any]:
        """校验管理员邮箱验证码并签发高权限 JWT Token。

        安全策略：
        - 验证码一次性核销，防止重放攻击
        - 签发前二次验证管理员身份，防止权限瞬时变更
        - 返回结构完全对齐 wxLogin 规范
        """
        code_key = f"admin:login:code:{email}"
        lock_key = f"lock:{code_key}"
        lock_token = await acquire_redis_lock(redis_client, lock_key, ttl_seconds=3, wait_timeout_seconds=1.0)
        if lock_token is None:
            raise BusinessHTTPException(
                code=settings.UPDATEPROFILE_FAILED_CODE,
                msg="验证码校验繁忙，请稍后重试",
                status_code=429,
            )

        try:
            stored_code = await redis_client.get(code_key)
            if not stored_code:
                raise BusinessHTTPException(
                    code=settings.UPDATEPROFILE_FAILED_CODE,
                    msg="验证码错误或已过期",
                    status_code=400,
                )

            if str(stored_code) != str(code):
                raise BusinessHTTPException(
                    code=settings.UPDATEPROFILE_FAILED_CODE,
                    msg="验证码输入错误",
                    status_code=400,
                )

            await redis_client.delete(code_key)
            await redis_client.delete(f"admin:login:lock:{email}")
        finally:
            await release_redis_lock(redis_client, lock_key, lock_token)

        # 二次提取用户实体，重新验证 is_admin 状态
        user_result = await db.execute(
            select(User).where(
                and_(
                    User.email == email,
                    User.is_deleted == False,
                    User.is_active == True,
                )
            )
        )
        user = user_result.scalar_one_or_none()
        if not user or not user.is_admin:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="认证失败，非系统授权管理员",
                status_code=403,
            )

        # 签发管理端高权限 JWT Token
        token = create_access_token(
            {
                "sub": str(user.user_id),
                "is_admin": True,
                "user_type": user.user_type.value if user.user_type else UserType.USER.value,
                "user_name": user.user_name,
            }
        )
        await AuthService._persist_user_token(user.user_id, token)

        return {
            "token": token,
            "userId": user.user_id,
            "user_name": user.user_name,
            "is_admin": True,
            "isNewUser": False,
        }
