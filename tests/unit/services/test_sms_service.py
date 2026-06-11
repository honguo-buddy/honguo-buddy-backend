from __future__ import annotations

import json
import sys
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.exception_handler import BusinessHTTPException


# ── dysmsapi SDK 替身 ──────────────────────────────────────────
if "alibabacloud_dysmsapi20170525.client" not in sys.modules:
    client_module = types.ModuleType("alibabacloud_dysmsapi20170525.client")

    class DummyClient:
        def __init__(self, *_args, **_kwargs):
            return None

        def send_sms_with_options(self, *_args, **_kwargs):
            return SimpleNamespace(body=SimpleNamespace(code="OK"))

    client_module.Client = DummyClient
    sys.modules["alibabacloud_dysmsapi20170525.client"] = client_module

if "alibabacloud_tea_openapi" not in sys.modules:
    openapi_module = types.ModuleType("alibabacloud_tea_openapi")

    class DummyConfig:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    openapi_module.models = SimpleNamespace(Config=DummyConfig)
    sys.modules["alibabacloud_tea_openapi"] = openapi_module

if "alibabacloud_dysmsapi20170525" not in sys.modules:
    dysmsapi_module = types.ModuleType("alibabacloud_dysmsapi20170525")

    class DummySendReq:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    dysmsapi_module.models = SimpleNamespace(SendSmsRequest=DummySendReq)
    sys.modules["alibabacloud_dysmsapi20170525"] = dysmsapi_module

if "alibabacloud_tea_util" not in sys.modules:
    util_module = types.ModuleType("alibabacloud_tea_util")

    class DummyRuntime:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    util_module.models = SimpleNamespace(RuntimeOptions=DummyRuntime)
    sys.modules["alibabacloud_tea_util"] = util_module

from app.services.sms_service import SMSService


pytestmark = pytest.mark.asyncio


async def test_create_client_requires_ak_sk(monkeypatch):
    monkeypatch.setattr("app.services.sms_service.settings.ALI_ACCESS_KEY_ID", "")
    monkeypatch.setattr("app.services.sms_service.settings.ALI_ACCESS_KEY_SECRET", "")

    with pytest.raises(BusinessHTTPException) as exc_info:
        SMSService._create_client()

    assert "短信服务未配置" in exc_info.value.detail["msg"]


async def test_create_client_success(monkeypatch):
    fake_config = object()
    fake_client = object()
    monkeypatch.setattr("app.services.sms_service.settings.ALI_ACCESS_KEY_ID", "ak")
    monkeypatch.setattr("app.services.sms_service.settings.ALI_ACCESS_KEY_SECRET", "sk")

    with patch("app.services.sms_service.open_api_models.Config", return_value=fake_config) as config_mock:
        with patch("app.services.sms_service.Dysmsapi20170525Client", return_value=fake_client) as client_mock:
            client = SMSService._create_client()

    assert client is fake_client
    assert config_mock.called
    assert client_mock.called


async def test_send_code_rate_limit_and_template_validation(monkeypatch, fake_redis):
    monkeypatch.setattr("app.services.sms_service.redis", fake_redis)
    await fake_redis.set("sms:rate:13800138000", "1")

    with pytest.raises(BusinessHTTPException) as rate_err:
        await SMSService.send_code("13800138000")
    assert "发送过于频繁" in rate_err.value.detail["msg"]

    await fake_redis.delete("sms:rate:13800138000")
    monkeypatch.setattr("app.services.sms_service.settings.SMS_TEMPLATE_CODE", "")
    monkeypatch.setattr("app.services.sms_service.settings.SMS_SIGN_NAME", "")

    with patch.object(SMSService, "_create_client", return_value=SimpleNamespace(send_sms_with_options=MagicMock())):
        with pytest.raises(BusinessHTTPException) as config_err:
            await SMSService.send_code("13800138000")
    assert "短信模板或签名未配置" in config_err.value.detail["msg"]


