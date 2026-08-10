import asyncio
import json
import logging
import random

from alibabacloud_dysmsapi20170525.client import Client as Dysmsapi20170525Client
from alibabacloud_tea_openapi import models as open_api_models
from alibabacloud_dysmsapi20170525 import models as dysmsapi_models
from alibabacloud_tea_util import models as util_models
from app.db import redis
from app.core import BusinessHTTPException, acquire_redis_lock, get_now, release_redis_lock, set_if_absent, settings

logger = logging.getLogger(__name__)

class SMSService:
    """短信验证码服务适配器。负责生成、发送、校验验证码与防刷控制。

    Redis Keys:
      - sms:code:{phone} -> {"code": str, "timestamp": float, "attempts": int}
      - sms:rate:{phone} -> 1 (TTL=RATE_LIMIT_SECONDS)
      - sms:verified:{phone} -> 1 (TTL=VERIFIED_WINDOW_SECONDS)
    """

    CODE_TTL_SECONDS = getattr(settings, "SMS_CODE_TTL_SECONDS", 300)  # 验证码有效期，默认5分钟
    RATE_LIMIT_SECONDS = getattr(settings, "SMS_RATE_LIMIT_SECONDS", 60)  # 发送间隔限制，默认60秒
    VERIFIED_WINDOW_SECONDS = getattr(settings, "SMS_VERIFIED_WINDOW_SECONDS", 900)  # 验证通过后的注册窗口，默认15分钟

    @staticmethod
    def _create_client() -> Dysmsapi20170525Client:
        """创建阿里云短信客户端。"""
        ak = settings.ALI_ACCESS_KEY_ID
        sk = settings.ALI_ACCESS_KEY_SECRET
        if not ak or not sk:
            raise BusinessHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="短信服务未配置，缺少 AK/SK"
            )
        config = open_api_models.Config(
            access_key_id=ak,
            access_key_secret=sk,
            endpoint="dysmsapi.aliyuncs.com",
        )
        return Dysmsapi20170525Client(config)

    @staticmethod
    def _generate_code(length: int = 6) -> str:
        return "".join(str(random.randint(0, 9)) for _ in range(length))

    @classmethod
    async def send_code(cls, phone: str) -> dict:
        # 基础节流：每手机号 60s
        rate_key = f"sms:rate:{phone}"
        rate_acquired = await set_if_absent(redis, rate_key, "1", ex=cls.RATE_LIMIT_SECONDS)
        if not rate_acquired:
            raise BusinessHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="发送过于频繁，请稍后再试"
            )

        code = cls._generate_code(6)
        data = {"code": code, "timestamp": get_now().timestamp(), "attempts": 0}
        await redis.set(f"sms:code:{phone}", json.dumps(data), ex=cls.CODE_TTL_SECONDS)

        # 发送短信
        client = cls._create_client()
        template_code = settings.SMS_TEMPLATE_CODE
        sign_name = settings.SMS_SIGN_NAME
        if not template_code or not sign_name:
            raise BusinessHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="短信模板或签名未配置"
            )
        req = dysmsapi_models.SendSmsRequest(
            phone_numbers=phone,
            sign_name=sign_name,
            template_code=template_code,
            template_param=json.dumps({"code": code}),
        )
        runtime = util_models.RuntimeOptions(
            connect_timeout=500,
            read_timeout=500,
        )
        try:
            response = await asyncio.to_thread(client.send_sms_with_options, req, runtime)
            if response.body.code != "OK":
                await redis.delete(f"sms:code:{phone}")
                err_msg = getattr(response.body, "message", "") or "未知错误"
                raise BusinessHTTPException(
                    code=settings.DATA_GET_FAILED_CODE,
                    msg=f"短信发送失败：{err_msg}"
                )
        except BusinessHTTPException:
            raise
        except Exception:
            await redis.delete(f"sms:code:{phone}")
            logger.warning("短信 SDK 发送失败", exc_info=True)
            raise BusinessHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="短信发送失败，请稍后重试"
            )
        return {"detail": "验证码已发送"}

    @classmethod
    async def verify_code(cls, phone: str, input_code: str) -> dict:
        code_key = f"sms:code:{phone}"
        lock_key = f"lock:{code_key}"
        lock_token = await acquire_redis_lock(redis, lock_key, ttl_seconds=3, wait_timeout_seconds=1.0)
        if lock_token is None:
            raise BusinessHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="验证码校验繁忙，请稍后重试"
            )

        try:
            raw = await redis.get(code_key)
            if not raw:
                raise BusinessHTTPException(
                    code=settings.DATA_GET_FAILED_CODE,
                    msg="验证码错误或已过期"
                )
            try:
                data = json.loads(raw)
                if not isinstance(data, dict):
                    raise ValueError("sms code data is not a dict")
            except Exception:
                await redis.delete(code_key)
                raise BusinessHTTPException(
                    code=settings.DATA_GET_FAILED_CODE,
                    msg="验证码状态异常，请重试"
                )

            attempts = int(data.get("attempts", 0))
            if attempts >= 3:
                await redis.delete(code_key)
                raise BusinessHTTPException(
                    code=settings.DATA_GET_FAILED_CODE,
                    msg="尝试次数过多，请重新获取验证码"
                )

            if get_now().timestamp() - float(data.get("timestamp", 0)) > cls.CODE_TTL_SECONDS:
                await redis.delete(code_key)
                raise BusinessHTTPException(
                    code=settings.DATA_GET_FAILED_CODE,
                    msg="验证码已过期，请重新获取"
                )

            if str(input_code) != str(data.get("code")):
                data["attempts"] = attempts + 1
                await redis.set(code_key, json.dumps(data), ex=cls.CODE_TTL_SECONDS)
                left = max(0, 3 - data["attempts"])
                raise BusinessHTTPException(
                    code=settings.DATA_GET_FAILED_CODE,
                    msg=f"验证码错误，还剩{left}次机会"
                )

            await redis.delete(code_key)
            await redis.set(f"sms:verified:{phone}", "1", ex=cls.VERIFIED_WINDOW_SECONDS)
            return {"detail": "验证码验证通过"}
        finally:
            await release_redis_lock(redis, lock_key, lock_token)
