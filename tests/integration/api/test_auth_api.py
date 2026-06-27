"""认证模块的集成测试（API 层）"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

from app.core import settings
from app.models import User
from tests.helpers import assert_api_error, assert_api_success

pytestmark = pytest.mark.asyncio


def _assert_success_payload(response_json: dict):
    if "code" in response_json:
        return assert_api_success(response_json)
    return response_json


class TestSwaggerLogin:
    async def test_swagger_login_with_debug_password_success(self, client: AsyncClient, test_user: User):
        response = await client.post(
            "/auth/swagger-login",
            json={"wx_id": test_user.wechat_openid, "password": settings.DEBUG_MASTER_PASSWORD},
        )

        assert response.status_code == 200
        payload = _assert_success_payload(response.json())
        assert "access_token" in payload or "token" in payload

    async def test_swagger_login_blank_wx_id_returns_business_error(self, client: AsyncClient):
        response = await client.post(
            "/auth/swagger-login",
            json={"wx_id": "", "password": settings.DEBUG_MASTER_PASSWORD},
        )

        assert response.status_code == 200
        message = assert_api_error(response.json(), code=settings.REQ_ERROR_CODE)
        assert "wx_id 不能为空" in message["msg"]

    async def test_swagger_login_invalid_openid(self, client: AsyncClient):
        response = await client.post(
            "/auth/swagger-login",
            json={"wx_id": "nonexistent_openid", "password": settings.DEBUG_MASTER_PASSWORD},
        )

        assert response.status_code == 200
        assert response.json()["code"] == settings.LOGIN_FAILED_CODE

    async def test_swagger_login_wrong_password(self, client: AsyncClient, test_user: User):
        response = await client.post(
            "/auth/swagger-login",
            json={"wx_id": test_user.wechat_openid, "password": "wrong_password_12345"},
        )

        assert response.status_code == 200
        assert response.json()["code"] == settings.LOGIN_FAILED_CODE

    async def test_swagger_login_form_urlencoded(self, client: AsyncClient, test_user: User):
        response = await client.post(
            "/auth/swagger-login",
            data={"wx_id": test_user.wechat_openid, "password": settings.DEBUG_MASTER_PASSWORD},
        )

        assert response.status_code == 200
        _assert_success_payload(response.json())

    async def test_swagger_login_query_params(self, client: AsyncClient, test_user: User):
        response = await client.post(
            f"/auth/swagger-login?wx_id={test_user.wechat_openid}&password={settings.DEBUG_MASTER_PASSWORD}"
        )

        assert response.status_code == 200
        _assert_success_payload(response.json())


class TestGetCurrentUser:
    async def test_get_current_user_with_valid_token(
        self,
        client: AsyncClient,
        test_user: User,
        test_user_token: str,
        fake_redis,
    ):
        await fake_redis.set(f"token:{test_user_token}", str(test_user.user_id))
        await fake_redis.set(f"user_token:{test_user.user_id}", test_user_token)

        response = await client.get("/users/info", headers={"Authorization": f"Bearer {test_user_token}"})

        assert response.status_code == 200
        body = response.json()
        assert body["code"] == settings.SUCCESS_CODE
        assert body["message"]["userName"] == test_user.user_name

    async def test_get_current_user_with_invalid_token(self, client: AsyncClient):
        response = await client.get("/users/info", headers={"Authorization": "Bearer invalid_token_12345"})

        assert response.status_code == 200
        assert response.json()["code"] == settings.TOKEN_INVALID_CODE

    async def test_get_current_user_without_token(self, client: AsyncClient):
        response = await client.get("/users/info")

        assert response.status_code == 200
        assert response.json()["code"] == settings.TOKEN_INVALID_CODE

    async def test_get_current_user_deleted_user(
        self,
        client: AsyncClient,
        db_session,
        test_user: User,
        test_user_token: str,
        fake_redis,
    ):
        test_user.is_deleted = True
        await db_session.flush()
        await fake_redis.set(f"token:{test_user_token}", str(test_user.user_id))

        response = await client.get("/users/info", headers={"Authorization": f"Bearer {test_user_token}"})

        assert response.status_code == 200
        assert response.json()["code"] == settings.TOKEN_INVALID_CODE

    async def test_get_current_user_inactive_user(
        self,
        client: AsyncClient,
        db_session,
        test_user: User,
        test_user_token: str,
        fake_redis,
    ):
        test_user.is_active = False
        await db_session.flush()
        await fake_redis.set(f"token:{test_user_token}", str(test_user.user_id))

        response = await client.get("/users/info", headers={"Authorization": f"Bearer {test_user_token}"})

        assert response.status_code == 200
        assert response.json()["code"] == settings.TOKEN_INVALID_CODE

    async def test_get_current_user_info_still_allows_logged_in_unverified_user(
        self,
        client: AsyncClient,
        db_session,
        test_user: User,
        test_user_token: str,
        fake_redis,
    ):
        test_user.phonenumber = None
        test_user.is_verified = False
        await db_session.flush()
        await fake_redis.set(f"token:{test_user_token}", str(test_user.user_id))
        await fake_redis.set(f"user_token:{test_user.user_id}", test_user_token)

        response = await client.get("/users/info", headers={"Authorization": f"Bearer {test_user_token}"})

        assert response.status_code == 200
        body = response.json()
        assert body["code"] == settings.SUCCESS_CODE
        assert body["message"]["userName"] == test_user.user_name


class TestLogout:
    async def test_logout_with_valid_token(
        self,
        client: AsyncClient,
        test_user: User,
        test_user_token: str,
        fake_redis,
    ):
        await fake_redis.set(f"token:{test_user_token}", str(test_user.user_id))
        await fake_redis.set(f"user_token:{test_user.user_id}", test_user_token)

        response = await client.post("/auth/logout", headers={"Authorization": f"Bearer {test_user_token}"})

        assert response.status_code == 200
        assert response.json()["code"] == settings.SUCCESS_CODE

    async def test_logout_without_token(self, client: AsyncClient):
        response = await client.post("/auth/logout")

        assert response.status_code == 200
        assert response.json()["code"] == settings.TOKEN_INVALID_CODE


class TestWxLogin:
    async def test_wx_login_or_register_new_user(self, client: AsyncClient):
        fake_user = MagicMock()
        fake_user.user_id = 3001
        fake_user.user_name = "用户测试"
        fake_user.user_type = None
        fake_user.is_active = True
        fake_user.is_deleted = False

        with patch("app.services.auth_service.httpx.AsyncClient") as mock_http_client, patch(
            "app.services.auth_service.AuthService._create_user", new=AsyncMock(return_value=fake_user)
        ):
            mock_response = MagicMock()
            mock_response.json.return_value = {"session_key": "test_session_key", "openid": "test_openid_123"}
            mock_response.raise_for_status = MagicMock(return_value=None)

            mock_http_instance = AsyncMock()
            mock_http_instance.get = AsyncMock(return_value=mock_response)
            mock_http_instance.__aenter__ = AsyncMock(return_value=mock_http_instance)
            mock_http_instance.__aexit__ = AsyncMock(return_value=None)
            mock_http_client.return_value = mock_http_instance

            response = await client.post("/auth/wxLogin", json={"code": "test_code_123"})

            assert response.status_code == 200
            assert_api_success(response.json())

    async def test_wx_login_existing_user(self, client: AsyncClient, test_user: User):
        with patch("app.services.auth_service.httpx.AsyncClient") as mock_http_client:
            mock_response = MagicMock()
            mock_response.json.return_value = {"session_key": "test_session_key", "openid": test_user.wechat_openid}
            mock_response.raise_for_status = MagicMock(return_value=None)

            mock_http_instance = AsyncMock()
            mock_http_instance.get = AsyncMock(return_value=mock_response)
            mock_http_instance.__aenter__ = AsyncMock(return_value=mock_http_instance)
            mock_http_instance.__aexit__ = AsyncMock(return_value=None)
            mock_http_client.return_value = mock_http_instance

            response = await client.post("/auth/wxLogin", json={"code": "test_code_123"})

            assert response.status_code == 200
            assert_api_success(response.json())

    async def test_wx_login_empty_code_is_validation_error(self, client: AsyncClient):
        response = await client.post("/auth/wxLogin", json={"code": ""})

        assert response.status_code == 200
        assert_api_error(response.json(), code=settings.REQ_ERROR_CODE)


class TestEmailVerification:
    async def test_send_email_verify_code_with_valid_token(
        self,
        client: AsyncClient,
        test_user: User,
        test_user_token: str,
        fake_redis,
    ):
        await fake_redis.set(f"token:{test_user_token}", str(test_user.user_id))
        await fake_redis.set(f"user_token:{test_user.user_id}", test_user_token)

        with patch("app.services.auth_service.send_email", return_value=True):
            response = await client.post(
                "/auth/email/send-verify-code",
                headers={"Authorization": f"Bearer {test_user_token}"},
                json={"email": "newemail@example.com"},
            )

        assert response.status_code == 200
        assert_api_success(response.json())

    async def test_send_email_verify_code_without_token(self, client: AsyncClient):
        response = await client.post("/auth/email/send-verify-code", json={"email": "newemail@example.com"})

        assert response.status_code == 200
        assert response.json()["code"] == settings.TOKEN_INVALID_CODE

    async def test_verify_email_code_with_valid_token(
        self,
        client: AsyncClient,
        test_user: User,
        test_user_token: str,
        fake_redis,
    ):
        await fake_redis.set(f"token:{test_user_token}", str(test_user.user_id))
        await fake_redis.set(f"user_token:{test_user.user_id}", test_user_token)
        await fake_redis.set(
            f"email_verify_code:{test_user.email}",
            json.dumps({"code": "123456", "timestamp": 9999999999, "attempts": 0, "user_id": test_user.user_id}),
        )

        response = await client.post(
            "/auth/email/verify-code",
            headers={"Authorization": f"Bearer {test_user_token}"},
            json={"email": test_user.email, "code": "123456"},
        )

        assert response.status_code == 200
        assert_api_success(response.json())

    async def test_verify_email_code_without_token(self, client: AsyncClient):
        response = await client.post(
            "/auth/email/verify-code",
            json={"email": "test@example.com", "code": "123456"},
        )

        assert response.status_code == 200
        assert response.json()["code"] == settings.TOKEN_INVALID_CODE

    async def test_verify_email_code_marks_campus_email_verified(
        self,
        client: AsyncClient,
        test_user: User,
        test_user_token: str,
        fake_redis,
        db_session,
    ):
        await fake_redis.set(f"token:{test_user_token}", str(test_user.user_id))
        await fake_redis.set(f"user_token:{test_user.user_id}", test_user_token)
        campus_email = "student@bjtu.edu.cn"
        await fake_redis.set(
            f"email_verify_code:{campus_email}",
            json.dumps({"code": "654321", "timestamp": 9999999999, "attempts": 0, "user_id": test_user.user_id}),
        )

        response = await client.post(
            "/auth/email/verify-code",
            headers={"Authorization": f"Bearer {test_user_token}"},
            json={"email": campus_email, "code": "654321"},
        )

        assert response.status_code == 200
        assert_api_success(response.json())
        await db_session.refresh(test_user)
        assert test_user.email == campus_email
        assert test_user.is_verified is True

# ── 意见反馈集成测试 ──────────────────────────────────────────

async def test_submit_feedback_anonymous(client):
    resp = await client.post(
        "/auth/feedback",
        json={"content": "这是一个匿名测试反馈，至少要有十个字才行", "feedback_type": "BUG"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert "感谢" in body["message"]["detail"]


async def test_submit_feedback_authenticated(client, test_user_token):
    resp = await client.post(
        "/auth/feedback",
        json={
            "content": "已登录用户提交的测试反馈，至少十个字",
            "feedback_type": "FEATURE",
            "contact_info": "13800138000",
        },
        headers={"Authorization": f"Bearer {test_user_token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0


async def test_submit_feedback_too_short(client):
    resp = await client.post(
        "/auth/feedback",
        json={"content": "太短"},
    )
    assert resp.status_code == 200  # FastAPI validation errors are still 200 via exception handlers
    body = resp.json()
    assert body["code"] != 0