async def test_send_code_provider_failure_and_success(monkeypatch, fake_redis):
    import app.services.sms_service as sms_module

    monkeypatch.setattr("app.services.sms_service.redis", fake_redis)
    monkeypatch.setattr("app.services.sms_service.settings.SMS_TEMPLATE_CODE", "TEMPLATE")
    monkeypatch.setattr("app.services.sms_service.settings.SMS_SIGN_NAME", "SIGN")
    monkeypatch.setattr(SMSService, "_generate_code", lambda length=6: "123456")

    to_thread_calls = []

    async def fake_to_thread(func, *args, **kwargs):
        to_thread_calls.append((func, args, kwargs))
        return func(*args, **kwargs)

    to_thread_mock = AsyncMock(side_effect=fake_to_thread)
    monkeypatch.setattr(sms_module, "asyncio", SimpleNamespace(to_thread=to_thread_mock), raising=False)

    # 模拟 SDK 抛出异常
    fail_client = SimpleNamespace(send_sms_with_options=MagicMock(side_effect=RuntimeError("provider error")))
    with patch.object(SMSService, "_create_client", return_value=fail_client):
        with pytest.raises(BusinessHTTPException) as send_err:
            await SMSService.send_code("13800138001")
    assert "短信发送失败" in send_err.value.detail["msg"]
    assert "provider error" not in send_err.value.detail["msg"]
    assert await fake_redis.get("sms:code:13800138001") is None

    # 模拟阿里云返回业务失败（code != "OK"）
    await fake_redis.delete("sms:rate:13800138001")
    fail_body = SimpleNamespace(code="isv.BUSINESS_LIMIT_CONTROL", message="触发业务限流")
    fail_resp = SimpleNamespace(body=fail_body)
    bad_client = SimpleNamespace(send_sms_with_options=MagicMock(return_value=fail_resp))
    with patch.object(SMSService, "_create_client", return_value=bad_client):
        with pytest.raises(BusinessHTTPException) as biz_err:
            await SMSService.send_code("13800138001")
    assert "触发业务限流" in biz_err.value.detail["msg"]
    assert await fake_redis.get("sms:code:13800138001") is None

    # 模拟发送成功
    await fake_redis.delete("sms:rate:13800138001")
    ok_body = SimpleNamespace(code="OK", message="OK")
    ok_resp = SimpleNamespace(body=ok_body)
    ok_client = SimpleNamespace(send_sms_with_options=MagicMock(return_value=ok_resp))
    with patch.object(SMSService, "_create_client", return_value=ok_client):
        result = await SMSService.send_code("13800138001")

    assert result["detail"] == "验证码已发送"
    assert json.loads(await fake_redis.get("sms:code:13800138001"))["code"] == "123456"
    assert to_thread_mock.await_count == 3
    assert all(len(call[1]) == 2 for call in to_thread_calls)
    _, (_, runtime), _ = to_thread_calls[-1]
    if hasattr(runtime, "kwargs"):
        assert runtime.kwargs["connect_timeout"] == 500
        assert runtime.kwargs["read_timeout"] == 500
    else:
        assert runtime.connect_timeout == 500
        assert runtime.read_timeout == 500


async def test_verify_code_error_paths(monkeypatch, fake_redis):
    monkeypatch.setattr("app.services.sms_service.redis", fake_redis)

    with pytest.raises(BusinessHTTPException) as missing:
        await SMSService.verify_code("13800138002", "111111")
    assert "验证码错误或已过期" in missing.value.detail["msg"]

    await fake_redis.set("sms:code:13800138002", "bad-json")
    with pytest.raises(BusinessHTTPException) as malformed:
        await SMSService.verify_code("13800138002", "111111")
    assert "验证码状态异常" in malformed.value.detail["msg"]

    await fake_redis.set(
        "sms:code:13800138002",
        json.dumps({"code": "111111", "timestamp": 9999999999, "attempts": 3}),
    )
    with pytest.raises(BusinessHTTPException) as too_many:
        await SMSService.verify_code("13800138002", "111111")
    assert "尝试次数过多" in too_many.value.detail["msg"]

    await fake_redis.set(
        "sms:code:13800138002",
        json.dumps({"code": "111111", "timestamp": 0.0, "attempts": 0}),
    )
    with patch("app.services.sms_service.get_now", return_value=SimpleNamespace(timestamp=lambda: SMSService.CODE_TTL_SECONDS + 1.0)):
        with pytest.raises(BusinessHTTPException) as expired:
            await SMSService.verify_code("13800138002", "111111")
    assert "验证码已过期" in expired.value.detail["msg"]


async def test_verify_code_wrong_then_success(monkeypatch, fake_redis):
    monkeypatch.setattr("app.services.sms_service.redis", fake_redis)
    monkeypatch.setattr("app.services.sms_service.get_now", lambda: SimpleNamespace(timestamp=lambda: 1000.0))
    await fake_redis.set(
        "sms:code:13800138003",
        json.dumps({"code": "222222", "timestamp": 999.0, "attempts": 1}),
    )

    with pytest.raises(BusinessHTTPException) as wrong:
        await SMSService.verify_code("13800138003", "111111")
    assert "还剩1次机会" in wrong.value.detail["msg"]

    await fake_redis.set(
        "sms:code:13800138003",
        json.dumps({"code": "222222", "timestamp": 999.0, "attempts": 0}),
    )
    result = await SMSService.verify_code("13800138003", "222222")

    assert result["detail"] == "验证码验证通过"
    assert await fake_redis.get("sms:verified:13800138003") == "1"
